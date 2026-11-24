"""Project/workstream/task relationships; corrections remain separate from AI labels."""
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound
from .models import TopicCorrections, User, TopicSummary
from .topic_discovery import valid_name, normalized, topic_id, mentions
from .topic_corrections import resolve
from .limits import limit
import uuid


def structure(owner, groups):
    record = TopicCorrections.objects.filter(owner=owner).first()
    state = record.state if record else {}
    redirects = state.get('redirects', {})
    nodes = state.get('nodes', {})
    kinds = {key: nodes.get(key, {}).get('kind', g['kind']) for key, g in groups.items()}
    membership = {key: {m.pk for m, _ in group['rows']} for key, group in groups.items()}
    parents = {}
    for key, group in groups.items():
        manual = nodes.get(key)
        kind = kinds[key]
        expected = {'work': 'project', 'task': 'work'}.get(kind)
        if not expected:
            parents[key] = None
            continue
        if manual:
            parent = resolve(manual.get('parent'), redirects)
            parents[key] = parent if parent in groups and kinds[parent] == expected else None
            continue
        hint = group.get('parent_hint')
        hint = resolve(hint, redirects) if hint else None
        if hint in groups and kinds[hint] == expected:
            parents[key] = hint
            continue
        # Unique explicit evidence only; never attach a generic work item by title alone.
        candidates = [candidate for candidate, target in groups.items() if candidate != key and kinds[candidate] == expected
                      and group['rows'] and all(m.pk in membership[candidate] or mentions(target['name'], m.text) for m, _ in group['rows'])]
        parents[key] = candidates[0] if len(candidates) == 1 else None
    return parents, kinds


def descendants(key, parents):
    result, pending = {key}, [key]
    while pending:
        current = pending.pop()
        for child, parent in parents.items():
            if parent == current and child not in result:
                result.add(child); pending.append(child)
    return result


def thread_group(owner, groups, key):
    parents, kinds = structure(owner, groups)
    members = {}
    for child in descendants(key, parents):
        for message, score in groups[child]['rows']:
            members[message.pk] = (message, score)
    group = dict(groups[key], rows=list(members.values()), kind=kinds[key])
    children = [{'id': child, 'name': groups[child]['name'], 'kind': kinds[child],
                 'message_count': len({m.pk for nested in descendants(child, parents) for m, _ in groups[nested]['rows']})}
                for child, parent in parents.items() if parent == key]
    ancestors = []
    parent = parents[key]
    while parent:
        ancestors.insert(0, {'id': parent, 'name': groups[parent]['name'], 'kind': kinds[parent]})
        parent = parents[parent]
    return group, children, ancestors


def nest_cards(owner, groups, cards):
    parents, kinds = structure(owner, groups)
    by_id = {card['id']: dict(card, children=[], kind=kinds[card['id']], direct_message_count=card['message_count']) for card in cards}
    for key, card in by_id.items():
        rows = {m.pk: m for nested in descendants(key, parents) for m, _ in groups[nested]['rows']}
        card['message_count'] = len(rows)
        card['chat_count'] = len({m.chat_id for m in rows.values()})
        parent = parents[key]
        if parent in by_id:
            by_id[parent]['children'].append(card)
    return [card for key, card in by_id.items() if parents[key] not in by_id]


class TopicStructure(APIView):
    def post(self, request, topic):
        from .topics import grouped
        body = request.data
        if not isinstance(body, dict) or body.get('action') not in ('create', 'place'):
            raise ValidationError('Choose create or place.')
        action = body['action']
        if set(body) != ({'action', 'name', 'kind'} if action == 'create' else {'action', 'parent', 'kind'}):
            raise ValidationError('Unsupported structure fields.')
        kind = body['kind']
        if not isinstance(kind, str) or kind not in ('project', 'work', 'task', 'topic'):
            raise ValidationError('Invalid item type.')
        if action == 'create' and not valid_name(body['name']):
            raise ValidationError('Use a name between 2 and 80 characters.')
        if action == 'place' and body['parent'] is not None and not isinstance(body['parent'], str):
            raise ValidationError('Invalid parent.')
        limit('topic-structure', str(request.user.pk), 120, 3600)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            groups = grouped(request.user)
            if topic not in groups:
                raise NotFound('Topic no longer exists.')
            parents, kinds = structure(request.user, groups)
            parent = topic if action == 'create' else body['parent']
            expected = {'work': 'project', 'task': 'work'}.get(kind)
            if expected:
                if parent not in groups or kinds[parent] != expected:
                    raise ValidationError('Workstreams need a project; tasks need a workstream.')
            elif parent is not None:
                raise ValidationError('Projects and standalone topics must be at the top level.')
            key = 'd-' + uuid.uuid4().hex[:24] if action == 'create' else topic
            if parent == key or (action == 'place' and parent in descendants(key, parents)):
                raise ValidationError('An item cannot be placed inside itself or its descendants.')
            if action == 'place' and any(parents[child] == key and {'work': 'project', 'task': 'work'}.get(kinds[child]) != kind for child in groups):
                raise ValidationError('Move its children first before changing this item type.')
            name = body['name'].strip() if action == 'create' else groups[key]['name']
            if action == 'create' and any(parents[c] == parent and normalized(g['name']) == normalized(name) for c, g in groups.items()):
                raise ValidationError('That name already exists inside this parent.')
            record, _ = TopicCorrections.objects.get_or_create(owner=request.user)
            state = record.state
            state.setdefault('nodes', {})[key] = {'kind': kind, 'parent': parent, 'keep_empty': True}
            state.setdefault('names', {})[key] = {'name': name, 'kind': kind}
            # Preserve current parent/child identities through AI reindexing.
            for keep in {topic, parent} - {None}:
                state['names'].setdefault(keep, {'name': groups[keep]['name'], 'kind': kinds[keep]})
                state['nodes'].setdefault(keep, {'kind': kinds[keep], 'parent': parents[keep], 'keep_empty': True})
            record.state = state
            from .workflow_views import save_correction
            save_correction(record, 'structure-' + action)
            TopicSummary.objects.filter(owner=request.user).delete()
        return Response({'topic_id': key}, status=201 if action == 'create' else 200)
