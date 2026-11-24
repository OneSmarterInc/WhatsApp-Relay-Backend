import json
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from .ai import generate
from .semantic import with_context, retrieve
from . import test_semantic as fixtures


class EvidenceAnswerTests(SimpleTestCase):
    @patch('relay.ai.urllib.request.build_opener')
    def test_real_quote_cannot_authorize_invented_completion(self,opener):
        text='You have extracted all the data right?'
        opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps({'done':True,'message':{'content':json.dumps({'answer':'The data extraction is complete and approved.','source_quotes':[text]})}}).encode()
        record={'id':'a','text':text,'sender':'Manager','chat__name':'Team','timestamp':'2026-11-21T06:37:00Z'}
        result=generate('Is extraction complete?',[record],'Is extraction complete?',strict=True)
        self.assertNotIn('complete and approved',result['answer'])
        self.assertIn(text,result['answer'])
        self.assertEqual(result['answer_mode'],'source_excerpt')
        payload=json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertIn(record['timestamp'],payload['messages'][1]['content'])

    @patch('relay.ai.urllib.request.build_opener')
    def test_missing_evidence_abstains(self,opener):
        opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps({'done':True,'message':{'content':json.dumps({'answer':'Made up response','source_quotes':[]})}}).encode()
        result=generate('What is the deadline?',[{'id':'a','text':'Hello','sender':'A','chat__name':'Team'}],'What is the deadline?',strict=True)
        self.assertFalse(result['grounded']);self.assertEqual(result['results'],[])
        self.assertNotIn('Made up',result['answer'])

    @patch('relay.ai.urllib.request.build_opener')
    def test_long_source_schema_and_visible_excerpt_agree(self,opener):
        text='Refund remains pending. '*80
        opener.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps({'done':True,'message':{'content':json.dumps({'answer':'Refund completed','source_quotes':[text[:1200]]})}}).encode()
        result=generate('Refund status?',[{'id':'a','text':text,'sender':'A','chat__name':'Team','excerpt_start':10}],'Refund status?',strict=True)
        self.assertEqual(result['results'][0]['text'],text[:1200])
        self.assertEqual(result['results'][0]['excerpt_end'],1210)
        self.assertNotIn('Refund completed',result['answer'])


class RetrievalEvidenceTests(TestCase):
    setUp=fixtures.SemanticTests.setUp
    create=fixtures.SemanticTests.create

    def test_exact_project_name_helps_semantic_ranking(self):
        target=self.create(self.chat,'Selfpay deployment is pending')
        for i in range(15):self.create(self.chat,f'Unrelated lunch discussion {i}')
        from .semantic import index_pending
        while index_pending(self.messages,'test-v1'):pass
        result=retrieve('Selfpay deployment?',self.messages)
        self.assertEqual(result[0]['id'],target.pk)

    def test_context_preserves_owner_and_project_scope(self):
        anchor=self.messages.first()
        followup=self.create(self.chat,'Correction: Tuesday, not Monday.')
        followup.source_line=anchor.source_line+1
        followup.save(update_fields=['source_line'])
        record={'id':anchor.pk,'text':anchor.text,'timestamp':anchor.timestamp,'chat_id':self.chat.pk,'sender':anchor.sender,'chat__name':self.chat.name}
        context=with_context([record],self.messages)
        self.assertIn(followup.pk,{r['id'] for r in context})
        self.assertNotIn(self.foreign.pk,{r['chat_id'] for r in context})
        scoped=with_context([record],self.messages.filter(pk=anchor.pk))
        self.assertEqual([r['id'] for r in scoped],[anchor.pk])
