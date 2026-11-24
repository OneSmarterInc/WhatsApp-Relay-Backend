from celery import shared_task
from django.utils import timezone
from .models import ImportBatch, RateBucket

@shared_task
def cleanup():
    ImportBatch.objects.filter(status='preview', expires_at__lte=timezone.now()).delete()
    RateBucket.objects.filter(expires_at__lte=timezone.now()).delete()
