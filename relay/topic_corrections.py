"""Durable owner-scoped overrides applied after AI grouping."""
import uuid
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound
from .models import TopicCorrections, TopicSummary, User, Message
from .topic_discovery import valid_name, normalized
from .limits import limit


def resolve(key, redirects):
    seen = set()
    while key in redirects and key not in seen:
        seen.add(key)
        key = redirects[key]
    return key


def apply_corrections(owner, groups):
    record = TopicCorrections.objects.filter(owner=owner).first()
    if not record:
        return groups
    state = record.state
    names, redirects, overrides = (state.get(key, {}) for key in ('names', 'redirects', 'messages'))
    memberships, messages = {}, {}
    for key, group in groups.items():
        for message, score in group['rows']:
            mid = str(message.pk)
            messages[mid] = message
            memberships.setdefault(mid, {})[resolve(key, redirects)] = score
    for message in Message.objects.filter(chat__owner=owner, pk__in=list(overrides)).select_related('chat', 'semantic_document'):
        mid = str(message.pk)
        messages[mid] = message
        memberships[mid] = {resolve(key, redirects): 1.0 for key in overrides[mid]}
    result = {}
    for mid, targets in memberships.items():
        message = messages[mid]
        for key, score in targets.items():
            metadata = names.get(key) or groups.get(key)
            if not metadata:
                continue
            group = result.setdefault(key, {'name': metadata['name'], 'kind': metadata['kind'], 'rows': [], 'parts': [], 'anchored': True, 'parent_hint': groups.get(key, {}).get('parent_hint')})
            group['rows'].append((message, score))
            if hasattr(message, 'semantic_document') and len(group['parts']) < 8:
                group['parts'].extend(message.semantic_document.chunks[:1])
    for key, node in state.get('nodes', {}).items():
        if resolve(key, redirects) != key or key not in names:
            continue
        if key not in result and not node.get('keep_empty'):
            continue
        result.setdefault(key, {'name': names[key]['name'], 'kind': node['kind'], 'rows': [], 'parts': [], 'anchored': True})
        result[key]['kind'] = node['kind']
    return result


class TopicDirectory(APIView):
    def get(self, request):
        from .topics import grouped
        groups = grouped(request.user)
        from .topic_hierarchy import structure
        parents, kinds = structure(request.user, groups)
        return Response({'topics': [{'id': key, 'name': group['name'], 'kind': kinds[key], 'parent': parents[key], 'message_count': len(group['rows'])}
                                   for key, group in sorted(groups.items(), key=lambda item: item[1]['name'].casefold())]})


class CorrectTopic(APIView):
    def post(self, request, topic):
        from .topics import grouped
        body = request.data
        if not isinstance(body, dict):
            raise ValidationError('Expected correction fields.')
        action = body.get('action')
        fields = {'rename': {'action', 'name'}, 'merge': {'action', 'target'},
                  'split': {'action', 'name', 'message_ids'}, 'move': {'action', 'target', 'message_ids'}}
        if not isinstance(action, str) or action not in fields or set(body) != fields[action]:
            raise ValidationError('Unsupported correction fields.')
        if action in ('rename', 'split'):
            if not valid_name(body['name']):
                raise ValidationError('Use a topic name between 2 and 80 characters, without links or control characters.')
        if action in ('merge', 'move') and not isinstance(body['target'], str):
            raise ValidationError('Choose a destination topic.')
        selected = None
        if action in ('split', 'move'):
            values = body['message_ids']
            if not isinstance(values, list) or not 1 <= len(values) <= 500 or any(not isinstance(v, str) for v in values):
                raise ValidationError('Select between 1 and 500 messages.')
            try:
                selected = {str(uuid.UUID(v)) for v in values}
            except ValueError:
                raise ValidationError('Invalid message identifier.')
            if len(selected) != len(values):
                raise ValidationError('Duplicate selected messages.')
        limit('topic-correction', str(request.user.pk), 120, 3600)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            groups = grouped(request.user)
            if topic not in groups:
                raise NotFound('Topic no longer exists. Refresh topics.')
            source = groups[topic]
            source_ids = {str(m.pk) for m, _ in source['rows']}
            if selected is not None and not selected <= source_ids:
                raise ValidationError('Selected messages must belong to this topic.')
            if action == 'split' and selected == source_ids:
                raise ValidationError('Leave at least one message in the original topic, or rename it instead.')
            if action in ('merge', 'move'):
                target = body['target']
                if target == topic or target not in groups:
                    raise ValidationError('Choose another existing topic in your workspace.')
            elif action == 'split':
                target = 'd-' + uuid.uuid4().hex[:24]
            else:
                target = topic
            if action in ('rename', 'split'):
                name = body['name'].strip()
                if any(key != target and normalized(group['name']) == normalized(name) for key, group in groups.items()):
                    raise ValidationError('That name already exists. Merge into the existing topic instead.')
            from .topic_hierarchy import structure, descendants
            parents, kinds = structure(request.user, groups)
            if action == 'merge' and (kinds[topic] != kinds[target] or target in descendants(topic, parents)):
                raise ValidationError('Merge items of the same type; a parent cannot merge into its child.')
            record, _ = TopicCorrections.objects.get_or_create(owner=request.user)
            state = record.state
            for key in ('names', 'redirects', 'messages'):
                state.setdefault(key, {})
            # Preserve metadata for every target used by a frozen manual assignment.
            for key, group in groups.items():
                state['names'].setdefault(key, {'name': group['name'], 'kind': group['kind']})
            if action in ('rename', 'split'):
                state['names'][target] = {'name': name, 'kind': source['kind']}
            affected = source_ids if action in ('rename', 'merge') else selected
            membership = {}
            for key, group in groups.items():
                for message, _ in group['rows']:
                    mid = str(message.pk)
                    if mid in affected:
                        membership.setdefault(mid, set()).add(key)
            for mid, targets in membership.items():
                if action != 'rename':
                    if action in ('move', 'split'):
                        targets.difference_update(descendants(topic, parents))
                    else:
                        targets.discard(topic)
                    targets.add(target)
                state['messages'][mid] = sorted(targets)
            if action == 'split':
                state.setdefault('nodes', {})[target] = {'kind': kinds[topic], 'parent': parents[topic]}
            if action == 'merge':
                state['redirects'][topic] = target
                for child, parent in parents.items():
                    if parent == topic:
                        state.setdefault('nodes', {})[child] = {**state.get('nodes', {}).get(child, {}), 'kind': kinds[child], 'parent': target}
                        state['names'].setdefault(child, {'name': groups[child]['name'], 'kind': kinds[child]})
            # Prune deleted messages without retaining identifiers indefinitely.
            live = {str(pk) for pk in Message.objects.filter(chat__owner=request.user, pk__in=list(state['messages'])).values_list('pk', flat=True)}
            state['messages'] = {mid: targets for mid, targets in state['messages'].items() if mid in live}
            record.state = state
            from .workflow_views import save_correction
            save_correction(record, action)
            TopicSummary.objects.filter(owner=request.user).delete()
        return Response({'topic_id': target, 'action': action, 'affected_messages': len(affected)})
