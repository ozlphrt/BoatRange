"""Regression checks for the real OSM-derived Bodrum/Kos coastline.

Unlike ``test_bodrum_kos_fixture.py`` (which checks a fully controlled
synthetic world), these checks assert against real geography — the same six
sanity-check points used to manually validate the ingested data (see
``data/manifests/bodrum_kos_real.json``). If OSM data changes and one of
these points flips, that is a signal worth investigating, not necessarily a
bug in this code — but it should never happen silently.
"""

import math

import pytest
from shapely.geometry import Point
from shapely.ops import unary_union

from marine_data.bodrum_kos_real import (
    REAL_BBOX_WGS84,
    generate_bodrum_kos_real_water,
    land_geometries,
)

# Mirrors marine_data.bodrum_kos_real's own projection constants (matches
# isochrone.projection.LocalProjection's defaults) rather than importing the
# isochrone package, which this test package does not depend on.
_LAT_REF = 37.0
_LON_REF = 27.5
_METERS_PER_DEG_LAT = 111320.0
_METERS_PER_DEG_LON = _METERS_PER_DEG_LAT * math.cos(math.radians(_LAT_REF))


@pytest.fixture(scope="module")
def water_and_land():
    return generate_bodrum_kos_real_water()


def _point(lon: float, lat: float) -> Point:
    x = (lon - _LON_REF) * _METERS_PER_DEG_LON
    y = (lat - _LAT_REF) * _METERS_PER_DEG_LAT
    return Point(x, y)


class TestRealGeometryValidity:
    def test_land_is_valid(self):
        land = unary_union(land_geometries())
        assert land.is_valid

    def test_water_is_valid(self, water_and_land):
        water, _land = water_and_land
        assert water.is_valid

    def test_land_and_water_do_not_overlap(self, water_and_land):
        water, land = water_and_land
        land_union = unary_union(land)
        assert water.intersection(land_union).area < 1.0  # numerical tolerance only

    def test_multiple_land_pieces_present(self):
        # Bodrum, Kos, and ~470 surrounding islets, filtered/simplified.
        assert len(land_geometries()) > 50


class TestRealGeographySanityChecks:
    """Six independently-verified points (CLAUDE.md Section 35 style fixture)."""

    def test_bodrum_peninsula_interior_is_land(self, water_and_land):
        _water, land = water_and_land
        land_union = unary_union(land)
        assert land_union.contains(_point(27.45, 37.05))

    def test_point_south_of_cape_is_water(self, water_and_land):
        water, _land = water_and_land
        assert water.contains(_point(27.30, 36.90))

    def test_kos_island_interior_is_land(self, water_and_land):
        _water, land = water_and_land
        land_union = unary_union(land)
        assert land_union.contains(_point(27.10, 36.85))

    def test_bodrum_kos_channel_midpoint_is_water(self, water_and_land):
        water, _land = water_and_land
        assert water.contains(_point(27.30, 36.95))

    def test_gulluk_bay_interior_is_land(self, water_and_land):
        _water, land = water_and_land
        land_union = unary_union(land)
        assert land_union.contains(_point(27.60, 37.10))

    def test_open_sea_south_of_kos_is_water(self, water_and_land):
        water, _land = water_and_land
        assert water.contains(_point(27.15, 36.75))


class TestRealBbox:
    def test_bbox_is_superset_of_synthetic_fixture_bbox(self):
        from marine_data.bodrum_kos_fixture import FIXTURE_BBOX_WGS84

        real_min_lon, real_min_lat, real_max_lon, real_max_lat = REAL_BBOX_WGS84
        fx_min_lon, fx_min_lat, fx_max_lon, fx_max_lat = FIXTURE_BBOX_WGS84
        assert real_min_lon <= fx_min_lon
        assert real_min_lat <= fx_min_lat
        assert real_max_lon >= fx_max_lon
        assert real_max_lat >= fx_max_lat
