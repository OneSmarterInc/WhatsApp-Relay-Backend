from django.test import SimpleTestCase
from unittest.mock import patch
import json
from .localization import language_of,protected_translation,fact_signature,localized_answer,phrase
from .ai import unsupported_details

class LocalizationTests(SimpleTestCase):
 def test_question_languages(self):
  for q,language in [('रिफंड कब आएगा?','hi'),('mera refund kitna hoga?','hinglish'),('payment ki due date batao','hinglish'),('When will my refund arrive?','en'),('What should we do?','en')]:
   self.assertEqual(language_of(q),language)

 def response(self,text):return {'done':True,'message':{'content':json.dumps({'translation':text},ensure_ascii=False)}}

 @patch('relay.localization.local_json')
 def test_protected_hindi_dates_and_amount(self,model):
  model.return_value=self.response('भुगतान [[FACT_A]] को [[FACT_B]] है।')
  self.assertEqual(protected_translation('Payment is due Friday for ₹1,250.50.','hi'),'भुगतान शुक्रवार को ₹1,250.50 है।')

 @patch('relay.localization.local_json')
 def test_hinglish_keeps_exact_decimal_and_date(self,model):
  model.return_value=self.response('Aapko [[FACT_A]] ki rakam [[FACT_B]] ko milegi.')
  result=protected_translation('Refund of ₹1,250.50 arrives on 24/11/2026.','hinglish')
  self.assertIn('₹1,250.50',result);self.assertIn('24/11/2026',result)

 @patch('relay.localization.local_json')
 def test_invented_or_dropped_values_are_rejected(self,model):
  for text in ['रकम 500 है।','रकम [[FACT_A]] और [[FACT_A]] है।','रकम [[FACT_Z]] है।','The amount is [[FACT_A]].','रकम [[FACT_A]] मंगलवार को मिलेगी।']:
   model.return_value=self.response(text)
   with self.assertRaises(ValueError):protected_translation('Amount ₹1,250.50','hi')

 def test_numeric_precision_and_weekday_validation(self):
  self.assertTrue(unsupported_details('मंगलवार 150 रुपये',['Monday 1.50 रुपये']))
  self.assertTrue(unsupported_details('रकम ₹1250 है',['रकम ₹1250.50 है']))
  self.assertFalse(unsupported_details('शुक्रवार ₹1,250.50',['Friday ₹1,250.50']))
  self.assertEqual(fact_signature('सोमवार १२'),fact_signature('Monday 12'))

 @patch('relay.localization.local_json')
 def test_localized_fallback_does_not_rewrite_sources(self,model):
  model.return_value=self.response('Invented English answer')
  quotes=['Refund is ₹1,250.50.']
  answer,mode=localized_answer('Refund is ₹1,250.50.',quotes,'hi')
  self.assertEqual(mode,'source_excerpt');self.assertIn('मूल संदेश',answer)
  self.assertEqual(quotes,['Refund is ₹1,250.50.'])

 def test_empty_and_abstention_messages_localized(self):
  self.assertIn('प्रमाण',phrase('hi','missing'))
  self.assertIn('saboot',phrase('hinglish','missing'))

 def test_fact_summary_uses_source_amount_and_does_not_treat_modal_may_as_month(self):
  from .localization import fact_summary
  text=fact_summary(['Payment of ₹1.5 lakh may arrive Friday.'],'hi')
  self.assertIn('₹1.5 lakh',text)
  self.assertIn('शुक्रवार',text)
  self.assertNotIn('मई',text)

 def test_same_digits_changed_precision_or_date_format_rejected(self):
  self.assertTrue(unsupported_details('राशि 150.0',['Amount 1.500']))
  self.assertTrue(unsupported_details('तारीख 12/11/2026',['Date 11/12/2026']))

 def test_simple_hinglish_preserves_negation_and_uncertainty(self):
  from .localization import simple_hinglish
  self.assertEqual(simple_hinglish('The designs are not ready.'),'designs ready nahi hain.')
  self.assertIsNone(simple_hinglish('The designs may be ready.'))

 def test_attached_time_suffix_is_not_interchangeable(self):
  self.assertTrue(unsupported_details('Departure 6PM',['Departure 6AM']))
