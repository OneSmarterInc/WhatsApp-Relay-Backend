from types import SimpleNamespace
from unittest.mock import patch
from django.test import SimpleTestCase
from .topic_language import topic_language,localize_topic

class TopicLanguageTests(SimpleTestCase):
 def messages(self,*texts):return [SimpleNamespace(text=t,system=False) for t in texts]
 def result(self,text='Designs are ready. [1]',source='Designs ready hain.'):
  return {'answer':text,'results':[{'id':'a','citation':1,'text':source,'sender':'A','timestamp':'2026-11-21T10:00:00Z'}],'answer_mode':'generated','grounded':True}
 def test_language_majority_ignores_system_and_has_stable_tie(self):
  self.assertEqual(topic_language(self.messages('काम तैयार है','यह सही है','Ready')),'hi')
  self.assertEqual(topic_language(self.messages('Designs ready hain','Kaam ho gaya','Ready')),'hinglish')
  self.assertEqual(topic_language(self.messages('काम तैयार है','Ready')),'en')
  self.assertEqual(topic_language([SimpleNamespace(text='काम तैयार है',system=True)]),'en')
 def test_hinglish_translation_preserves_sources_and_citations(self):
  r=self.result();original=dict(r['results'][0])
  with patch('relay.topic_language.protected_translation',return_value='Designs ready hain.'):
   out=localize_topic(r,'hinglish')
  self.assertEqual(out['answer'],'Designs ready hain. [1]')
  self.assertEqual(out['results'][0],original)
  self.assertEqual(r['answer'],'Designs are ready. [1]')
 def test_amounts_and_dates_stay_attached_to_each_source(self):
  r=self.result(source='₹2,500 ka refund 23/11/2026 ko milega.')
  r['results'].append({'id':'b','citation':2,'text':'₹900 payment 25/11/2026 ko pending hai.'})
  with patch('relay.topic_language.protected_translation') as model:out=localize_topic(r,'hi')
  model.assert_not_called()
  self.assertEqual(out['answer_mode'],'localized_facts')
  first,second=out['answer'].split('[2]')
  self.assertIn('₹2,500',first);self.assertIn('23/11/2026',first)
  self.assertNotIn('₹900',first);self.assertIn('₹900',second);self.assertIn('25/11/2026',second)
  self.assertEqual(out['results'],r['results'])
 def test_relative_date_invented_by_translation_is_rejected(self):
  with patch('relay.topic_language.protected_translation',return_value='Designs kal ready hain.'):
   out=localize_topic(self.result(),'hinglish')
  self.assertEqual(out['answer_mode'],'source_excerpt')
  self.assertNotIn('kal',out['answer'])
 def test_ambiguous_kal_is_not_resolved(self):
  r=self.result('Meeting is tomorrow. [1]','Meeting kal hai.')
  with patch('relay.topic_language.protected_translation') as model:out=localize_topic(r,'hinglish')
  model.assert_not_called();self.assertEqual(out['answer_mode'],'source_excerpt')
  self.assertEqual(out['results'][0]['text'],'Meeting kal hai.')
 def test_failed_hindi_translation_is_localized_notice(self):
  with patch('relay.topic_language.protected_translation',side_effect=ValueError()):out=localize_topic(self.result(),'hi')
  self.assertIn('मूल संदेश',out['answer']);self.assertEqual(out['answer_language'],'hi')
