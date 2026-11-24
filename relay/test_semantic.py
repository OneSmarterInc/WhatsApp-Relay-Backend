from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch
from .models import User, Chat, Message, SemanticDocument
from .semantic import retrieve, chunks, index_pending, normalize, SemanticError, IndexPending, DIMENSIONS

def unit(n=0):
    return [1.0 if i==n else 0.0 for i in range(DIMENSIONS)]

class SemanticTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user('semantic@example.test','Long-test-password-928')
        self.other=User.objects.create_user('foreign@example.test','Long-test-password-928')
        self.chat=Chat.objects.create(owner=self.user,name='Own')
        self.foreign=Chat.objects.create(owner=self.other,name='Private')
        self.create(self.chat,'Train tickets are booked for Monday.')
        self.create(self.foreign,'Hidden financial secret.')
        self.messages=Message.objects.filter(chat__owner=self.user)
        p=patch('relay.semantic.model_version',return_value='test-v1');p.start();self.addCleanup(p.stop)
        self.encoder=patch('relay.semantic.embed',side_effect=lambda texts:[unit(0) for t in texts]).start()
        self.addCleanup(patch.stopall)

    def create(self,chat,text):
        return Message.objects.create(chat=chat,sender='Aditi',text=text,timestamp=timezone.now(),source_line=1,fingerprint=str(Message.objects.count()))

    def test_retrieval_is_scoped_cached_and_deleted(self):
        result=retrieve('यात्रा कब है?',self.messages)
        self.assertEqual(len(result),1)
        self.assertNotIn('secret',result[0]['text'])
        self.assertEqual(SemanticDocument.objects.count(),1)
        self.encoder.reset_mock()
        retrieve('safar kab hoga?',self.messages)
        self.assertEqual(self.encoder.call_count,1) # query only; document is cached
        self.chat.delete()
        self.assertFalse(SemanticDocument.objects.exists())

    def test_new_messages_and_versions_are_indexed(self):
        retrieve('travel',self.messages)
        self.create(self.chat,'Lunch at noon')
        retrieve('food',self.messages)
        self.assertEqual(SemanticDocument.objects.count(),2)
        with patch('relay.semantic.model_version',return_value='test-v2'):
            retrieve('travel',self.messages)
        self.assertEqual(set(SemanticDocument.objects.values_list('version',flat=True)),{'test-v2'})

    def test_long_tail_retrieved_without_keyword_filter(self):
        text='unrelated filler '*120+'रकम वापस मंगलवार को आएगी।'
        message=self.create(self.chat,text)
        self.encoder.side_effect=lambda texts:[unit(0) if ('रकम' in t or t.startswith('task:')) else unit(1) for t in texts]
        result=retrieve('refund ka status?',self.messages)
        self.assertEqual(result[0]['id'],message.pk)
        self.assertIn('रकम',result[0]['text'])
        self.assertGreater(result[0]['excerpt_start'],0)
        self.assertEqual(result[0]['text'],text[result[0]['excerpt_start']:result[0]['excerpt_end']])

    def test_overlap_has_no_gaps(self):
        text='हिन्दी message '*300
        covered=set()
        for part in chunks(text):covered.update(range(part['start'],part['end']))
        self.assertEqual(covered,set(range(len(text))))

    def test_partial_index_is_resumable(self):
        for i in range(14):self.create(self.chat,f'Message {i}')
        with self.assertRaises(IndexPending):retrieve('topic',self.messages)
        self.assertEqual(SemanticDocument.objects.count(),12)
        retrieve('topic',self.messages)
        self.assertEqual(SemanticDocument.objects.count(),15)

    def test_invalid_vectors_rejected(self):
        for v in ([0]*768,[1,2],['bad']*768,[float('nan')]*768):
            with self.assertRaises(SemanticError):normalize(v)

    def test_embedding_failure_does_not_cache_partial_document(self):
        self.encoder.side_effect=SemanticError('offline')
        with self.assertRaises(SemanticError):retrieve('topic',self.messages)
        self.assertFalse(SemanticDocument.objects.exists())

    def test_chat_scope_excludes_other_owned_chat(self):
        second=Chat.objects.create(owner=self.user,name='Other own chat')
        self.create(second,'A different conversation')
        result=retrieve('conversation',self.messages.filter(chat=self.chat))
        self.assertEqual({r['chat_id'] for r in result},{self.chat.pk})

    def test_changed_message_invalidates_cached_vectors(self):
        retrieve('travel',self.messages)
        message=self.messages.get();message.text='Updated train departure';message.save()
        with self.assertRaises(IndexPending):retrieve('travel',self.messages)
        self.assertFalse(SemanticDocument.objects.exists())
        self.assertEqual(retrieve('travel',self.messages)[0]['text'],'Updated train departure')

    def test_empty_library_never_contacts_ollama(self):
        self.chat.delete()
        self.assertEqual(retrieve('travel',self.messages),[])
        self.encoder.assert_not_called()


    def test_multibyte_chunk_budget(self):
        text='😀नमस्ते'*500
        parts=chunks(text)
        self.assertEqual(parts[-1]['end'],len(text))
        self.assertTrue(all(len(p['text'].encode('utf-8'))<=1200 for p in parts))
