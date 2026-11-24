from django.db.models import Count, Max, Q
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from .models import Chat, Message
from .limits import limit
import re

FIELDS = ('id', 'sender', 'text', 'timestamp', 'chat_id', 'chat__name')

class Workspace(APIView):
    def get(self, request):
        chats = Chat.objects.filter(owner=request.user)
        messages = Message.objects.filter(chat__owner=request.user)
        return Response({'chat_count': chats.count(), 'message_count': messages.count(),
            'people_count': messages.exclude(sender='').values('sender').distinct().count(),
            'chats': list(chats.annotate(message_count=Count('messages'), latest=Max('messages__timestamp')).order_by('-created_at').values('id','name','message_count','latest')[:100]),
            'recent': list(messages.order_by('-timestamp','-id').values(*FIELDS)[:6])})

class SearchMessages(APIView):
    def post(self, request):
        limit('workspace-search', str(request.user.pk), 120, 3600)
        if set(request.data) - {'query','chat_id','offset','mode'}:
            raise ValidationError('Unexpected search field.')
        query=request.data.get('query','')
        offset=request.data.get('offset',0)
        mode=request.data.get('mode','explore')
        if not isinstance(query,str) or len(query)>300 or type(offset) is not int or not 0<=offset<=100000 or mode not in ('explore','ask'):
            raise ValidationError('Invalid search. Use up to 300 characters.')
        messages=Message.objects.filter(chat__owner=request.user)
        target=request.data.get('chat_id')
        if target:
            try:
                chat=get_object_or_404(Chat,pk=target,owner=request.user)
            except (ValueError,TypeError,DjangoValidationError):
                raise ValidationError('Invalid chat.')
            messages=messages.filter(chat=chat)
        if mode=='ask':
            stop=set('what when where who how did does the a an is are was were about tell me find in my chats please kya hai hain ka ki ke mujhe batao'.split())
            words=[w for w in re.findall(r'\w+',query.lower()) if len(w)>1 and w not in stop][:12]
            if not words:
                return Response({'count':0,'results':[], 'has_more':False,'mode':'keyword','hint':'Try a specific word from your chat, such as launch, payment or meeting.'})
            clause=Q()
            for word in words:
                clause |= Q(text__icontains=word)|Q(sender__icontains=word)
            messages=messages.filter(clause)
        elif query.strip():
            messages=messages.filter(Q(text__icontains=query.strip())|Q(sender__icontains=query.strip()))
        count=messages.count()
        return Response({'count':count,'results':list(messages.order_by('-timestamp','-id').values(*FIELDS)[offset:offset+30]),'has_more':count>offset+30,'mode':'keyword'})
