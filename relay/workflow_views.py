"""History, ungrouped messages, evidence views, and relationship graph APIs."""
import copy, uuid
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .models import User, Message, TopicCorrections, TopicSummary, CorrectionEvent, MessageInsight
from .topics import grouped
from .topic_hierarchy import structure, thread_group
from .semantic import content_hash
from .insights import VERSION
from .limits import limit


def save_correction(record, action):
    previous=TopicCorrections.objects.get(pk=record.pk).state
    CorrectionEvent.objects.create(owner=record.owner,action=action,before=previous,after=copy.deepcopy(record.state))
    record.save()


def source(m):
    return {'id':str(m.pk),'text':m.text,'sender':m.sender,'timestamp':m.timestamp,'chat_id':str(m.chat_id),'chat__name':m.chat.name}


def offset(request):
    try: value=int(request.query_params.get('offset','0'))
    except (ValueError,TypeError): raise ValidationError('Invalid offset.')
    if not 0<=value<=25000:raise ValidationError('Invalid offset.')
    return value


class History(APIView):
    def get(self, request):
        rows=CorrectionEvent.objects.filter(owner=request.user).order_by('-id')
        latest=rows.filter(undone=False).first()
        recent=list(rows[:50])
        if latest and latest.pk not in {e.pk for e in recent}:recent=recent[:49]+[latest]
        return Response({'events':[{'id':e.pk,'action':e.action,'created_at':e.created_at,'undone':e.undone,'can_undo': bool(latest and e.pk==latest.pk)} for e in recent]})
    def post(self, request):
        if not isinstance(request.data,dict) or set(request.data)!={'event_id'} or type(request.data['event_id']) is not int:raise ValidationError('Choose the latest correction to undo.')
        limit('correction-undo',str(request.user.pk),60,3600)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            event=get_object_or_404(CorrectionEvent,pk=request.data['event_id'],owner=request.user,undone=False)
            latest=CorrectionEvent.objects.filter(owner=request.user,undone=False).order_by('-id').first()
            record=get_object_or_404(TopicCorrections,owner=request.user)
            if event.pk!=latest.pk or record.state!=event.after:raise ValidationError('Undo the latest correction first. Refresh history.')
            record.state=event.before;record.save()
            event.undone=True;event.save(update_fields=['undone'])
            TopicSummary.objects.filter(owner=request.user).delete()
        return Response({'detail':'Correction undone.'})


class Ungrouped(APIView):
    def get(self,request):
        groups=grouped(request.user)
        ids={m.pk for g in groups.values() for m,_ in g['rows']}
        rows=Message.objects.filter(chat__owner=request.user).exclude(pk__in=ids).select_related('chat').order_by('timestamp','chat_id','source_line','id')
        start=offset(request);count=rows.count()
        return Response({'count':count,'has_more':count>start+30,'results':[source(m) for m in rows[start:start+30]]})
    def post(self,request):
        data=request.data
        if not isinstance(data,dict) or set(data)!={'target','message_ids'} or not isinstance(data['target'],str) or not isinstance(data['message_ids'],list) or not 1<=len(data['message_ids'])<=500:raise ValidationError('Choose a target and up to 500 messages.')
        try: ids={uuid.UUID(v) for v in data['message_ids'] if isinstance(v,str)}
        except ValueError:raise ValidationError('Invalid message IDs.')
        if len(ids)!=len(data['message_ids']):raise ValidationError('Invalid or duplicate IDs.')
        limit('ungrouped-assign',str(request.user.pk),120,3600)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            groups=grouped(request.user); target=data['target']
            if target not in groups:raise ValidationError('Choose a topic in your workspace.')
            messages=list(Message.objects.filter(chat__owner=request.user,pk__in=ids))
            assigned={m.pk for g in groups.values() for m,_ in g['rows']}
            if len(messages)!=len(ids) or assigned & ids:raise ValidationError('Messages must be ungrouped and owned by you. Refresh the list.')
            record,_=TopicCorrections.objects.get_or_create(owner=request.user)
            record.state.setdefault('names',{})[target]={'name':groups[target]['name'],'kind':groups[target]['kind']}
            for m in messages:record.state.setdefault('messages',{})[str(m.pk)]=[target]
            save_correction(record,'assign-ungrouped')
            TopicSummary.objects.filter(owner=request.user).delete()
        return Response({'assigned':len(messages)})


class Insights(APIView):
    def get(self,request,topic):
        groups=grouped(request.user)
        if topic not in groups:raise ValidationError('Topic not found.')
        group,_,_=thread_group(request.user,groups,topic)
        messages={m.pk:m for m,_ in group['rows']}
        rows=MessageInsight.objects.filter(message_id__in=messages,version=VERSION).select_related('message__chat').order_by('message__timestamp','message_id')
        items=[];processed=0
        for row in rows:
            if row.content_hash!=content_hash(row.message):continue
            processed+=1
            for i,item in enumerate(row.items):items.append(dict(item,id=f'{row.message_id}:{i}',source=source(row.message)))
        from .task_history import reconcile
        from .topic_hierarchy import descendants
        parents,kinds=structure(request.user,groups)
        memberships={}
        for key in groups:
            if kinds[key]!='project':continue
            for child in descendants(key,parents):
                for message,_ in groups[child]['rows']:
                    memberships.setdefault(str(message.pk),set()).add(key)
        items=reconcile(items,memberships)
        start=offset(request)
        return Response({'items':items[start:start+30],'has_more':len(items)>start+30,'count':len(items),'processed_messages':processed,'total_messages':len(messages)})


class RelationshipMap(APIView):
    def get(self,request):
        groups=grouped(request.user);parents,kinds=structure(request.user,groups)
        # Topic selection bounds graph size, and all people are attributed only to original sender labels.
        chosen=request.query_params.get('topic')
        if chosen and chosen not in groups:raise ValidationError('Topic not found.')
        keys=[chosen] if chosen else sorted(groups)[:100]
        if chosen:
            from .topic_hierarchy import descendants
            keys=sorted(descendants(chosen,parents))[:100]
        nodes=[{'id':k,'name':groups[k]['name'],'kind':kinds[k]} for k in keys];edges=[];people={}
        for key in keys:
            if parents[key] in keys:edges.append({'from':parents[key],'to':key,'kind':'contains'})
            senders={m.sender for m,_ in groups[key]['rows'] if m.sender}
            for sender in sorted(senders)[:30]:
                if sender not in people:
                    if len(people)>=100:continue
                    people[sender]='person-'+str(len(people))
                    nodes.append({'id':people[sender],'name':sender,'kind':'person'})
                edges.append({'from':people[sender],'to':key,'kind':'contributed'})
        return Response({'nodes':nodes,'edges':edges,'limited':len(groups)>len(keys) or len(people)>=100,'note':'People represent original sender names, not verified identities or assigned owners.'})
