"""Real OSM-derived coastline for the Bodrum/Kos region.

Unlike :mod:`marine_data.bodrum_kos_fixture` (a hand-built synthetic
regression fixture), this module loads land geometry actually ingested from
OpenStreetMap -- the Bodrum administrative boundary relation, the Kos island
relation, and ~470 surrounding islets reconstructed from raw coastline ways.
See ``scripts/ingest/fetch_bodrum_kos_osm.py`` for how ``land.geojson`` was
produced and ``data/manifests/bodrum_kos_real.json`` for provenance
(CLAUDE.md Section 15/28).

This is still not chart-grade data (CLAUDE.md Section 45). Bathymetry is
provided separately by the EMODnet layer registered in ``apps.api.regions``;
restriction zones and marina/fuel-dock POIs remain unavailable. Callers must
keep reporting LOW confidence.

Geometry on disk is WGS84 GeoJSON; :func:`generate_bodrum_kos_real_water`
projects it to local meters via the same convention as
``isochrone.projection.LocalProjection`` (lat_ref=37.0, lon_ref=27.5), so it
is a drop-in replacement for the synthetic fixture's ``(water, land)``
contract used by ``apps.api.app.regions``.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform, unary_union

REAL_BBOX_WGS84 = (26.85, 36.60, 27.85, 37.25)  # (min_lon, min_lat, max_lon, max_lat)

# Matches isochrone.projection.LocalProjection's defaults exactly, so
# coordinates produced here are directly comparable to the engine's.
_LAT_REF = 37.0
_LON_REF = 27.5
_METERS_PER_DEG_LAT = 111320.0
_METERS_PER_DEG_LON = _METERS_PER_DEG_LAT * math.cos(math.radians(_LAT_REF))

_DATA_PATH = (
    Path(__file__).resolve().parents[3]
    / "data" / "fixtures" / "bodrum_kos_real" / "land.geojson"
)


def _wgs84_to_local(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - _LON_REF) * _METERS_PER_DEG_LON, (lat - _LAT_REF) * _METERS_PER_DEG_LAT)


@lru_cache(maxsize=1)
def _load_land_wgs84() -> MultiPolygon:
    if not _DATA_PATH.exists():
        raise FileNotFoundError(
            f"real Bodrum/Kos land data not found at {_DATA_PATH} -- run "
            "scripts/ingest/fetch_bodrum_kos_osm.py first (CLAUDE.md Section 27: "
            "hard-required data must fail closed, never silently fall back to "
            "the synthetic fixture)."
        )
    fc = json.loads(_DATA_PATH.read_text())
    polys = [shape(feature["geometry"]) for feature in fc["features"]]
    land = unary_union(polys)
    if not land.is_valid:
        land = land.buffer(0)
    return land


def land_geometries() -> list[Polygon]:
    """Every land polygon in the real region, projected to local meters."""
    land_wgs84 = _load_land_wgs84()
    land_local = transform(_wgs84_to_local, land_wgs84)
    return list(land_local.geoms) if hasattr(land_local, "geoms") else [land_local]


def real_bbox() -> Polygon:
    """The projected compute box the real region's water is cut from."""
    min_lon, min_lat, max_lon, max_lat = REAL_BBOX_WGS84
    corners = [
        _wgs84_to_local(min_lon, min_lat), _wgs84_to_local(max_lon, min_lat),
        _wgs84_to_local(max_lon, max_lat), _wgs84_to_local(min_lon, max_lat),
        _wgs84_to_local(min_lon, min_lat),
    ]
    return Polygon(corners)


def generate_bodrum_kos_real_water() -> tuple[MultiPolygon, list[Polygon]]:
    """Return ``(water, land)`` for the real region, in local meters.

    Same contract as ``bodrum_kos_fixture.generate_bodrum_kos_water``: water
    is the compute box minus raw, unbuffered land (safety clearance is
    applied later by the routing engine, not baked in here).
    """
    land_geoms = land_geometries()
    land_union = unary_union(land_geoms)

    water_geom = real_bbox().difference(land_union)
    if water_geom.is_empty:
        raise ValueError("real Bodrum/Kos region produced no navigable water")

    if isinstance(water_geom, Polygon):
        water_mp = MultiPolygon([water_geom])
    elif isinstance(water_geom, MultiPolygon):
        water_mp = water_geom
    else:
        raise ValueError(f"unexpected water geometry type: {water_geom.geom_type}")

    return water_mp, land_geoms
