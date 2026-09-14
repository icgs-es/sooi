"""Immutable evolution of the Spain geography registry."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

from .importer import _html_rows, canonical_content_digest
from .loader import GeographyRegistry
from .resolver import normalize_geography_input


CHANGE_RE = re.compile(r"^(\d{2})\s+(\d{3})\s+(\d)$")

ARTICLES = {
    "A",
    "El",
    "La",
    "Las",
    "Los",
    "O",
    "el",
}


def iso_date(value):
    return datetime.strptime(
        str(value).strip(),
        "%d/%m/%Y",
    ).date().isoformat()


def read_denomination_changes(path):
    changes = []

    for row in _html_rows(Path(path)):
        if len(row) < 5:
            continue

        match = CHANGE_RE.fullmatch(
            str(row[2]).strip()
        )

        if not match:
            continue

        province, municipality, digit = (
            match.groups()
        )

        changes.append({
            "canonical_key": (
                f"municipality:{province}{municipality}"
            ),
            "official_code": (
                province + municipality
            ),
            "control_digit": digit,
            "province_name": str(
                row[0]
            ).strip(),
            "new_name": str(
                row[1]
            ).strip(),
            "old_name": str(
                row[3]
            ).strip(),
            "resolution_date": iso_date(
                row[4]
            ),
        })

    return changes


def natural_article_alias(name):
    if ", " not in name:
        return None

    stem, article = name.rsplit(
        ", ",
        1,
    )

    if article not in ARTICLES:
        return None

    return f"{article} {stem}"


def indexes(
    identities,
    aliases,
):
    canonical = {}
    alias_index = {}

    for item in identities:
        norm = normalize_geography_input(
            item["canonical_name"]
        )

        canonical.setdefault(
            norm,
            set(),
        ).add(
            item["canonical_key"]
        )

    for item in aliases:
        norm = normalize_geography_input(
            item["raw_alias"]
        )

        alias_index.setdefault(
            norm,
            set(),
        ).add(
            item["target_canonical_key"]
        )

    return canonical, alias_index


def ensure_available(
    name,
    target,
    canonical,
    alias_index,
):
    norm = normalize_geography_input(
        name
    )

    foreign_canonical = (
        canonical.get(norm, set())
        - {target}
    )

    foreign_aliases = (
        alias_index.get(norm, set())
        - {target}
    )

    if (
        foreign_canonical
        or foreign_aliases
    ):
        raise ValueError(
            f"name collision {name!r} "
            f"target={target!r}"
        )


def validate_provenance_contract(
    identities,
    aliases,
):
    required = (
        "provenance_id",
        "kind",
        "reference",
        "note",
    )

    failures = []

    for group_name, rows in (
        ("identities", identities),
        ("aliases", aliases),
    ):
        for index, row in enumerate(rows):
            for pindex, provenance in enumerate(
                row.get("provenance")
                or []
            ):
                for field in required:
                    value = provenance.get(
                        field
                    )

                    if (
                        not isinstance(
                            value,
                            str,
                        )
                        or not value.strip()
                    ):
                        failures.append(
                            f"{group_name}[{index}]"
                            f".provenance[{pindex}]"
                            f".{field}"
                        )

    if failures:
        raise ValueError(
            "invalid provenance contract: "
            + ", ".join(
                failures[:20]
            )
        )


def evolve_registry(
    base,
    modifications,
    output,
    registry_version,
    *,
    as_of_date,
    source_reference,
    expected_change_count=None,
    apply=False,
):
    base = Path(base)
    modifications = Path(
        modifications
    )
    output = Path(output)

    base_manifest = json.loads(
        (
            base / "manifest.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    identities = json.loads(
        (
            base / "identities.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    aliases = json.loads(
        (
            base / "aliases.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    memberships = json.loads(
        (
            base / "memberships.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    changes = (
        read_denomination_changes(
            modifications
        )
    )

    if (
        expected_change_count is not None
        and len(changes)
        != expected_change_count
    ):
        raise ValueError(
            "change count expected="
            f"{expected_change_count} "
            f"actual={len(changes)}"
        )

    by_key = {
        item["canonical_key"]: item
        for item in identities
    }

    canonical, alias_index = (
        indexes(
            identities,
            aliases,
        )
    )

    alias_ids = {
        item["alias_id"]
        for item in aliases
    }

    historical_added = 0
    article_added = 0

    for change in changes:
        key = change[
            "canonical_key"
        ]

        identity = by_key[key]

        if (
            identity["type"]
            != "municipality"
        ):
            raise ValueError(
                f"not municipality {key}"
            )

        if (
            identity["canonical_name"]
            != change["old_name"]
        ):
            raise ValueError(
                f"old name mismatch {key}"
            )

        if (
            identity["official_code"]
            != change["official_code"]
        ):
            raise ValueError(
                f"official code mismatch {key}"
            )

        if (
            str(
                identity[
                    "control_digit"
                ]
            )
            != change[
                "control_digit"
            ]
        ):
            raise ValueError(
                f"control digit mismatch {key}"
            )

        old_name = change[
            "old_name"
        ]

        new_name = change[
            "new_name"
        ]

        effective = change[
            "resolution_date"
        ]

        ensure_available(
            new_name,
            key,
            canonical,
            alias_index,
        )

        old_norm = (
            normalize_geography_input(
                old_name
            )
        )

        canonical[
            old_norm
        ].discard(key)

        if not canonical[
            old_norm
        ]:
            del canonical[
                old_norm
            ]

        new_norm = (
            normalize_geography_input(
                new_name
            )
        )

        canonical.setdefault(
            new_norm,
            set(),
        ).add(key)

        identity[
            "canonical_name"
        ] = new_name

        province_name = None

        if identity.get(
            "province"
        ):
            province_name = (
                by_key[
                    identity[
                        "province"
                    ]
                ][
                    "canonical_name"
                ]
            )

        identity[
            "query_label"
        ] = (
            f"{new_name}, "
            f"{province_name}"
            if province_name
            else new_name
        )

        identity.setdefault(
            "provenance",
            [],
        ).append({
            "provenance_id": (
                "ine-denomination-"
                + key.replace(
                    ":",
                    "-",
                )
                + "-"
                + effective
            ),
            "kind": (
                "official_denomination_change"
            ),
            "reference": (
                source_reference
            ),
            "note": (
                "Official denomination "
                f"changed from {old_name!r} "
                f"to {new_name!r} "
                f"effective {effective}"
            ),
        })

        historical_id = (
            key
            + ":alias:historical:"
            + effective
        )

        if historical_id in alias_ids:
            raise ValueError(
                "duplicate alias "
                + historical_id
            )

        aliases.append({
            "alias_id": historical_id,
            "raw_alias": old_name,
            "target_canonical_key": key,
            "province_scope": (
                identity.get(
                    "province"
                )
            ),
            "type_scope": (
                "municipality"
            ),
            "provenance": [{
                "provenance_id": (
                    "ine-historical-"
                    + key.replace(
                        ":",
                        "-",
                    )
                    + "-"
                    + effective
                ),
                "kind": (
                    "historical_official_name"
                ),
                "reference": (
                    source_reference
                ),
                "note": (
                    "Official municipality "
                    "denomination before "
                    f"{effective}"
                ),
            }],
            "note": (
                "Historical official "
                "denomination preserved "
                "for compatibility"
            ),
        })

        alias_ids.add(
            historical_id
        )

        alias_index.setdefault(
            old_norm,
            set(),
        ).add(key)

        historical_added += 1

        natural = (
            natural_article_alias(
                new_name
            )
        )

        if natural:
            ensure_available(
                natural,
                key,
                canonical,
                alias_index,
            )

            article_id = (
                key
                + ":alias:article:"
                + effective
            )

            if (
                article_id
                in alias_ids
            ):
                raise ValueError(
                    "duplicate alias "
                    + article_id
                )

            aliases.append({
                "alias_id": article_id,
                "raw_alias": natural,
                "target_canonical_key": (
                    key
                ),
                "province_scope": (
                    identity.get(
                        "province"
                    )
                ),
                "type_scope": (
                    "municipality"
                ),
                "provenance": [{
                    "provenance_id": (
                        "ine-article-"
                        + key.replace(
                            ":",
                            "-",
                        )
                        + "-"
                        + effective
                    ),
                    "kind": (
                        "deterministic_"
                        "orthographic_alias"
                    ),
                    "reference": (
                        source_reference
                    ),
                    "note": (
                        "Article moved to "
                        "natural query order"
                    ),
                }],
                "note": (
                    "Deterministic "
                    "current-name article "
                    "variant"
                ),
            })

            alias_ids.add(
                article_id
            )

            alias_index.setdefault(
                normalize_geography_input(
                    natural
                ),
                set(),
            ).add(key)

            article_added += 1

    validate_provenance_contract(
        identities,
        aliases,
    )

    digest = (
        canonical_content_digest(
            identities,
            aliases,
            memberships,
        )
    )

    source_sha = (
        hashlib.sha256(
            modifications.read_bytes()
        ).hexdigest()
    )

    manifest = {
        **base_manifest,
        "registry_version": (
            registry_version
        ),
        "base_registry_version": (
            base_manifest[
                "registry_version"
            ]
        ),
        "base_content_digest": (
            base_manifest[
                "content_digest"
            ]
        ),
        "registry_as_of_date": (
            as_of_date
        ),
        "post_baseline_modifications_discovered": (
            len(changes)
        ),
        "post_baseline_modifications_applied": (
            True
        ),
        "post_baseline_modifications_applied_count": (
            len(changes)
        ),
        "historical_aliases_added": (
            historical_added
        ),
        "article_aliases_added": (
            article_added
        ),
        "post_baseline_modifications_source": (
            source_reference
        ),
        "post_baseline_modifications_sha256": (
            source_sha
        ),
        "latest_modification_resolution_date": (
            max(
                row[
                    "resolution_date"
                ]
                for row in changes
            )
        ),
        "alias_count": (
            len(aliases)
        ),
        "membership_count": (
            len(memberships)
        ),
        "content_digest": digest,
        "validation": "PASS",
    }

    GeographyRegistry.from_documents(
        manifest,
        identities,
        aliases,
        memberships,
    )

    if apply:
        output.mkdir(
            parents=True,
            exist_ok=True,
        )

        for filename, document in (
            (
                "manifest.json",
                manifest,
            ),
            (
                "identities.json",
                identities,
            ),
            (
                "aliases.json",
                aliases,
            ),
            (
                "memberships.json",
                memberships,
            ),
        ):
            (
                output / filename
            ).write_text(
                json.dumps(
                    document,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )

    return manifest
