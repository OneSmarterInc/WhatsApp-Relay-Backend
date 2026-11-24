"""Live local-model/PostgreSQL check; synthetic account always removed."""
from pathlib import Path
import os, sys, json, uuid
os.chdir(Path(__file__).resolve().parent)
sys.path.insert(0, str(Path.cwd()))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.test import Client
from django.utils import timezone
from relay.models import User, Chat, Message
from relay.topic_discovery import topic_id
u = User.objects.create_user('discovery-'+uuid.uuid4().hex+'@example.test', 'Temporary-check-123!')
try:
    chats = [Chat.objects.create(owner=u, name=n) for n in ['Product team', 'Client updates']]
    cases = [('MySelfPay', 'MySelfPay (MSP) payment dashboard refund button needs a fix.', 0),
             ('MySelfPay', 'The refund button fix is ready for review.', 0),
             ('MySelfPay', 'MSP payment review ready', 1),
             ('MySelfPay', 'My Self Pay dashboard changes ready', 1),
             ('MySelfPay', 'MySelfPay mein refund ₹500 ka 25/11/2026 ko karna hai.', 1),
             ('Campus Scraper', 'Campus Scraper deployment is ready for review.', 0),
             ('Campus Scraper', 'Campus Scraper का परीक्षण पूरा हो गया है।', 1),
             ('Goa trip', 'Goa trip hotel booking confirmed for 28/11/2026.', 0),
             (None, 'yes sir', 1),
             (None, 'batch-5c16b300f0.xlsx', 1)]
    expected = {}
    for i, (name, text, chat) in enumerate(cases):
        m = Message.objects.create(chat=chats[chat], text=text, sender='Demo', timestamp=timezone.now(), fingerprint=str(i), source_line=i+1)
        expected[str(m.pk)] = name
    client = Client(HTTP_HOST='localhost'); client.force_login(u)
    for _ in range(30):
        r = client.post('/api/topics/prepare/', {}, content_type='application/json'); d=r.json()
        print(r.status_code, d.get('status'), flush=True)
        assert r.status_code in (200,202), d
        if d['status']=='ready': break
    else: raise AssertionError('Discovery not ready')
    report=[]
    for card in d['topics']:
        rows=client.get('/api/topics/'+card['id']+'/messages/').json()['results']
        assert all(expected[m['id']] == card['name'] or (expected[m['id']] == 'Goa trip' and card['name'] in ('Goa trip', 'Goa trip hotel booking')) for m in rows), card
        assert all(s['id'] in {m['id'] for m in rows} for s in card['summary']['results'])
        report.append({'name':card['name'],'messages':len(rows),'chats':card['chat_count'],'summary':card['summary']})
        print(card['name'],len(rows),flush=True)
    assert 2 <= len(d['topics']) <= 3
    assert {topic_id('MySelfPay'), topic_id('Campus Scraper')} <= {t['id'] for t in d['topics']}
    travel_found = any(t['name'] in ('Goa trip', 'Goa trip hotel booking') for t in d['topics'])
    assert d['ungrouped_count'] == (2 if travel_found else 3), d
    print('Optional travel topic discovered:', travel_found, flush=True)
    assert all(t['message_count']==(5 if t['name']=='MySelfPay' else 2) and t['chat_count']==2 for t in d['topics'] if t['kind']=='project')
    Path('.local/topic-discovery-evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: two named projects and contextual followup across two chats; greetings and attachments ungrouped.',flush=True)
finally:
    u.delete()
