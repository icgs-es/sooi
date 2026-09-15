import hashlib
import json
from functools import lru_cache
from pathlib import Path


CONTRACT = "SOOI_MUNICIPAL_CONTEXT_V1"
REGISTRY_VERSION = "SOOI_MUNICIPAL_CONTEXT_REGISTRY_ES_V1"

DATA_DIR = Path(__file__).resolve().parent / "data" / "v1"


def _sha256(path):
    h = hashlib.sha256()

    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


class MunicipalContextRegistry:

    def __init__(self, records, manifest):
        self._records = records
        self.manifest = manifest

    def __len__(self):
        return len(self._records)

    def get(self, canonical_key):
        record = self._records.get(str(canonical_key or ""))

        if record is None:
            return None

        return dict(record)


@lru_cache(maxsize=1)
def load_municipal_context_registry():

    manifest_path = DATA_DIR / "manifest.json"
    data_path = DATA_DIR / "municipality_context.json"

    with manifest_path.open(encoding="utf-8") as fh:
        manifest = json.load(fh)

    if manifest["registry_version"] != REGISTRY_VERSION:
        raise RuntimeError("Municipal context registry version mismatch")

    expected = manifest["data_sha256"].removeprefix("sha256:")

    if _sha256(data_path) != expected:
        raise RuntimeError("Municipal context data digest mismatch")

    with data_path.open(encoding="utf-8") as fh:
        rows = json.load(fh)

    if len(rows) != 8132:
        raise RuntimeError("Municipal context record count mismatch")

    records = {}

    for row in rows:
        key = row["canonical_key"]

        if key in records:
            raise RuntimeError(
                f"Duplicate municipal context identity: {key}"
            )

        records[key] = row

    return MunicipalContextRegistry(
        records=records,
        manifest=manifest,
    )


def get_municipal_context(canonical_key):
    return load_municipal_context_registry().get(canonical_key)
