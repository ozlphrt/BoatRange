"""Normalized marine obstacle and restriction features with provenance."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Callable

from shapely.geometry import shape
from shapely.ops import transform


class RestrictionClass(str, Enum):
    HARD_NO_GO = "HARD_NO_GO"
    CAUTION_CONDITIONAL = "CAUTION_CONDITIONAL"
    INFORMATIONAL = "INFORMATIONAL"


@dataclass(frozen=True)
class MarineFeature:
    source_id: str
    feature_type: str
    restriction_class: RestrictionClass
    geometry: object
    properties: dict
    source_name: str
    source_version: str
    classification_rule: str


HARD_RESTRICTIONS = {
    "no_entry",
    "entry_prohibited",
    "prohibited_navigation",
    "navigation_prohibited",
}
FEATURE_SNAPSHOT_SHA256 = "b225513bd4412abbc9ba889c1485103208d487166572557c874b379a0ae6db12"


def classify_osm_tags(tags: dict) -> tuple[str, RestrictionClass, str]:
    """Map OSM seamark tags to the three project restriction classes."""
    man_made = tags.get("man_made")
    seamark_type = tags.get("seamark:type")
    if man_made in {"breakwater", "groyne"}:
        return (
            man_made,
            RestrictionClass.HARD_NO_GO,
            f"man_made={man_made} is a physical marine obstacle",
        )
    if seamark_type in {"rock", "wreck", "obstruction"}:
        return (
            seamark_type,
            RestrictionClass.HARD_NO_GO,
            f"seamark:type={seamark_type} is a physical hazard",
        )
    if seamark_type == "restricted_area":
        restrictions = {
            item.strip()
            for item in tags.get("seamark:restricted_area:restriction", "").split(";")
            if item.strip()
        }
        if restrictions & HARD_RESTRICTIONS:
            return (
                "restriction",
                RestrictionClass.HARD_NO_GO,
                "restricted area explicitly prohibits vessel entry/navigation",
            )
        return (
            "restriction",
            RestrictionClass.CAUTION_CONDITIONAL,
            "restricted area does not explicitly prohibit vessel navigation",
        )
    return (
        "seamark",
        RestrictionClass.INFORMATIONAL,
        "unrecognized seamark retained as informational metadata",
    )


@lru_cache(maxsize=1)
def load_features_wgs84() -> tuple[MarineFeature, ...]:
    path = (
        Path(__file__).resolve().parents[3]
        / "data" / "fixtures" / "bodrum_kos_real" / "marine-features.geojson"
    )
    if not path.exists():
        raise FileNotFoundError(
            f"marine feature layer is missing: {path}; run "
            "scripts/ingest/fetch_bodrum_kos_osm_features.py"
        )
    actual_checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_checksum != FEATURE_SNAPSHOT_SHA256:
        raise ValueError(
            f"marine feature checksum mismatch: expected {FEATURE_SNAPSHOT_SHA256}, "
            f"got {actual_checksum}"
        )
    collection = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        MarineFeature(
            source_id=item["properties"]["source_id"],
            feature_type=item["properties"]["feature_type"],
            restriction_class=RestrictionClass(item["properties"]["restriction_class"]),
            geometry=shape(item["geometry"]),
            properties=item["properties"].get("tags", {}),
            source_name=item["properties"]["source_name"],
            source_version=item["properties"]["source_version"],
            classification_rule=item["properties"]["classification_rule"],
        )
        for item in collection["features"]
    )


def load_features_projected(
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
        for item in load_features_wgs84()
    ]
