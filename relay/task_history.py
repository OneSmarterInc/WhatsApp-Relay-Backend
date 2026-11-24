"""Conservative, source-derived task histories; no mutation of original evidence."""
import re
from .topic_discovery import subject_words


def subject(quote, owner):
    text=quote.casefold()
    if owner:text=text.replace(owner.casefold(),' ')
    text=re.sub(r'\b(?:by|before|due on|due by)\b.*$', '', text)
    words=subject_words(text)
    return words-{'will','would','could','should','must','need','please','fix','fixed','complete','completed','finish','finished','resolved','pending','done','tomorrow','today','yesterday','now','already','still','not','was','has','been','have','the','is','are','am','can','my','our','your'}


def action(quote):
    for name,pattern in [('fix',r'\b(fix|fixed|fixing|resolve|resolved)\b'),('review',r'\b(review|reviewed|reviewing)\b'),('test',r'\b(test|tested|testing)\b'),('deploy',r'\b(deploy|deployed|deployment)\b')]:
        if re.search(pattern,quote,re.I):return name
    return None


def reconcile(items, memberships):
    """Merge only unique same-subject, same-project candidates; ambiguous updates remain separate."""
    histories=[]
    for item in items:
        item=dict(item)
        mid=item['source']['id']
        projects=frozenset(memberships.get(mid,()))
        words=subject(item['quote'],item.get('owner'))
        verb=action(item['quote'])
        matches=[]
        if item['kind'] in ('task','commitment') and len(words)>=2 and len(projects)==1:
            for old in histories:
                if old['kind'] not in ('task','commitment') or old['_projects']!=projects:continue
                if item.get('owner') and old.get('owner') and item['owner'].casefold()!=old['owner'].casefold():continue
                if verb and old['_action'] and verb!=old['_action']:continue
                # Exact normalized subject avoids merging similarly named but distinct actions.
                if words==old['_subject']:matches.append(old)
        evidence={'quote':item['quote'],'status':item['status'],'owner':item.get('owner'),'deadline':item.get('deadline'),'source':item['source']}
        if len(matches)==1:
            old=matches[0]
            old['history'].append(evidence)
            if item['status']!='unknown':old['status']=item['status']
            for field in ('owner','deadline'):
                if item.get(field):old[field]=item[field]
            old['completion_confirmed']=old['status']=='completed'
        else:
            histories.append(dict(item,history=[evidence],_subject=words,_projects=projects,_action=verb))
    return [{k:v for k,v in item.items() if not k.startswith('_')} for item in histories]
