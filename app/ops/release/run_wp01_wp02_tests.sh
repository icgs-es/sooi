#!/bin/sh
set -eu

: "${IMAGE_TAG:?IMAGE_TAG is required}"
: "${OUTPUT_DIR:?OUTPUT_DIR is required}"
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$OUTPUT_DIR"

IMAGE_TAG="$IMAGE_TAG" OUTPUT_DIR="$OUTPUT_DIR/verification" sh "$script_dir/verify_candidate.sh"
docker run --rm --network=none "$IMAGE_TAG" \
  python -m unittest discover -s tests_wp01_wp02 -v >"$OUTPUT_DIR/tests.txt" 2>&1
printf 'WP-01/WP-02 deterministic tests passed\n'
