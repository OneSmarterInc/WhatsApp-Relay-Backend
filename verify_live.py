from pathlib import Path
import os
os.chdir(Path(__file__).resolve().parent)
"""Exercise the running Vite -> Django -> PostgreSQL flow with synthetic data."""
import http.cookiejar, urllib.request, urllib.error, json, secrets, uuid
base='http://127.0.0.1:5173/api/'
jar=http.cookiejar.CookieJar()
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
csrf=''
def call(path,method='GET',data=None,content_type='application/json',expected=200):
    global csrf
    headers={'X-CSRFToken':csrf,'Origin':'http://127.0.0.1:5173','Content-Type':content_type}
    body=json.dumps(data).encode() if data is not None and content_type=='application/json' else data
    try:
        r=client.open(urllib.request.Request(base+path,data=body,headers=headers,method=method))
    except urllib.error.HTTPError as e:r=e
    payload=r.read()
    assert r.status==expected,(path,r.status,payload[:300])
    result=json.loads(payload) if payload else {}
    if isinstance(result,dict) and 'csrfToken' in result:csrf=result['csrfToken']
    return result
call('auth/csrf/')
email='qa-'+uuid.uuid4().hex[:10]+'@example.test'; password=secrets.token_urlsafe(24)
call('auth/signup/','POST',{'name':'Relay QA','email':email,'password':password},expected=201)
call('auth/logout/','POST',{})
call('auth/csrf/')
call('auth/login/','POST',{'email':email,'password':password})
boundary=uuid.uuid4().hex
def preview():
    parts=[]
    for name,value in [('name','Website Launch — QA'),('date_order','DMY'),('timezone','Asia/Kolkata')]:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n')
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="sample.txt"\r\nContent-Type: text/plain\r\n\r\n20/11/2026, 10:10 - Rahul: Homepage ready hai.\n20/11/2026, 10:35 - Priya: Logo approval pending hai.\n20/11/2026, 11:20 - Amit: Friday launch karte hain.\r\n--{boundary}--\r\n')
    return call('imports/preview/','POST',''.join(parts).encode(),f'multipart/form-data; boundary={boundary}',201)
p=preview(); assert p['total']==3
r=call(f"imports/{p['id']}/confirm/",'POST',{}); assert r['inserted']==3
chat=r['chat_id']; assert len(call(f'chats/{chat}/messages/'))==3
p=preview(); r=call(f"imports/{p['id']}/confirm/",'POST',{'chat_id':chat}); assert r['inserted']==0 and r['duplicates']==3
call('auth/logout/','POST',{}); call('chats/',expected=403)
print('PASS: signup, logout, login, upload preview, confirm, persisted messages, duplicate reimport, anonymous denial; through Vite proxy and real PostgreSQL.')
