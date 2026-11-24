"""Topic-language routing and conservative, source-linked localization."""
import re
from collections import Counter
from .localization import language_of, protected_translation, fact_summary, phrase
from .semantic import SemanticError


def topic_language(messages):
    # One vote per non-system message; ties favor English, then Hinglish.
    votes=Counter(language_of(m.text) for m in messages if not m.system and m.text.strip())
    return max(('en','hinglish','hi'),key=lambda lang:votes[lang])


def relative_facts(text):
    groups={'today':('today','aaj','आज'),'tomorrow':('tomorrow',),'yesterday':('yesterday',),
            'kal':('kal','कल'),'parso':('parso','परसों'),'tonight':('tonight',)}
    return Counter({key:sum(len(re.findall(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',text,re.I)) for alias in aliases) for key,aliases in groups.items()})


def localize_topic(result,language):
    result=dict(result,answer_language=language)
    if language=='en':return result
    sources=result['results']
    citations=' '.join('['+str(s['citation'])+']' for s in sources)
    text=re.sub(r'\[\d+\]','',result['answer']).strip()
    evidence=' '.join(s['text'] for s in sources)
    # Never resolve ambiguous kal/parso to an absolute or English relative date.
    ambiguous=bool(relative_facts(evidence)['kal'] or relative_facts(evidence)['parso'])
    numeric=any(c.isdecimal() for c in evidence)
    if result.get('answer_mode')!='source_excerpt' and not ambiguous and not numeric:
        try:
            translated=protected_translation(text,language)
            if relative_facts(translated)!=relative_facts(text):raise ValueError('Relative date changed')
            result.update(answer=translated+' '+citations,answer_mode='localized')
            return result
        except (ValueError,KeyError,TypeError,SemanticError):pass
    # Keep numeric details grouped by their individual source, never combine totals.
    details=[]
    for source in sources:
        facts=fact_summary([source['text']],language)
        if facts:details.append('['+str(source['citation'])+'] '+facts)
    excerpts='\n'.join('['+str(s['citation'])+'] '+s['text'] for s in sources if language_of(s['text'])==language)
    notice=phrase(language,'sources')+'\n'+excerpts if excerpts else phrase(language,'fallback')+' '+citations
    result.update(answer='\n\n'.join(details) if details else notice,
                  answer_mode='localized_facts' if details else 'source_excerpt')
    return result
