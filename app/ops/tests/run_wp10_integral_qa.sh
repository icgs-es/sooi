#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

BASE_COMMIT="dd0dfa834a34a54cecac3b025975130a02f5088e"
APP_IMAGE="sha256:84b1e780987ff848b8f63f119d272cac6da95bd3edff511dff2894cc666032bf"
POSTGRES_IMAGE="postgres:16"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ATLAS_USER="atlas-agent"
ATLAS_UID="$(id -u "$ATLAS_USER")"
ATLAS_GID="$(id -g "$ATLAS_USER")"
ATLAS_GROUP="$(id -gn "$ATLAS_USER")"
REPO_ROOT="$(
  sudo -u "$ATLAS_USER" -H \
    git -C "$SCRIPT_DIR/../.." rev-parse --show-toplevel
)"
BACKUP_ROOT="/home/atlas-agent/atlas_backups"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="${BACKUP_ROOT}/sooi_wp10_integral_qa_v2_${TS}"
TMP_ROOT="$(mktemp -d /tmp/sooi_wp10_integral_qa_v2.XXXXXX)"
SETTINGS_DIR="$TMP_ROOT/settings"
NETWORK="sooi-wp10-net-${TS,,}"
PG_CONTAINER="sooi-wp10-pg-${TS,,}"
LOG="$RUN_ROOT/WP10_INTEGRAL_QA.log"
STATUS="FAILED"
CLEANUP_STATUS="NOT_RUN"
EVIDENCE_STATUS="NOT_RUN"
APP_CONTAINERS=()

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: WP-10 QA debe iniciarse con sudo/root." >&2
  exit 2
fi

install -d -o "$ATLAS_USER" -g "$ATLAS_GROUP" -m 0700 "$RUN_ROOT"
install -d -m 0755 "$SETTINGS_DIR"

exec > >(tee -a "$LOG") 2>&1

git_as_atlas() {
  sudo -u "$ATLAS_USER" -H git "$@"
}

write_receipt() {
  local final_rc="$1"
  python3 - "$RUN_ROOT/WP10_INTEGRAL_QA_RECEIPT.json" \
    "$STATUS" "$CLEANUP_STATUS" "$EVIDENCE_STATUS" "$final_rc" \
    "$REPO_ROOT" "$BASE_COMMIT" "$APP_IMAGE" "$POSTGRES_IMAGE" \
    "$NETWORK" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "operation": "SOOI_WP10_INTEGRAL_QA_V2",
    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    "status": sys.argv[2],
    "cleanup_status": sys.argv[3],
    "evidence_status": sys.argv[4],
    "exit_code": int(sys.argv[5]),
    "repository": sys.argv[6],
    "required_base_commit": sys.argv[7],
    "application_runtime_image": sys.argv[8],
    "postgres_image": sys.argv[9],
    "ephemeral_internal_network": sys.argv[10],
    "runtime_contract": {
        "application_image_role": "dependencies_only",
        "candidate_source_mount": "/opt/sooi",
        "candidate_source_read_only": True,
        "application_rootfs_read_only": True,
        "application_uid_gid": "atlas-agent",
        "docker_pull": False,
        "docker_build": False,
        "published_ports": 0,
    },
    "effects": {
        "repository_writes": False,
        "main_modified": False,
        "hub_modified": False,
        "staging_accessed": False,
        "production_accessed": False,
        "real_data_accessed": False,
        "system_groups_modified": False,
        "docker_socket_permissions_modified": False,
    },
}
path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
PY
}

on_int() {
  STATUS="FAILED_SIGNAL_INT"
  exit 130
}

on_term() {
  STATUS="FAILED_SIGNAL_TERM"
  exit 143
}

cleanup() {
  local original_rc=$?
  local final_rc="$original_rc"
  local cleanup_rc=0
  local evidence_rc=0
  local bundle="${RUN_ROOT}.tar.gz"
  local name

  trap - EXIT INT TERM
  set +e

  for name in "${APP_CONTAINERS[@]}"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      docker rm -f "$name" >/dev/null 2>&1 || cleanup_rc=1
    fi
  done

  if docker container inspect "$PG_CONTAINER" >/dev/null 2>&1; then
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || cleanup_rc=1
  fi

  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network rm "$NETWORK" >/dev/null 2>&1 || cleanup_rc=1
  fi

  rm -rf "$TMP_ROOT" || cleanup_rc=1

  for name in "${APP_CONTAINERS[@]}"; do
    if docker container inspect "$name" >/dev/null 2>&1; then
      cleanup_rc=1
    fi
  done
  if docker container inspect "$PG_CONTAINER" >/dev/null 2>&1; then
    cleanup_rc=1
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    cleanup_rc=1
  fi
  if [[ -e "$TMP_ROOT" ]]; then
    cleanup_rc=1
  fi

  if ((cleanup_rc != 0)); then
    CLEANUP_STATUS="FAIL"
    STATUS="FAILED_CLEANUP"
    final_rc=90
  else
    CLEANUP_STATUS="PASS"
  fi

  if ((final_rc == 0)) && [[ "$STATUS" != "PASS" ]]; then
    STATUS="FAILED_INTERNAL_STATUS"
    final_rc=1
  fi

  EVIDENCE_STATUS="PASS"
  write_receipt "$final_rc" || evidence_rc=1

  if ((evidence_rc == 0)); then
    (
      cd "$RUN_ROOT"
      find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
        | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
    ) || evidence_rc=1
  fi

  if ((evidence_rc == 0)); then
    rm -f "$bundle" "${bundle}.sha256"
    tar -C "$(dirname "$RUN_ROOT")" -czf "$bundle" "$(basename "$RUN_ROOT")" \
      || evidence_rc=1
  fi

  if ((evidence_rc == 0)); then
    sha256sum "$bundle" > "${bundle}.sha256" || evidence_rc=1
  fi

  if ((evidence_rc == 0)); then
    chown -R "$ATLAS_USER:$ATLAS_GROUP" "$RUN_ROOT" "$bundle" "${bundle}.sha256" \
      || evidence_rc=1
  fi

  if ((evidence_rc != 0)); then
    EVIDENCE_STATUS="FAIL"
    if [[ "$STATUS" == "FAILED_CLEANUP" ]]; then
      STATUS="FAILED_CLEANUP_AND_EVIDENCE"
    else
      STATUS="FAILED_EVIDENCE"
    fi
    final_rc=91

    write_receipt "$final_rc" || true
    (
      cd "$RUN_ROOT"
      find . -maxdepth 1 -type f ! -name SHA256SUMS -print0 \
        | LC_ALL=C sort -z | xargs -0 sha256sum > SHA256SUMS
    ) || true
    rm -f "$bundle" "${bundle}.sha256"
    tar -C "$(dirname "$RUN_ROOT")" -czf "$bundle" "$(basename "$RUN_ROOT")" \
      || true
    [[ -f "$bundle" ]] && sha256sum "$bundle" > "${bundle}.sha256" || true
    chown -R "$ATLAS_USER:$ATLAS_GROUP" "$RUN_ROOT" "$bundle" "${bundle}.sha256" \
      2>/dev/null || true
  fi

  echo "RUN_ROOT=$RUN_ROOT"
  echo "BUNDLE=$bundle"
  echo "BUNDLE_SHA256_FILE=${bundle}.sha256"
  echo "CLEANUP_STATUS=$CLEANUP_STATUS"
  echo "EVIDENCE_STATUS=$EVIDENCE_STATUS"
  echo "FINAL_STATUS=$STATUS"
  exit "$final_rc"
}

trap cleanup EXIT
trap on_int INT
trap on_term TERM

echo "======================================================================"
echo "SOOI NEXT SAFE VERSION V1 · WP-10 QA INTEGRAL V2"
echo "UTC=$TS"
echo "ORCHESTRATOR_USER=$(id -un)"
echo "APPLICATION_RUNTIME_IMAGE=$APP_IMAGE"
echo "APPLICATION_IMAGE_ROLE=DEPENDENCIES_ONLY"
echo "GIT_OWNER=$ATLAS_USER"
echo "REPO_ROOT=$REPO_ROOT"
echo "BASE_COMMIT=$BASE_COMMIT"
echo "======================================================================"

git_as_atlas -C "$REPO_ROOT" cat-file -e "${BASE_COMMIT}^{commit}"
git_as_atlas -C "$REPO_ROOT" merge-base --is-ancestor "$BASE_COMMIT" HEAD
git_as_atlas -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=all \
  > "$RUN_ROOT/git_status_before.txt"

ALLOWED_PATHS=(
  "docs/operations/wp10_integral_qa.md"
  "docs/operations/wp10_operational_matrix.md"
  "docs/security/wp10_synthetic_fixtures.md"
  "ops/tests/run_wp10_integral_qa.sh"
  "tests_wp10/__init__.py"
  "tests_wp10/test_integral_contracts.py"
)

mapfile -t DIRTY_PATHS < <(
  sed -E 's/^.. //' "$RUN_ROOT/git_status_before.txt" | LC_ALL=C sort
)
expected="$(printf '%s\n' "${ALLOWED_PATHS[@]}" | LC_ALL=C sort)"
actual="$(printf '%s\n' "${DIRTY_PATHS[@]}")"
[[ "$actual" == "$expected" ]] || {
  echo "ERROR: hay cambios ajenos a WP-10." >&2
  printf '%s\n' "${DIRTY_PATHS[@]}" >&2
  exit 3
}

docker image inspect "$APP_IMAGE" > "$RUN_ROOT/app_runtime_image_inspect.json"
docker image inspect "$POSTGRES_IMAGE" > "$RUN_ROOT/postgres_image_inspect.json"

ACTUAL_APP_IMAGE="$(
  docker image inspect --format '{{.Id}}' "$APP_IMAGE"
)"
[[ "$ACTUAL_APP_IMAGE" == "$APP_IMAGE" ]] || {
  echo "ERROR: identidad inesperada de imagen de aplicación." >&2
  exit 4
}

cat > "$SETTINGS_DIR/wp10_runtime_settings.py" <<'PY'
import os

from config.settings.test import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["PGDATABASE"],
        "USER": os.environ["PGUSER"],
        "PASSWORD": os.environ["PGPASSWORD"],
        "HOST": os.environ["PGHOST"],
        "PORT": os.environ.get("PGPORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {},
        "TEST": {"NAME": "test_sooi_wp10_integral"},
    }
}

TIME_ZONE = "Europe/Madrid"
USE_TZ = True
ALLOWED_HOSTS = ["*"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "sooi-wp10-integral-qa",
    }
}
SENTRY_DSN = ""
SOOI_WP09_AGGREGATION_ENABLED = False
PY
chmod 0644 "$SETTINGS_DIR/wp10_runtime_settings.py"

docker network create --internal --driver bridge "$NETWORK" \
  > "$RUN_ROOT/network_id.txt"
docker network inspect "$NETWORK" > "$RUN_ROOT/network_inspect.json"

python3 - "$RUN_ROOT/network_inspect.json" "$NETWORK" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
network = payload[0]
if network.get("Name") != sys.argv[2]:
    raise SystemExit("Nombre de red inesperado")
if network.get("Internal") is not True:
    raise SystemExit("La red Docker no es interna")
print("DOCKER_NETWORK_INTERNAL=YES")
PY

docker run -d --pull never \
  --name "$PG_CONTAINER" \
  --network "$NETWORK" \
  --tmpfs /var/lib/postgresql/data:rw,nosuid,nodev,noexec,size=768m \
  --tmpfs /var/run/postgresql:rw,nosuid,nodev,size=16m \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  -e POSTGRES_DB=sooi_wp10 \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust \
  "$POSTGRES_IMAGE" > "$RUN_ROOT/postgres_container_id.txt"

for _ in $(seq 1 60); do
  if docker exec "$PG_CONTAINER" pg_isready -U postgres -d sooi_wp10 >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker exec "$PG_CONTAINER" pg_isready -U postgres -d sooi_wp10
docker inspect "$PG_CONTAINER" > "$RUN_ROOT/postgres_container_inspect.json"

python3 - "$RUN_ROOT/postgres_container_inspect.json" "$NETWORK" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))[0]
host = payload["HostConfig"]
if host.get("NetworkMode") != sys.argv[2]:
    raise SystemExit("PostgreSQL no está en la red efímera esperada")
port_bindings = host.get("PortBindings") or {}
runtime_ports = payload.get("NetworkSettings", {}).get("Ports") or {}
published_ports = bool(port_bindings) or any(
    bool(bindings) for bindings in runtime_ports.values()
)
if published_ports:
    raise SystemExit("PostgreSQL publicó puertos")
mounts = host.get("Tmpfs") or {}
if "/var/lib/postgresql/data" not in mounts:
    raise SystemExit("PostgreSQL no usa almacenamiento tmpfs")
print("POSTGRES_INTERNAL_NETWORK=YES")
print("POSTGRES_PUBLISHED_PORTS=0")
print("POSTGRES_DATA_TMPFS=YES")
PY

run_app_step() {
  local step="$1"
  local output="$2"
  shift 2
  local name="sooi-wp10-app-${step}-${TS,,}"
  local inspect_file="$RUN_ROOT/app_${step}_inspect.json"
  local id_file="$RUN_ROOT/app_${step}_container_id.txt"
  local rc

  APP_CONTAINERS+=("$name")

  docker create --pull never \
    --name "$name" \
    --network "$NETWORK" \
    --read-only \
    --user "${ATLAS_UID}:${ATLAS_GID}" \
    --workdir /opt/sooi \
    --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 \
    --mount "type=bind,src=$REPO_ROOT,dst=/opt/sooi,readonly" \
    --mount "type=bind,src=$SETTINGS_DIR,dst=/wp10-settings,readonly" \
    -e DJANGO_SETTINGS_MODULE=wp10_runtime_settings \
    -e PYTHONPATH=/wp10-settings:/opt/sooi \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONPYCACHEPREFIX=/tmp/pycache \
    -e HOME=/tmp \
    -e XDG_CACHE_HOME=/tmp/.cache \
    -e TMPDIR=/tmp \
    -e PGDATABASE=sooi_wp10 \
    -e PGUSER=postgres \
    -e PGPASSWORD= \
    -e PGHOST="$PG_CONTAINER" \
    -e PGPORT=5432 \
    -e HTTP_PROXY=http://127.0.0.1:9 \
    -e HTTPS_PROXY=http://127.0.0.1:9 \
    -e ALL_PROXY=socks5://127.0.0.1:9 \
    -e NO_PROXY=localhost,127.0.0.1,"$PG_CONTAINER" \
    "$APP_IMAGE" "$@" > "$id_file"

  docker inspect "$name" > "$inspect_file"

  python3 - "$inspect_file" "$APP_IMAGE" "$NETWORK" "$REPO_ROOT" \
    "$ATLAS_UID:$ATLAS_GID" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))[0]
expected_image, expected_network, expected_source, expected_user = sys.argv[2:]
host = payload["HostConfig"]
config = payload["Config"]

if payload.get("Image") != expected_image:
    raise SystemExit("La aplicación no usa la imagen exacta de WP-09")
if host.get("ReadonlyRootfs") is not True:
    raise SystemExit("El rootfs de aplicación no es read-only")
if config.get("User") != expected_user:
    raise SystemExit("UID/GID de aplicación inesperado")
if host.get("NetworkMode") != expected_network:
    raise SystemExit("La aplicación no está en la red interna efímera")
port_bindings = host.get("PortBindings") or {}
runtime_ports = payload.get("NetworkSettings", {}).get("Ports") or {}
published_ports = bool(port_bindings) or any(
    bool(bindings) for bindings in runtime_ports.values()
)
if published_ports:
    raise SystemExit("La aplicación publicó puertos")

mounts = {
    item.get("Destination"): item
    for item in payload.get("Mounts") or []
}
source = mounts.get("/opt/sooi")
settings = mounts.get("/wp10-settings")
if not source:
    raise SystemExit("Falta montaje del candidato")
if source.get("Source") != expected_source or source.get("RW") is not False:
    raise SystemExit("El candidato no está montado read-only desde el worktree")
if not settings or settings.get("RW") is not False:
    raise SystemExit("Los settings no están montados read-only")

print("APP_IMAGE_EXACT=YES")
print("APP_CANDIDATE_MOUNT_READ_ONLY=YES")
print("APP_ROOTFS_READ_ONLY=YES")
print("APP_PUBLISHED_PORTS=0")
PY

  docker start "$name" >/dev/null
  docker logs -f "$name" 2>&1 | tee "$output"
  rc="$(docker wait "$name")"
  if [[ "$rc" != "0" ]]; then
    echo "ERROR: paso $step terminó con código $rc." >&2
    return "$rc"
  fi
  docker rm "$name" >/dev/null
}

run_app_step check "$RUN_ROOT/manage_check.txt" \
  python3 manage.py check

run_app_step migration_check "$RUN_ROOT/makemigrations_check.txt" \
  python3 manage.py makemigrations --check --dry-run

run_app_step migrate "$RUN_ROOT/migrate_full.txt" \
  python3 manage.py migrate --noinput

run_app_step tests "$RUN_ROOT/tests_full.txt" \
  python3 manage.py test \
    tests_wp01_wp02 \
    tests_wp03_wp04 \
    tests_wp05_wp06_wp07 \
    tests_wp08 \
    tests_wp09 \
    tests_wp10 \
    --verbosity 2

python3 - "$RUN_ROOT/tests_full.txt" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
match = re.search(r"Ran\s+(\d+)\s+tests?", text)
if not match:
    raise SystemExit("No se pudo determinar el total de pruebas")
count = int(match.group(1))
if count != 125:
    raise SystemExit(f"Total inesperado: {count}; se esperaban 125")
if not re.search(r"^OK(?:\s+\([^)]*\))?\s*$", text, re.MULTILINE):
    raise SystemExit("La suite no terminó en OK")
print(f"TOTAL_TESTS={count}")
print("TEST_RESULT=PASS")
PY

git_as_atlas -C "$REPO_ROOT" status --porcelain=v1 --untracked-files=all \
  > "$RUN_ROOT/git_status_after.txt"
cmp "$RUN_ROOT/git_status_before.txt" "$RUN_ROOT/git_status_after.txt"
echo "REPOSITORY_STATE_UNCHANGED=YES"

STATUS="PASS"
