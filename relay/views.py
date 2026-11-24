import hashlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .models import Chat, Message, ImportBatch, User
from .limits import limit


class ImportPreview(APIView):
    def post(self, request):
        limit('import-preview', str(request.user.pk), 20, 3600)
        f = request.FILES.get('file')
        if not f or len(request.FILES) != 1 or not f.name.lower().endswith('.txt') or f.size > settings.IMPORT_MAX_BYTES:
            raise ValidationError('Upload one UTF-8 .txt export, at most 2 MB. ZIP support is not enabled yet.')
        try:
            text = f.read(settings.IMPORT_MAX_BYTES + 1).decode('utf-8-sig')
            zone = ZoneInfo(request.data.get('timezone', 'Asia/Kolkata'))
        except (UnicodeError, ZoneInfoNotFoundError):
            raise ValidationError('Invalid UTF-8 text or timezone.')
        if '\x00' in text:
            raise ValidationError('Binary content is not accepted.')
        name = request.data.get('name', '').strip()
        order = request.data.get('date_order', 'DMY')
        if not name or len(name) > 120 or order not in ('DMY', 'MDY'):
            raise ValidationError('Enter a chat name and valid date order.')
        records, skipped = [], []
        pattern = re.compile(r'^\[?(\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}),?\s+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]?\s*(?:-\s*)?(.*)$')
        for n, raw in enumerate(text.splitlines(), 1):
            line = raw.replace('\u200e', '').replace('\u202f', ' ')
            match = pattern.match(line)
            if not match:
                if records:
                    records[-1]['text'] += '\n' + line
                elif line.strip():
                    skipped.append(n)
                continue
            date, clock, body = match.groups()
            date = re.sub(r'[.-]', '/', date)
            fmt = ('%d/%m/' if order == 'DMY' else '%m/%d/') + ('%Y' if len(date.split('/')[-1]) == 4 else '%y')
            clock = clock.upper().strip()
            tf = '%I:%M' if clock.endswith(('AM', 'PM')) else '%H:%M'
            if clock.count(':') == 2:
                tf += ':%S'
            if clock.endswith(('AM', 'PM')):
                clock = re.sub(r'\s*(AM|PM)$', r' \1', clock)
                tf += ' %p'
            try:
                stamp = datetime.strptime(date + ' ' + clock, fmt + ' ' + tf).replace(tzinfo=zone)
            except ValueError:
                raise ValidationError(f'Cannot parse date/time on line {n}. Check date order.')
            sender, sep, content = body.partition(': ')
            records.append({'timestamp': stamp.isoformat(), 'sender': sender[:200] if sep else '', 'text': content if sep else body, 'system': not bool(sep), 'source_line': n})
        if not records or len(records) > settings.IMPORT_MAX_MESSAGES or any(len(r['text']) > settings.IMPORT_MAX_MESSAGE_LENGTH for r in records):
            raise ValidationError('No supported messages found, or message limits exceeded.')
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            if ImportBatch.objects.filter(owner=request.user, status='preview', expires_at__gt=timezone.now()).count() >= settings.IMPORT_MAX_PENDING:
                raise ValidationError('Remove or confirm pending imports first.')
            batch = ImportBatch.objects.create(owner=request.user, filename='chat-export.txt', chat_name=name, date_order=order, timezone_name=str(zone), records=records, total=len(records), warnings=[f'{len(skipped)} initial lines ignored'], expires_at=timezone.now()+timedelta(hours=settings.IMPORT_TTL_HOURS))
        return Response({'id': str(batch.pk), 'total': batch.total, 'preview': records[:10], 'warnings': batch.warnings}, status=201)


class ImportConfirm(APIView):
    def post(self, request, pk):
        limit('import-confirm', str(request.user.pk), 30, 3600)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            batch = get_object_or_404(ImportBatch.objects.select_for_update(), pk=pk, owner=request.user)
            if batch.status == 'complete':
                return Response({'chat_id': str(batch.chat_id), 'inserted': batch.inserted, 'duplicates': batch.duplicates})
            if batch.expires_at <= timezone.now():
                raise ValidationError('Preview expired. Upload again.')
            target = request.data.get('chat_id')
            chat = get_object_or_404(Chat, pk=target, owner=request.user) if target else Chat.objects.create(owner=request.user, name=batch.chat_name)
            if Message.objects.filter(chat__owner=request.user).count() + batch.total > settings.USER_MAX_MESSAGES:
                raise ValidationError('Account message quota exceeded.')
            inserted = 0
            for r in batch.records:
                fingerprint = hashlib.sha256((r['timestamp']+'\0'+r['sender']+'\0'+r['text']).encode()).hexdigest()
                _, created = Message.objects.get_or_create(chat=chat, fingerprint=fingerprint, defaults=r)
                inserted += int(created)
            batch.chat, batch.status, batch.inserted, batch.duplicates = chat, 'complete', inserted, batch.total-inserted
            batch.records = []
            batch.save()
            # Save the queue entry in the same transaction as the imported messages.
            from .processing import enqueue
            enqueue(request.user)
        return Response({'chat_id': str(chat.pk), 'inserted': inserted, 'duplicates': batch.duplicates})


class ImportList(APIView):
    def get(self, request):
        return Response(list(ImportBatch.objects.filter(owner=request.user).order_by('-created_at').values('id','chat_name','status','total','inserted')[:100]))


class ImportDetail(APIView):
    def delete(self, request, pk):
        get_object_or_404(ImportBatch, pk=pk, owner=request.user).delete()
        return Response(status=204)


class ChatList(APIView):
    def get(self, request):
        return Response(list(Chat.objects.filter(owner=request.user).order_by('-created_at').values('id','name')[:100]))


class ChatDetail(APIView):
    def delete(self, request, pk):
        get_object_or_404(Chat, pk=pk, owner=request.user).delete()
        return Response(status=204)


class MessageList(APIView):
    def get(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk, owner=request.user)
        return Response(list(chat.messages.order_by('timestamp','source_line').values('id','sender','text','timestamp')[:100]))
