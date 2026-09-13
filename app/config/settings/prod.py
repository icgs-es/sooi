from .base import *

import math
from email.utils import parseaddr

from django.core.exceptions import ImproperlyConfigured

from .validation import production_list, production_value, validate_production

DEBUG = False

SECRET_KEY = production_value(env, "DJANGO_SECRET_KEY")
ALLOWED_HOSTS = production_list(env, "DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = production_list(env, "DJANGO_CSRF_TRUSTED_ORIGINS")
DATABASES["default"]["PASSWORD"] = production_value(env, "DB_PASSWORD")

SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"

SECURE_SSL_REDIRECT = env.bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = env.bool("DJANGO_USE_X_FORWARDED_HOST", default=True)
SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=True
)
SECURE_HSTS_PRELOAD = env.bool("DJANGO_SECURE_HSTS_PRELOAD", default=True)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

# TRANSACTIONAL_EMAIL_MUST_HAVE_BOUNDED_TRANSPORT.
# PRODUCTION_SMTP_MUST_FAIL_CLOSED.
if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend":
    smtp_errors = []
    if not EMAIL_HOST.strip():
        smtp_errors.append("EMAIL_HOST must be non-empty for production SMTP.")
    if EMAIL_PORT <= 0:
        smtp_errors.append("EMAIL_PORT must be positive for production SMTP.")
    if not EMAIL_HOST_USER.strip():
        smtp_errors.append("EMAIL_HOST_USER must be non-empty for production SMTP.")
    if not EMAIL_HOST_PASSWORD.strip():
        smtp_errors.append("EMAIL_HOST_PASSWORD must be non-empty for production SMTP.")
    if not math.isfinite(EMAIL_TIMEOUT) or EMAIL_TIMEOUT <= 0:
        smtp_errors.append("EMAIL_TIMEOUT must be a positive finite number.")
    if EMAIL_USE_TLS == EMAIL_USE_SSL:
        smtp_errors.append("Production SMTP requires exactly one of EMAIL_USE_TLS or EMAIL_USE_SSL.")

    explicit_from = env("DEFAULT_FROM_EMAIL", default="").strip()
    _display_name, from_address = parseaddr(explicit_from)
    if not explicit_from or not from_address or "@" not in from_address:
        smtp_errors.append("DEFAULT_FROM_EMAIL must be an explicit usable email identity.")
    elif from_address.lower().endswith(("@sooi.local", ".local")):
        smtp_errors.append("DEFAULT_FROM_EMAIL cannot use a local-only domain in production.")

    if smtp_errors:
        raise ImproperlyConfigured("Invalid production SMTP configuration: " + " ".join(smtp_errors))

validate_production(globals())
