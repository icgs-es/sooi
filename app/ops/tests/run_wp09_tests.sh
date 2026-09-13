#!/usr/bin/env bash
set -euo pipefail

export DJANGO_SETTINGS_MODULE=config.settings.test
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests_wp09 -v 2
