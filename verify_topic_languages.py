from pathlib import Path
import os
os.chdir(Path(__file__).resolve().parent)
import os,sys,json
sys.path.insert(0,str(Path(__file__).resolve().parent));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django;django.setup()
from types import SimpleNamespace
from relay.ai import generate
from relay.topic_language import topic_language,localize_topic
cases=[('hi','डिज़ाइन तैयार हैं।'),('hinglish','Designs ready hain.'),('hi','₹2,500 का भुगतान 23/11/2026 को करना है।'),('hinglish','₹900 ka refund 25/11/2026 ko milega.')]
report=[]
for language,text in cases:
 records=[{'id':'demo-source','sender':'','text':text,'timestamp':'2026-11-21T10:00:00Z','chat_id':'demo-chat','chat__name':'','excerpt_start':0,'excerpt_end':len(text)}]
 detected=topic_language([SimpleNamespace(text=text,system=False)])
 assert detected==language
 q='Summarize these messages briefly. Preserve uncertainty and do not add facts.'
 result=localize_topic(generate(q,records,q),detected)
 assert result['answer_language']==language
 assert all(s['text']==text for s in result['results'])
 if any(c.isdecimal() for c in text):
  assert result['answer_mode']=='localized_facts',result
  for value in ('2,500','23/11/2026') if language=='hi' else ('900','25/11/2026'):assert value in result['answer']
 report.append(result)
 print(language,result['answer_mode'],flush=True)
open('.local/topic-language-evaluation.json','w',encoding='utf-8').write(json.dumps(report,ensure_ascii=False,indent=2))
