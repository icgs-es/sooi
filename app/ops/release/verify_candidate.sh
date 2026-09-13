#!/bin/sh
set -eu

: "${IMAGE_TAG:?IMAGE_TAG is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
: "${RELEASE_MANIFEST:?RELEASE_MANIFEST is required}"
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"
: "${SOURCE_TREE_ID:?SOURCE_TREE_ID is required}"
: "${SOURCE_ARCHIVE_SHA256:?SOURCE_ARCHIVE_SHA256 is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"
: "${BASE_IMAGE:?BASE_IMAGE is required}"

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
manifest_dir=$(CDPATH= cd -- "$(dirname -- "$RELEASE_MANIFEST")" && pwd)
manifest_name=$(basename -- "$RELEASE_MANIFEST")
checksum_file="$manifest_dir/release-manifest.sha256"
test -f "$RELEASE_MANIFEST" || { echo "release manifest not found" >&2; exit 2; }
test -f "$checksum_file" || { echo "release manifest checksum not found" >&2; exit 2; }
test -f "$SOURCE_DIR/.dockerignore" || { echo "SOURCE_DIR lacks .dockerignore" >&2; exit 2; }
test -f "$SOURCE_DIR/ops/release/Dockerfile" || { echo "SOURCE_DIR lacks ops/release/Dockerfile" >&2; exit 2; }

checksum_target=$(awk 'NR == 1 { sub(/^\\*/, "", $2); print $2 } NR > 1 { exit 2 }' "$checksum_file")
test "$checksum_target" = "$manifest_name" || { echo "manifest checksum must contain a relative filename" >&2; exit 1; }
(cd "$manifest_dir" && sha256sum -c "$(basename -- "$checksum_file")")

manifest_value() {
  python3 - "$RELEASE_MANIFEST" "$1" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)[sys.argv[2]]
print(value)
PY
}

assert_equal() {
  test "$1" = "$2" || { echo "attribution mismatch: $3" >&2; exit 1; }
}

candidate_id=$(docker image inspect --format '{{.Id}}' "$IMAGE_TAG")
base_id=$(docker image inspect --format '{{.Id}}' "$BASE_IMAGE")
assert_equal "$(manifest_value source_commit)" "$SOURCE_COMMIT" source_commit
assert_equal "$(manifest_value source_tree_id)" "$SOURCE_TREE_ID" source_tree_id
assert_equal "$(manifest_value source_archive_sha256)" "$SOURCE_ARCHIVE_SHA256" source_archive_sha256
assert_equal "$(manifest_value base_image)" "$BASE_IMAGE" base_image
assert_equal "$(manifest_value base_image_id)" "$base_id" base_image_id
assert_equal "$(manifest_value candidate_tag)" "$IMAGE_TAG" candidate_tag
assert_equal "$(manifest_value candidate_image_id)" "$candidate_id" candidate_image_id
assert_equal "$(manifest_value dockerfile_sha256)" "$(sha256sum "$SOURCE_DIR/ops/release/Dockerfile" | cut -d' ' -f1)" dockerfile_sha256
assert_equal "$(manifest_value dockerignore_sha256)" "$(sha256sum "$SOURCE_DIR/.dockerignore" | cut -d' ' -f1)" dockerignore_sha256

for pair in \
  "org.opencontainers.image.revision:$SOURCE_COMMIT" \
  "io.sooi.source.tree:$SOURCE_TREE_ID" \
  "io.sooi.source.archive.sha256:$SOURCE_ARCHIVE_SHA256" \
  "io.sooi.base.image.id:$base_id"
do
  label=${pair%%:*}
  expected=${pair#*:}
  actual=$(docker image inspect --format "{{ index .Config.Labels \"$label\" }}" "$IMAGE_TAG")
  assert_equal "$actual" "$expected" "OCI label $label"
done

mkdir -p "$OUTPUT_DIR"
docker image inspect "$IMAGE_TAG" >"$OUTPUT_DIR/candidate-inspect.json"
docker run --rm --network=none \
  -e DJANGO_SETTINGS_MODULE=config.settings.prod \
  -e DJANGO_SECRET_KEY=synthetic-verification-key-with-more-than-fifty-characters-only \
  -e DJANGO_ALLOWED_HOSTS=check.example.invalid \
  -e DJANGO_CSRF_TRUSTED_ORIGINS=https://check.example.invalid \
  -e DB_PASSWORD=synthetic-database-password \
  "$IMAGE_TAG" python manage.py check --deploy >"$OUTPUT_DIR/django-check.txt"

IMAGE_TAG="$IMAGE_TAG" ENV_FILE="$source_root/.env.release.example" docker compose \
  --file "$script_dir/compose.candidate.yml" config --images >"$OUTPUT_DIR/compose-images.txt"
test "$(sort -u "$OUTPUT_DIR/compose-images.txt" | sed '/^$/d' | wc -l)" -eq 1 || {
  echo "web, worker and beat do not resolve to one image" >&2; exit 1;
}
test "$(sed '/^$/d' "$OUTPUT_DIR/compose-images.txt" | wc -l)" -eq 3 || {
  echo "compose must define exactly web, worker and beat" >&2; exit 1;
}

# SOOI_IMAGE_BACKUP_HYGIENE_V1
IMAGE_BACKUP_INVENTORY="${OUTPUT_DIR}/image-backup-inventory.txt"
docker run --rm --network none --user 0:0 --entrypoint sh \
  "$IMAGE_TAG" -lc '
set -eu
for root in /app /opt/sooi; do
  if [ -d "$root" ]; then
    find "$root" -xdev -type f \
      \( -name "*.bak" -o -name "*.bak.*" -o -name "*.bak_*" \
         -o -name "*.backup" -o -name "*.backup.*" \
         -o -name "*.backup_*" \) -print
  fi
done
' | LC_ALL=C sort -u >"$IMAGE_BACKUP_INVENTORY"

if [ -s "$IMAGE_BACKUP_INVENTORY" ]; then
  cat "$IMAGE_BACKUP_INVENTORY" >&2
  echo "candidate image contains backup artifacts" >&2
  exit 1
fi

echo "candidate image backup hygiene passed for $IMAGE_TAG"

printf 'candidate verification passed for %s\n' "$IMAGE_TAG"
