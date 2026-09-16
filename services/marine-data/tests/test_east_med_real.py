"""Regression checks for the real, full-detail OSM coastline covering
Italy, Greece, Cyprus, and Turkey. Same underlying OSM source as
bodrum_kos_real, clipped to a much bigger box (and more aggressively
sliver-filtered, since the box is so much wider — see
data/manifests/east_med_real.json).
"""

import math

import pytest
from shapely.geometry import Point
from shapely.ops import unary_union

from marine_data.east_med_real import (
    EAST_MED_BBOX_WGS84,
    generate_east_med_water,
    land_geometries,
)

_LAT_REF = 40.0
_LON_REF = 24.0
_METERS_PER_DEG_LAT = 111320.0
_METERS_PER_DEG_LON = _METERS_PER_DEG_LAT * math.cos(math.radians(_LAT_REF))


@pytest.fixture(scope="module")
def water_and_land():
    return generate_east_med_water()


def _point(lon: float, lat: float) -> Point:
    x = (lon - _LON_REF) * _METERS_PER_DEG_LON
    y = (lat - _LAT_REF) * _METERS_PER_DEG_LAT
    return Point(x, y)


class TestEastMedGeometryValidity:
    def test_land_is_valid(self):
        land = unary_union(land_geometries())
        assert land.is_valid

    def test_water_is_valid(self, water_and_land):
        water, _land = water_and_land
        assert water.is_valid

    def test_many_land_pieces_present(self):
        # Italy, Greece (mainland + many islands), Cyprus, Turkey, and
        # incidental neighbors, even after the 1-hectare sliver filter.
        assert len(land_geometries()) > 1000


class TestEastMedGeographySanityChecks:
    def test_rome_is_land(self, water_and_land):
        _water, land = water_and_land
        assert unary_union(land).contains(_point(12.49, 41.90))

    def test_athens_is_land(self, water_and_land):
        _water, land = water_and_land
        assert unary_union(land).contains(_point(23.73, 37.98))

    def test_nicosia_is_land(self, water_and_land):
        _water, land = water_and_land
        assert unary_union(land).contains(_point(33.36, 35.17))

    def test_istanbul_is_land(self, water_and_land):
        _water, land = water_and_land
        assert unary_union(land).contains(_point(28.98, 41.01))

    def test_antalya_coast_is_land(self, water_and_land):
        _water, land = water_and_land
        assert unary_union(land).contains(_point(30.70, 36.90))

    def test_bodrum_peninsula_is_land(self, water_and_land):
        """Cross-check against the higher-detail bodrum_kos_real region:
        even at this region's coarser sliver-filter threshold, Bodrum must
        still be land."""
        _water, land = water_and_land
        assert unary_union(land).contains(_point(27.45, 37.05))

    def test_kos_is_land(self, water_and_land):
        _water, land = water_and_land
        assert unary_union(land).contains(_point(27.10, 36.85))

    def test_mid_aegean_is_water(self, water_and_land):
        water, _land = water_and_land
        assert water.contains(_point(25.0, 37.5))

    def test_ionian_sea_is_water(self, water_and_land):
        water, _land = water_and_land
        assert water.contains(_point(19.0, 38.5))

    def test_bodrum_kos_channel_is_water(self, water_and_land):
        water, _land = water_and_land
        assert water.contains(_point(27.30, 36.95))


class TestEastMedBbox:
    def test_bbox_covers_all_four_countries_roughly(self):
        min_lon, min_lat, max_lon, max_lat = EAST_MED_BBOX_WGS84
        for lon, lat in [(12.49, 41.90), (23.73, 37.98), (33.36, 35.17), (27.45, 37.05)]:
            assert min_lon <= lon <= max_lon
            assert min_lat <= lat <= max_lat
