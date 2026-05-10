#!/usr/bin/env bash
set -euo pipefail

cd /opt/sooi/infra

mkdir -p /opt/sooi/logs

echo ""
echo "============================================================"
echo "SOOI automated searches - $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

docker compose exec -T web sh -lc '
cd /app && \
python manage.py run_automated_searches --execute --limit 6
'
