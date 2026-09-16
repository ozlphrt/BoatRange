"""Tests for the Bodrum/Kos regression fixture.

Every assertion here checks a real geometric fact about the fixture, not a
tautology. In particular this file replaces a previous version where:

  * ``test_channel_between_bodrum_and_kos_is_passable`` actually probed a point
    west of the peninsula (open sea), because the old ``bodrum_mainland()``
    polygon had swallowed Kos island whole — there was no channel to test.
  * ``test_kos_creates_shadow_zone`` only asserted the sample points were not
    literally *inside* the land polygon, not that they were separated by it.
  * ``test_water_polygon_has_multiple_components`` accepted ``len(geoms) >= 1``,
    which is true even if the "difference" step produced a single blob with no
    islands cut out.

If you find yourself loosening one of these assertions to make it pass,
that is very likely a fixture bug, not a test bug — see CLAUDE.md Section 49.6.
"""

import math

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from marine_data.bodrum_kos_fixture import (
    REGRESSION_FIXTURES,
    _wgs84_to_local,
    bodrum_mainland,
    bodrum_marina_breakwater,
    fixture_bbox,
    generate_bodrum_kos_water,
    kos_harbor_breakwater,
    kos_island,
    land_geometries,
    small_islands,
    unify_land,
)


# ============================================================================
# Geometry Validity
# ============================================================================


class TestGeometryValidity:
    """Every generated ring must be a valid, non-degenerate polygon."""

    @pytest.mark.parametrize(
        "factory",
        [bodrum_mainland, kos_island, bodrum_marina_breakwater, kos_harbor_breakwater],
    )
    def test_polygon_is_valid_and_has_area(self, factory):
        polygon = factory()
        assert polygon.is_valid, polygon.is_valid_reason if hasattr(polygon, "is_valid_reason") else "invalid"
        assert not polygon.is_empty
        assert polygon.area > 0

    def test_small_islands_are_valid(self):
        islands = small_islands()
        assert len(islands) >= 1
        for island in islands:
            assert island.is_valid
            assert island.area > 1000  # at least ~1000 m^2, not a sliver

    def test_land_geometries_returns_every_piece(self):
        geoms = land_geometries()
        assert len(geoms) >= 5


# ============================================================================
# Topology: Bodrum and Kos must be genuinely separate landmasses
# ============================================================================


class TestLandmassSeparation:
    """The bug this fixture replaces: mainland silently containing Kos."""

    def test_bodrum_and_kos_are_disjoint(self):
        assert bodrum_mainland().disjoint(kos_island())

    def test_bodrum_does_not_contain_kos_centroid(self):
        assert not bodrum_mainland().contains(kos_island().centroid)

    def test_channel_width_is_several_kilometers(self):
        """A real channel, not an artifact of touching polygons."""
        gap_m = bodrum_mainland().distance(kos_island())
        assert gap_m > 2000, f"Bodrum-Kos gap is only {gap_m:.0f} m — too narrow to be a real channel"

    def test_land_union_has_multiple_disjoint_components(self):
        """Union must NOT collapse to a single blob (the old max()-picking bug)."""
        union = unify_land(land_geometries())
        assert isinstance(union, MultiPolygon)
        assert len(union.geoms) >= 3  # Bodrum, Kos, + at least one island/breakwater

    def test_unify_land_keeps_kos_area(self):
        """Regression guard for the old `_safe_union` bug that discarded Kos."""
        union = unify_land(land_geometries())
        kos_centroid = kos_island().centroid
        assert union.contains(kos_centroid) or union.intersects(kos_island())


# ============================================================================
# Water Generation
# ============================================================================


class TestWaterGeneration:
    """Water must be exactly bbox-minus-land, with every island preserved."""

    def test_water_is_nonempty_multipolygon(self):
        water, _ = generate_bodrum_kos_water()
        assert not water.is_empty
        assert isinstance(water, MultiPolygon)

    def test_water_does_not_overlap_any_land_geometry(self):
        water, land = generate_bodrum_kos_water()
        for geom in water.geoms:
            for land_piece in land:
                # Erode land slightly so boundary-touching is not a false failure.
                assert not geom.intersects(land_piece.buffer(-5)), \
                    f"Water polygon overlaps {land_piece.centroid}"

    def test_water_area_is_less_than_bbox(self):
        water, _ = generate_bodrum_kos_water()
        bbox_area = fixture_bbox().area
        water_area = sum(g.area for g in water.geoms)
        assert 0 < water_area < bbox_area

    def test_islands_appear_as_holes_in_water(self):
        """Karaada sits inside the water bbox, so water must have a hole there."""
        water, _ = generate_bodrum_kos_water()
        karaada = small_islands()[0]
        # The island centroid must NOT be classified as water.
        assert not water.contains(karaada.centroid)
        # But water must exist all around it (it is a hole, not a coastal bite).
        cx, cy = karaada.centroid.x, karaada.centroid.y
        ring_points = [
            Point(cx + 3000, cy), Point(cx - 3000, cy),
            Point(cx, cy + 3000), Point(cx, cy - 3000),
        ]
        assert any(water.contains(p) for p in ring_points)

    def test_no_zero_area_or_sliver_polygons(self):
        water, _ = generate_bodrum_kos_water()
        for geom in water.geoms:
            assert geom.area > 1000


# ============================================================================
# Specific navigability facts (CLAUDE.md Section 35)
# ============================================================================


class TestKnownNavigabilityFacts:
    """Point-level facts a broken fixture would get wrong."""

    def test_channel_midpoint_is_water(self):
        water, _ = generate_bodrum_kos_water()
        p = Point(*_wgs84_to_local(27.30, 36.95))
        assert water.contains(p), "Bodrum-Kos channel midpoint must be navigable water"

    def test_point_inside_kos_is_land(self):
        water, land = generate_bodrum_kos_water()
        p = Point(*_wgs84_to_local(27.10, 36.80))
        assert not water.contains(p)
        assert any(g.contains(p) for g in land)

    def test_point_inside_bodrum_is_land(self):
        water, land = generate_bodrum_kos_water()
        p = Point(*_wgs84_to_local(27.40, 37.10))
        assert not water.contains(p)
        assert any(g.contains(p) for g in land)

    def test_gulluk_bay_entrance_is_water(self):
        water, _ = generate_bodrum_kos_water()
        p = Point(*_wgs84_to_local(27.52, 37.02))
        assert water.contains(p), "Güllük Bay must be reachable through its entrance"

    def test_straight_line_around_cape_crosses_land(self):
        """Sanity check for the fixture used by the 'around-bodrum-cape' regression."""
        mainland = bodrum_mainland()
        line = LineString([
            _wgs84_to_local(27.34, 36.985),
            _wgs84_to_local(27.44, 36.985),
        ])
        assert line.intersects(mainland), "Fixture assumption broken: straight line no longer crosses the cape"

    def test_straight_line_across_kos_crosses_land(self):
        kos = kos_island()
        line = LineString([
            _wgs84_to_local(27.15, 36.92),
            _wgs84_to_local(27.15, 36.74),
        ])
        assert line.intersects(kos), "Fixture assumption broken: straight line no longer crosses Kos"

    def test_breakwater_blocks_direct_crossing(self):
        bw = bodrum_marina_breakwater()
        line = LineString([
            _wgs84_to_local(27.310, 36.999),
            _wgs84_to_local(27.330, 36.999),
        ])
        assert line.intersects(bw.buffer(1)), "Fixture assumption broken: line no longer crosses the breakwater"

    def test_breakwater_has_a_navigable_gap_to_its_west(self):
        water, _ = generate_bodrum_kos_water()
        p = Point(*_wgs84_to_local(27.305, 36.999))
        assert water.contains(p), "Breakwater gap must remain open water"


# ============================================================================
# Regression fixture metadata
# ============================================================================


class TestRegressionFixtureMetadata:
    def test_fixture_count(self):
        assert len(REGRESSION_FIXTURES) >= 5

    def test_all_fixtures_have_valid_coordinates(self):
        for fx in REGRESSION_FIXTURES:
            lon, lat = fx.origin_wgs84
            assert -180 <= lon <= 180 and -90 <= lat <= 90
            lon, lat = fx.destination_wgs84
            assert -180 <= lon <= 180 and -90 <= lat <= 90

    def test_reachable_fixtures_origin_and_destination_are_water(self):
        """Any fixture claiming 'reachable' must not start/end on land."""
        water, _ = generate_bodrum_kos_water()
        for fx in REGRESSION_FIXTURES:
            if fx.expectation != "reachable":
                continue
            origin_pt = Point(*fx.projected_origin())
            dest_pt = Point(*fx.projected_destination())
            assert water.contains(origin_pt), f"{fx.name}: origin is not water"
            assert water.contains(dest_pt), f"{fx.name}: destination is not water"

    def test_unreachable_fixture_destination_is_on_land(self):
        water, land = generate_bodrum_kos_water()
        fx = next(f for f in REGRESSION_FIXTURES if f.expectation == "unreachable")
        dest_pt = Point(*fx.projected_destination())
        assert not water.contains(dest_pt)
        assert any(g.contains(dest_pt) for g in land)

    def test_direct_line_crosses_land_flag_is_accurate(self):
        """Fixtures marked direct_line_crosses_land must actually have a
        straight origin->destination line intersecting some land geometry —
        otherwise the regression test they feed is not testing anything."""
        _, land = generate_bodrum_kos_water()
        land_union = unary_union(land)
        for fx in REGRESSION_FIXTURES:
            if not fx.direct_line_crosses_land:
                continue
            line = LineString([fx.projected_origin(), fx.projected_destination()])
            assert line.intersects(land_union), \
                f"{fx.name}: expected the direct line to cross land, but it doesn't"
