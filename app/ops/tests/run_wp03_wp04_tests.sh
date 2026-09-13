#!/usr/bin/env bash
set -eu

cd "$(dirname "$0")/../.."
export DJANGO_SETTINGS_MODULE=config.settings.test
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/sooi_wp03_wp04_pycache"

python3 manage.py test tests_wp03_wp04 --verbosity 2
python3 -m py_compile \
  apps/core/observability.py \
  apps/inbox/admin.py \
  apps/inbox/views.py \
  apps/inbox/services.py \
  apps/inbox/secrets.py \
  apps/inbox/models.py \
  apps/inbox/forms.py \
  apps/inbox/management/commands/sync_inbox_email.py \
  apps/inmuebles/views.py \
  apps/busquedas/tasks.py \
  config/settings/test.py \
  apps/inbox/migrations/0002_emailaccount_imap_secret_ref.py \
  apps/inbox/migrations/0003_remove_plaintext_imap_password.py \
  tests_wp03_wp04/test_contracts.py \
  tests_wp03_wp04/test_migrations.py
