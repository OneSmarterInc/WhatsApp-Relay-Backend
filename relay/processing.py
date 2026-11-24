from django.conf import settings
from datetime import timedelta
from django.db import transaction
from django.db.models import Q, Exists, OuterRef
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .models import ProcessingJob, User, Message, MessageInsight, MessageTopics
from .limits import limit
from contextlib import contextmanager
from threading import Event, Thread
from django.db import connections
import logging

logger=logging.getLogger(__name__)


@contextmanager
def active_claim(job):
    stop=Event()
    def heartbeat():
        while not stop.wait(settings.WORKER_HEARTBEAT_SECONDS):
            try:
                ProcessingJob.objects.filter(pk=job.pk,status='running',lease_until=job.lease_until).update(heartbeat_at=timezone.now())
            except Exception:
                logger.warning('Worker heartbeat could not reach the database.')
            finally:connections.close_all()
    thread=Thread(target=heartbeat,daemon=True)
    thread.start()
    try:yield
    except BaseException:
        ProcessingJob.objects.filter(pk=job.pk,status='running',lease_until=job.lease_until).update(status='queued',lease_until=None)
        raise
    finally:
        stop.set();thread.join(timeout=2)


def enqueue(owner):
    with transaction.atomic():
        User.objects.select_for_update().get(pk=owner.pk)
        job,_=ProcessingJob.objects.get_or_create(owner=owner)
        live_heartbeat=job.heartbeat_at and job.heartbeat_at>timezone.now()-timedelta(seconds=settings.WORKER_STALE_SECONDS)
        if job.status=='running' and ((job.lease_until and job.lease_until>timezone.now()) or live_heartbeat):
            job.rerun=True
        else:
            job.status='queued';job.phase='topics';job.error='';job.lease_until=None;job.rerun=False
        job.save()
    return job


class Processing(APIView):
    def post(self,request):
        if not isinstance(request.data,dict) or request.data:raise ValidationError('No processing fields accepted.')
        limit('enqueue',str(request.user.pk),60,3600)
        enqueue(request.user)
        return self.get(request)
    def get(self,request):
        from .topics import grouped, cards_for, CLASS_VERSION
        from .topic_hierarchy import nest_cards
        groups=grouped(request.user)
        # Derive saved versions without contacting Ollama on a polling GET.
        versions=list(MessageTopics.objects.filter(message__chat__owner=request.user).values_list('version',flat=True).distinct())
        cards=cards_for(request.user,groups,versions[0] if len(versions)==1 else '')
        job=ProcessingJob.objects.filter(owner=request.user).first()
        count=Message.objects.filter(chat__owner=request.user).count()
        # A completed job may belong to an earlier discovery configuration.
        # Report this explicitly without mutating saved results on a polling GET.
        stale = bool(job and job.status == 'complete' and any(
            not version.startswith(CLASS_VERSION + ':') for version in versions))
        status = 'failed' if stale else job.status if job else 'idle'
        error = ('Topic configuration changed. Your imported messages are saved. '
                 'Click Refresh topics and keep the worker running to rebuild the groupings.') if stale else job.error if job else ''
        heartbeat_age=(timezone.now()-job.heartbeat_at).total_seconds() if job and job.heartbeat_at else None
        activity='active' if job and job.status=='running' and heartbeat_age is not None and heartbeat_age<settings.WORKER_STALE_SECONDS else 'waiting' if job and job.status=='queued' else 'unresponsive' if job and job.status=='running' else 'idle'
        return Response({'status':status,'phase':job.phase if job else 'topics','remaining':job.remaining if job else count,'error':error,
            'progress':job.progress if job else {},'worker_activity':activity,'heartbeat_at':job.heartbeat_at if job else None,'last_saved_at':job.progress.get('saved_at') if job else None,'retry_available':status=='failed',
            'message_count':count,'ungrouped_count':max(0,count-len({m.pk for g in groups.values() for m,_ in g['rows']})),
            'topics':nest_cards(request.user,groups,cards)})


def run_step():
    with transaction.atomic():
        stale_heartbeat=Q(heartbeat_at__isnull=True)|Q(heartbeat_at__lt=timezone.now()-timedelta(seconds=settings.WORKER_STALE_SECONDS))
        job=ProcessingJob.objects.select_for_update(skip_locked=True).filter(Q(status='queued')|(Q(status='running',lease_until__lt=timezone.now())&stale_heartbeat)).order_by('updated_at').first()
        if not job:return False
        job.status='running';job.lease_until=timezone.now()+timedelta(seconds=settings.WORKER_LEASE_SECONDS);job.heartbeat_at=timezone.now();job.save()
        lease=job.lease_until
    with active_claim(job):
        phase=job.phase;remaining=0;done=False;error='';progress=job.progress
        logger.warning('Processing batch started: job=%s phase=%s',job.pk,phase)
        try:
            if phase=='topics':
                from rest_framework.test import APIRequestFactory, force_authenticate
                from .topics import PrepareTopics
                request=APIRequestFactory().post('/api/topics/prepare/',{},format='json')
                request._relay_worker=True;request._relay_job=(job.pk,lease);force_authenticate(request,user=job.owner)
                response=PrepareTopics.as_view()(request)
                if response.status_code>=400:raise RuntimeError('Local topic processing failed. Check Ollama and retry processing.')
                remaining=response.data.get('remaining',0)
                progress=response.data.get('progress',{'stage':response.data.get('status','topics'),'completed':None,'total':None})
                if response.data.get('status')=='ready':phase='insights'
            else:
                from .insights import VERSION,process_message
                pending=Message.objects.filter(chat__owner=job.owner,system=False).annotate(done=Exists(MessageInsight.objects.filter(message_id=OuterRef('pk'),version=VERSION))).filter(done=False)
                total=Message.objects.filter(chat__owner=job.owner,system=False).count()
                ProcessingJob.objects.filter(pk=job.pk,lease_until=lease).update(progress={'stage':'task extraction','completed':total-pending.count(),'total':total,'saved_at':timezone.now().isoformat()})
                for message in pending.order_by('timestamp','source_line')[:settings.AI_TASK_BATCH_SIZE]:process_message(message)
                remaining=pending.count();done=remaining==0
                total=Message.objects.filter(chat__owner=job.owner,system=False).count()
                progress={'stage':'task extraction','completed':total-remaining,'total':total}
        except Exception:
            # Never persist raw chat text, model output or credentials in job errors.
            error='Processing paused. Check local models and retry; completed batches are saved.'
        with transaction.atomic():
            current=ProcessingJob.objects.select_for_update().filter(pk=job.pk).first()
            if current is None:return True
            if current.lease_until!=lease:return True
            if not error:progress=dict(progress,saved_at=timezone.now().isoformat())
            current.phase=phase;current.remaining=remaining;current.error=error;current.lease_until=None;current.progress=progress
            current.heartbeat_at=timezone.now()
            if error:current.status='failed'
            elif done and current.rerun:current.status='queued';current.phase='topics';current.rerun=False
            else:current.status='complete' if done else 'queued'
            current.save()
            logger.warning("Processing batch saved: job=%s stage=%s progress=%s/%s status=%s",job.pk,progress.get("stage",phase),progress.get("completed"),progress.get("total"),current.status)
        return True
