from django.conf import settings
"""Owner-scoped dynamic project/topic discovery and source-backed summaries."""
import hashlib
import json
import re
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.core.serializers.json import DjangoJSONEncoder
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound
from .models import Message, MessageTopics, SemanticDocument, TopicSummary
from .semantic import model_version, index_pending, content_hash, SemanticError
from .ai import LOCK, generate, ModelFailure
from .limits import limit
from .topic_language import localize_topic
from .topic_discovery import DISCOVERY_VERSION, BATCH_SIZE, discover, mentions, topic_id, valid_name, useful_name, project_catalog, supported_work_title

CLASS_VERSION = DISCOVERY_VERSION
from config.runtime import fingerprint
SUMMARY_VERSION = 'summary-v10:'+fingerprint(settings.AI_TOPIC_MODEL,settings.AI_TEMPERATURE,settings.AI_CONTEXT_TOKENS,settings.AI_TOPIC_MAX_TOKENS)


def registry_signature(registry):
    value = [(key, g['name'], g.get('aliases', []), g.get('domains', [])) for key, g in sorted(registry.items())]
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def grouped(owner, version=None):
    rows = MessageTopics.objects.filter(message__chat__owner=owner)
    if version:
        rows = rows.filter(version=version)
    else:
        current_registry = project_catalog(Message.objects.filter(chat__owner=owner).order_by('timestamp', 'chat_id', 'source_line').iterator(chunk_size=200))
        rows = rows.filter(version__startswith=CLASS_VERSION + ':', version__endswith=':' + registry_signature(current_registry))
    groups = {}
    for item in rows.select_related('message__chat', 'message__semantic_document').order_by('message__timestamp', 'message_id').iterator(chunk_size=100):
        m = item.message
        if item.content_hash != content_hash(m):
            continue
        for label in item.labels:
            name = label.get('name')
            if not valid_name(name) or label.get('topic') != topic_id((label['parent'] + ':' if label.get('parent') else '') + name):
                continue
            structural = label.get('structural') is True and name == 'General work' and label.get('parent')
            if not structural and not useful_name(name, label.get('kind', 'topic')):
                continue
            if label.get('project_context'):
                from datetime import timedelta
                ref=label['project_context']
                anchor=Message.objects.filter(pk=ref.get('id'),chat=m.chat,chat__owner=owner,timestamp__gte=m.timestamp-timedelta(hours=4),timestamp__lte=m.timestamp).first()
                if not anchor or content_hash(anchor)!=ref.get('hash'):continue
            key = label['topic']
            group = groups.setdefault(key, {'name': name, 'kind': label.get('kind', 'topic'), 'rows': [], 'parts': [], 'anchored': False, 'parent_hint': label.get('parent')})
            group['rows'].append((m, label.get('score', 0)))
            group['anchored'] |= (label.get('structural') is True and name == 'General work' and label.get('parent') in {x.get('topic') for x in item.labels}) or mentions(name, m.text) or (label.get('kind') in ('work', 'task') and supported_work_title(name, m.text))
            if not group['anchored'] and label.get('context') and label.get('kind') in ('work', 'task'):
                from datetime import timedelta
                refs = {r['id']: r['hash'] for r in label['context'][:8]}
                from django.db.models import Q
                context = Message.objects.filter(chat=m.chat, chat__owner=owner, pk__in=refs, timestamp__gte=m.timestamp-timedelta(hours=4), timestamp__lte=m.timestamp).filter(Q(timestamp__lt=m.timestamp) | Q(timestamp=m.timestamp, source_line__lt=m.source_line))
                evidence = [row.text[:500] for row in context if content_hash(row) == refs[str(row.pk)]]
                group['anchored'] |= supported_work_title(name, m.text + ' ' + ' '.join(evidence))
            if hasattr(m, 'semantic_document') and len(group['parts']) < 8:
                group['parts'].extend(m.semantic_document.chunks[:1])
    # A deleted naming source must not leave an unsupported project title behind.
    from .topic_corrections import apply_corrections
    return apply_corrections(owner, {key: group for key, group in groups.items() if group['anchored']})


def signature(rows, version):
    value = SUMMARY_VERSION + version + '|'.join(sorted(str(m.pk) + ':' + content_hash(m) for m, _ in rows))
    return hashlib.sha256(value.encode()).hexdigest()


def cards_for(owner, groups, version):
    cache = {s.topic: s for s in TopicSummary.objects.filter(owner=owner)}
    cards = []
    for key, group in groups.items():
        rows = group['rows']
        summary = cache.get(key)
        cards.append({'id': key, 'name': group['name'], 'kind': group['kind'],
            'message_count': len(rows), 'chat_count': len({m.chat_id for m, _ in rows}),
            'summary': {'answer': 'No direct messages yet. Open this item to organize its work.', 'results': [], 'sampled_messages': 0, 'answer_mode': 'source_excerpt'} if not rows else summary.payload if summary and summary.fingerprint == signature(rows, version) else None})
    return sorted(cards, key=lambda card: (-card['message_count'], card['name'].casefold(), card['id']))


def repair_stale(messages, version):
    # No invalidation on GET. Imports are normally immutable; this also handles edits.
    for doc in SemanticDocument.objects.filter(message__in=messages, version=version).select_related('message').iterator(chunk_size=100):
        if doc.content_hash != content_hash(doc.message):
            MessageTopics.objects.filter(message=doc.message).delete()
            doc.delete()


def summarize(owner, group, key, embedding_version, version):
    rows = group['rows']
    chosen = sorted(rows, key=lambda row: (row[1], row[0].timestamp), reverse=True)[:8]
    records = []
    mixed_context = False
    for m, _ in chosen:
        doc = SemanticDocument.objects.filter(message=m, version=embedding_version).first()
        if not doc or doc.content_hash != content_hash(m):
            return False
        part = next((p for p in doc.chunks if mentions(group['name'], p['text'])), doc.chunks[0])
        labels = getattr(m, 'topic_index', None)
        names = [label.get('name', '') for label in labels.labels] if labels else []
        if len(names) > 1:
            # Summarize only an isolated original clause for this project. If the
            # projects share a clause, show sources rather than blend their facts.
            segments = [match for match in re.finditer(r'[^;\n]+', m.text)
                        if mentions(group['name'], match.group()) and not any(
                            name != group['name'] and mentions(name, match.group()) for name in names)]
            if segments:
                segment = segments[0]
                part = {'text': segment.group(), 'start': segment.start(), 'end': segment.end()}
            else:
                mixed_context = True
        records.append({'id': m.pk, 'sender': m.sender, 'text': part['text'], 'timestamp': m.timestamp,
            'chat_id': m.chat_id, 'chat__name': m.chat.name, 'excerpt_start': part['start'], 'excerpt_end': part['end']})
    question = ('Summarize the main discussion about ' + group['name'] +
        ' in these messages in two brief sentences. Preserve proposals and uncertainty. '
        'Do not infer totals, final decisions or dates that were not stated.')
    try:
        if mixed_context:
            raise ModelFailure('Mixed project source needs original context')
        result = generate(question, [dict(r, sender='', chat__name='') for r in records], question, model=settings.AI_TOPIC_MODEL, max_tokens=settings.AI_TOPIC_MAX_TOKENS)
    except ModelFailure:
        # An unreliable summary must not hide a valid project or its evidence.
        result = {'answer': 'AI summary unavailable. Read the original messages below.',
                  'results': [dict(r, citation=i+1) for i, r in enumerate(records)],
                  'answer_mode': 'source_excerpt', 'grounded': True}

    originals = {r['id']: r for r in records}
    result['results'] = [dict(originals[r['id']], citation=r.get('citation', i+1)) for i, r in enumerate(result['results'])]
    evidence = ' '.join(r['text'] for r in result['results']).lower()
    aliases = {'today': ('today', 'aaj', 'आज'), 'tomorrow': ('tomorrow', 'kal', 'कल'), 'yesterday': ('yesterday', 'kal', 'कल')}
    unsupported = any(re.search(r'\b'+word+r'\b', result['answer'].lower()) and not any(re.search(r'(?<!\w)'+a+r'(?!\w)', evidence) for a in variants) for word, variants in aliases.items())
    if unsupported or not result.get('grounded'):
        result.update(answer='Read the original messages for this topic.', answer_mode='source_excerpt',
            results=[dict(r, citation=i+1) for i, r in enumerate(records)], grounded=True)
    result = localize_topic(result, 'en')
    result.update(sampled_messages=len(records), topic_messages=len(rows))
    payload = json.loads(json.dumps(result, cls=DjangoJSONEncoder))
    from .models import User
    with transaction.atomic():
        User.objects.select_for_update().get(pk=owner.pk)
        current = grouped(owner, version).get(key)
        if not current or current['name'] != group['name'] or signature(current['rows'], version) != signature(rows, version):
            return False
        TopicSummary.objects.update_or_create(owner=owner, topic=key,
            defaults={'fingerprint': signature(rows, version), 'payload': payload})
    return True


class PrepareTopics(APIView):
    def post(self, request):
        def checkpoint(stage,completed,total):
            claim=getattr(request._request,'_relay_job',None)
            if claim:
                from .models import ProcessingJob
                from django.utils import timezone
                ProcessingJob.objects.filter(pk=claim[0],lease_until=claim[1],status='running').update(progress={'stage':stage,'completed':completed,'total':total,'saved_at':timezone.now().isoformat()})
        if not isinstance(request.data, dict) or request.data:
            raise ValidationError('No topic-generation fields are accepted.')
        if not getattr(request._request, '_relay_worker', False):
            limit('topics-prepare', str(request.user.pk), 240, 3600)
        messages = Message.objects.filter(chat__owner=request.user)
        count = messages.count()
        if not count:
            return Response({'status': 'ready', 'topics': [], 'message_count': 0, 'ungrouped_count': 0, 'discovery': 'dynamic'})
        if count > settings.SEARCH_MAX_SCAN_CHUNKS:
            raise ValidationError(f'Automatic topics support up to {settings.SEARCH_MAX_SCAN_CHUNKS} messages per account.')
        if not LOCK.acquire(blocking=False):
            return Response({'status': 'busy', 'retry_after': 5, 'topics': []}, status=202, headers={'Retry-After': '5'})
        try:
            embedding_version = model_version()
            registry = project_catalog(messages.order_by('timestamp', 'chat_id', 'source_line').iterator(chunk_size=200))
            version = CLASS_VERSION + ':' + embedding_version + ':' + registry_signature(registry)
            repair_stale(messages, embedding_version)
            checkpoint('semantic indexing',SemanticDocument.objects.filter(message__in=messages,version=embedding_version).count(),count)
            remaining = index_pending(messages, embedding_version)
            if remaining:
                return Response({'status': 'indexing', 'remaining': remaining, 'topics': [], 'progress': {'stage':'semantic indexing','completed':count-remaining,'total':count}}, status=202)
            groups = grouped(request.user, version)
            # If all evidence for a named topic was removed, reconsider its continuations.
            from .models import TopicCorrections
            from .topic_corrections import resolve
            correction = TopicCorrections.objects.filter(owner=request.user).first()
            overrides = correction.state if correction else {}
            for item in MessageTopics.objects.filter(message__in=messages, version=version).iterator(chunk_size=100):
                if str(item.message_id) in overrides.get('messages', {}):
                    continue
                if any(resolve(label.get('topic'), overrides.get('redirects', {})) not in groups for label in item.labels):
                    item.delete()
            pending = messages.annotate(done=Exists(MessageTopics.objects.filter(message_id=OuterRef('pk'), version=version))).filter(done=False)
            if pending.exists():
                checkpoint('project grouping',count-pending.count(),count)
                documents = list(SemanticDocument.objects.filter(message_id__in=pending.values('id'), version=embedding_version)
                    .select_related('message__chat').order_by('message__timestamp', 'message__chat_id', 'message__source_line', 'message_id')[:BATCH_SIZE])
                for key, group in groups.items():
                    if key in registry:
                        registry[key]['parts'] = group['parts']
                    else:
                        registry[key] = group
                try:
                    assignments = discover(documents, registry)
                except ModelFailure:
                    # Reduce the context on the next attempt; never invent a label.
                    # Process one message so a bad batch cannot block the whole library.
                    try:
                        assignments = discover(documents[:1], registry)
                    except ModelFailure:
                        assignments = [(documents[0], [])] if documents else []
                for doc, labels in assignments:
                    with transaction.atomic():
                        current = Message.objects.select_for_update().filter(pk=doc.message_id, chat__owner=request.user).first()
                        if current and content_hash(current) == doc.content_hash:
                            MessageTopics.objects.update_or_create(message=current,
                                defaults={'version': version, 'content_hash': doc.content_hash, 'labels': labels})
                remaining = pending.count()
                if remaining:
                    return Response({'status': 'grouping', 'remaining': remaining, 'topics': [], 'discovery': 'dynamic', 'progress': {'stage':'project grouping','completed':count-remaining,'total':count}}, status=202)
            groups = grouped(request.user, version)
            cards = cards_for(request.user, groups, version)
            checkpoint('summaries',sum(c['summary'] is not None for c in cards),len(cards))
            missing = next((c for c in cards if c['summary'] is None), None)
            if missing:
                if not summarize(request.user, groups[missing['id']], missing['id'], embedding_version, version):
                    return Response({'status': 'grouping', 'remaining': 1, 'topics': []}, status=202)
                cards = cards_for(request.user, grouped(request.user, version), version)
            ready = all(c['summary'] is not None for c in cards)
            grouped_count = len({m.pk for group in groups.values() for m, _ in group['rows']})
            from .topic_hierarchy import nest_cards
            return Response({'progress': {'stage':'summaries','completed':sum(c['summary'] is not None for c in cards),'total':len(cards)}, 'status': 'ready' if ready else 'summarizing', 'topics': nest_cards(request.user, groups, cards),
                'message_count': count, 'ungrouped_count': max(0, count-grouped_count), 'discovery': 'dynamic'}, status=200 if ready else 202)
        except (SemanticError, ModelFailure) as exc:
            return Response({'detail': str(exc) if isinstance(exc, SemanticError) else
                'Local AI could not complete reliable topic discovery or summaries. Retry shortly.'}, status=503)
        finally:
            LOCK.release()


class TopicMessages(APIView):
    def get(self, request, topic):
        try:
            offset = int(request.query_params.get('offset', '0'))
        except ValueError:
            raise ValidationError('Invalid offset.')
        if not 0 <= offset <= 25000:
            raise ValidationError('Invalid offset.')
        groups = grouped(request.user)
        group = groups.get(topic)
        if not group:
            raise NotFound('Topic not found. Refresh topics to discover current conversations.')
        from .topic_hierarchy import thread_group
        direct_ids = {m.pk for m, _ in group['rows']}
        group, children, ancestors = thread_group(request.user, groups, topic)
        rows = sorted(group['rows'], key=lambda row: (row[0].timestamp, str(row[0].chat_id), row[0].source_line, str(row[0].pk)))
        found = [{'id': m.pk, 'sender': m.sender, 'text': m.text, 'timestamp': m.timestamp,
                  'chat_id': m.chat_id, 'chat__name': m.chat.name, 'direct_member': m.pk in direct_ids} for m, _ in rows[offset:offset+30]]
        return Response({'topic': {'id': topic, 'name': group['name'], 'kind': group['kind'], 'chat_count': len({m.chat_id for m, _ in rows}), 'direct_message_count': len(direct_ids), 'children': children, 'ancestors': ancestors}, 'count': len(rows), 'results': found, 'has_more': len(rows) > offset+30})


class TopicMessageContext(APIView):
    def get(self, request, topic, message_id):
        from django.db.models import Q
        groups = grouped(request.user)
        group = groups.get(topic)
        if not group:
            raise NotFound('Topic not found. Refresh topics in Discover.')
        from .topic_hierarchy import thread_group
        group, _, _ = thread_group(request.user, groups, topic)
        members = {m.pk for m, _ in group['rows']}
        message = Message.objects.select_related('chat').filter(pk=message_id, chat__owner=request.user).first()
        if not message or message.pk not in members:
            raise NotFound('Message not found in this topic.')
        same_chat = Message.objects.filter(chat=message.chat, chat__owner=request.user)
        before = same_chat.filter(Q(timestamp__lt=message.timestamp) |
            Q(timestamp=message.timestamp, source_line__lt=message.source_line) |
            Q(timestamp=message.timestamp, source_line=message.source_line, pk__lt=message.pk))
        after = same_chat.filter(Q(timestamp__gt=message.timestamp) |
            Q(timestamp=message.timestamp, source_line__gt=message.source_line) |
            Q(timestamp=message.timestamp, source_line=message.source_line, pk__gt=message.pk))
        previous = list(before.order_by('-timestamp', '-source_line', '-pk')[:4])
        following = list(after.order_by('timestamp', 'source_line', 'pk')[:4])
        rows = list(reversed(previous[:3])) + [message] + following[:3]
        return Response({'chat_name': message.chat.name, 'focus_id': message.pk,
            'has_earlier': len(previous) > 3, 'has_later': len(following) > 3,
            'results': [{'id': m.pk, 'sender': m.sender, 'text': m.text, 'timestamp': m.timestamp,
                         'chat_id': m.chat_id, 'chat__name': message.chat.name,
                         'in_topic': m.pk in members, 'is_focus': m.pk == message.pk} for m in rows]})
