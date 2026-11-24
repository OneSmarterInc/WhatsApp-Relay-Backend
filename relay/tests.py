from django.test import TestCase, Client
from django.core.files.uploadedfile import SimpleUploadedFile
from .models import User, Chat, Message

class SecurityTests(TestCase):
    def setUp(self):
        self.a=User.objects.create_user('a@example.com','Long-test-password-923')
        self.b=User.objects.create_user('b@example.com','Long-test-password-923')
        self.client.force_login(self.a)

    def test_import_ownership_and_idempotency(self):
        f=SimpleUploadedFile('chat.txt',b'20/11/2026, 10:10 - Rahul: Hello\ncontinued line')
        r=self.client.post('/api/imports/preview/',{'file':f,'name':'Team','date_order':'DMY'})
        self.assertEqual(r.status_code,201,r.content)
        pk=r.json()['id']
        self.client.force_login(self.b)
        self.assertEqual(self.client.post(f'/api/imports/{pk}/confirm/').status_code,404)
        self.client.force_login(self.a)
        url=f'/api/imports/{pk}/confirm/'
        self.assertEqual(self.client.post(url).status_code,200)
        self.assertEqual(self.client.post(url).status_code,200)
        self.assertEqual(Message.objects.count(),1)
        chat=Chat.objects.get()
        self.client.force_login(self.b)
        self.assertEqual(self.client.get('/api/chats/').json(),[])
        self.assertEqual(self.client.get(f'/api/chats/{chat.pk}/messages/').status_code,404)
        self.assertEqual(self.client.delete(f'/api/chats/{chat.pk}/').status_code,404)

    def test_csrf_and_anonymous(self):
        c=Client(enforce_csrf_checks=True)
        self.assertEqual(c.post('/api/auth/signup/',{},content_type='application/json').status_code,403)
        self.assertEqual(c.get('/api/chats/').status_code,403)

    def test_binary_upload_rejected(self):
        r=self.client.post('/api/imports/preview/',{'name':'Bad','file':SimpleUploadedFile('a.txt',b'\x00hello')})
        self.assertEqual(r.status_code,400)

    def test_privileged_signup_fields_rejected(self):
        r=self.client.post('/api/auth/signup/',{'name':'A','email':'new@example.com','password':'Something-very-unique-237','is_staff':'true'},content_type='application/json')
        self.assertEqual(r.status_code,400)
        self.assertFalse(User.objects.filter(email='new@example.com').exists())

    def test_workspace_and_search_are_owner_scoped(self):
        from django.utils import timezone
        for owner, text in [(self.a,'launch review'),(self.b,'launch private secret')]:
            chat=Chat.objects.create(owner=owner,name='Team')
            Message.objects.create(chat=chat,sender='Rahul',text=text,timestamp=timezone.now(),fingerprint=str(owner.pk),source_line=1)
        overview=self.client.get('/api/workspace/').json()
        self.assertEqual(overview['message_count'],1)
        self.assertEqual(overview['recent'][0]['text'],'launch review')
        for mode in ('ask','explore'):
            response=self.client.post('/api/search/',{'query':'launch','mode':mode},content_type='application/json')
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['count'],1)
            self.assertNotIn('private secret',str(response.json()))
        other=Chat.objects.get(owner=self.b)
        self.assertEqual(self.client.post('/api/search/',{'chat_id':str(other.pk)},content_type='application/json').status_code,404)
        self.assertEqual(self.client.post('/api/search/',{'offset':-1},content_type='application/json').status_code,400)
        self.assertEqual(self.client.post('/api/search/',{'query':'nothing-matches'},content_type='application/json').json()['count'],0)
        self.client.logout()
        self.assertEqual(self.client.get('/api/workspace/').status_code,403)
        self.assertEqual(self.client.post('/api/search/',{},content_type='application/json').status_code,403)
