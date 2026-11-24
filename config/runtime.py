"""Validated runtime configuration. Loaded after backend/.env by Django settings."""
import hashlib
import json
import math
import os
from urllib.parse import urlsplit
from django.core.exceptions import ImproperlyConfigured


def integer(name, default, minimum=1, maximum=1000000):
    try:value=int(os.environ.get(name,str(default)))
    except ValueError:raise ImproperlyConfigured(f'{name} must be an integer.') from None
    if not minimum<=value<=maximum:
        raise ImproperlyConfigured(f'{name} must be between {minimum} and {maximum}.')
    return value


def boolean(name, default):
    value=os.environ.get(name,'1' if default else '0').lower()
    if value not in ('1','0','true','false'):raise ImproperlyConfigured(f'{name} must be 1 or 0.')
    return value in ('1','true')


def text(name, default):
    value=os.environ.get(name,default).strip()
    if not value or len(value)>300 or any(ord(c)<32 for c in value):
        raise ImproperlyConfigured(f'{name} must be a nonempty single-line value.')
    return value


def fingerprint(*values):
    return hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest()[:16]


STRING_DEFAULTS={
    'OLLAMA_BASE_URL':'http://127.0.0.1:11434',
    'AI_ANSWER_MODEL':'qwen3:1.7b', 'AI_TOPIC_MODEL':'qwen3:1.7b',
    'AI_TASK_MODEL':'qwen3:1.7b', 'AI_TRANSLATION_MODEL':'qwen3:1.7b',
    'AI_EMBEDDING_MODEL':'embeddinggemma:300m-qat-q4_0', 'AI_KEEP_ALIVE':'5m',
}
INT_DEFAULTS={
    'AI_REQUEST_TIMEOUT_SECONDS':(120,5,600), 'AI_CONTEXT_TOKENS':(8192,2048,131072),
    'AI_ANSWER_MAX_TOKENS':(1000,128,8192), 'AI_TOPIC_MAX_TOKENS':(1200,128,8192),
    'AI_TASK_MAX_TOKENS':(1200,128,8192), 'AI_TRANSLATION_MAX_TOKENS':(1200,180,8192),
    'AI_TOPIC_BATCH_SIZE':(6,1,20), 'AI_TASK_BATCH_SIZE':(4,1,20),
    'SEARCH_EMBEDDING_DIMENSIONS':(768,1,8192), 'SEARCH_CHUNK_SIZE':(800,128,4000),
    'SEARCH_CHUNK_MAX_BYTES':(1200,128,12000), 'SEARCH_CHUNK_OVERLAP':(120,0,2000),
    'SEARCH_RETRIEVAL_COUNT':(8,4,16), 'SEARCH_MAX_SCAN_CHUNKS':(25000,100,100000),
    'SEARCH_INDEX_BATCH_SIZE':(12,1,100),
    'WORKER_BUSY_POLL_SECONDS':(1,1,60), 'WORKER_IDLE_POLL_SECONDS':(3,1,60),
    'WORKER_HEARTBEAT_SECONDS':(15,1,60), 'WORKER_STALE_SECONDS':(60,5,600),
    'WORKER_LEASE_SECONDS':(600,30,3600),
    'PGCONNECT_TIMEOUT':(10,1,120), 'PGCONN_MAX_AGE':(0,0,600),
    'PGSTATEMENT_TIMEOUT_MS':(15000,1000,300000), 'PGLOCK_TIMEOUT_MS':(5000,100,60000),
    'SESSION_COOKIE_AGE':(43200,300,2592000),
    'IMPORT_MAX_BYTES':(2097152,1024,52428800),
    'IMPORT_MAX_MESSAGES':(10000,1,100000), 'IMPORT_MAX_MESSAGE_LENGTH':(20000,100,100000),
    'IMPORT_MAX_PENDING':(5,1,100), 'USER_MAX_MESSAGES':(100000,1,1000000),
    'IMPORT_TTL_HOURS':(24,1,168),
}
for _name,_default in STRING_DEFAULTS.items():globals()[_name]=text(_name,_default)
for _name,(_default,_low,_high) in INT_DEFAULTS.items():globals()[_name]=integer(_name,_default,_low,_high)
try:AI_TEMPERATURE=float(os.environ.get('AI_TEMPERATURE','0'))
except ValueError:raise ImproperlyConfigured('AI_TEMPERATURE must be a number from 0 to 2.') from None
if not math.isfinite(AI_TEMPERATURE) or not 0<=AI_TEMPERATURE<=2:
    raise ImproperlyConfigured('AI_TEMPERATURE must be a number from 0 to 2.')
try:
    _url=urlsplit(OLLAMA_BASE_URL)
    _port=_url.port
except ValueError:raise ImproperlyConfigured('OLLAMA_BASE_URL is invalid.') from None
if _url.scheme not in ('http','https') or not _url.hostname or _url.username or _url.password or _url.query or _url.fragment or _url.path not in ('','/'):
    raise ImproperlyConfigured('OLLAMA_BASE_URL must be an HTTP(S) server origin without credentials, path, query or fragment.')
OLLAMA_BASE_URL=OLLAMA_BASE_URL.rstrip('/')
if SEARCH_CHUNK_OVERLAP>=min(SEARCH_CHUNK_SIZE,SEARCH_CHUNK_MAX_BYTES//4):
    raise ImproperlyConfigured('SEARCH_CHUNK_OVERLAP must be smaller than chunk size and one quarter of the byte limit.')
if WORKER_STALE_SECONDS<=2*WORKER_HEARTBEAT_SECONDS or WORKER_LEASE_SECONDS<=WORKER_STALE_SECONDS:
    raise ImproperlyConfigured('Worker lease must exceed stale timeout; stale timeout must exceed twice the heartbeat interval.')
