import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')
from .runtime import *
LOCAL_DEV = os.environ.get('RELAY_LOCAL_DEV') == '1'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('Set a random DJANGO_SECRET_KEY of at least 50 characters.')
DEBUG = LOCAL_DEV
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', ('localhost,127.0.0.1,10.0.2.2' if LOCAL_DEV else 'localhost,127.0.0.1')).split(',')
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions',
                  'django.contrib.staticfiles', 'rest_framework', 'knox', 'relay']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'relay.middleware.PrivateResponsesMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
AUTH_USER_MODEL = 'relay.User'
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ.get('PGDATABASE', 'relay'),
    'USER': os.environ.get('PGUSER', 'relay'), 'PASSWORD': os.environ.get('PGPASSWORD', ''),
    'HOST': os.environ.get('PGHOST', '127.0.0.1'), 'PORT': os.environ.get('PGPORT', '55432'),
    # Thread-per-request development servers must not retain idle connections.
    'CONN_MAX_AGE': PGCONN_MAX_AGE, 'CONN_HEALTH_CHECKS': True,
    'OPTIONS': {'connect_timeout': PGCONNECT_TIMEOUT, 'sslmode': os.environ.get('PGSSLMODE', 'disable' if LOCAL_DEV else 'verify-full'),
                'options': f'-c statement_timeout={PGSTATEMENT_TIMEOUT_MS} -c lock_timeout={PGLOCK_TIMEOUT_MS}'},
}}
if os.environ.get('PGSSLROOTCERT'):
    DATABASES['default']['OPTIONS']['sslrootcert'] = os.environ['PGSSLROOTCERT']
PASSWORD_HASHERS = ['django.contrib.auth.hashers.Argon2PasswordHasher',
                    'django.contrib.auth.hashers.PBKDF2PasswordHasher']
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 12}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
SESSION_COOKIE_NAME = 'relay_session'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = boolean('SESSION_COOKIE_SECURE', not LOCAL_DEV)
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_SAVE_EVERY_REQUEST = False
CSRF_COOKIE_NAME = 'relay_csrf'
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = boolean('CSRF_COOKIE_SECURE', not LOCAL_DEV)
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_FAILURE_VIEW = 'relay.auth_views.csrf_failure'
CSRF_TRUSTED_ORIGINS = [s for s in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if s]
if LOCAL_DEV:
    CSRF_TRUSTED_ORIGINS += ['http://127.0.0.1:5174', 'http://localhost:5174']
SECURE_SSL_REDIRECT = boolean('SECURE_SSL_REDIRECT', not LOCAL_DEV)
SECURE_HSTS_SECONDS = int(os.environ.get('HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get('HSTS_SUBDOMAINS') == '1'
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
TRUST_PROXY = os.environ.get('TRUST_PROXY') == '1'
if TRUST_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication', 'knox.auth.TokenAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination', 'PAGE_SIZE': 50,
    'EXCEPTION_HANDLER': 'relay.errors.api_exception_handler',
}
DATA_UPLOAD_MAX_MEMORY_SIZE = IMPORT_MAX_BYTES + 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = IMPORT_MAX_BYTES
DATA_UPLOAD_MAX_NUMBER_FILES = 1
DATA_UPLOAD_MAX_NUMBER_FIELDS = 10
TIME_ZONE = 'UTC'
USE_TZ = True
LANGUAGE_CODE = 'en-us'
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_SOFT_TIME_LIMIT = 100
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_IGNORE_RESULT = True
CELERY_BEAT_SCHEDULE = {'expire-staged-imports': {'task': 'relay.tasks.cleanup', 'schedule': 3600.0}}
# No raw files are retained in phase 1. Future export storage is private S3.
if os.environ.get('AWS_STORAGE_BUCKET_NAME'):
    STORAGES = {'default': {'BACKEND': 'storages.backends.s3.S3Storage', 'OPTIONS': {
        'bucket_name': os.environ['AWS_STORAGE_BUCKET_NAME'], 'default_acl': None,
        'querystring_auth': True, 'querystring_expire': 60, 'file_overwrite': False,
        'object_parameters': {'ServerSideEncryption': 'aws:kms'},
    }}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}

LOGGING = {'version': 1, 'disable_existing_loggers': False,
           'handlers': {'console': {'class': 'logging.StreamHandler'}},
           'loggers': {'relay.audit': {'handlers': ['console'], 'level': 'INFO', 'propagate': False}}}
