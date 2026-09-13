import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
SMTP_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
BASE_PRODUCTION_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": "synthetic-test-secret-with-at-least-fifty-characters-123456789",
    "DJANGO_ALLOWED_HOSTS": "test.example.invalid",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://test.example.invalid",
    "DB_PASSWORD": "synthetic-database-password",
    "EMAIL_BACKEND": SMTP_BACKEND,
    "EMAIL_HOST": "smtp.example.invalid",
    "EMAIL_PORT": "587",
    "EMAIL_HOST_USER": "synthetic-user",
    "EMAIL_HOST_PASSWORD": "synthetic-password",
    "EMAIL_USE_TLS": "true",
    "EMAIL_USE_SSL": "false",
    "DEFAULT_FROM_EMAIL": "SOOI <no-reply@example.invalid>",
}
EMAIL_KEYS = {
    "EMAIL_BACKEND", "EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER",
    "EMAIL_HOST_PASSWORD", "EMAIL_USE_TLS", "EMAIL_USE_SSL",
    "EMAIL_TIMEOUT", "DEFAULT_FROM_EMAIL",
}


def isolated_settings_import(settings_module, overrides=None, remove=()):
    environment = os.environ.copy()
    for key in EMAIL_KEYS | set(BASE_PRODUCTION_ENV):
        environment.pop(key, None)
    environment.update(BASE_PRODUCTION_ENV if settings_module.endswith("prod") else {})
    environment.update(overrides or {})
    for key in remove:
        environment.pop(key, None)
    environment["DJANGO_SETTINGS_MODULE"] = settings_module
    return subprocess.run(
        [sys.executable, "-c", "from django.conf import settings; settings.EMAIL_BACKEND"],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


@unittest.skipUnless(importlib.util.find_spec("django"), "Django is supplied by BASE_IMAGE")
class SMTPReadinessSR022B3A1Tests(unittest.TestCase):
    def assert_production_rejected(self, overrides=None, remove=(), message=""):
        result = isolated_settings_import("config.settings.prod", overrides, remove)
        self.assertNotEqual(result.returncode, 0)
        if message:
            self.assertIn(message, result.stderr)

    def test_default_timeout_is_bounded(self):
        result = isolated_settings_import("config.settings.prod")
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = dict(BASE_PRODUCTION_ENV)
        environment.pop("EMAIL_TIMEOUT", None)
        environment["DJANGO_SETTINGS_MODULE"] = "config.settings.prod"
        result = subprocess.run(
            [sys.executable, "-c", "from django.conf import settings; assert settings.EMAIL_TIMEOUT == 10.0"],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_valid_timeout_override_is_accepted(self):
        result = isolated_settings_import("config.settings.prod", {"EMAIL_TIMEOUT": "15"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_timeouts_are_rejected(self):
        for value in ("0", "-1", "invalid"):
            with self.subTest(value=value):
                self.assert_production_rejected({"EMAIL_TIMEOUT": value})

    def test_tls_and_ssl_conflict_is_rejected(self):
        self.assert_production_rejected({"EMAIL_USE_TLS": "true", "EMAIL_USE_SSL": "true"}, message="exactly one")

    def test_unencrypted_smtp_is_rejected(self):
        self.assert_production_rejected({"EMAIL_USE_TLS": "false", "EMAIL_USE_SSL": "false"}, message="exactly one")

    def test_missing_smtp_host_is_rejected(self):
        self.assert_production_rejected({"EMAIL_HOST": ""}, message="EMAIL_HOST")

    def test_missing_smtp_auth_is_rejected(self):
        for key in ("EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD"):
            with self.subTest(key=key):
                self.assert_production_rejected({key: ""}, message=key)

    def test_sender_identity_contract(self):
        for value in ("", "no-reply@sooi.local"):
            with self.subTest(value=value):
                self.assert_production_rejected({"DEFAULT_FROM_EMAIL": value}, message="DEFAULT_FROM_EMAIL")
        result = isolated_settings_import(
            "config.settings.prod", {"DEFAULT_FROM_EMAIL": "SOOI <no-reply@sooi.io>"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_test_settings_keep_non_delivery_backend(self):
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
        result = subprocess.run(
            [sys.executable, "-c", "from django.conf import settings; assert settings.EMAIL_BACKEND == 'django.core.mail.backends.locmem.EmailBackend'"],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_contract_is_shared_and_secret_safe(self):
        compose = (ROOT / "ops/release/compose.candidate.yml").read_text()
        example = (ROOT / ".env.release.example").read_text()
        gitignore = (ROOT / ".gitignore").read_text()
        self.assertEqual(compose.count("<<: *candidate-env"), 3)
        self.assertIn("${ENV_FILE:-../../.env.release}", compose)
        self.assertIn("EMAIL_TIMEOUT=10", example)
        self.assertIn("EMAIL_HOST_PASSWORD=\n", example)
        self.assertIn(".env.release", gitignore)
        self.assertIn("!.env.release.example", gitignore)


if __name__ == "__main__":
    unittest.main()
