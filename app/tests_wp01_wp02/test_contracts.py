import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SECURE_ENV = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": "synthetic-test-secret-with-at-least-fifty-characters-123456789",
    "DJANGO_ALLOWED_HOSTS": "test.example.invalid",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://test.example.invalid",
    "DB_PASSWORD": "synthetic-database-password",
    "EMAIL_HOST": "smtp.example.invalid",
    "EMAIL_PORT": "587",
    "EMAIL_HOST_USER": "synthetic-user",
    "EMAIL_HOST_PASSWORD": "synthetic-password",
    "EMAIL_USE_TLS": "true",
    "EMAIL_USE_SSL": "false",
    "EMAIL_TIMEOUT": "10",
    "DEFAULT_FROM_EMAIL": "SOOI <no-reply@example.invalid>",
}


def import_production(extra=None):
    env = os.environ.copy()
    for key in SECURE_ENV:
        env.pop(key, None)
    env.update(extra or {})
    return subprocess.run(
        [sys.executable, "-c", "import config.settings.prod"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@unittest.skipUnless(
    importlib.util.find_spec("django") and importlib.util.find_spec("celery"),
    "Django/Celery are supplied by BASE_IMAGE",
)
class ProductionSettingsTests(unittest.TestCase):
    def test_missing_secret_fails_closed(self):
        result = import_production({
            "DJANGO_ALLOWED_HOSTS": "test.example.invalid",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "https://test.example.invalid",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_wildcard_host_fails_closed(self):
        env = dict(SECURE_ENV, DJANGO_ALLOWED_HOSTS="*")
        result = import_production(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit host names", result.stderr)

    def test_http_origin_fails_closed(self):
        env = dict(SECURE_ENV, DJANGO_CSRF_TRUSTED_ORIGINS="http://test.example.invalid")
        result = import_production(env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTPS origins", result.stderr)

    def test_secure_synthetic_configuration_loads(self):
        result = import_production(SECURE_ENV)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_effective_security_values(self):
        script = (
            "from django.conf import settings; "
            "assert settings.DEBUG is False; "
            "assert settings.SESSION_COOKIE_SECURE; assert settings.CSRF_COOKIE_SECURE; "
            "assert settings.SECURE_SSL_REDIRECT; assert settings.SECURE_HSTS_SECONDS == 31536000"
        )
        env = os.environ.copy()
        env.update(SECURE_ENV)
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, check=False)
        self.assertEqual(result.returncode, 0)


class ReleaseContractTests(unittest.TestCase):
    def test_required_artifacts_exist(self):
        required = [
            ".env.release.example",
            "docs/security/production_configuration.md",
            "ops/release/Dockerfile",
            "ops/release/compose.candidate.yml",
            "ops/release/rollback_runbook.md",
            "ops/release/release_manifest.schema.json",
            "ops/release/build_candidate.sh",
            "ops/release/verify_candidate.sh",
            "ops/release/verify_reproducible.sh",
            "ops/release/run_wp01_wp02_tests.sh",
        ]
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_one_image_contract_and_no_source_bind_mount(self):
        compose = (ROOT / "ops/release/compose.candidate.yml").read_text()
        self.assertEqual(compose.count("image: *candidate-image"), 3)
        self.assertNotIn("volumes:", compose)
        for service in ("web:", "worker:", "beat:"):
            self.assertIn(service, compose)

    def test_build_is_offline_and_no_pull(self):
        build = (ROOT / "ops/release/build_candidate.sh").read_text()
        self.assertIn("--network=none", build)
        self.assertIn("--pull=false", build)
        self.assertNotIn("pip install", build)

    def test_manifest_schema_is_valid_json(self):
        schema = json.loads((ROOT / "ops/release/release_manifest.schema.json").read_text())
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)


if __name__ == "__main__":
    unittest.main()
