"""Versioned marine POIs used for display and reachable fuel-stop checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from shapely.geometry import Point

POI_SHA256 = "e8a64a7b569c056eed1c00c55ef79884bc7bc6f2996d00dfbf1d0715dd9b67c4"


@dataclass(frozen=True)
class MarinePoi:
    source_id: str
    name: str
    poi_type: str
    categories: tuple[str, ...]
    geometry: Point
    confidence: str
    source_name: str
    source_version: str
    classification_rule: str
    tags: dict


def _fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "fixtures" / "bodrum_kos_real" / "marine-pois.geojson"


@lru_cache(maxsize=1)
def load_marine_pois_wgs84() -> tuple[MarinePoi, ...]:
    path = _fixture_path()
    if not path.exists():
        raise FileNotFoundError(f"required marine POI layer is missing: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != POI_SHA256:
        raise ValueError(f"marine POI checksum mismatch: expected {POI_SHA256}, got {actual}")
    collection = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        MarinePoi(
            source_id=item["properties"]["source_id"],
            name=item["properties"]["name"],
            poi_type=item["properties"]["poi_type"],
            categories=tuple(item["properties"]["categories"]),
            geometry=Point(item["geometry"]["coordinates"]),
            confidence=item["properties"]["confidence"],
            source_name=item["properties"]["source_name"],
            source_version=item["properties"]["source_version"],
            classification_rule=item["properties"]["classification_rule"],
            tags=item["properties"].get("tags", {}),
        )
        for item in collection["features"]
    )


def load_marine_pois_projected(
    project: Callable[[float, float], tuple[float, float]],
) -> list[MarinePoi]:
    return [
        MarinePoi(
            source_id=item.source_id,
            name=item.name,
            poi_type=item.poi_type,
            categories=item.categories,
            geometry=Point(project(item.geometry.x, item.geometry.y)),
            confidence=item.confidence,
            source_name=item.source_name,
            source_version=item.source_version,
            classification_rule=item.classification_rule,
            tags=item.tags,
        )
        for item in load_marine_pois_wgs84()
    ]
