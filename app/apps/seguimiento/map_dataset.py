from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from decimal import Decimal
from typing import Any

from apps.busquedas.geography_coordinates import (
    MAP_DISPLAY_LABEL_ES,
    PRECISION_CLASS,
    STATUS_READY,
    resolve_opportunity_map_points,
)


DATASET_CONTRACT = (
    "SOOI_G3_OPPORTUNITY_MAP_DATASET_V1"
)

DATASET_CONTRACT_SHA256 = (
    "f4692829ed6d81c63a55901967a8301f91484de4da363ab81905f0c37c8b7678"
)

GROUPING_CONTRACT = (
    "ONE_FEATURE_PER_CANONICAL_MUNICIPALITY"
)


def _decimal_string(
    value: Decimal | None,
) -> str | None:
    if value is None:
        return None

    return format(
        value,
        "f",
    )


def _source_name(
    opportunity: Any,
) -> str:
    value = getattr(
        opportunity,
        "source_name",
        "",
    )

    if callable(value):
        value = value()

    return str(
        value or ""
    )


def _summary(
    opportunity: Any,
    *,
    detail_url_resolver: Callable[[int], str],
) -> dict[str, Any]:

    pk = int(
        opportunity.pk
    )

    return {
        "id":
            pk,

        "title":
            str(
                opportunity.title
                or ""
            ),

        "status":
            str(
                opportunity.status
                or ""
            ),

        "priority":
            str(
                opportunity.priority
                or ""
            ),

        "asking_price_current":
            _decimal_string(
                opportunity
                .asking_price_current
            ),

        "opportunity_score":
            opportunity
            .opportunity_score,

        "province":
            str(
                opportunity.province
                or ""
            ),

        "municipality":
            str(
                opportunity.municipality
                or ""
            ),

        "zone":
            str(
                opportunity.zone
                or ""
            ),

        "source_name":
            _source_name(
                opportunity
            ),

        "detail_url":
            detail_url_resolver(
                pk
            ),
    }


def build_opportunity_map_dataset(
    opportunities: Iterable[Any],
    *,
    detail_url_resolver: Callable[[int], str],
) -> dict[str, Any]:
    """
    Build the V1 opportunity-map payload from an already
    authorized and product-filtered opportunity iterable.

    Authorization and product visibility MUST happen before
    this function is called.
    """

    rows = list(
        opportunities
    )

    resolutions = (
        resolve_opportunity_map_points(
            rows
        )
    )

    resolution_by_id = {
        item.opportunity_id: item
        for item in resolutions
    }


    groups: dict[
        str,
        list[Any],
    ] = defaultdict(list)


    ready_count = 0
    fail_closed_count = 0


    for opportunity in rows:

        resolution = resolution_by_id[
            opportunity.pk
        ]

        if (
            resolution.status
            != STATUS_READY
            or resolution.point
            is None
        ):
            fail_closed_count += 1
            continue

        ready_count += 1

        groups[
            resolution.canonical_key
        ].append(
            opportunity
        )


    features: list[
        dict[str, Any]
    ] = []


    for canonical_key in sorted(
        groups
    ):

        bucket = sorted(
            groups[
                canonical_key
            ],
            key=lambda item: item.pk,
        )

        first_resolution = (
            resolution_by_id[
                bucket[0].pk
            ]
        )

        point = (
            first_resolution.point
        )

        assert point is not None


        summaries = [
            _summary(
                opportunity,
                detail_url_resolver=(
                    detail_url_resolver
                ),
            )
            for opportunity in bucket
        ]


        feature = {
            "type":
                "Feature",

            "id":
                canonical_key,

            "geometry": {
                "type":
                    "Point",

                "coordinates": [
                    point.longitude,
                    point.latitude,
                ],
            },

            "properties": {
                "canonical_key":
                    canonical_key,

                "precision_class":
                    point.precision_class,

                "approximation_label_es":
                    point
                    .approximation_label_es,

                "opportunity_count":
                    len(summaries),

                "opportunities":
                    summaries,
            },
        }

        features.append(
            feature
        )


    return {
        "type":
            "FeatureCollection",

        "contract":
            DATASET_CONTRACT,

        "contract_sha256":
            DATASET_CONTRACT_SHA256,

        "precision_class":
            PRECISION_CLASS,

        "approximation_label_es":
            MAP_DISPLAY_LABEL_ES,

        "grouping":
            GROUPING_CONTRACT,

        "features":
            features,

        "meta": {
            "opportunities_total_visible":
                len(rows),

            "map_ready":
                ready_count,

            "fail_closed":
                fail_closed_count,

            "municipality_features":
                len(features),
        },
    }
