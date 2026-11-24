import hashlib
import hmac
import time
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import Throttled
from .models import RateBucket


def client_ip(request):
    # Set only behind a private proxy that OVERWRITES X-Forwarded-For.
    if settings.TRUST_PROXY:
        return request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', 'unknown'))
    return request.META.get('REMOTE_ADDR', 'unknown')


def limit(scope, identity, maximum, seconds):
    window = int(time.time()) // seconds
    key = hmac.new(settings.SECRET_KEY.encode(), f'{scope}:{identity}:{window}'.encode(), hashlib.sha256).hexdigest()
    with transaction.atomic():
        bucket, _ = RateBucket.objects.get_or_create(key=key, defaults={'expires_at': timezone.now() + timedelta(seconds=seconds * 2)})
        bucket = RateBucket.objects.select_for_update().get(pk=bucket.pk)
        if bucket.count >= maximum:
            raise Throttled(wait=seconds - int(time.time()) % seconds)
        bucket.count += 1
        bucket.save(update_fields=['count'])
