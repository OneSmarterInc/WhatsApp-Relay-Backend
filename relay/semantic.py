from django.conf import settings
"""Multilingual local embeddings, durable chunk cache and exact cosine retrieval."""
import hashlib
import heapq
import json
import math
import time
import re
import urllib.request
from django.db import transaction
from django.db.models import Exists, OuterRef
from .models import SemanticDocument, Message

EMBED_MODEL=settings.AI_EMBEDDING_MODEL
DIMENSIONS=settings.SEARCH_EMBEDDING_DIMENSIONS
CHUNK_SIZE=settings.SEARCH_CHUNK_SIZE
OVERLAP=settings.SEARCH_CHUNK_OVERLAP
MAX_SCAN_CHUNKS=settings.SEARCH_MAX_SCAN_CHUNKS

class SemanticError(Exception):
    pass

class IndexPending(Exception):
    def __init__(self, remaining):
        self.remaining=remaining

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def local_json(path,payload=None,limit=2_000_000):
    request=urllib.request.Request(settings.OLLAMA_BASE_URL+'/api/'+path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type':'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=settings.AI_REQUEST_TIMEOUT_SECONDS) as response:
            raw=response.read(limit+1)
        if len(raw)>limit:raise ValueError('Oversized response')
        return json.loads(raw)
    except (OSError,ValueError) as exc:
        raise SemanticError('Local AI service is unavailable. Start Ollama and check the configured models.') from exc

def model_version():
    try:
        models=local_json('tags')['models']
        digest=next(m['digest'] for m in models if m['name']==EMBED_MODEL)
        if not isinstance(digest,str) or len(digest)>80:raise ValueError()
        return 'embedding-v3:'+hashlib.sha256(json.dumps([EMBED_MODEL,digest,settings.OLLAMA_BASE_URL,DIMENSIONS,CHUNK_SIZE,OVERLAP,settings.SEARCH_CHUNK_MAX_BYTES]).encode()).hexdigest()
    except (KeyError,TypeError,ValueError,StopIteration) as exc:
        raise SemanticError(f'Install the configured embedding model with ollama pull {EMBED_MODEL}.') from exc

def normalize(vector):
    if not isinstance(vector,list) or len(vector)!=DIMENSIONS or any(type(v) not in (int,float) or not math.isfinite(v) for v in vector):
        raise SemanticError('Embedding model returned an invalid vector.')
    norm=math.sqrt(sum(v*v for v in vector))
    if not math.isfinite(norm) or norm<1e-12:raise SemanticError('Embedding vector is empty.')
    return [v/norm for v in vector]

def embed(texts):
    response=local_json('embed',{'model':EMBED_MODEL,'input':texts,'truncate':False,'keep_alive':settings.AI_KEEP_ALIVE})
    vectors=response.get('embeddings')
    if not isinstance(vectors,list) or len(vectors)!=len(texts):
        raise SemanticError('Embedding response is incomplete.')
    return [normalize(v) for v in vectors]

def chunks(text):
    # Bound both characters and UTF-8 bytes so Hindi/emoji inputs cannot silently truncate.
    parts=[];start=0
    while start<len(text):
        end=min(start+CHUNK_SIZE,len(text))
        while len(text[start:end].encode('utf-8'))>settings.SEARCH_CHUNK_MAX_BYTES:end-=1
        parts.append({'start':start,'end':end,'text':text[start:end]})
        if end==len(text):break
        start=max(start+1,end-OVERLAP)
    return parts or [{'start':0,'end':0,'text':''}]

def content_hash(message):
    return hashlib.sha256((message.sender+'\0'+message.text).encode()).hexdigest()

def index_pending(messages,version,batch=None):
    batch=batch or settings.SEARCH_INDEX_BATCH_SIZE
    # Imported messages are immutable; model/prompt/chunk changes invalidate this version.
    indexed=SemanticDocument.objects.filter(message_id=OuterRef('pk'),version=version)
    pending=messages.annotate(ready=Exists(indexed)).filter(ready=False).order_by('id')
    started=time.monotonic()
    for message in pending.select_related('chat')[:batch]:
        if time.monotonic()-started>40:break
        parts=chunks(message.text)
        vectors=[]
        for i in range(0,len(parts),8):
            vectors.extend(embed(['title: Chat message | text: '+message.sender+': '+p['text'] for p in parts[i:i+8]]))
        for part,vector in zip(parts,vectors):part['vector']=vector
        # Lock/recheck the message so a concurrent delete cannot resurrect its index.
        with transaction.atomic():
            if Message.objects.select_for_update().filter(pk=message.pk,chat__owner_id=message.chat.owner_id).exists():
                SemanticDocument.objects.update_or_create(message=message,defaults={'version':version,'content_hash':content_hash(message),'chunks':parts})
    return pending.count()

def retrieve(question,messages,translated=None):
    count=messages.count()
    if not count:return []
    if count>MAX_SCAN_CHUNKS:raise SemanticError('Select a smaller chat for local semantic search; this scope exceeds the exact-search limit.')
    version=model_version()
    remaining=index_pending(messages,version)
    if remaining:raise IndexPending(remaining)
    queries=list(dict.fromkeys([question,translated or question]))
    question_vectors=embed(['task: search result | query: '+q for q in queries])
    # Exact normalized cosine; no keyword prefilter, and no cross-user candidate pool.
    documents=SemanticDocument.objects.filter(message_id__in=messages.values('id'),version=version).select_related('message__chat')
    heap=[]; scanned=0; serial=0
    stop={'what','when','where','which','who','how','the','was','were','are','and','for','from','about','have','has','did','does','please','tell','show','said','message','messages','chat','chats'}
    terms={word for q in queries for word in re.findall(r'\w+',q.casefold()) if len(word)>2 and word not in stop}
    for document in documents.iterator(chunk_size=64):
        if document.content_hash!=content_hash(document.message):
            document.delete()
            raise IndexPending(1)
        for part in document.chunks:
            scanned+=1
            if scanned>MAX_SCAN_CHUNKS:raise SemanticError('This library is too large for local exact search. Select one chat and try again.')
            vector=part['vector']
            if len(vector)!=DIMENSIONS:raise SemanticError('Stored index is invalid. Rebuild the semantic index.')
            score=max(sum(a*b for a,b in zip(q,vector)) for q in question_vectors)
            # Exact names/details complement multilingual semantic recall; never prefilter candidates.
            words=set(re.findall(r'\w+',part['text'].casefold()))
            rank=score+(0.15*len(terms & words)/len(terms) if terms else 0)
            serial+=1
            m=document.message
            record={'id':m.id,'sender':m.sender,'text':part['text'],'timestamp':m.timestamp,'chat_id':m.chat_id,'chat__name':m.chat.name,'excerpt_start':part['start'],'excerpt_end':part['end'],'similarity':round(score,4)}
            entry=(rank,serial,record)
            if len(heap)<settings.SEARCH_RETRIEVAL_COUNT*3:heapq.heappush(heap,entry)
            elif rank>heap[0][0]:heapq.heapreplace(heap,entry)
    # Similarity is a ranking signal, not factual confidence; Qwen must abstain without evidence.
    ranked=sorted(heap,reverse=True)
    # Relative relevance band reduces unrelated context for the small answer model.
    cutoff=max(0.0,ranked[0][0]*0.75) if ranked else 0.0
    selected=[];per_message={}
    for score,_,record in ranked:
        key=record['id']
        if score<cutoff or per_message.get(key,0)>=2:continue
        selected.append(record);per_message[key]=per_message.get(key,0)+1
        if len(selected)==settings.SEARCH_RETRIEVAL_COUNT:break
    return selected


def with_context(records,messages):
    """Add at most two nearby source messages, strictly within the authorized query scope."""
    from datetime import timedelta
    from django.db.models import Q
    result=list(records[:4]);seen={str(r['id']) for r in result};added=0
    for record in records[:2]:
        if added>=2 or len(result)>=settings.SEARCH_RETRIEVAL_COUNT:break
        anchor=messages.filter(pk=record['id']).first()
        if anchor is None:continue
        for before in (True,False):
            if added>=2 or len(result)>=settings.SEARCH_RETRIEVAL_COUNT:break
            rows=messages.filter(chat_id=anchor.chat_id,system=False,timestamp__gte=anchor.timestamp-timedelta(minutes=5),timestamp__lte=anchor.timestamp+timedelta(minutes=5)).exclude(pk__in=seen)
            condition=Q(timestamp__lt=anchor.timestamp)|Q(timestamp=anchor.timestamp,source_line__lt=anchor.source_line) if before else Q(timestamp__gt=anchor.timestamp)|Q(timestamp=anchor.timestamp,source_line__gt=anchor.source_line)
            row=rows.filter(condition).select_related('chat').order_by('-timestamp' if before else 'timestamp','-source_line' if before else 'source_line').first()
            if row:
                text=row.text[:800]
                result.append({'id':row.pk,'sender':row.sender,'text':text,'timestamp':row.timestamp,'chat_id':row.chat_id,'chat__name':row.chat.name,'excerpt_start':0,'excerpt_end':len(text),'context_only':True})
                seen.add(str(row.pk))
                added+=1
    for record in records[4:]:
        if len(result)>=settings.SEARCH_RETRIEVAL_COUNT:break
        if str(record['id']) not in seen:
            result.append(record);seen.add(str(record['id']))
    return result
