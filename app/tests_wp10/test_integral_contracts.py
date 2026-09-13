from __future__ import annotations

import ast
import hashlib
import re
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "ops/tests/run_wp10_integral_qa.sh"
QA_DOC = ROOT / "docs/operations/wp10_integral_qa.md"
MATRIX_DOC = ROOT / "docs/operations/wp10_operational_matrix.md"
FIXTURE_DOC = ROOT / "docs/security/wp10_synthetic_fixtures.md"

CANONICAL_SUITES = (
    "tests_wp01_wp02",
    "tests_wp03_wp04",
    "tests_wp05_wp06_wp07",
    "tests_wp08",
    "tests_wp09",
    "tests_wp10",
)

PRE_WP10_TEST_MODULES = (
    "tests_wp01_wp02/test_contracts.py",
    "tests_wp03_wp04/test_contracts.py",
    "tests_wp03_wp04/test_migrations.py",
    "tests_wp05_wp06_wp07/test_contracts.py",
    "tests_wp05_wp06_wp07/test_migrations.py",
    "tests_wp08/test_contracts.py",
    "tests_wp09/test_contracts.py",
    "tests_wp09/test_migrations.py",
)

ALLOWED_PHONE_SHA256 = {
    "cb24629d1dbeb6ee24e7c20610896274e8102e67aa6efc2f3a1be2893c38008b",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _test_method_count(paths: tuple[str, ...]) -> int:
    total = 0
    for relative in paths:
        tree = ast.parse(_read(ROOT / relative))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                total += sum(
                    isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and child.name.startswith("test_")
                    for child in node.body
                )
    return total


class WP10IntegralContracts(SimpleTestCase):
    def test_01_required_artifacts_exist(self):
        for path in (RUNNER, QA_DOC, MATRIX_DOC, FIXTURE_DOC):
            self.assertTrue(path.is_file(), path)

    def test_02_pre_wp10_static_test_count_is_113(self):
        self.assertEqual(_test_method_count(PRE_WP10_TEST_MODULES), 113)

    def test_03_runner_executes_all_canonical_suites(self):
        source = _read(RUNNER)
        for suite in CANONICAL_SUITES:
            self.assertIn(suite, source)
        self.assertIn("count != 125", source)
        self.assertIn('SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"', source)
        self.assertIn('git_as_atlas -C "$REPO_ROOT"', source)
        self.assertIn('if [[ "$(id -u)" -ne 0 ]]', source)
        self.assertIn('docker logs -f "$name" 2>&1 | tee "$output"', source)

    def test_04_runner_uses_exact_wp09_runtime_and_ephemeral_postgresql_16(self):
        source = _read(RUNNER)
        self.assertIn('APP_IMAGE="sha256:84b1e780987ff848b8f63f119d272cac6da95bd3edff511dff2894cc666032bf"', source)
        self.assertIn('POSTGRES_IMAGE="postgres:16"', source)
        self.assertIn("--tmpfs /var/lib/postgresql/data", source)
        self.assertIn("django.db.backends.postgresql", source)
        self.assertIn('docker run -d --pull never', source)
        self.assertIn('docker create --pull never', source)
        self.assertIn('"$APP_IMAGE" "$@"', source)

    def test_05_runner_publishes_no_ports_and_uses_internal_network(self):
        source = _read(RUNNER)
        self.assertIn("docker network create --internal", source)
        self.assertIn('--network "$NETWORK"', source)
        self.assertNotRegex(source, r"(^|\s)(-p|--publish)(\s|=)")
        self.assertIn("POSTGRES_PUBLISHED_PORTS=0", source)
        self.assertIn("APP_PUBLISHED_PORTS=0", source)
        self.assertEqual(
            source.count('port_bindings = host.get("PortBindings") or {}'),
            2,
        )
        self.assertEqual(
            source.count(
                'runtime_ports = payload.get("NetworkSettings", {}).get("Ports") or {}'
            ),
            2,
        )
        self.assertEqual(
            source.count("published_ports = bool(port_bindings) or any("),
            2,
        )
        self.assertEqual(
            source.count(
                "bool(bindings) for bindings in runtime_ports.values()"
            ),
            2,
        )
        self.assertNotIn(
            'host.get("PortBindings") or '
            '(payload.get("NetworkSettings", {}).get("Ports") or {})',
            source,
        )

    def test_06_runner_has_mandatory_cleanup_and_receipt(self):
        source = _read(RUNNER)
        self.assertIn("trap cleanup EXIT", source)
        self.assertIn("trap on_int INT", source)
        self.assertIn("trap on_term TERM", source)
        self.assertIn('docker rm -f "$name"', source)
        self.assertIn('docker rm -f "$PG_CONTAINER"', source)
        self.assertIn('docker network rm "$NETWORK"', source)
        self.assertIn('STATUS="FAILED_CLEANUP"', source)
        self.assertIn("final_rc=90", source)
        self.assertIn("final_rc=91", source)
        self.assertIn("EVIDENCE_STATUS", source)
        self.assertIn("WP10_INTEGRAL_QA_RECEIPT.json", source)
        self.assertIn('path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\\n")', source)
        self.assertIn("SHA256SUMS", source)

    def test_07_runner_performs_required_django_gates(self):
        source = _read(RUNNER)
        for command in (
            "manage.py check",
            "makemigrations --check --dry-run",
            "manage.py migrate --noinput",
            "manage.py test",
        ):
            self.assertIn(command, source)

    def test_08_runner_forces_local_test_backends(self):
        source = _read(RUNNER)
        for contract in (
            "locmem.EmailBackend",
            'CELERY_BROKER_URL = "memory://"',
            "LocMemCache",
            'SENTRY_DSN = ""',
        ):
            self.assertIn(contract, source)

    def test_09_runner_contains_no_promotion_or_environment_access(self):
        source = _read(RUNNER)
        lower = source.lower()
        forbidden = (
            "git push",
            "git merge",
            "git rebase",
            "git cherry-pick",
            "docker pull ",
            "docker build",
            "staging.",
            "production.",
            "icgs_hub",
            "src=/opt/sooi",
        )
        for marker in forbidden:
            self.assertNotIn(marker, lower)
        for unsafe_permission_change in (
            "usermod",
            "groupadd",
            "chgrp docker",
            "chmod 666 /var/run/docker.sock",
            "chmod 777 /var/run/docker.sock",
        ):
            self.assertNotIn(unsafe_permission_change, lower)
        self.assertIn(
            '--mount "type=bind,src=$REPO_ROOT,dst=/opt/sooi,readonly"',
            source,
        )
        self.assertIn("--read-only", source)
        self.assertIn('--user "${ATLAS_UID}:${ATLAS_GID}"', source)
        self.assertIn('"application_image_role": "dependencies_only"', source)

    def test_10_operational_matrix_covers_all_required_domains(self):
        text = _read(MATRIX_DOC).lower()
        required = (
            "seguridad",
            "roles",
            "trial",
            "búsquedas",
            "runs",
            "captación",
            "inbox",
            "conversión",
            "oportunidades",
            "seguimiento",
            "demo",
            "bienvenida",
            "métricas",
            "rollback",
            "smoke",
        )
        for item in required:
            self.assertIn(item, text)

    def test_11_fixture_literals_are_reserved_or_allowlisted(self):
        email_rx = re.compile(
            r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.I
        )
        url_rx = re.compile(r"https?://([^/\s\"'<>]+)", re.I)
        phone_rx = re.compile(r"(?<!\d)(?:\+34[\s.-]?)?[6789]\d{8}(?!\d)")

        for relative in PRE_WP10_TEST_MODULES:
            text = _read(ROOT / relative)
            for domain in email_rx.findall(text):
                self.assertTrue(
                    domain.lower().endswith((".test", ".invalid")),
                    (relative, domain),
                )
            for host in url_rx.findall(text):
                self.assertTrue(
                    host.lower().endswith((".test", ".invalid")),
                    (relative, host),
                )
            for phone in phone_rx.findall(text):
                digest = hashlib.sha256(phone.encode()).hexdigest()
                self.assertIn(digest, ALLOWED_PHONE_SHA256, relative)

    def test_12_documents_define_scope_abort_and_no_real_data(self):
        combined = "\n".join(
            _read(path).lower() for path in (QA_DOC, MATRIX_DOC, FIXTURE_DOC)
        )
        for phrase in (
            "datos reales",
            "criterios de aborto",
            "sin staging",
            "sin producción",
            "sin promoción",
            "postgresql 16",
            "125 pruebas",
        ):
            self.assertIn(phrase, combined)
