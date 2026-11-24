import json
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, Client
from django.utils import timezone
from rest_framework.response import Response
from . import test_topic_corrections as fixtures
from .models import CorrectionEvent, MessageInsight, ProcessingJob, TopicSummary
from .processing import enqueue, run_step
from .topics import grouped
from .semantic import content_hash
from .insights import extract, VERSION
from .ai import ModelFailure


class WorkflowTests(TestCase):
    setUp = fixtures.CorrectionTests.setUp
    message = fixtures.CorrectionTests.message
    reindex = fixtures.CorrectionTests.reindex
    correct = fixtures.CorrectionTests.correct

    def test_completed_old_configuration_requests_refresh_without_deleting_data(self):
        from .models import MessageTopics
        rows = MessageTopics.objects.filter(message__chat__owner=self.owner)
        rows.update(version='discovery-v8:old-config')
        before = rows.count()
        ProcessingJob.objects.update_or_create(owner=self.owner, defaults={'status':'complete', 'phase':'insights'})
        data = self.client.get('/api/processing/').json()
        self.assertEqual(data['status'], 'failed')
        self.assertTrue(data['retry_available'])
        self.assertIn('configuration changed', data['error'])
        self.assertEqual(rows.count(), before)
        self.assertEqual(ProcessingJob.objects.get(owner=self.owner).status, 'complete')

    def post(self, path, body):
        return self.client.post('/api/'+path, body, content_type='application/json')

    def test_history_latest_first_restores_merge_then_rename(self):
        self.correct(self.first, action='rename', name='Payments platform')
        old=CorrectionEvent.objects.latest('id')
        self.correct(self.first, action='merge', target=self.second)
        new=CorrectionEvent.objects.latest('id')
        self.assertEqual(self.post('corrections/history/', {'event_id':old.pk}).status_code,400)
        self.assertEqual(self.post('corrections/history/', {'event_id':new.pk}).status_code,200)
        self.assertEqual(grouped(self.owner)[self.first]['name'],'Payments platform')
        self.assertEqual(self.post('corrections/history/', {'event_id':old.pk}).status_code,200)
        self.assertEqual(grouped(self.owner)[self.first]['name'],'MySelfPay')
        self.assertFalse(any(e['can_undo'] for e in self.client.get('/api/corrections/history/').json()['events']))

    def test_ungrouped_assignment_and_undo(self):
        m=self.message(self.chat,'Please review the latest layout',5)
        data=self.client.get('/api/ungrouped/').json()
        self.assertEqual([r['id'] for r in data['results']],[str(m.pk)])
        for ids in [[str(self.foreign.pk)],[str(self.a.pk)],[str(m.pk),str(m.pk)],['bad']]:
            self.assertEqual(self.post('ungrouped/',{'target':self.first,'message_ids':ids}).status_code,400)
        self.assertEqual(self.post('ungrouped/',{'target':self.first,'message_ids':[str(m.pk)]}).status_code,200)
        self.assertEqual(self.client.get('/api/ungrouped/').json()['count'],0)
        event=CorrectionEvent.objects.latest('id')
        self.assertEqual(self.post('corrections/history/',{'event_id':event.pk}).status_code,200)
        self.assertEqual(self.client.get('/api/ungrouped/').json()['count'],1)

    def test_structure_undo_and_scoped_ask_include_descendants_only(self):
        work=self.post(f'topics/{self.first}/structure/',{'action':'create','name':'Checkout','kind':'work'}).json()['topic_id']
        self.correct(self.first, action='move',target=work,message_ids=[str(self.a.pk)])
        from .workspace_views import FIELDS
        with patch('relay.ai.retrieve',side_effect=lambda q,ms,*a:list(ms.values(*FIELDS))), patch('relay.ai.generate',return_value={'answer':'ok','results':[]}) as model:
            self.assertEqual(self.post('ask/',{'query':'Show updates','topic_id':self.first}).status_code,200)
            self.assertEqual({r['id'] for r in model.call_args.args[1]},{self.a.pk,self.b.pk})
            self.assertEqual(self.post('ask/',{'query':'Show updates','topic_id':work}).status_code,200)
            self.assertEqual({r['id'] for r in model.call_args.args[1]},{self.a.pk})
            self.assertEqual(self.post('ask/',{'query':'Show updates','topic_id':'not-owned'}).status_code,400)
        for event in CorrectionEvent.objects.order_by('-id'):
            self.assertEqual(self.post('corrections/history/',{'event_id':event.pk}).status_code,200)
        self.assertNotIn(work,grouped(self.owner))

    def test_api_account_isolation_and_csrf(self):
        self.correct(self.first,action='rename',name='Platform')
        event=CorrectionEvent.objects.latest('id')
        enqueue(self.owner)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get('/api/corrections/history/').json()['events'],[])
        self.assertEqual(self.post('corrections/history/',{'event_id':event.pk}).status_code,404)
        self.assertEqual(self.client.get('/api/processing/').json()['status'],'idle')
        self.assertEqual(self.client.get(f'/api/topics/{self.first}/insights/').status_code,400)
        self.assertEqual(self.client.get(f'/api/relationships/?topic={self.first}').status_code,400)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.owner)
        for path in ['processing/','corrections/history/','ungrouped/']:
            self.assertEqual(csrf.post('/api/'+path,{},content_type='application/json').status_code,403)

    def test_insights_original_sources_and_stale_hash(self):
        item={'kind':'task','quote':self.a.text,'owner':None,'deadline':None,'status':'unknown'}
        row=MessageInsight.objects.create(message=self.a,version=VERSION,content_hash=content_hash(self.a),items=[item])
        data=self.client.get(f'/api/topics/{self.first}/insights/').json()
        self.assertEqual(data['processed_messages'],1)
        self.assertEqual(data['items'][0]['source']['chat__name'],'Team')
        self.assertEqual(data['items'][0]['source']['text'],self.a.text)
        row.content_hash='stale';row.save()
        self.assertEqual(self.client.get(f'/api/topics/{self.first}/insights/').json()['items'],[])

    def test_relationships_contain_hierarchy_without_foreign_sources(self):
        work=self.post(f'topics/{self.first}/structure/',{'action':'create','name':'Checkout','kind':'work'}).json()['topic_id']
        data=self.client.get(f'/api/relationships/?topic={self.first}').json()
        self.assertIn({'from':self.first,'to':work,'kind':'contains'},data['edges'])
        self.assertNotIn(self.second,{n['id'] for n in data['nodes']})
        self.assertIn('not verified identities',data['note'])

    @patch('relay.topics.PrepareTopics.as_view')
    def test_durable_worker_phases_and_saved_extraction(self, view):
        view.return_value=lambda request:Response({'status':'ready'})
        enqueue(self.owner)
        self.assertTrue(run_step())
        self.assertEqual(ProcessingJob.objects.get(owner=self.owner).phase,'insights')
        with patch('relay.insights.extract',return_value=[]):self.assertTrue(run_step())
        self.assertEqual(MessageInsight.objects.count(),3)
        self.assertEqual(ProcessingJob.objects.get(owner=self.owner).status,'complete')
        self.assertFalse(run_step())

    @patch('relay.topics.PrepareTopics.as_view')
    def test_worker_lease_failure_resume_and_new_import(self, view):
        job=enqueue(self.owner);job.status='running';job.lease_until=timezone.now()+timedelta(minutes=1);job.save()
        self.assertFalse(run_step())
        enqueue(self.owner);job.refresh_from_db();self.assertTrue(job.rerun)
        job.lease_until=timezone.now()-timedelta(seconds=1);job.save()
        view.return_value=lambda request:Response({},status=503)
        self.assertTrue(run_step());job.refresh_from_db();self.assertEqual(job.status,'failed')
        enqueue(self.owner);job.refresh_from_db();self.assertEqual(job.status,'queued');self.assertFalse(job.rerun)
        view.return_value=lambda request:Response({'status':'ready'})
        self.assertTrue(run_step());job.refresh_from_db();self.assertEqual(job.phase,'insights')

    def test_poll_does_not_call_ai_or_return_stale_summaries(self):
        TopicSummary.objects.create(owner=self.owner,topic=self.first,fingerprint='old',payload={'answer':'stale','results':[]})
        with patch('relay.topics.model_version') as model:
            data=self.client.get('/api/processing/').json()
        model.assert_not_called()
        self.assertIsNone(next(t for t in data['topics'] if t['id']==self.first)['summary'])

    def test_progress_reports_stage_counts_and_heartbeat(self):
        job=enqueue(self.owner)
        job.status='running';job.heartbeat_at=timezone.now();job.progress={'stage':'project grouping','completed':2,'total':3};job.save()
        data=self.client.get('/api/processing/').json()
        self.assertEqual(data['progress']['completed'],2)
        self.assertEqual(data['worker_activity'],'active')
        job.heartbeat_at=timezone.now()-timedelta(seconds=90);job.save()
        self.assertEqual(self.client.get('/api/processing/').json()['worker_activity'],'unresponsive')

    def test_live_heartbeat_prevents_duplicate_claim_after_lease_expiry(self):
        job=enqueue(self.owner);job.status='running';job.lease_until=timezone.now()-timedelta(seconds=1);job.heartbeat_at=timezone.now();job.save()
        self.assertFalse(run_step())
        enqueue(self.owner);job.refresh_from_db()
        self.assertEqual(job.status,'running');self.assertTrue(job.rerun)

    @patch('relay.topics.PrepareTopics.as_view')
    def test_clean_interrupt_releases_claim(self,view):
        view.return_value=lambda request:(_ for _ in ()).throw(KeyboardInterrupt())
        job=enqueue(self.owner)
        with self.assertRaises(KeyboardInterrupt):run_step()
        job.refresh_from_db()
        self.assertEqual(job.status,'queued');self.assertIsNone(job.lease_until)

    def test_context_coverage_requires_current_same_chat_evidence(self):
        from .models import MessageTopics
        from .topic_discovery import topic_id
        context=self.message(self.chat,'College collection is our next workstream',10)
        update=self.message(self.chat,'India records collected',11)
        key=topic_id('College data collection')
        labels=[dict(topic=key,name='College data collection',kind='work',score=.8,context=[{'id':str(context.pk),'hash':content_hash(context)}])]
        row=MessageTopics.objects.create(message=update,version='test',content_hash=content_hash(update),labels=labels)
        self.assertIn(key,grouped(self.owner,'test'))
        context.text='Unrelated changed source';context.save()
        self.assertNotIn(key,grouped(self.owner,'test'))
        labels[0]['context']=[{'id':str(self.foreign.pk),'hash':content_hash(self.foreign)}]
        row.labels=labels;row.save()
        self.assertNotIn(key,grouped(self.owner,'test'))

    def test_worker_requeues_when_import_arrives_during_extraction(self):
        job=enqueue(self.owner);job.phase='insights';job.save()
        def store(message):
            enqueue(self.owner)
            MessageInsight.objects.create(message=message,version=VERSION,content_hash=content_hash(message),items=[])
        with patch('relay.insights.process_message',side_effect=store):self.assertTrue(run_step())
        job.refresh_from_db()
        self.assertEqual((job.status,job.phase,job.rerun),('queued','topics',False))

    @patch('relay.insights.local_json')
    def test_extraction_verifies_quotes_and_explicit_fields(self, model):
        self.a.text='Riya will deploy checkout by Friday. Not done yet.'
        def response(**changes):
            item=dict(kind='commitment',quote=self.a.text,owner='Riya',deadline='Friday',status='pending');item.update(changes)
            model.return_value={'done':True,'message':{'content':json.dumps({'items':[item]})}}
            return extract(self.a)
        result=response()[0]
        self.assertEqual((result['owner'],result['deadline'],result['status']),('Riya','Friday','pending'))
        self.assertEqual(response(status='completed')[0]['status'],'unknown')
        self.assertIsNone(response(owner='Source')[0]['owner'])
        self.assertIsNone(response(deadline='Saturday')[0]['deadline'])
        self.assertEqual(response(quote='Invented action'),[])
        model.return_value={'done':False}
        with self.assertRaises(ModelFailure):extract(self.a)

    @patch('relay.insights.local_json')
    def test_extraction_does_not_infer_owner_from_sender(self, model):
        self.a.text='I will review checkout tomorrow.'
        model.return_value={'done':True,'message':{'content':json.dumps({'items':[dict(kind='commitment',quote=self.a.text,owner='Source',deadline='tomorrow',status='pending')]})}}
        item=extract(self.a)[0]
        self.assertIsNone(item['owner']);self.assertIsNone(item['deadline'])
