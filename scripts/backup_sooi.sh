#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="/opt/sooi"
INFRA_DIR="$PROJECT_ROOT/infra"
BACKUP_ROOT="/opt/sooi_backups"
TS="$(date +%Y%m%d_%H%M%S)"
DEST="$BACKUP_ROOT/$TS"

mkdir -p "$DEST"

echo "== SOOI backup iniciado: $TS =="

echo "1) Backup PostgreSQL..."
cd "$INFRA_DIR"
docker compose exec -T db sh -lc 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$DEST/sooi_db_$TS.sql"

echo "2) Backup media/uploads..."
docker compose exec -T web sh -lc 'tar -czf - /vol/media 2>/dev/null || true' > "$DEST/sooi_media_$TS.tar.gz"

echo "3) Backup configuración sensible env..."
if [ -d "$INFRA_DIR/env" ]; then
  tar -czf "$DEST/sooi_env_$TS.tar.gz" -C "$INFRA_DIR" env
fi

echo "4) Registrar commit actual..."
cd "$PROJECT_ROOT"
git log --oneline -1 > "$DEST/git_commit_$TS.txt"
git status --short > "$DEST/git_status_$TS.txt"

echo "5) Limpiar backups antiguos +30 días..."
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +30 -exec rm -rf {} \;

echo "== Backup completado =="
echo "$DEST"
