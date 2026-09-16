"""OSM-derived coastline for the broader Aegean routing region."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform, unary_union

from isochrone.projection import LocalProjection


AEGEAN_BBOX_WGS84 = (24.75, 35.25, 30.25, 39.25)
_PROJECTION = LocalProjection(lat_ref=37.0, lon_ref=27.5)
_DATA_PATH = (
    Path(__file__).resolve().parents[3]
    / "data" / "fixtures" / "aegean_real" / "land.geojson"
)


@lru_cache(maxsize=1)
def _load_land_wgs84():
    if not _DATA_PATH.exists():
        raise FileNotFoundError(f"Aegean coastline is missing: {_DATA_PATH}")
    document = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    merged = unary_union([shape(feature["geometry"]) for feature in document["features"]])
    return merged if merged.is_valid else merged.buffer(0)


def land_geometries() -> list[Polygon]:
    projected = transform(_PROJECTION.to_projected, _load_land_wgs84())
    return list(projected.geoms) if hasattr(projected, "geoms") else [projected]


def generate_aegean_water() -> tuple[MultiPolygon, list[Polygon]]:
    west, south, east, north = AEGEAN_BBOX_WGS84
    bbox = Polygon([
        _PROJECTION.to_projected(west, south),
        _PROJECTION.to_projected(east, south),
        _PROJECTION.to_projected(east, north),
        _PROJECTION.to_projected(west, north),
    ])
    land = land_geometries()
    water = bbox.difference(unary_union(land))
    if isinstance(water, Polygon):
        water = MultiPolygon([water])
    elif not isinstance(water, MultiPolygon):
        water = MultiPolygon([part for part in water.geoms if isinstance(part, Polygon)])
    return water, land
