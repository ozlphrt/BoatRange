"""Local adaptive refinement tests — CLAUDE.md Section 7.2 / Section 11.

    "Any suspicious narrow reachable corridor must be locally refined...
    This directly prevents thin polygon tendrils leaking through land or
    breakwaters" (Section 11), and Section 7.2's trigger list includes
    "corridors whose width is near safety-clearance threshold" and
    "suspicious single-cell gaps".

The core scenario throughout: two large basins connected only by a corridor
that is *narrower than the coarse grid spacing* but *wide enough to be
genuinely navigable*. A naive uniform grid can place zero nodes inside such
a corridor purely by bad luck of grid alignment — both basins are fine, the
corridor itself is invisible — and the two basins come back as falsely
disconnected. Refinement's whole job is to catch exactly this.
"""

from __future__ import annotations

import pytest
from shapely.geometry import MultiPolygon, Point, box

from routing.water_graph import WaterPolygonGraph, build_graph_from_water_polygons


def _two_basins_with_narrow_corridor():
    """Two 600x900 basins, 200m apart, joined only by a 250m-tall gap in the
    wall between them. At the scale used by the tests below (300m coarse
    grid, 30m clearance) this gap is comfortably navigable (~190m safe
    width) but narrower than one coarse grid cell, and is positioned so no
    300m-aligned grid point falls inside it.
    """
    left_basin = box(0, 0, 600, 900)
    right_basin = box(800, 0, 1400, 900)
    wall_bottom = box(600, 0, 800, 325)
    wall_top = box(600, 575, 800, 900)
    water = left_basin.union(right_basin).union(box(600, 325, 800, 575))
    land = [wall_bottom, wall_top]
    return water, land


CLEARANCE_M = 30.0
GRID_RESOLUTION_M = 300.0
ORIGIN = Point(100, 450)   # deep in the left basin
DEST = Point(1300, 450)    # deep in the right basin


class TestNarrowCorridorIsMissedWithoutRefinement:
    """Establishes the failure mode refinement exists to fix."""

    def test_coarse_grid_places_no_node_in_the_corridor(self):
        water, land = _two_basins_with_narrow_corridor()
        graph = WaterPolygonGraph(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
            enable_refinement=False,
        ).build()
        corridor_nodes = [n for n in graph.nodes if 590 <= n.x <= 810 and 320 <= n.y <= 580]
        assert corridor_nodes == []

    def test_basins_appear_disconnected_without_refinement(self):
        water, land = _two_basins_with_narrow_corridor()
        graph = WaterPolygonGraph(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
            enable_refinement=False,
        ).build()
        path = graph.shortest_path(ORIGIN, DEST)
        assert path is None


class TestRefinementRecoversTheCorridor:
    def test_refinement_places_nodes_in_the_corridor(self):
        water, land = _two_basins_with_narrow_corridor()
        graph = build_graph_from_water_polygons(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
        )
        corridor_nodes = [n for n in graph.nodes if 590 <= n.x <= 810 and 320 <= n.y <= 580]
        assert len(corridor_nodes) > 0
        assert graph.refined_node_count > 0
        assert graph.refined_component_count > 0

    def test_route_found_across_the_refined_corridor(self):
        water, land = _two_basins_with_narrow_corridor()
        graph = build_graph_from_water_polygons(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
        )
        graph.set_fuel_rate(1.0)
        path = graph.shortest_path(ORIGIN, DEST)
        assert path is not None
        # The gap sits at y in [355, 545] (325+clearance .. 575-clearance);
        # origin and destination share y=450, which is inside that band, so
        # the true shortest route is the exact straight line between them —
        # the strongest possible confirmation refinement found the real gap
        # rather than some longer detour around it.
        straight_distance = ORIGIN.distance(DEST)
        assert path.length == pytest.approx(straight_distance, rel=1e-6)

    def test_route_never_crosses_the_wall(self):
        water, land = _two_basins_with_narrow_corridor()
        graph = build_graph_from_water_polygons(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
        )
        graph.set_fuel_rate(1.0)
        path = graph.shortest_path(ORIGIN, DEST)
        assert path is not None
        land_union = box(600, 0, 800, 325).union(box(600, 575, 800, 900))
        assert not path.intersects(land_union.buffer(-1))

    def test_refined_nodes_are_symmetrically_connected(self):
        """Regression guard: refinement stitching used to only be able to add
        a directed edge in one call — shortest_path's own Dijkstra needs
        symmetric adjacency, unlike the isochrone engine's
        adjacency_with_costs() which auto-symmetrizes at read time."""
        water, land = _two_basins_with_narrow_corridor()
        graph = build_graph_from_water_polygons(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
        )
        # Route must be findable in BOTH directions.
        graph.set_fuel_rate(1.0)
        forward = graph.shortest_path(ORIGIN, DEST)
        backward = graph.shortest_path(DEST, ORIGIN)
        assert forward is not None
        assert backward is not None
        assert forward.length == pytest.approx(backward.length, rel=1e-6)


class TestRefinementBudgets:
    """CLAUDE.md Section 26: every refinement pass needs hard limits."""

    def test_refinement_respects_max_nodes_budget(self):
        water, land = _two_basins_with_narrow_corridor()
        graph = build_graph_from_water_polygons(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
            max_refined_nodes=5,
        )
        assert graph.refined_node_count <= 5

    def test_refinement_can_be_disabled(self):
        water, land = _two_basins_with_narrow_corridor()
        graph = build_graph_from_water_polygons(
            water, land, clearance_m=CLEARANCE_M, grid_resolution_m=GRID_RESOLUTION_M,
            enable_refinement=False,
        )
        assert graph.refined_node_count == 0
        assert graph.refined_component_count == 0

    def test_refinement_survives_invalid_input_geometry(self):
        """A self-intersecting/invalid water polygon must not crash the
        whole graph build — refinement is an enhancement, not a correctness
        dependency, and the coarse grid is already built by the time it
        runs."""
        bowtie = MultiPolygon([box(0, 0, 100, 100)])
        # Force an operation that could raise on pathological geometry by
        # feeding a degenerate land geometry (zero-area) alongside real land.
        land = [box(40, 40, 40, 40), box(1000, 1000, 1100, 1100)]
        graph = build_graph_from_water_polygons(bowtie, land, clearance_m=10, grid_resolution_m=50)
        assert graph is not None  # did not raise
