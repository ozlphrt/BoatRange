"""Real, full-detail OSM coastline covering Italy, Greece, Cyprus, and Turkey.

Replaces the earlier Natural Earth-based approximation
(``marine_data.east_med_natural_earth``, since removed) with OSM's own
pre-built "land polygons" dataset — the same underlying data quality as
``bodrum_kos_real``, just clipped to a much bigger bounding box instead of
reconstructed from individual admin/island relations. See
``data/manifests/east_med_real.json`` for provenance and
``scripts/ingest/fetch_east_med_osm.py`` for how ``land.geojson`` was built.

This is still not chart-grade data (no bathymetry, no restriction zones, no
marina/fuel-dock POIs — CLAUDE.md Section 45), and the region spans enough
latitude that this project's single-reference-point ``LocalProjection``
loses distance/fuel accuracy far from that reference (documented in the
manifest, not hidden).
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform, unary_union

EAST_MED_BBOX_WGS84 = (6.5, 34.5, 42.0, 46.5)  # (min_lon, min_lat, max_lon, max_lat)

# Roughly central to the AOI, weighted slightly toward the Aegean (this
# project's original focus) rather than the true geometric centroid — see
# the manifest for the resulting distance-accuracy trade-off.
_LAT_REF = 40.0
_LON_REF = 24.0
_METERS_PER_DEG_LAT = 111320.0
_METERS_PER_DEG_LON = _METERS_PER_DEG_LAT * math.cos(math.radians(_LAT_REF))

_DATA_PATH = (
    Path(__file__).resolve().parents[3]
    / "data" / "fixtures" / "east_med_real" / "land.geojson"
)


def _wgs84_to_local(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - _LON_REF) * _METERS_PER_DEG_LON, (lat - _LAT_REF) * _METERS_PER_DEG_LAT)


@lru_cache(maxsize=1)
def _load_land_wgs84() -> MultiPolygon:
    if not _DATA_PATH.exists():
        raise FileNotFoundError(
            f"East Med land data not found at {_DATA_PATH} -- run "
            "scripts/ingest/fetch_east_med_osm.py first (CLAUDE.md Section 27: "
            "hard-required data must fail closed, never silently fall back to "
            "a smaller region)."
        )
    fc = json.loads(_DATA_PATH.read_text())
    polys = [shape(feature["geometry"]) for feature in fc["features"]]
    land = unary_union(polys)
    if not land.is_valid:
        land = land.buffer(0)
    return land


def land_geometries() -> list[Polygon]:
    """Every land polygon in the East Med region, projected to local meters."""
    land_wgs84 = _load_land_wgs84()
    land_local = transform(_wgs84_to_local, land_wgs84)
    return list(land_local.geoms) if hasattr(land_local, "geoms") else [land_local]


def east_med_bbox() -> Polygon:
    """The projected compute box the region's water is cut from."""
    min_lon, min_lat, max_lon, max_lat = EAST_MED_BBOX_WGS84
    corners = [
        _wgs84_to_local(min_lon, min_lat), _wgs84_to_local(max_lon, min_lat),
        _wgs84_to_local(max_lon, max_lat), _wgs84_to_local(min_lon, max_lat),
        _wgs84_to_local(min_lon, min_lat),
    ]
    return Polygon(corners)


def generate_east_med_water() -> tuple[MultiPolygon, list[Polygon]]:
    """Return ``(water, land)`` for the region, in local meters."""
    land_geoms = land_geometries()
    land_union = unary_union(land_geoms)

    water_geom = east_med_bbox().difference(land_union)
    if water_geom.is_empty:
        raise ValueError("East Med region produced no navigable water")

    if isinstance(water_geom, Polygon):
        water_mp = MultiPolygon([water_geom])
    elif isinstance(water_geom, MultiPolygon):
        water_mp = water_geom
    else:
        raise ValueError(f"unexpected water geometry type: {water_geom.geom_type}")

    return water_mp, land_geoms
