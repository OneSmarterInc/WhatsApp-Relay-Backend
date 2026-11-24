"""Read-only real-chat grouping audit. No saved assignments or changes to originals."""
import os, sys, json, argparse
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django
django.setup()
from relay.models import Chat, SemanticDocument
from relay.topic_discovery import discover, project_catalog, topic_id, BATCH_SIZE
from relay.ai import ModelFailure
parser=argparse.ArgumentParser();parser.add_argument('--chat-name',required=True);args=parser.parse_args()
chat=Chat.objects.get(name=args.chat_name)
messages=list(chat.messages.order_by('timestamp','source_line'))
groups=project_catalog(messages)
docs={doc.message_id:doc for doc in SemanticDocument.objects.filter(message__chat=chat).select_related('message')}
assert len(docs)==len(messages),'Index messages before this read-only audit'
remaining=[docs[m.pk] for m in messages];report=[]
while remaining:
    batch=remaining[:BATCH_SIZE]
    try: assignments=discover(batch,groups)
    except ModelFailure:
        try:assignments=discover(batch[:1],groups)
        except ModelFailure:assignments=[(batch[0],[])]
    assert assignments
    for doc,labels in assignments:
        report.append({'line':doc.message.source_line,'labels':[{'name':l['name'],'kind':l['kind']} for l in labels]})
        for label in labels:
            group=groups.setdefault(label['topic'],{'name':label['name'],'kind':label['kind'],'parts':[]})
            group['parts']=(group['parts']+doc.chunks)[:8]
    remaining=remaining[len(assignments):]
    print('Remaining:',len(remaining),flush=True)
counts={}
for row in report:
    for label in row['labels']:counts[label['name']]=counts.get(label['name'],0)+1
result={'counts':counts,'ungrouped':sum(not r['labels'] for r in report),'assignments':report}
(root/'.local'/'grouping-real-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'counts':counts,'ungrouped':result['ungrouped']},ensure_ascii=True),flush=True)
