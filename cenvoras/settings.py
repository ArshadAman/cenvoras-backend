from pathlib import Path
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'django-insecure-local-dev-fallback')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'False').lower() in ('1', 'true', 'yes', 'on')

raw_allowed_hosts = os.environ.get('DJANGO_ALLOWED_HOSTS', '')
if raw_allowed_hosts.strip():
    ALLOWED_HOSTS = [host.strip() for host in raw_allowed_hosts.split(',') if host.strip()]
else:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1'] if DEBUG else []


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party apps
    'rest_framework',
    'corsheaders',
    'drf_yasg',

    # Project apps
    'users',
    'inventory',
    'billing',
    'ledger',
    'subscription',
    'analytics',
    'ai_assistant',
    'integration',
    'reports',
    'audit_log',
    'cloudinary',
    'cloudinary_storage',
    'dbbackup',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',  # Added for CORS
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'subscription.middleware.SubscriptionAccessMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'audit_log.middleware.AuditMiddleware',
]

# OWASP Security Hardening
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_HTTPONLY = True

ROOT_URLCONF = 'cenvoras.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'cenvoras.wsgi.application'


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'cenvoras_db'),
        'USER': os.environ.get('POSTGRES_USER', 'cenvoras_user'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'cenvoras_password'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),  # 'localhost' for local dev, 'db' for docker
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'ATOMIC_REQUESTS': True,
        'CONN_MAX_AGE': int(os.environ.get('CONN_MAX_AGE', 120)),  # Preserve and reuse TCP connections for 2 minutes
        'CONN_HEALTH_CHECKS': True,
        'OPTIONS': {
            'connect_timeout': int(os.environ.get('POSTGRES_CONNECT_TIMEOUT', 10)),
            'sslmode': os.environ.get('POSTGRES_SSLMODE', 'prefer'),
        },
    }
}

# Redis Caching
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/1'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
}

# Celery Configuration
CELERY_BROKER_URL = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'



# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_DIRS = [
    BASE_DIR / 'static',
]

# Media files (user uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework configuration (basic, can be extended)
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.OrderingFilter',
        'rest_framework.filters.SearchFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'cenvoras.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 15,
}

# CORS configuration (allow all for development, restrict in production)
CORS_ALLOW_ALL_ORIGINS = os.environ.get('CORS_ALLOW_ALL_ORIGINS', 'False').lower() in ('1', 'true', 'yes', 'on')
raw_cors_allowed = os.environ.get('CORS_ALLOWED_ORIGINS', 'https://cenvora.app,https://www.cenvora.app,https://dev.cenvora.app,https://devapi.cenvora.app,https://api.cenvora.app')
cors_origins = [origin.strip() for origin in raw_cors_allowed.split(',') if origin.strip()]
for required_origin in ('https://cenvora.app', 'https://www.cenvora.app'):
    if required_origin not in cors_origins:
        cors_origins.append(required_origin)
CORS_ALLOWED_ORIGINS = cors_origins

raw_csrf_trusted = os.environ.get('CSRF_TRUSTED_ORIGINS', 'https://cenvora.app,https://www.cenvora.app,https://dev.cenvora.app,https://devapi.cenvora.app,https://api.cenvora.app')
csrf_trusted_origins = [origin.strip() for origin in raw_csrf_trusted.split(',') if origin.strip()]
for required_origin in ('https://cenvora.app', 'https://www.cenvora.app'):
    if required_origin not in csrf_trusted_origins:
        csrf_trusted_origins.append(required_origin)
CSRF_TRUSTED_ORIGINS = csrf_trusted_origins

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', 31536000))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# Custom user model (if you implement one)
AUTH_USER_MODEL = 'users.User'

# =============================================================================
# INTEGRATION SETTINGS
# =============================================================================

# Transactional Email Service
# Set these in .env / environment
TRANSACTIONAL_EMAIL_API_KEY = os.environ.get('TRANSACTIONAL_EMAIL_API_KEY', '') 
TRANSACTIONAL_EMAIL_SENDER_EMAIL = os.environ.get('TRANSACTIONAL_EMAIL_SENDER_EMAIL', 'noreply@cenvora.app')
TRANSACTIONAL_EMAIL_SENDER_NAME = os.environ.get('TRANSACTIONAL_EMAIL_SENDER_NAME', 'Cenvora')
TRANSACTIONAL_EMAIL_API_URL = os.environ.get('TRANSACTIONAL_EMAIL_API_URL', '')
TRANSACTIONAL_EMAIL_SEND_ENDPOINT = os.environ.get('TRANSACTIONAL_EMAIL_SEND_ENDPOINT', '/email/send')
TRANSACTIONAL_EMAIL_TIMEOUT_SECONDS = int(os.environ.get('TRANSACTIONAL_EMAIL_TIMEOUT_SECONDS', 20))

# WhatsApp Business API — Coming Soon
# Set these when the WhatsApp integration is launched
WHATSAPP_API_TOKEN = os.environ.get('WHATSAPP_API_TOKEN', '')
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID', '')

# Gemini AI (load from environment variables)
GEMINI_API_KEY = os.environ.get('Gemini_Key', '')

# Cashfree Payments
CASHFREE_CLIENT_ID = os.environ.get('CASHFREE_CLIENT_ID', '')
CASHFREE_CLIENT_SECRET = os.environ.get('CASHFREE_CLIENT_SECRET', '')
CASHFREE_ENV = os.environ.get('CASHFREE_ENV', 'sandbox')  # sandbox | production
CASHFREE_API_VERSION = os.environ.get('CASHFREE_API_VERSION', '2023-08-01')
CASHFREE_RETURN_URL = os.environ.get('CASHFREE_RETURN_URL', 'https://cenvora.app/profile')
CASHFREE_WEBHOOK_URL = os.environ.get('CASHFREE_WEBHOOK_URL', 'https://api.cenvora.app/api/subscription/webhooks/cashfree/')
# Cashfree SDK webhook verification typically uses PG client secret.
# Keep webhook secret override for flexibility, but fallback to client secret by default.
CASHFREE_WEBHOOK_SECRET = os.environ.get('CASHFREE_WEBHOOK_SECRET', CASHFREE_CLIENT_SECRET)
CASHFREE_WEBHOOK_MAX_SKEW_MS = int(os.environ.get('CASHFREE_WEBHOOK_MAX_SKEW_MS', 600000))
CASHFREE_PAYMENT_ORDER_REUSE_WINDOW_SECONDS = int(os.environ.get('CASHFREE_PAYMENT_ORDER_REUSE_WINDOW_SECONDS', 1800))
CASHFREE_REQUIRE_WEBHOOK_SIGNATURE = (
    os.environ.get('CASHFREE_REQUIRE_WEBHOOK_SIGNATURE', 'true').strip().lower() == 'true'
)
CASHFREE_ALLOW_UNSIGNED_WEBHOOKS = (
    os.environ.get('CASHFREE_ALLOW_UNSIGNED_WEBHOOKS', 'false').strip().lower() == 'true'
)

# =============================================================================
# BACKUP & STORAGE SETTINGS (Cloudinary)
# =============================================================================

# Cloudinary requires these environment variables (or CLOUDINARY_URL)
CLOUDINARY_STORAGE = {
    'CLOUD_NAME': os.environ.get('CLOUDINARY_CLOUD_NAME', ''),
    'API_KEY': os.environ.get('CLOUDINARY_API_KEY', ''),
    'API_SECRET': os.environ.get('CLOUDINARY_API_SECRET', ''),
}

# django-dbbackup storage configuration
# DBBACKUP_STORAGE = 'cloudinary_storage.storage.RawMediaCloudinaryStorage'
DBBACKUP_CLEANUP_KEEP = 7
DBBACKUP_EXTENSION = 'backup'  # Cloudinary blocks .bin, 'backup' is safer for Raw uploads

# Resilient backup scheduler configuration
BACKUP_CLOUDINARY_FOLDER = os.environ.get('BACKUP_CLOUDINARY_FOLDER', 'cenvoras/db_backups')
BACKUP_SCHEDULE_MINUTE = int(os.environ.get('BACKUP_SCHEDULE_MINUTE', 15))
BACKUP_MAX_ATTEMPTS = int(os.environ.get('BACKUP_MAX_ATTEMPTS', 3))
BACKUP_CIRCUIT_OPEN_SECONDS = int(os.environ.get('BACKUP_CIRCUIT_OPEN_SECONDS', 21600))
BACKUP_ALERT_EMAIL = os.environ.get('BACKUP_ALERT_EMAIL', 'cenvoras@gmail.com')
