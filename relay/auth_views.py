import json
import logging
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST
from rest_framework.exceptions import Throttled
from .limits import client_ip, limit
from .models import User

audit = logging.getLogger('relay.audit')


def profile(user):
    return {'id': user.pk, 'name': user.name, 'email': user.email}


def csrf_failure(request, reason=''):
    return JsonResponse({'detail': 'Security token expired. Refresh the page and try again.', 'code': 'csrf_failed'}, status=403)


def payload(request, allowed):
    if request.content_type != 'application/json':
        raise ValueError('Expected a JSON request.')
    if len(request.body) > 4096:
        raise ValueError('Request is too large.')
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('Invalid JSON request.')
    if not isinstance(data, dict) or set(data) - set(allowed) or any(not isinstance(v, str) for v in data.values()):
        raise ValueError('Unsupported fields or values.')
    return data


def throttled(exc):
    response = JsonResponse({'detail': 'Too many attempts. Please try again later.'}, status=429)
    response['Retry-After'] = str(exc.wait)
    return response


@require_GET
@ensure_csrf_cookie
def csrf(request):
    return JsonResponse({'csrfToken': get_token(request)})


@require_GET
def me(request):
    if not request.user.is_authenticated:
        return JsonResponse({'detail': 'Please sign in.'}, status=401)
    return JsonResponse({'user': profile(request.user), 'csrfToken': get_token(request)})


@require_POST
@csrf_protect
@sensitive_post_parameters()
def signup(request):
    try:
        limit('signup-ip', client_ip(request), 10, 3600)
        data = payload(request, ['name', 'email', 'password'])
        email = data.get('email', '').strip().lower()
        name = data.get('name', '').strip()
        password = data.get('password', '')
        if not name or len(name) > 80 or len(email) > 254 or len(password) > 256:
            raise ValueError('Enter a name (up to 80 characters), valid email and password (up to 256 characters).')
        validate_email(email)
        candidate = User(email=email, name=name)
        validate_password(password, candidate)
        try:
            with transaction.atomic():
                candidate.set_password(password)
                candidate.save()
        except IntegrityError:
            return JsonResponse({'detail': 'Unable to create this account. Try signing in or use another email.'}, status=400)
        login(request, candidate)
        audit.info('account_created user=%s', candidate.pk)
        return JsonResponse({'user': profile(candidate), 'csrfToken': get_token(request)}, status=201)
    except Throttled as exc:
        return throttled(exc)
    except ValidationError as exc:
        return JsonResponse({'detail': ' '.join(exc.messages)}, status=400)
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)


@require_POST
@csrf_protect
@sensitive_post_parameters()
def signin(request):
    try:
        limit('login-ip', client_ip(request), 30, 300)
        data = payload(request, ['email', 'password'])
        email = data.get('email', '').strip().lower()
        password = data.get('password', '')
        if len(email) > 254 or len(password) > 256:
            raise ValueError('Email or password is invalid.')
        limit('login-account', email, 10, 300)
        user = authenticate(request, email=email, password=password)
        if user is None:
            return JsonResponse({'detail': 'Email or password is incorrect.'}, status=400)
        login(request, user)
        audit.info('login user=%s', user.pk)
        return JsonResponse({'user': profile(user), 'csrfToken': get_token(request)})
    except Throttled as exc:
        return throttled(exc)
    except ValueError as exc:
        return JsonResponse({'detail': str(exc)}, status=400)


@require_POST
@csrf_protect
def signout(request):
    if request.user.is_authenticated:
        audit.info('logout user=%s', request.user.pk)
    logout(request)
    return JsonResponse({'detail': 'Signed out.'})
