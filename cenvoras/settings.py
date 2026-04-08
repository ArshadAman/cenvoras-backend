from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-^b68wd86pdzoj4goxo_o--!6w4tygg0sgzl)r7mb4m_^j-n0x0'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']


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

import os

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('POSTGRES_DB', 'cenvoras_db'),
        'USER': os.environ.get('POSTGRES_USER', 'cenvoras_user'),
        'PASSWORD': os.environ.get('POSTGRES_PASSWORD', 'cenvoras_password'),
        'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),  # 'localhost' for local dev, 'db' for docker
        'PORT': os.environ.get('POSTGRES_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('CONN_MAX_AGE', 120)),  # Preserve and reuse TCP connections for 2 minutes
        'CONN_HEALTH_CHECKS': True,
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
CORS_ALLOW_ALL_ORIGINS = True
CSRF_TRUSTED_ORIGINS = ["https://devapi.cenvora.app"]

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

# WhatsApp Business API — Coming Soon
# Set these when the WhatsApp integration is launched
WHATSAPP_API_TOKEN = os.environ.get('WHATSAPP_API_TOKEN', '')
WHATSAPP_PHONE_ID = os.environ.get('WHATSAPP_PHONE_ID', '')

# Gemini AI (load from environment variables)
GEMINI_API_KEY = os.environ.get('Gemini_Key', '')

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
DBBACKUP_STORAGE = 'cloudinary_storage.storage.RawMediaCloudinaryStorage'
DBBACKUP_CLEANUP_KEEP = 7
DBBACKUP_EXTENSION = 'backup'  # Cloudinary blocks .bin, 'backup' is safer for Raw uploads
