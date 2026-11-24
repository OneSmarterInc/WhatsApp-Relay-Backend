import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase, Client, SimpleTestCase
from django.utils import timezone
from .models import User, Chat, Message, SemanticDocument, MessageTopics
from .semantic import content_hash
from .topics import CLASS_VERSION
from .topic_discovery import discover, topic_id
from .ai import ModelFailure


def vector(n):
    return [1.0 if i == n else 0.0 for i in range(768)]


def envelope(assignments):
    return {'done': True, 'message': {'content': json.dumps({'assignments': assignments})}}


def extract_response(path, payload, **kwargs):
    sources = json.loads(payload['messages'][1]['content'])['messages']
    items = []
    for source in sources:
        for name in ['MySelfPay', 'Campus Scraper', 'Goa trip', 'भुगतान']:
            if name.lower() in source['text'].lower():
                items.append({'source': source['source'], 'name': name, 'kind': 'project' if name in ['MySelfPay', 'Campus Scraper'] else 'topic'})
                break
    return envelope(items)


class TopicTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user('topics-a@example.test', 'Very-long-password-942')
        self.b = User.objects.create_user('topics-b@example.test', 'Very-long-password-942')
        self.chat = Chat.objects.create(owner=self.a, name='Own')
        self.message = self.add(self.chat, 'MySelfPay refund ₹500 Tuesday', 0)
        self.add(Chat.objects.create(owner=self.b, name='Secret'), 'MySelfPay private ₹900', 0)
        self.client.force_login(self.a)
        for target, value in [('relay.topics.model_version', 'embed-v1'), ('relay.topics.index_pending', 0)]:
            p = patch(target, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch('relay.topic_discovery.local_json', side_effect=extract_response)
        self.model = p.start(); self.addCleanup(p.stop)
        p = patch('relay.topics.generate', side_effect=lambda q, records, en, **kwargs: {'answer': 'Refund ₹500 Tuesday [1]', 'results': [dict(records[0], citation=1)], 'grounded': True, 'answer_mode': 'generated'})
        self.generated = p.start(); self.addCleanup(p.stop)

    def add(self, chat, text, n):
        m = Message.objects.create(chat=chat, text=text, sender='A', timestamp=timezone.now(), fingerprint=str(Message.objects.count()), source_line=Message.objects.count()+1)
        SemanticDocument.objects.create(message=m, version='embed-v1', content_hash=content_hash(m), chunks=[{'start': 0, 'end': len(text), 'text': text, 'vector': vector(n)}])
        return m

    def prepare(self):
        for _ in range(10):
            r = self.client.post('/api/topics/prepare/', {}, content_type='application/json')
            self.assertIn(r.status_code, (200, 202), r.content)
            if r.json()['status'] == 'ready': return r.json()
        self.fail('Discovery did not complete')

    def test_owner_scoped_dynamic_grouping_and_cache(self):
        d = self.prepare(); topic = d['topics'][0]
        self.assertEqual(topic['name'], 'MySelfPay')
        self.assertEqual(topic['id'], topic_id('MySelfPay'))
        self.assertEqual(topic['kind'], 'project')
        self.assertEqual(topic['message_count'], 1)
        self.assertEqual(topic['summary']['results'][0]['text'], self.message.text)
        self.prepare(); self.assertEqual(self.generated.call_count, 1); self.assertEqual(self.model.call_count, 1)
        rows = self.client.get(f"/api/topics/{topic['id']}/messages/").json()
        self.assertEqual(rows['count'], 1); self.assertNotIn('private', str(rows))
        self.assertNotIn('private', str(self.model.call_args))
        self.assertEqual(MessageTopics.objects.count(), 1)

    def test_cross_chat_project_and_unrelated_project_stay_separate(self):
        self.add(Chat.objects.create(owner=self.a, name='Second'), 'MySelfPay dashboard ready hai', 0)
        self.add(self.chat, 'Campus Scraper dashboard ready hai', 1)
        self.add(self.chat, 'Thanks', 2)
        d = self.prepare(); cards = {c['name']: c for c in d['topics']}
        self.assertEqual(set(cards), {'MySelfPay', 'Campus Scraper'})
        self.assertEqual(cards['MySelfPay']['message_count'], 2)
        self.assertEqual(cards['MySelfPay']['chat_count'], 2)
        self.assertEqual(d['ungrouped_count'], 1)

    def test_new_import_reuses_id_and_invalidates_summary(self):
        first = self.prepare()['topics'][0]
        self.add(self.chat, 'myselfpay another refund ₹250', 0)
        later = self.prepare()['topics'][0]
        self.assertEqual(first['id'], later['id']); self.assertEqual(later['message_count'], 2)
        self.assertEqual(self.generated.call_count, 2)

    def test_deleted_sources_and_foreign_topics_are_not_returned(self):
        key = self.prepare()['topics'][0]['id']
        self.chat.delete()
        self.assertEqual(self.prepare()['topics'], [])
        self.assertEqual(self.client.get(f'/api/topics/{key}/messages/').status_code, 404)
        self.assertFalse(MessageTopics.objects.exists())

    def test_csrf_auth_and_input(self):
        c = Client(enforce_csrf_checks=True); c.force_login(self.a)
        self.assertEqual(c.post('/api/topics/prepare/', {}, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.get('/api/topics/not-real/messages/?offset=-1').status_code, 400)
        self.assertEqual(self.client.get('/api/topics/not-real/messages/').status_code, 404)
        self.assertEqual(self.client.post('/api/topics/prepare/', {'owner': self.b.pk}, content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post('/api/topics/prepare/', [], content_type='application/json').status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.post('/api/topics/prepare/', {}, content_type='application/json').status_code, 403)
        self.assertEqual(self.client.get('/api/topics/not-real/messages/').status_code, 403)

    def test_busy_ai_returns_retryable_progress_without_running_model(self):
        with patch('relay.topics.LOCK') as lock:
            lock.acquire.return_value = False
            response = self.client.post('/api/topics/prepare/', {}, content_type='application/json')
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()['status'], 'busy')
            self.assertEqual(response['Retry-After'], '5')
            self.model.assert_not_called()
            lock.release.assert_not_called()

    def test_pending_embeddings_dont_discover(self):
        with patch('relay.topics.index_pending', return_value=4):
            r = self.client.post('/api/topics/prepare/', {}, content_type='application/json')
        self.assertEqual(r.status_code, 202); self.model.assert_not_called(); self.generated.assert_not_called()

    def test_legacy_category_labels_are_replaced(self):
        MessageTopics.objects.create(message=self.message, version='topics-v4:embed-v1', content_hash=content_hash(self.message), labels=[{'topic': 'work', 'score': 1}])
        self.assertEqual(self.prepare()['topics'][0]['name'], 'MySelfPay')
        self.assertEqual(self.client.get('/api/topics/work/messages/').status_code, 404)

    def test_relative_date_guard_and_original_hindi_sources(self):
        self.message.text = 'MySelfPay का भुगतान ₹500 का 23/11/2026 को करना है।'; self.message.save()
        doc = self.message.semantic_document; doc.content_hash = content_hash(self.message)
        doc.chunks = [{'start': 0, 'end': len(self.message.text), 'text': self.message.text, 'vector': vector(0)}]; doc.save()
        self.generated.side_effect = lambda q, rows, en, **kwargs: {'answer': 'Refund tomorrow', 'results': [dict(rows[0], citation=1)], 'grounded': True, 'answer_mode': 'generated'}
        summary = self.prepare()['topics'][0]['summary']
        self.assertEqual(summary['answer_language'], 'en'); self.assertEqual(summary['answer_mode'], 'source_excerpt')
        self.assertEqual(summary['results'][0]['text'], self.message.text)

    def test_invalid_model_response_leaves_message_ungrouped(self):
        self.model.return_value = envelope([{'source': 999, 'name': 'MySelfPay', 'kind': 'project'}]); self.model.side_effect = None
        r = self.client.post('/api/topics/prepare/', {}, content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['ungrouped_count'], 1)
        self.assertEqual(r.json()['topics'], [])
        self.assertEqual(MessageTopics.objects.get(message=self.message).labels, [])
        self.assertEqual(self.model.call_count, 2)

    def test_failed_summary_keeps_topic_and_original_evidence(self):
        self.generated.side_effect = ModelFailure('Invalid generated summary')
        card = self.prepare()['topics'][0]
        self.assertEqual(card['name'], 'MySelfPay')
        self.assertEqual(card['summary']['answer_mode'], 'source_excerpt')
        self.assertEqual(card['summary']['results'][0]['text'], self.message.text)

    def test_invalid_batch_retries_one_source_without_losing_others(self):
        second = self.add(self.chat, 'Campus Scraper ready', 1)
        self.model.side_effect = [ModelFailure('Invalid batch'), envelope([{'source': 1, 'name': 'MySelfPay', 'kind': 'project'}])]
        response = self.client.post('/api/topics/prepare/', {}, content_type='application/json')
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['remaining'], 1)
        self.assertEqual(MessageTopics.objects.filter(message__chat__owner=self.a).count(), 1)
        self.assertEqual(self.model.call_count, 2)

    def test_context_is_same_chat_and_time_bounded(self):
        from datetime import timedelta
        from .topic_discovery import nearby
        followup = self.add(self.chat, 'The refund button fix is ready', 0)
        followup.source_line = 2
        followup.timestamp = self.message.timestamp + timedelta(minutes=2)
        followup.save()
        context = nearby(followup.semantic_document)
        self.assertEqual([r['text'] for r in context], [self.message.text])
        followup.timestamp = self.message.timestamp + timedelta(hours=4)
        followup.save()
        self.assertEqual([r['text'] for r in nearby(followup.semantic_document)], [self.message.text])
        followup.timestamp = self.message.timestamp + timedelta(hours=4,seconds=1)
        followup.save()
        self.assertEqual(nearby(followup.semantic_document), [])

    def test_declared_aliases_and_spelling_variants_group_across_chats(self):
        self.message.text = 'MySelfPay (MSP) refund dashboard ready'
        self.message.save()
        doc = self.message.semantic_document
        doc.content_hash = content_hash(self.message)
        doc.chunks = [{'start': 0, 'end': len(self.message.text), 'text': self.message.text, 'vector': vector(0)}]
        doc.save()
        second = Chat.objects.create(owner=self.a, name='Client')
        self.add(second, 'MSP refund button fixed', 0)
        self.add(second, 'My Self Pay payment review done', 0)
        cards = self.prepare()['topics']
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['message_count'], 3)
        self.assertEqual(cards[0]['chat_count'], 2)

    def test_mixed_project_message_counts_once_in_coverage(self):
        self.add(self.chat, 'MySelfPay refund ready; Campus Scraper deployment ready', 0)
        self.add(self.chat, 'yes sir', 2)
        data = self.prepare()
        self.assertEqual({t['name']: t['message_count'] for t in data['topics']}, {'MySelfPay': 2, 'Campus Scraper': 1})
        self.assertEqual(data['ungrouped_count'], 1)

    def test_alias_declarations_from_other_account_are_not_used(self):
        other_chat = Chat.objects.create(owner=self.b, name='Other registry')
        self.add(other_chat, 'SecretPortal (SP) deployment ready', 0)
        self.add(self.chat, 'SP deployment ready', 0)
        data = self.prepare()
        self.assertNotIn('SecretPortal', [c['name'] for c in data['topics']])
        self.assertEqual(data['ungrouped_count'], 1)
        self.assertNotIn('SecretPortal', str(self.model.call_args_list))

    def test_mixed_project_summary_uses_only_its_own_clause(self):
        self.add(self.chat, 'MySelfPay refund ₹500 ready; Campus Scraper deployment ₹900 pending', 0)
        self.prepare()
        campus = next(call for call in self.generated.call_args_list if 'Campus Scraper' in call.args[0])
        self.assertTrue(all('₹500' not in r['text'] for r in campus.args[1]))
        self.assertTrue(any('₹900' in r['text'] for r in campus.args[1]))

    def test_deleted_alias_declaration_invalidates_old_assignments(self):
        declaration = self.add(self.chat, 'MySelfPay (MSP) dashboard ready', 0)
        self.add(self.chat, 'MSP refund ready', 0)
        first = self.prepare()['topics'][0]
        self.assertEqual(first['message_count'], 3)
        declaration.delete()
        # Even before a refresh, stale aliases are not returned by the sources API.
        self.assertEqual(self.client.get(f"/api/topics/{first['id']}/messages/").status_code, 404)
        current = self.prepare()
        self.assertEqual(current['topics'][0]['message_count'], 1)
        self.assertEqual(current['ungrouped_count'], 1)

    def test_thread_metadata_pagination_and_cross_chat_order(self):
        from datetime import timedelta
        second_chat = Chat.objects.create(owner=self.a, name='Client timeline')
        for i in range(32):
            message = self.add(second_chat if i % 2 else self.chat, f'MySelfPay update {i}', 0)
            message.timestamp = self.message.timestamp + timedelta(minutes=i+1)
            message.save()
        topic = self.prepare()['topics'][0]
        calls = self.model.call_count
        first = self.client.get(f"/api/topics/{topic['id']}/messages/").json()
        second = self.client.get(f"/api/topics/{topic['id']}/messages/?offset=30").json()
        self.assertEqual(first['topic']['name'], 'MySelfPay')
        self.assertEqual(first['topic']['chat_count'], 2)
        self.assertEqual(first['count'], 33)
        self.assertEqual(len(first['results']), 30)
        self.assertEqual(len(second['results']), 3)
        self.assertTrue(first['has_more']); self.assertFalse(second['has_more'])
        all_rows = first['results'] + second['results']
        self.assertEqual(len({r['id'] for r in all_rows}), 33)
        self.assertEqual([r['timestamp'] for r in all_rows], sorted(r['timestamp'] for r in all_rows))
        self.assertTrue(all(r['chat__name'] and r['sender'] and r['text'] for r in all_rows))
        self.assertEqual(self.model.call_count, calls)

    def test_thread_context_is_bounded_and_highlights_original(self):
        from datetime import timedelta
        self.message.source_line = 100
        self.message.save()
        for i in range(1, 6):
            before = self.add(self.chat, f'Prior context {i}', 2)
            before.timestamp = self.message.timestamp-timedelta(minutes=i)
            before.save()
            after = self.add(self.chat, f'Later context {i}', 2)
            after.timestamp = self.message.timestamp+timedelta(minutes=i)
            after.save()
        foreign_chat = Chat.objects.create(owner=self.a, name='Unrelated chat')
        unrelated = self.add(foreign_chat, 'Nearby in time but another chat', 2)
        key = self.prepare()['topics'][0]['id']
        result = self.client.get(f'/api/topics/{key}/messages/{self.message.pk}/context/').json()
        self.assertEqual(len(result['results']), 7)
        self.assertTrue(result['has_earlier']); self.assertTrue(result['has_later'])
        self.assertEqual(result['results'][3]['id'], str(self.message.pk))
        self.assertTrue(result['results'][3]['is_focus'])
        self.assertEqual(sum(r['is_focus'] for r in result['results']), 1)
        self.assertEqual(sum(r['in_topic'] for r in result['results']), 1)
        self.assertTrue(all(r['chat_id'] == str(self.chat.pk) for r in result['results']))
        self.assertNotIn(str(unrelated.pk), str(result))

    def test_thread_context_rejects_foreign_unassigned_and_deleted_messages(self):
        key = self.prepare()['topics'][0]['id']
        other = Message.objects.filter(chat__owner=self.b).first()
        unrelated = self.add(self.chat, 'Not part of the topic', 2)
        for message_id in [other.pk, unrelated.pk]:
            self.assertEqual(self.client.get(f'/api/topics/{key}/messages/{message_id}/context/').status_code, 404)
        self.client.force_login(self.b)
        self.assertEqual(self.client.get(f'/api/topics/{key}/messages/{self.message.pk}/context/').status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(f'/api/topics/{key}/messages/{self.message.pk}/context/').status_code, 403)
        self.client.force_login(self.a)
        original_id = self.message.pk
        self.message.delete()
        self.assertEqual(self.client.get(f'/api/topics/{key}/messages/{original_id}/context/').status_code, 404)

    def test_source_messages_are_chronological(self):
        second = self.add(self.chat, 'MySelfPay later update', 0)
        key = self.prepare()['topics'][0]['id']
        rows = self.client.get(f'/api/topics/{key}/messages/').json()['results']
        self.assertEqual([r['id'] for r in rows], [str(self.message.pk), str(second.pk)])


class DiscoveryValidationTests(SimpleTestCase):
    def doc(self, text, n=0):
        return SimpleNamespace(message=SimpleNamespace(text=text), chunks=[{'text': text, 'start': 0, 'end': len(text), 'vector': vector(n)}])

    def test_hallucinated_name_cannot_create_topic(self):
        with patch('relay.topic_discovery.local_json', return_value=envelope([{'source': 1, 'name': 'Invented Project', 'kind': 'project'}])):
            result = discover([self.doc('The website is ready')], {})
        self.assertEqual(result[0][1], [])

    def test_name_from_other_message_cannot_be_transferred(self):
        with patch('relay.topic_discovery.local_json', return_value=envelope([{'source': 2, 'name': 'MySelfPay', 'kind': 'project'}])):
            result = discover([self.doc('MySelfPay ready'), self.doc('Goa trip booked', 1)], {})
        self.assertEqual(result[1][1], [])

    def test_similar_project_names_do_not_merge(self):
        known = {topic_id('Campus Scraper'): {'name': 'Campus Scraper', 'kind': 'project', 'parts': self.doc('Campus Scraper ready').chunks}}
        with patch('relay.topic_discovery.local_json', return_value=envelope([{'source': 1, 'name': 'Hospital Scraper', 'kind': 'project'}])):
            result = discover([self.doc('Hospital Scraper ready')], known)
        self.assertEqual(result[0][1][0]['name'], 'Hospital Scraper')

    def test_duplicate_source_ids_reject_entire_response(self):
        item = {'source': 1, 'name': 'MySelfPay', 'kind': 'project'}
        with patch('relay.topic_discovery.local_json', return_value=envelope([item, item])):
            with self.assertRaises(ModelFailure): discover([self.doc('MySelfPay ready'), self.doc('Goa trip')], {})

    def test_truncated_model_response_is_rejected(self):
        response = envelope([]); response['done_reason'] = 'length'
        with patch('relay.topic_discovery.local_json', return_value=response):
            with self.assertRaises(ModelFailure): discover([self.doc('MySelfPay ready')], {})

    def test_topic_id_case_and_spacing_are_stable(self):
        self.assertEqual(topic_id(' Campus   Scraper '), topic_id('campus scraper'))

    def test_model_cannot_make_acknowledgement_a_topic(self):
        with patch('relay.topic_discovery.local_json', return_value=envelope([{'source': 1, 'name': 'Thanks!', 'kind': 'topic'}])):
            self.assertEqual(discover([self.doc('Thanks!')], {})[0][1], [])

    def test_rejects_fragments_even_when_model_calls_them_projects(self):
        from .topic_discovery import useful_name
        for name in ['yes sir', 'okay sir', '1pm call', 'batch-5c16b300f0.xlsx', 'new project', 'Australia', 'India is left']:
            for kind in ['project', 'task', 'work', 'topic']:
                self.assertFalse(useful_name(name, kind), (name, kind))
        self.assertTrue(useful_name('MySelfPay', 'project'))
        self.assertTrue(useful_name('login bug fix', 'task'))
        self.assertTrue(useful_name('database migration', 'work'))

    def test_context_name_can_attach_substantive_followup(self):
        response = envelope([{'source': 1, 'name': 'MySelfPay', 'kind': 'project'}])
        with patch('relay.topic_discovery.nearby', return_value=[{'text': 'MySelfPay payment dashboard refund button'}]), patch('relay.topic_discovery.local_json', return_value=response):
            result = discover([self.doc('The refund button fix is ready for review')], {})
        self.assertEqual(result[0][1][0]['name'], 'MySelfPay')

    def test_context_does_not_turn_yes_sir_into_a_project(self):
        response = envelope([{'source': 1, 'name': 'MySelfPay', 'kind': 'project'}])
        with patch('relay.topic_discovery.nearby', return_value=[{'text': 'MySelfPay payment dashboard refund button'}]), patch('relay.topic_discovery.local_json', return_value=response):
            self.assertEqual(discover([self.doc('yes sir')], {})[0][1], [])

    def test_explicit_project_name_wins_over_activity_label(self):
        response = envelope([{'source': 1, 'name': 'deployment', 'kind': 'work'}])
        with patch('relay.topic_discovery.local_json', return_value=response):
            result = discover([self.doc('Campus Scraper deployment is ready')], {})
        self.assertEqual(result[0][1][0]['name'], 'Campus Scraper')
        self.assertEqual(result[0][1][0]['kind'], 'project')

    def test_attachment_only_message_cannot_inherit_project(self):
        response = envelope([{'source': 1, 'name': 'MySelfPay', 'kind': 'project'}])
        with patch('relay.topic_discovery.nearby', return_value=[{'text': 'MySelfPay dashboard'}]), patch('relay.topic_discovery.local_json', return_value=response):
            self.assertEqual(discover([self.doc('batch-5c16b300f0.xlsx')], {})[0][1], [])

    def test_unrelated_subject_cannot_borrow_nearby_project(self):
        response = envelope([{'source': 1, 'name': 'Campus Scraper', 'kind': 'project'}])
        with patch('relay.topic_discovery.nearby', return_value=[{'text': 'Campus Scraper deployment ready for review'}]), patch('relay.topic_discovery.local_json', return_value=response):
            self.assertEqual(discover([self.doc('Goa trip hotel booking confirmed')], {})[0][1], [])

    def test_strong_context_recovers_omitted_work_followup(self):
        with patch('relay.topic_discovery.nearby', return_value=[{'text': 'MySelfPay refund button needs a fix'}]), patch('relay.topic_discovery.local_json', return_value=envelope([])):
            labels = discover([self.doc('The refund button fix is ready for review')], {})[0][1]
        self.assertEqual(labels[0]['name'], 'MySelfPay')

    def test_ambiguous_acronym_is_not_guessed(self):
        from .topic_discovery import project_catalog
        docs = [self.doc('MySelfPay (MSP) launch'), self.doc('MarketSyncPortal (MSP) launch')]
        for doc in docs: doc.message.system = False
        registry = project_catalog([d.message for d in docs])
        self.assertTrue(all(not g['aliases'] for g in registry.values()))
        with patch('relay.topic_discovery.local_json', return_value=envelope([])):
            self.assertEqual(discover([self.doc('MSP ready')], registry)[0][1], [])

    def test_spelling_ids_match_without_merging_distinct_projects(self):
        self.assertEqual(topic_id('MySelfPay'), topic_id('my-self-pay'))
        self.assertEqual(topic_id('MySelfPay'), topic_id('My Self Pay'))
        self.assertNotEqual(topic_id('Campus Scraper'), topic_id('Hospital Scraper'))

    def test_all_explicit_projects_retained_in_long_message(self):
        doc = self.doc('MySelfPay refund ready. Campus Scraper deployment ready.')
        doc.chunks = [{'text': 'MySelfPay refund ready.', 'start': 0, 'end': 22, 'vector': vector(0)},
                      {'text': 'Campus Scraper deployment ready.', 'start': 23, 'end': len(doc.message.text), 'vector': vector(1)}]
        with patch('relay.topic_discovery.local_json', return_value=envelope([])):
            labels = discover([doc], {})[0][1]
        self.assertEqual({label['name'] for label in labels}, {'MySelfPay', 'Campus Scraper'})

    def test_lowercase_parenthetical_is_not_an_alias(self):
        from .topic_discovery import project_catalog
        doc = self.doc('MySelfPay (refund) deployment ready')
        doc.message.system = False
        registry = project_catalog([doc.message])
        self.assertEqual(registry[topic_id('MySelfPay')]['aliases'], [])

    def test_descriptive_work_title_requires_subject_evidence(self):
        from .topic_discovery import supported_work_title
        self.assertTrue(supported_work_title('College data collection', 'Collected records from 58 colleges'))
        self.assertFalse(supported_work_title('College data collection', 'Australia is ready'))
        self.assertFalse(supported_work_title('MySelfPay refund', 'Refund ready'))

    def test_urls_do_not_seed_project_names(self):
        from .topic_discovery import explicit_projects
        self.assertEqual(explicit_projects('https://example.test/MyRandomCode'), [])

    def test_real_chat_fragments_are_not_titles(self):
        from .topic_discovery import useful_name, explicit_projects
        for name in ['Image omitted', '@all are invited', 'I think mishra did vietnam!', 'Rename it with country name please']:
            self.assertFalse(useful_name(name, 'topic'))
        self.assertIn('selfpay', explicit_projects('@Gitesh selfpay push all changes to main branch'))
        self.assertIn('whatsapp project overlay', explicit_projects('find one chat whatsapp project overlay'))
        self.assertEqual(explicit_projects('No leaves will be given for ReUT. Adjust working time'), [])
