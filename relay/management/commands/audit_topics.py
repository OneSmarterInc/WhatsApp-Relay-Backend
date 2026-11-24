"""Read-only coverage audit. Counts do not certify semantic accuracy."""
import json
from django.core.management.base import BaseCommand
from relay.models import User, Message, ProcessingJob
from relay.topics import grouped
from relay.topic_hierarchy import structure


class Command(BaseCommand):
    help='Report saved project coverage without running AI or changing chats.'
    def handle(self,*args,**options):
        for owner in User.objects.filter(is_active=True):
            messages=Message.objects.filter(chat__owner=owner)
            if not messages.exists():continue
            groups=grouped(owner);parents,kinds=structure(owner,groups)
            job=ProcessingJob.objects.filter(owner=owner).values('status','phase','progress','updated_at').first()
            assigned={m.pk for group in groups.values() for m,_ in group['rows']}
            self.stdout.write(json.dumps({'account_id':owner.pk,'messages':messages.count(),'assigned':len(assigned),'ungrouped':messages.exclude(pk__in=assigned).count(),'processing':job,'groups':[{'id':key,'name':g['name'],'kind':kinds[key],'parent':parents[key],'messages':len(g['rows'])} for key,g in groups.items()],'note':'Coverage is not an accuracy score. Inspect original sources before accepting assignments.'},default=str))
