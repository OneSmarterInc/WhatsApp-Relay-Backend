from django.test import TestCase, Client
from django.utils import timezone
from .models import User, Chat, Message, MessageTopics, TopicCorrections
from .semantic import content_hash
from .topic_discovery import topic_id, project_catalog
from .topics import CLASS_VERSION, registry_signature, grouped


class CorrectionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('correct@example.test', 'Correction-password-932')
        self.other = User.objects.create_user('private@example.test', 'Correction-password-943')
        self.chat = Chat.objects.create(owner=self.owner, name='Team')
        self.a = self.message(self.chat, 'MySelfPay refund ready', 1)
        self.b = self.message(self.chat, 'MySelfPay dashboard ready', 2)
        self.c = self.message(self.chat, 'Campus Scraper deployment ready', 3)
        self.foreign = self.message(Chat.objects.create(owner=self.other, name='Private'), 'MySelfPay private', 1)
        self.first, self.second = topic_id('MySelfPay'), topic_id('Campus Scraper')
        self.reindex()
        self.client.force_login(self.owner)

    def message(self, chat, text, line):
        return Message.objects.create(chat=chat, text=text, sender='Source', timestamp=timezone.now(), source_line=line, fingerprint=str(line))

    def reindex(self):
        version = CLASS_VERSION + ':embed:' + registry_signature(project_catalog(Message.objects.filter(chat__owner=self.owner).order_by('timestamp', 'chat_id', 'source_line')))
        for message, name in [(self.a, 'MySelfPay'), (self.b, 'MySelfPay'), (self.c, 'Campus Scraper')]:
            MessageTopics.objects.update_or_create(message=message, defaults={'version': version, 'content_hash': content_hash(message), 'labels': [{'topic': topic_id(name), 'name': name, 'kind': 'project', 'score': 1}]})

    def correct(self, topic, **body):
        return self.client.post(f'/api/topics/{topic}/corrections/', body, content_type='application/json')

    def test_rename_persists_without_rewriting_messages(self):
        response = self.correct(self.first, action='rename', name='Payments platform')
        self.assertEqual(response.status_code, 200)
        self.reindex()
        self.assertEqual(grouped(self.owner)[self.first]['name'], 'Payments platform')
        self.a.refresh_from_db(); self.assertEqual(self.a.text, 'MySelfPay refund ready')
        MessageTopics.objects.all().delete()
        self.assertEqual(len(grouped(self.owner)[self.first]['rows']), 2)

    def test_merge_is_deduplicated_and_survives_reindex(self):
        self.assertEqual(self.correct(self.first, action='merge', target=self.second).status_code, 200)
        self.reindex()
        groups = grouped(self.owner)
        self.assertNotIn(self.first, groups)
        self.assertEqual({m.pk for m, _ in groups[self.second]['rows']}, {self.a.pk, self.b.pk, self.c.pk})
        self.assertEqual(self.client.get(f'/api/topics/{self.first}/messages/').status_code, 404)
        self.assertEqual(self.client.get(f'/api/topics/{self.second}/messages/{self.a.pk}/context/').status_code, 200)

    def test_split_selected_then_move_back(self):
        response = self.correct(self.first, action='split', name='Refund work', message_ids=[str(self.a.pk)])
        self.assertEqual(response.status_code, 200, response.content)
        new = response.json()['topic_id']
        self.assertEqual({m.pk for m, _ in grouped(self.owner)[new]['rows']}, {self.a.pk})
        self.assertEqual({m.pk for m, _ in grouped(self.owner)[self.first]['rows']}, {self.b.pk})
        self.reindex()
        self.assertEqual(len(grouped(self.owner)[new]['rows']), 1)
        self.assertEqual(self.correct(new, action='move', target=self.first, message_ids=[str(self.a.pk)]).status_code, 200)
        self.assertNotIn(new, grouped(self.owner))
        self.assertEqual(len(grouped(self.owner)[self.first]['rows']), 2)

    def test_move_preserves_other_memberships(self):
        item = self.a.topic_index
        item.labels.append({'topic': self.second, 'name': 'Campus Scraper', 'kind': 'project', 'score': 1})
        item.save()
        result = self.correct(self.first, action='move', target=self.second, message_ids=[str(self.a.pk)])
        self.assertEqual(result.status_code, 200)
        self.reindex()
        self.assertEqual({m.pk for m, _ in grouped(self.owner)[self.second]['rows']}, {self.a.pk, self.c.pk})
        self.assertEqual({m.pk for m, _ in grouped(self.owner)[self.first]['rows']}, {self.b.pk})

    def test_validation_and_atomic_failure(self):
        for body in [dict(action='split', name='Split', message_ids=[str(self.a.pk), str(self.b.pk)]),
                     dict(action='move', target=self.second, message_ids=[str(self.foreign.pk)]),
                     dict(action='move', target=self.second, message_ids=['bad']),
                     dict(action='merge', target=self.first), dict(action='merge', target='unknown'),
                     dict(action='rename', name='Campus Scraper'), dict(action='rename', name='<script>'),
                     dict(action='rename', name='Fine', owner=self.other.pk)]:
            self.assertEqual(self.correct(self.first, **body).status_code, 400, body)
        self.assertFalse(TopicCorrections.objects.exists())

    def test_owner_scope_auth_csrf(self):
        self.client.force_login(self.other)
        self.assertEqual(self.correct(self.first, action='rename', name='Stolen').status_code, 404)
        self.assertEqual(self.client.get('/api/topics/').json()['topics'], [])
        client = Client(enforce_csrf_checks=True); client.force_login(self.owner)
        self.assertEqual(client.post(f'/api/topics/{self.first}/corrections/', {'action': 'rename', 'name': 'Protected'}, content_type='application/json').status_code, 403)
        self.client.logout()
        self.assertEqual(self.correct(self.first, action='rename', name='Nope').status_code, 403)
        self.assertEqual(self.client.get('/api/topics/').status_code, 403)

    def test_deleting_source_removes_manual_membership(self):
        self.correct(self.first, action='move', target=self.second, message_ids=[str(self.a.pk)])
        self.a.delete()
        self.assertEqual({m.pk for m, _ in grouped(self.owner)[self.second]['rows']}, {self.c.pk})

    def test_merge_redirects_new_ai_messages_and_clears_only_own_summaries(self):
        from .models import TopicSummary
        TopicSummary.objects.create(owner=self.owner, topic=self.first, fingerprint='old', payload={'answer': 'old'})
        TopicSummary.objects.create(owner=self.other, topic=self.first, fingerprint='private', payload={'answer': 'private'})
        self.assertEqual(self.correct(self.first, action='merge', target=self.second).status_code, 200)
        self.assertFalse(TopicSummary.objects.filter(owner=self.owner).exists())
        self.assertTrue(TopicSummary.objects.filter(owner=self.other).exists())
        future = self.message(self.chat, 'MySelfPay next release', 4)
        version = self.a.topic_index.version
        MessageTopics.objects.create(message=future, version=version, content_hash=content_hash(future),
            labels=[{'topic': self.first, 'name': 'MySelfPay', 'kind': 'project', 'score': 1}])
        self.assertEqual({m.pk for m, _ in grouped(self.owner)[self.second]['rows']}, {self.a.pk, self.b.pk, self.c.pk, future.pk})
        self.assertNotIn(self.first, grouped(self.owner))

    def test_merge_does_not_force_endless_ai_reindexing(self):
        from unittest.mock import patch
        from .models import TopicSummary
        from .topics import signature
        self.correct(self.first, action='merge', target=self.second)
        group = grouped(self.owner)[self.second]
        version = self.a.topic_index.version
        TopicSummary.objects.create(owner=self.owner, topic=self.second, fingerprint=signature(group['rows'], version), payload={'answer': 'saved', 'results': []})
        with patch('relay.topics.model_version', return_value='embed'), patch('relay.topics.index_pending', return_value=0), patch('relay.topics.discover') as discover:
            response = self.client.post('/api/topics/prepare/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['status'], 'ready')
        discover.assert_not_called()
