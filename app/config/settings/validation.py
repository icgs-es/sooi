"""Fail-closed validation used only by production settings."""

from django.core.exceptions import ImproperlyConfigured


def production_value(env, name):
    value = env(name, default="").strip()
    if not value:
        raise ImproperlyConfigured(f"Production requires non-empty {name}.")
    return value


def production_list(env, name):
    values = [value.strip() for value in env.list(name, default=[]) if value.strip()]
    if not values:
        raise ImproperlyConfigured(
            f"Production requires {name} as a non-empty comma-separated list."
        )
    return values


def validate_production(settings):
    errors = []
    secret_key = settings["SECRET_KEY"]
    if len(secret_key) < 50 or secret_key in {
        "change-me",
        "development-only-not-for-production",
    }:
        errors.append("DJANGO_SECRET_KEY must be unique and at least 50 characters.")

    hosts = settings["ALLOWED_HOSTS"]
    if any(host == "*" or "://" in host or "/" in host for host in hosts):
        errors.append("DJANGO_ALLOWED_HOSTS must contain explicit host names only.")

    origins = settings["CSRF_TRUSTED_ORIGINS"]
    if any(not origin.startswith("https://") for origin in origins):
        errors.append("DJANGO_CSRF_TRUSTED_ORIGINS must contain HTTPS origins only.")

    hsts_seconds = settings["SECURE_HSTS_SECONDS"]
    if hsts_seconds < 0:
        errors.append("DJANGO_SECURE_HSTS_SECONDS cannot be negative.")

    if errors:
        raise ImproperlyConfigured("Invalid production configuration: " + " ".join(errors))
