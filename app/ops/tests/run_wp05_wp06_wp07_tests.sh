#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")/../.."
export DJANGO_SETTINGS_MODULE=config.settings.test
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/sooi_wp05_wp06_wp07_pycache"

python3 manage.py test tests_wp05_wp06_wp07 --verbosity 2
python3 manage.py makemigrations --check --dry-run
python3 -m py_compile \
  apps/core/plans.py apps/core/models.py apps/core/notifications.py apps/core/views.py \
  apps/core/migrations/0008_wp05_wp06_entitlements_delivery.py \
  tests_wp05_wp06_wp07/test_contracts.py tests_wp05_wp06_wp07/test_migrations.py
