from datetime import timedelta
from django.test import TestCase,Client
from django.utils import timezone
from knox.models import AuthToken
from .models import User,Chat

class MobileAuthTests(TestCase):
 def setUp(self):
  self.user=User.objects.create_user('native@example.test','Native-test-password-932',name='Native')
  self.client=Client(enforce_csrf_checks=True)
 def login(self):
  response=self.client.post('/api/mobile/login/',{'email':self.user.email,'password':'Native-test-password-932'},content_type='application/json')
  self.assertEqual(response.status_code,200,response.content)
  return response.json()['token']
 def test_native_login_owner_scope_and_revocation(self):
  other=User.objects.create_user('other-native@example.test','Strong-other-932')
  Chat.objects.create(owner=self.user,name='Mine');Chat.objects.create(owner=other,name='Private')
  token=self.login();self.client.defaults['HTTP_AUTHORIZATION']='Token '+token
  self.assertEqual(self.client.get('/api/mobile/me/').json()['user']['email'],self.user.email)
  self.assertEqual([c['name'] for c in self.client.get('/api/chats/').json()],['Mine'])
  self.assertNotEqual(AuthToken.objects.get(user=self.user).digest,token)
  self.assertEqual(self.client.post('/api/mobile/logout/').status_code,200)
  self.assertEqual(self.client.get('/api/mobile/me/').status_code,401)
 def test_expired_and_disabled_tokens_rejected(self):
  token=self.login();self.client.defaults['HTTP_AUTHORIZATION']='Token '+token
  AuthToken.objects.filter(user=self.user).update(expiry=timezone.now()-timedelta(seconds=1))
  self.assertEqual(self.client.get('/api/mobile/me/').status_code,401)
  token=self.login();self.client.defaults['HTTP_AUTHORIZATION']='Token '+token
  self.user.is_active=False;self.user.save()
  self.assertEqual(self.client.get('/api/mobile/me/').status_code,401)
 def test_native_signup_and_validation(self):
  bad=self.client.post('/api/mobile/signup/',{'name':'New','email':'bad','password':'short'},content_type='application/json')
  self.assertEqual(bad.status_code,400)
  good=self.client.post('/api/mobile/signup/',{'name':'New','email':'new-native@example.test','password':'Another-password-654!'},content_type='application/json')
  self.assertEqual(good.status_code,201,good.content);self.assertTrue(good.json()['token'])
 def test_cookie_login_still_requires_csrf(self):
  self.assertEqual(self.client.post('/api/auth/login/',{'email':self.user.email,'password':'Native-test-password-932'},content_type='application/json').status_code,403)
 def test_invalid_and_unknown_credentials(self):
  self.assertEqual(self.client.get('/api/mobile/me/').status_code,401)
  self.assertEqual(self.client.post('/api/mobile/login/',{'email':self.user.email,'password':'wrong'},content_type='application/json').status_code,400)
  self.assertEqual(self.client.post('/api/mobile/login/',{'email':self.user.email,'password':'Native-test-password-932','owner':5},content_type='application/json').status_code,400)

 def test_password_login_at_capacity_replaces_only_oldest_session(self):
  tokens=[self.login() for _ in range(5)]
  # Windows clock resolution can tie rapid test logins; make the intended age explicit.
  from knox.crypto import hash_token
  for index,token in enumerate(tokens):
   AuthToken.objects.filter(digest=hash_token(token)).update(created=timezone.now()-timedelta(minutes=5-index))
  other=User.objects.create_user('separate@example.test','Separate-password-932')
  AuthToken.objects.create(user=other)
  newest=self.login()
  self.assertEqual(AuthToken.objects.filter(user=self.user).count(),5)
  self.assertEqual(AuthToken.objects.filter(user=other).count(),1)
  for token,expected in [(tokens[0],401),(tokens[1],200),(newest,200)]:
   self.client.defaults['HTTP_AUTHORIZATION']='Token '+token
   self.assertEqual(self.client.get('/api/mobile/me/').status_code,expected)
 def test_wrong_password_at_capacity_does_not_revoke_sessions(self):
  for _ in range(5): self.login()
  before=set(AuthToken.objects.filter(user=self.user).values_list('digest',flat=True))
  response=self.client.post('/api/mobile/login/',{'email':self.user.email,'password':'wrong'},content_type='application/json')
  self.assertEqual(response.status_code,400)
  self.assertEqual(set(AuthToken.objects.filter(user=self.user).values_list('digest',flat=True)),before)

 def test_browser_session_survives_reload_without_device_tokens(self):
  csrf=self.client.get('/api/auth/csrf/').json()['csrfToken']
  response=self.client.post('/api/auth/login/',{'email':self.user.email,'password':'Native-test-password-932'},content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
  self.assertEqual(response.status_code,200,response.content)
  self.assertTrue(response.cookies['relay_session']['httponly'])
  reloaded=Client(enforce_csrf_checks=True)
  reloaded.cookies=self.client.cookies
  for _ in range(6): self.assertEqual(reloaded.get('/api/auth/me/').status_code,200)
  self.assertEqual(AuthToken.objects.filter(user=self.user).count(),0)
  self.assertEqual(reloaded.post('/api/auth/logout/').status_code,403)
  csrf=reloaded.get('/api/auth/me/').json()['csrfToken']
  self.assertEqual(reloaded.post('/api/auth/logout/',HTTP_X_CSRFTOKEN=csrf).status_code,200)
  self.assertEqual(reloaded.get('/api/auth/me/').status_code,401)
