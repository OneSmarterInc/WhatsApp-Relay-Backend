"""Native app authentication using maintained Knox expiring, hashed tokens."""
from datetime import timedelta
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from knox.auth import TokenAuthentication
from knox.models import AuthToken
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .auth_views import profile, payload
from .limits import client_ip, limit
from .models import User


def issue(user):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=user.pk)
        AuthToken.objects.filter(user=user,expiry__lte=timezone.now()).delete()
        # Password-authenticated login replaces the oldest session at capacity.
        # Browser memory-only tokens can otherwise strand all five slots on refresh.
        sessions = AuthToken.objects.filter(user=user).order_by('created', 'digest')
        excess = sessions.count() - 4
        if excess > 0:
            oldest = list(sessions.values_list('digest', flat=True)[:excess])
            AuthToken.objects.filter(user=user, digest__in=oldest).delete()
        record,token=AuthToken.objects.create(user=user,expiry=timedelta(days=7))
    return {'user':profile(user),'token':token,'expiry':record.expiry}

@method_decorator(sensitive_post_parameters(), name='dispatch')
class MobileLogin(APIView):
    authentication_classes=[]
    permission_classes=[AllowAny]
    def post(self,request):
        limit('login-ip',client_ip(request),30,300)
        try:
            data=payload(request._request,['email','password'])
            email=data.get('email','').strip().lower();password=data.get('password','')
            if not email or len(email)>254 or not password or len(password)>256:raise ValueError('Email or password is invalid.')
            limit('login-account',email,10,300)
            user=authenticate(request,email=email,password=password)
            if user is None:raise ValueError('Email or password is incorrect.')
            return Response(issue(user))
        except ValueError as exc:raise ValidationError(str(exc))

@method_decorator(sensitive_post_parameters(), name='dispatch')
class MobileSignup(APIView):
    authentication_classes=[]
    permission_classes=[AllowAny]
    def post(self,request):
        limit('signup-ip',client_ip(request),10,3600)
        try:
            data=payload(request._request,['name','email','password'])
            name=data.get('name','').strip();email=data.get('email','').strip().lower();password=data.get('password','')
            if not name or len(name)>80 or len(email)>254 or len(password)>256:raise ValueError('Enter a valid name, email and password.')
            validate_email(email)
            user=User(name=name,email=email);validate_password(password,user)
            with transaction.atomic():
                user.set_password(password);user.save()
                result=issue(user)
            return Response(result,status=201)
        except IntegrityError:raise ValidationError('Unable to create this account. Try signing in or use another email.')
        except DjangoValidationError as exc:raise ValidationError(' '.join(exc.messages))
        except ValueError as exc:raise ValidationError(str(exc))

class MobileMe(APIView):
    authentication_classes=[TokenAuthentication]
    def get(self,request):return Response({'user':profile(request.user)})

class MobileLogout(APIView):
    authentication_classes=[TokenAuthentication]
    def post(self,request):
        request.auth.delete()
        return Response({'detail':'Signed out.'})
