from django.test import TestCase, Client
from . import test_topic_corrections as fixtures
from .models import MessageTopics, TopicCorrections
from .topics import grouped, cards_for
from .topic_hierarchy import structure, nest_cards


class HierarchyTests(TestCase):
    setUp = fixtures.CorrectionTests.setUp
    message = fixtures.CorrectionTests.message
    reindex = fixtures.CorrectionTests.reindex
    correct = fixtures.CorrectionTests.correct

    def edit(self, topic, **data):
        return self.client.post(f'/api/topics/{topic}/structure/', data, content_type='application/json')

    def create(self, topic, name, kind):
        response = self.edit(topic, action='create', name=name, kind=kind)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()['topic_id']

    def test_three_levels_roll_up_deduplicated_messages_and_context(self):
        work = self.create(self.first, 'Checkout', 'work')
        task = self.create(work, 'Fix refund button', 'task')
        self.assertEqual(self.correct(self.first, action='move', target=task, message_ids=[str(self.a.pk)]).status_code, 200)
        thread = self.client.get(f'/api/topics/{self.first}/messages/').json()
        self.assertEqual(thread['count'], 2)
        self.assertEqual(thread['topic']['direct_message_count'], 1)
        self.assertEqual(thread['topic']['children'][0]['id'], work)
        inherited = next(m for m in thread['results'] if m['id'] == str(self.a.pk))
        self.assertFalse(inherited['direct_member'])
        self.assertEqual(self.client.get(f'/api/topics/{self.first}/messages/{self.a.pk}/context/').status_code, 200)
        leaf = self.client.get(f'/api/topics/{task}/messages/').json()
        self.assertEqual([a['id'] for a in leaf['topic']['ancestors']], [self.first, work])
        self.assertEqual(leaf['count'], 1)
        groups = grouped(self.owner)
        tree = nest_cards(self.owner, groups, cards_for(self.owner, groups, self.a.topic_index.version))
        root = next(t for t in tree if t['id'] == self.first)
        self.assertEqual(root['message_count'], 2)
        self.assertEqual(root['children'][0]['children'][0]['id'], task)
        self.assertNotIn(task, [t['id'] for t in tree])

    def test_empty_children_survive_refresh_and_parent_merge(self):
        work = self.create(self.first, 'Checkout', 'work')
        task = self.create(work, 'Review checkout', 'task')
        self.reindex()
        self.assertIn(task, grouped(self.owner))
        self.assertEqual(self.correct(self.first, action='merge', target=self.second).status_code, 200)
        parents, _ = structure(self.owner, grouped(self.owner))
        self.assertEqual(parents[work], self.second)
        self.assertEqual(parents[task], work)
        self.assertNotIn(self.first, parents)

    def test_reparent_and_rename_survive_ai_reindex(self):
        work = self.create(self.first, 'Checkout', 'work')
        result = self.edit(work, action='place', kind='work', parent=self.second)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.correct(work, action='rename', name='Billing').status_code, 200)
        self.reindex()
        groups = grouped(self.owner)
        self.assertEqual(groups[work]['name'], 'Billing')
        self.assertEqual(structure(self.owner, groups)[0][work], self.second)

    def test_invalid_edges_cycles_foreign_and_csrf(self):
        work = self.create(self.first, 'Checkout', 'work')
        task = self.create(work, 'Review checkout', 'task')
        for key, data in [(self.first, dict(action='place', kind='work', parent=self.first)),
                          (self.first, dict(action='place', kind='task', parent=work)),
                          (task, dict(action='place', kind='task', parent=self.first)),
                          (self.first, dict(action='create', name='Wrong level', kind='task')),
                          (work, dict(action='place', kind='work', parent='unknown'))]:
            self.assertEqual(self.edit(key, **data).status_code, 400)
        self.client.force_login(self.other)
        self.assertEqual(self.edit(work, action='place', kind='project', parent=None).status_code, 404)
        client = Client(enforce_csrf_checks=True); client.force_login(self.owner)
        self.assertEqual(client.post(f'/api/topics/{self.first}/structure/', {'action': 'create', 'name': 'Bad', 'kind': 'work'}, content_type='application/json').status_code, 403)
        self.client.logout()
        self.assertEqual(self.edit(self.first, action='create', name='Nope', kind='work').status_code, 403)

    def test_automatic_link_requires_unique_evidence(self):
        groups = grouped(self.owner)
        groups['work'] = {'name': 'Refund changes', 'kind': 'work', 'rows': [(self.a, 1)], 'parts': []}
        parents, _ = structure(self.owner, groups)
        self.assertEqual(parents['work'], self.first)
        groups[self.second]['rows'].append((self.a, 1))
        self.assertIsNone(structure(self.owner, groups)[0]['work'])

    def test_move_from_project_removes_its_descendant_memberships(self):
        work = self.create(self.first, 'Checkout', 'work')
        task = self.create(work, 'Fix refund button', 'task')
        state = TopicCorrections.objects.get(owner=self.owner)
        state.state.setdefault('messages', {})[str(self.a.pk)] = [self.first, task]
        state.save()
        self.assertEqual(self.correct(self.first, action='move', target=self.second, message_ids=[str(self.a.pk)]).status_code, 200)
        first = self.client.get(f'/api/topics/{self.first}/messages/').json()
        self.assertNotIn(str(self.a.pk), [r['id'] for r in first['results']])
        self.assertEqual(self.client.get(f'/api/topics/{task}/messages/').json()['count'], 0)

    def test_auto_task_keeps_project_and_scoped_workstream(self):
        from unittest.mock import patch
        from .test_topics import envelope, vector
        from .topic_discovery import discover
        from types import SimpleNamespace
        text = 'MySelfPay refund button fix is ready'
        doc = SimpleNamespace(message=SimpleNamespace(text=text), chunks=[{'text': text, 'start': 0, 'end': len(text), 'vector': vector(0)}])
        with patch('relay.topic_discovery.local_json', return_value=envelope([{'source': 1, 'name': 'refund button fix', 'kind': 'task'}])):
            labels = discover([doc], {})[0][1]
        self.assertEqual([label['kind'] for label in labels], ['project', 'work', 'task'])
        self.assertEqual(labels[1]['parent'], labels[0]['topic'])
        self.assertEqual(labels[2]['parent'], labels[1]['topic'])
