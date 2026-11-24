from django.test import SimpleTestCase
from .task_history import reconcile
from .topic_discovery import contextual_project


class TaskHistoryTests(SimpleTestCase):
    def item(self,key,text,status='pending',owner=None):
        return {'id':key,'kind':'task','quote':text,'status':status,'owner':owner,'deadline':None,'source':{'id':key}}

    def test_fix_and_completion_form_one_history(self):
        items=[self.item('1','I will fix login bug'),self.item('2','Login bug fixed','completed')]
        result=reconcile(items,{'1':['project-a'],'2':['project-a']})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['status'],'completed')
        self.assertEqual(len(result[0]['history']),2)
        self.assertEqual(result[0]['history'][0]['quote'],items[0]['quote'])

    def test_different_projects_and_owners_do_not_merge(self):
        a=self.item('1','Riya will fix login bug',owner='Riya')
        b=self.item('2','Asha will fix login bug',owner='Asha')
        self.assertEqual(len(reconcile([a,b],{'1':['a'],'2':['a']})),2)
        b=self.item('2','Login bug fixed','completed')
        self.assertEqual(len(reconcile([a,b],{'1':['a'],'2':['b']})),2)
        self.assertEqual(len(reconcile([a,b],{'1':['a','b'],'2':['a','b']})),2)

    def test_different_actions_and_ambiguous_done_stay_separate(self):
        items=[self.item('1','Please review login bug'),self.item('2','Login bug fixed','completed'),self.item('3','Done','completed')]
        self.assertEqual(len(reconcile(items,{k:['p'] for k in ['1','2','3']})),3)

    def test_later_pending_reopens_same_subject(self):
        result=reconcile([self.item('1','Login bug fixed','completed'),self.item('2','Login bug not fixed','pending')],{'1':['p'],'2':['p']})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['status'],'pending')
        self.assertFalse(result[0]['completion_confirmed'])

    def test_context_needs_one_supported_project(self):
        groups={'a':{'name':'NimbusMart','kind':'project'},'b':{'name':'RapidSim','kind':'project'}}
        context=[{'text':'NimbusMart login bug needs a fix'}]
        self.assertEqual(contextual_project('login bug fixed',context,groups)[0],'a')
        self.assertIsNone(contextual_project('yes sir',context,groups))
        context.append({'text':'RapidSim login bug needs a fix'})
        self.assertIsNone(contextual_project('login bug fixed',context,groups))

    def test_website_feedback_uses_host_evidence_not_proximity_alone(self):
        groups={'a':{'name':'site.example','kind':'project','domains':['site.example']}}
        context=[{'text':'Please browse this website https://site.example'}]
        self.assertEqual(contextual_project('The mobile responsiveness looks good',context,groups)[0],'a')
        self.assertIsNone(contextual_project('Happy birthday',context,groups))
        self.assertIsNone(contextual_project('The mobile responsiveness looks good',[{'text':'https://different.example'}],groups))
