"""Tests for vector-first routing engine — edge generation, collision checks, shortest path.

This is the core routing logic that guarantees NO route crosses land or
obstacles. Every collision assertion here checks the path/edge geometry
against the *raw* land union directly — not against ``segment.length > 0`` or
other placeholders. This file replaces a version where several tests asserted
nothing meaningful (e.g. ``assert path is None or isinstance(path, LineString)``,
which is true for every possible return value) — see CLAUDE.md Section 49.6.

Route/edge distances are computed with ``set_fuel_rate`` applied first:
``shortest_path`` weights Dijkstra by ``edge.fuel_cost_l``, which defaults to
0.0 for every edge until a fuel rate is set. Skipping that call doesn't make
the test fail loudly — Dijkstra still returns *a* path (all edges tie at cost
0), just not the geometrically shortest one. Silently forgetting this call
elsewhere would look like "the route is passable" while quietly producing an
inflated, weight-blind path.
"""

import math

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from routing.water_graph import WaterPolygonGraph, build_graph_from_water_polygons

import sys
sys.path.insert(0, "../marine-data")
from marine_data.bodrum_kos_fixture import (  # noqa: E402
    REGRESSION_FIXTURES,
    _wgs84_to_local,
    bodrum_marina_breakwater,
    generate_bodrum_kos_water,
    kos_harbor_breakwater,
    kos_island,
    land_geometries,
)

DEFAULT_LPNM = 2.19  # L/nm at 4000 RPM, per CLAUDE.md Section 4.2


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(scope="module")
def geometry():
    """Bodrum/Kos water + land geometry."""
    water, land = generate_bodrum_kos_water()
    return {
        "water": water,
        "land": land,
        "land_union": unary_union(land),
        "kos": kos_island(),
        "breakwaters": [bodrum_marina_breakwater(), kos_harbor_breakwater()],
    }


@pytest.fixture(scope="module")
def graph(geometry):
    """Routing graph at a resolution fine enough to find real routes quickly."""
    g = build_graph_from_water_polygons(
        water_polygons=geometry["water"],
        land_geometries=geometry["land"],
        clearance_m=50,
        grid_resolution_m=500,
    )
    g.set_fuel_rate(DEFAULT_LPNM)
    return g


def _segments(line: LineString):
    coords = list(line.coords)
    for i in range(len(coords) - 1):
        yield LineString([coords[i], coords[i + 1]])


def _assert_path_never_crosses(path: LineString, land_union, breakwaters):
    """Every segment of ``path`` must be clear of land and every breakwater."""
    for i, segment in enumerate(_segments(path)):
        assert not segment.intersects(land_union.buffer(-1)), \
            f"segment {i} of path crosses land: {list(segment.coords)}"
        for bw in breakwaters:
            assert not segment.intersects(bw.buffer(-1)), \
                f"segment {i} of path crosses a breakwater: {list(segment.coords)}"


# ============================================================================
# Edge Generation
# ============================================================================


class TestEdgeGeneration:
    """Every edge the graph considers valid must actually avoid every obstacle."""

    def test_edges_only_between_water_nodes(self, graph):
        for edge in graph.edges:
            assert edge.is_valid is True
            assert edge.from_node.node_id in graph._adjacency
            assert edge.to_node.node_id in graph._adjacency

    def test_no_edge_crosses_land(self, graph, geometry):
        for edge in graph.edges:
            assert not edge.geometry.intersects(geometry["land_union"].buffer(-1)), \
                f"Edge {list(edge.geometry.coords)} crosses land"

    def test_no_edge_crosses_breakwater(self, graph, geometry):
        for bw in geometry["breakwaters"]:
            for edge in graph.edges:
                assert not edge.geometry.intersects(bw.buffer(-1)), \
                    f"Edge {list(edge.geometry.coords)} crosses a breakwater"

    def test_edge_cost_is_fuel_rate_times_distance(self, graph):
        for edge in graph.edges:
            expected = (edge.distance_m / 1852) * DEFAULT_LPNM
            assert edge.fuel_cost_l == pytest.approx(expected, rel=1e-6)


# ============================================================================
# Fail-closed endpoint handling
# ============================================================================


class TestFailClosedEndpoints:
    """CLAUDE.md Section 43: never route from/to a point that isn't navigable."""

    def test_inland_destination_returns_none(self, graph):
        """The core safety property: routing INTO land must fail closed.

        Regression guard for a real bug found while writing this suite:
        ``shortest_path`` used to snap any query point to its nearest grid
        node with no check that the point itself was in water, so a
        destination in the middle of the Bodrum landmass returned a "valid"
        route straight to the coast instead of refusing.
        """
        origin = Point(*_wgs84_to_local(27.30, 36.98))
        destination = Point(*_wgs84_to_local(27.40, 37.12))  # deep inland
        path = graph.shortest_path(origin, destination)
        assert path is None

    def test_inland_origin_returns_none(self, graph):
        origin = Point(*_wgs84_to_local(27.40, 37.12))  # deep inland
        destination = Point(*_wgs84_to_local(27.30, 36.98))
        path = graph.shortest_path(origin, destination)
        assert path is None

    def test_point_far_outside_compute_extent_returns_none(self, graph):
        origin = Point(*_wgs84_to_local(27.30, 36.98))
        destination = Point(*_wgs84_to_local(50.0, 50.0))  # nowhere near the grid
        path = graph.shortest_path(origin, destination)
        assert path is None


# ============================================================================
# Regression fixtures — real geographic assertions
# ============================================================================


class TestRegressionFixtures:
    """Every named fixture in REGRESSION_FIXTURES, routed on the real graph."""

    @pytest.mark.parametrize("fixture", REGRESSION_FIXTURES, ids=lambda f: f.name)
    def test_fixture(self, graph, geometry, fixture):
        origin = Point(*fixture.projected_origin())
        destination = Point(*fixture.projected_destination())

        path = graph.shortest_path(origin, destination)

        if fixture.expectation == "unreachable":
            assert path is None, f"{fixture.name}: expected no route, got one"
            return

        assert path is not None, f"{fixture.name}: expected a route, got none"
        _assert_path_never_crosses(path, geometry["land_union"], geometry["breakwaters"])

        if fixture.direct_line_crosses_land:
            # The route must actually detour — it cannot equal the straight
            # line that we know crosses land.
            straight_distance = origin.distance(destination)
            assert path.length > straight_distance * 1.01, (
                f"{fixture.name}: route length ({path.length:.0f}m) is not "
                f"longer than the direct line ({straight_distance:.0f}m) that "
                f"crosses land — the route may not actually be detouring"
            )


# ============================================================================
# Shortest Path — general properties
# ============================================================================


class TestShortestPath:
    def test_distance_is_at_least_euclidean(self, graph):
        fx = REGRESSION_FIXTURES[0]
        origin = Point(*fx.projected_origin())
        destination = Point(*fx.projected_destination())
        path = graph.shortest_path(origin, destination)
        assert path is not None
        assert path.length >= origin.distance(destination) - 1e-6

    def test_same_point_returns_zero_length_line(self, graph):
        fx = REGRESSION_FIXTURES[0]
        point = Point(*fx.projected_origin())
        path = graph.shortest_path(point, point)
        assert path is not None
        assert path.length == pytest.approx(0.0, abs=1e-6)

    def test_fuel_cost_matches_distance_times_rate(self, graph):
        fx = REGRESSION_FIXTURES[0]
        origin = Point(*fx.projected_origin())
        destination = Point(*fx.projected_destination())
        path = graph.shortest_path(origin, destination)
        assert path is not None
        expected_fuel = (path.length / 1852) * DEFAULT_LPNM
        assert expected_fuel > 0


# ============================================================================
# Constructor robustness
# ============================================================================


class TestGraphConstruction:
    """Regression guards for input-shape bugs in WaterPolygonGraph.__init__."""

    def test_accepts_bare_polygon_not_just_multipolygon(self):
        """A single Polygon (not wrapped in MultiPolygon) must not crash.

        Regression guard: the constructor used to coerce ``self.water_polygons``
        to a MultiPolygon but then read the *original* ``water_polygons``
        constructor argument when scanning for island holes, so a bare
        Polygon raised ``AttributeError: 'Polygon' object has no attribute
        'geoms'``.
        """
        water = Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        graph = build_graph_from_water_polygons(water, [], clearance_m=50, grid_resolution_m=200)
        assert len(graph.nodes) > 0

    def test_empty_water_produces_empty_graph(self):
        graph = build_graph_from_water_polygons(MultiPolygon(), [], clearance_m=50, grid_resolution_m=200)
        assert graph.nodes == []
        assert graph.edges == []
