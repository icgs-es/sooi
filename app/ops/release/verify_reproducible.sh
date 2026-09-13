#!/bin/sh
set -eu

: "${BASE_IMAGE:?BASE_IMAGE is required}"
: "${OUTPUT_PREFIX:?OUTPUT_PREFIX is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
: "${SOURCE_DATE_EPOCH:?SOURCE_DATE_EPOCH is required}"
: "${SOURCE_COMMIT:?SOURCE_COMMIT is required}"
: "${SOURCE_TREE_ID:?SOURCE_TREE_ID is required}"
: "${SOURCE_ARCHIVE_SHA256:?SOURCE_ARCHIVE_SHA256 is required}"
: "${SOURCE_DIR:?SOURCE_DIR is required}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$OUTPUT_DIR/build-1" "$OUTPUT_DIR/build-2"

build_once() {
  output_tag=$1
  output_dir=$2
  BASE_IMAGE="$BASE_IMAGE" OUTPUT_TAG="$output_tag" OUTPUT_DIR="$output_dir" \
    SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH" SOURCE_COMMIT="$SOURCE_COMMIT" \
    SOURCE_TREE_ID="$SOURCE_TREE_ID" SOURCE_ARCHIVE_SHA256="$SOURCE_ARCHIVE_SHA256" \
    SOURCE_DIR="$SOURCE_DIR" sh "$script_dir/build_candidate.sh"
}

build_once "${OUTPUT_PREFIX}:rebuild-1" "$OUTPUT_DIR/build-1"
build_once "${OUTPUT_PREFIX}:rebuild-2" "$OUTPUT_DIR/build-2"

id1=$(docker image inspect --format '{{.Id}}' "${OUTPUT_PREFIX}:rebuild-1")
id2=$(docker image inspect --format '{{.Id}}' "${OUTPUT_PREFIX}:rebuild-2")
report="$OUTPUT_DIR/reproducibility.txt"
if test "$id1" = "$id2"; then
  printf 'method=exact-image-identity\nimage_id=%s\nresult=pass\n' "$id1" >"$report"
else
  docker image inspect --format '{{json .Config}}|{{json .RootFS.Layers}}' "${OUTPUT_PREFIX}:rebuild-1" >"$OUTPUT_DIR/normalized-1.txt"
  docker image inspect --format '{{json .Config}}|{{json .RootFS.Layers}}' "${OUTPUT_PREFIX}:rebuild-2" >"$OUTPUT_DIR/normalized-2.txt"
  cmp "$OUTPUT_DIR/normalized-1.txt" "$OUTPUT_DIR/normalized-2.txt"
  printf 'method=normalized-config-and-rootfs-layer-identities\nfirst_image_id=%s\nsecond_image_id=%s\nresult=pass\n' "$id1" "$id2" >"$report"
fi
cat "$report"
