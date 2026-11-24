from pathlib import Path
import os
os.chdir(Path(__file__).resolve().parent)
"""Live answer-language and fact preservation evaluation, synthetic sources only."""
import os,sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django;django.setup()
from relay.ai import generate
from relay.localization import language_of,language_ok,fact_signature
cases=[
 ('ट्रेन कब चलेगी?','The train departs on Monday at 6 AM.','When does the train leave?'),
 ('mera refund kitna hai aur kab milega?','Your refund of ₹1,250.50 arrives on Tuesday, 24/11/2026.','How much is my refund and when will it arrive?'),
 ('भुगतान कितना है और अंतिम तारीख क्या है?','Payment of ₹2,500 is due on Friday, 27/11/2026.','How much is the payment and what is its due date?'),
 ('demo kis din rakha hai?','Team demo is scheduled on Friday at 3 PM.','Which day is the demo scheduled?'),
 ('When does the train leave?','The train departs on Monday at 6 AM.','When does the train leave?'),
 ('डिज़ाइन की स्थिति क्या है?','The website designs are ready.','What is the status of the designs?'),
 ('designs ka status batao','The website designs are ready.','What is the status of the designs?'),
 ('मेरा पासपोर्ट नंबर क्या है?','The train departs on Monday at 6 AM.','What is my passport number?'),
]
report=[]
for q,text,en in cases:
 start=time.monotonic();record={'id':'synthetic','sender':'Aditi','chat__name':'Synthetic evaluation','timestamp':'2026-11-21','text':text}
 answer=generate(q,[record],en)
 language=language_of(q)
 no_evidence='पासपोर्ट' in q
 row={'question':q,'language':language,'answer':answer['answer'],'mode':answer.get('answer_mode'),'language_pass':language_ok(answer['answer'],language),'sources_unchanged':all(r['text']==text for r in answer['results']),'grounded':answer['grounded'],'seconds':round(time.monotonic()-start,1)}
 if not no_evidence:
  import re
  clean=re.sub(r'\[\d+\]','',answer['answer'])
  row['facts_pass']=fact_signature(clean)==fact_signature(text)
  if 'design' in en.lower():row['meaning_pass']=('ready' in clean.lower() if language=='hinglish' else 'तैयार' in clean)
 else:row['abstention_pass']=not answer['grounded']
 report.append(row);print(json.dumps(row,ensure_ascii=True),flush=True)
Path('.local/language-evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
if not all(r['language_pass'] and r['sources_unchanged'] and r.get('facts_pass',True) and r.get('abstention_pass',True) and r.get('meaning_pass',True) for r in report):raise SystemExit('Language evaluation needs review.')
