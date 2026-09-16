"""Official/EU-harmonized managed areas and unresolved HNHS notices."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Callable

from shapely.geometry import shape
from shapely.ops import transform

from .features import MarineFeature, RestrictionClass

AREA_SHA256 = "914f80faea4d67a0e1bc6d4ddd1ed4ae5f80ff8e6f1a551f568ffe69e8d35cb9"
NOTICE_SHA256 = "4293b02b5c1ca5007c32d0a4bf257cc84b4bb4154a558600256ffaca994cc82c"


def classify_managed_area(upstream_layer: str, properties: dict) -> tuple[RestrictionClass, str]:
    """Classify legal effect without equating designation with prohibition."""
    if upstream_layer.endswith("militaryareaspoly"):
        status = str(properties.get("status") or "").strip().lower()
        if status in {"active", "prohibited", "closed"}:
            return RestrictionClass.HARD_NO_GO, f"published military-area status is {status}"
        return RestrictionClass.CAUTION_CONDITIONAL, "military area has no explicit active/prohibited status"
    return RestrictionClass.INFORMATIONAL, "Natura 2000 designation does not itself prohibit navigation"


def _fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "fixtures" / "bodrum_kos_real" / name


def _verified_json(name: str, expected_sha256: str) -> dict:
    path = _fixture_path(name)
    if not path.exists():
        raise FileNotFoundError(f"required official marine layer is missing: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"official marine layer checksum mismatch: expected {expected_sha256}, got {actual}")
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_official_areas_wgs84() -> tuple[MarineFeature, ...]:
    collection = _verified_json("official-managed-areas.geojson", AREA_SHA256)
    return tuple(
        MarineFeature(
            source_id=item["properties"]["source_id"],
            feature_type=item["properties"]["feature_type"],
            restriction_class=RestrictionClass(item["properties"]["restriction_class"]),
            geometry=shape(item["geometry"]),
            properties=item["properties"].get("properties", {}),
            source_name=item["properties"]["source_name"],
            source_version=item["properties"]["source_version"],
            classification_rule=item["properties"]["classification_rule"],
        )
        for item in collection["features"]
    )


def load_official_areas_projected(
    project: Callable[[float, float], tuple[float, float]],
) -> list[MarineFeature]:
    return [
        MarineFeature(
            source_id=item.source_id,
            feature_type=item.feature_type,
            restriction_class=item.restriction_class,
            geometry=transform(project, item.geometry),
            properties=item.properties,
            source_name=item.source_name,
            source_version=item.source_version,
            classification_rule=item.classification_rule,
        )
        for item in load_official_areas_wgs84()
    ]


@lru_cache(maxsize=1)
def load_unresolved_official_notices() -> tuple[dict, ...]:
    document = _verified_json("official-unresolved-notices.json", NOTICE_SHA256)
    return tuple(
        {
            **notice,
            "sourceName": document["source"],
            "sourceUrl": document["sourceUrl"],
            "fetchedAt": document["fetchedAt"],
        }
        for notice in document["notices"]
    )
