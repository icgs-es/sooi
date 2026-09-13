#!/bin/sh
set -eu

: "${BASE_IMAGE:?BASE_IMAGE is required}"
: "${OUTPUT_TAG:?OUTPUT_TAG is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
: "${SOURCE_DATE_EPOCH:?SOURCE_DATE_EPOCH is required}"
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"
: "${SOURCE_TREE_ID:?SOURCE_TREE_ID is required}"
: "${SOURCE_ARCHIVE_SHA256:?SOURCE_ARCHIVE_SHA256 is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"

case "$SOURCE_DATE_EPOCH" in *[!0-9]*|'') echo "SOURCE_DATE_EPOCH must be an integer" >&2; exit 2;; esac
case "$SOURCE_COMMIT" in *[!0-9a-f]*|'') echo "SOURCE_COMMIT must be a lowercase hexadecimal Git object ID" >&2; exit 2;; esac
case "$SOURCE_TREE_ID" in *[!0-9a-f]*|'') echo "SOURCE_TREE_ID must be a lowercase hexadecimal Git object ID" >&2; exit 2;; esac
case "$SOURCE_ARCHIVE_SHA256" in *[!0-9a-f]*|'') echo "SOURCE_ARCHIVE_SHA256 must be a lowercase SHA-256" >&2; exit 2;; esac
test "${#SOURCE_COMMIT}" -eq 40 || test "${#SOURCE_COMMIT}" -eq 64 || { echo "SOURCE_COMMIT has an invalid length" >&2; exit 2; }
test "${#SOURCE_TREE_ID}" -eq 40 || test "${#SOURCE_TREE_ID}" -eq 64 || { echo "SOURCE_TREE_ID has an invalid length" >&2; exit 2; }
test "${#SOURCE_ARCHIVE_SHA256}" -eq 64 || { echo "SOURCE_ARCHIVE_SHA256 has an invalid length" >&2; exit 2; }
test -d "$SOURCE_DIR" || { echo "SOURCE_DIR must be an existing directory" >&2; exit 2; }
test ! -e "$SOURCE_DIR/.git" || { echo "SOURCE_DIR must be an exported Git tree, not a worktree" >&2; exit 2; }

source_abs=$(CDPATH= cd -- "$SOURCE_DIR" && pwd)
dockerfile="$source_abs/ops/release/Dockerfile"
dockerignore="$source_abs/.dockerignore"
test -f "$dockerfile" || { echo "SOURCE_DIR lacks ops/release/Dockerfile" >&2; exit 2; }
test -f "$dockerignore" || { echo "SOURCE_DIR lacks .dockerignore" >&2; exit 2; }
mkdir -p "$OUTPUT_DIR"
output_abs=$(CDPATH= cd -- "$OUTPUT_DIR" && pwd)
case "$output_abs/" in "$source_abs/"*) echo "OUTPUT_DIR must be outside SOURCE_DIR" >&2; exit 2;; esac

dockerfile_sha256=$(sha256sum "$dockerfile" | cut -d' ' -f1)
dockerignore_sha256=$(sha256sum "$dockerignore" | cut -d' ' -f1)

docker image inspect "$BASE_IMAGE" >/dev/null
base_id=$(docker image inspect --format '{{.Id}}' "$BASE_IMAGE")

DOCKER_BUILDKIT=1 SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" docker build \
  --pull=false --network=none \
  --file "$dockerfile" \
  --build-arg "BASE_IMAGE=$BASE_IMAGE" \
  --build-arg "BASE_IMAGE_ID=$base_id" \
  --build-arg "SOURCE_COMMIT=$SOURCE_COMMIT" \
  --build-arg "SOURCE_TREE_ID=$SOURCE_TREE_ID" \
  --build-arg "SOURCE_ARCHIVE_SHA256=$SOURCE_ARCHIVE_SHA256" \
  --build-arg "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" \
  --tag "$OUTPUT_TAG" "$source_abs"

candidate_id=$(docker image inspect --format '{{.Id}}' "$OUTPUT_TAG")
manifest="$OUTPUT_DIR/release-manifest.json"
python3 - "$manifest" "$SOURCE_COMMIT" "$SOURCE_TREE_ID" "$SOURCE_ARCHIVE_SHA256" "$BASE_IMAGE" "$base_id" "$candidate_id" "$OUTPUT_TAG" "$dockerfile_sha256" "$dockerignore_sha256" "$SOURCE_DATE_EPOCH" <<'PY'
import json, sys
path, commit, tree, archive, base_tag, base_id, candidate, tag, dockerfile, dockerignore, epoch = sys.argv[1:]
document = {
    "schema_version": 1,
    "source_commit": commit,
    "source_tree_id": tree,
    "source_archive_sha256": archive,
    "base_image": base_tag,
    "base_image_id": base_id,
    "candidate_image_id": candidate,
    "candidate_tag": tag,
    "dockerfile_sha256": dockerfile,
    "dockerignore_sha256": dockerignore,
    "source_date_epoch": int(epoch),
}
with open(path, "w", encoding="utf-8", newline="\n") as stream:
    json.dump(document, stream, indent=2, sort_keys=True)
    stream.write("\n")
PY
(cd "$OUTPUT_DIR" && sha256sum release-manifest.json >release-manifest.sha256)
printf 'candidate=%s\nmanifest=%s\n' "$candidate_id" "$manifest"
