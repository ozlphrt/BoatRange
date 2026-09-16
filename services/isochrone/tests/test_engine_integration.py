"""Integration tests for the REAL isochrone engine (services/isochrone/isochrone).

``test_isochrone.py`` in this same directory never imports anything from the
``isochrone`` package — it defines its own throwaway ``SimpleCostField`` class
and tests that instead. Its 20 passing tests say nothing about whether
``IsochroneEngine``, ``build_region_polygon`` or ``validate_polygon`` actually
work. This file exercises the real pipeline end to end against the corrected
Bodrum/Kos fixture, per CLAUDE.md Section 50 steps 13-14:

    "implement one-way isochrone" / "prove that isochrone cannot leak
    through land"

and Section 48 (Definition of Done): a fixed origin near Bodrum with a fixed
fuel setting must produce a water-constrained reachable area that wraps
around islands/peninsulas and never crosses blocked terrain.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "../routing")
sys.path.insert(0, "../marine-data")

import pytest
from shapely.geometry import LineString, Point
from shapely.ops import unary_union

from marine_data.bodrum_kos_fixture import (
    _wgs84_to_local,
    generate_bodrum_kos_water,
    land_geometries,
    small_islands,
)
from isochrone.engine import calculate_one_way_range
from isochrone.navigability import NavigabilityRegion
from isochrone.polygon_utils import PolygonValidation, validate_polygon, cleanup_polygon
from shapely.geometry import Polygon, MultiPolygon


# A channel-water origin, per REGRESSION_FIXTURES — well clear of every
# landmass and breakwater so the full pipeline has room to expand.
ORIGIN_WGS84 = (27.30, 36.95)
LPNM = 2.19  # L/nm at 4000 RPM (CLAUDE.md Section 4.2)
RESOLUTION_M = 400.0  # coarser than production (75-150m) for test speed


@pytest.fixture(scope="module")
def geometry():
    water, land = generate_bodrum_kos_water()
    return {"water": water, "land": land, "land_union": unary_union(land)}


@pytest.fixture(scope="module")
def result(geometry):
    """One computed isochrone, reused by every assertion in this module."""
    return calculate_one_way_range(
        origin_wgs84=ORIGIN_WGS84,
        lpnm=LPNM,
        usable_fuel_l=60.0,
        resolution_m=RESOLUTION_M,
        clearance_m=50.0,
        water=geometry["water"],
        land=geometry["land"],
    )


class TestEndToEndPipeline:
    """The engine must actually run against real (fixture) geometry."""

    def test_computes_without_raising(self, result):
        assert result is not None

    def test_produces_all_four_bands(self, result):
        assert set(result.bands.keys()) == {0.25, 0.50, 0.75, 1.00}

    def test_bands_grow_monotonically(self, result):
        areas = [result.bands[f].polygon.area for f in (0.25, 0.50, 0.75, 1.00)]
        for a, b in zip(areas, areas[1:]):
            assert b >= a - 1.0, "a larger fuel band must not shrink"

    def test_all_bands_pass_validation(self, result):
        for frac, band in result.bands.items():
            assert band.validation.ok, (
                f"{int(frac * 100)}% band failed independent verification: "
                f"{band.validation.to_dict()}"
            )

    def test_no_band_polygon_intersects_land(self, result, geometry):
        land_union = geometry["land_union"]
        for frac, band in result.bands.items():
            if band.polygon.is_empty:
                continue
            # Erode land slightly so shared-boundary touching isn't a false
            # positive — the polygon is allowed to touch the coastline, not
            # overlap its interior.
            assert not band.polygon.intersects(land_union.buffer(-1)), (
                f"{int(frac * 100)}% band overlaps land"
            )

    def test_no_band_covers_the_channel_island(self, result, geometry):
        """Karaada sits inside the fixture channel — a leaking polygon would
        paper over it instead of showing a shadow."""
        karaada = small_islands()[0]
        full_band = result.bands[1.00].polygon
        if not full_band.is_empty:
            assert not full_band.contains(karaada.centroid)

    def test_100pct_band_covers_the_origin(self, result):
        origin_xy = result.origin_projected
        assert result.bands[1.00].polygon.covers(Point(*origin_xy))

    def test_boundary_validation_actually_samples_multiple_points(self, result):
        """Regression guard for the interpolate()/generator bugs in
        polygon_utils.validate_polygon — a validation result claiming 0 or 1
        boundary sample almost certainly means the sampler is broken, not that
        the polygon has no boundary."""
        for frac, band in result.bands.items():
            if band.polygon.is_empty:
                continue
            assert band.validation.n_boundary_samples > 1, (
                f"{int(frac * 100)}% band validation only took "
                f"{band.validation.n_boundary_samples} boundary samples"
            )


class TestValidatorCatchesInjectedLeaks:
    """The independent verifier must reject a polygon that leaks, not just
    rubber-stamp whatever the main engine produced."""

    def test_polygon_covering_land_fails_validation(self, geometry):
        region = NavigabilityRegion(
            water_polygons=geometry["water"],
            land_geometries=geometry["land"],
            clearance_m=50.0,
            resolution_m=RESOLUTION_M,
        )
        # Deliberately corrupt: a big box that covers open water AND a chunk
        # of the Bodrum mainland.
        corrupted = MultiPolygon([Polygon([
            (*_wgs84_to_local(27.20, 36.90), ),
            (*_wgs84_to_local(27.60, 36.90), ),
            (*_wgs84_to_local(27.60, 37.10), ),
            (*_wgs84_to_local(27.20, 37.10), ),
        ])])
        origin_point = Point(*_wgs84_to_local(27.30, 36.95))
        validation = validate_polygon(corrupted, region, origin_point)
        assert not validation.ok
        assert validation.boundary_land_touches > 0

    def test_polygon_strictly_inside_water_passes_validation(self, geometry):
        region = NavigabilityRegion(
            water_polygons=geometry["water"],
            land_geometries=geometry["land"],
            clearance_m=50.0,
            resolution_m=RESOLUTION_M,
        )
        # A small box placed well inside the channel, nowhere near any coast.
        small = MultiPolygon([Polygon([
            _wgs84_to_local(27.28, 36.94),
            _wgs84_to_local(27.32, 36.94),
            _wgs84_to_local(27.32, 36.97),
            _wgs84_to_local(27.28, 36.97),
        ])])
        origin_point = Point(*_wgs84_to_local(27.30, 36.955))
        validation = validate_polygon(small, region, origin_point)
        assert validation.ok, validation.to_dict()


class TestFailClosedOrigin:
    """An origin outside navigable water must raise, never return a result
    computed from the nearest coastal node."""

    def test_origin_on_land_raises(self, geometry):
        with pytest.raises(RuntimeError):
            calculate_one_way_range(
                origin_wgs84=(27.40, 37.10),  # inside Bodrum mainland
                lpnm=LPNM,
                usable_fuel_l=60.0,
                resolution_m=RESOLUTION_M,
                clearance_m=50.0,
                water=geometry["water"],
                land=geometry["land"],
            )
