"""Tests for vector-first water polygon graph construction.

This is the core of our approach: instead of raster cells, we build a graph
directly from pre-computed water polygons. Routes on this graph are guaranteed
to stay in water because every edge is validated against land geometry.
"""

import pytest
from shapely.geometry import Point, LineString, Polygon, MultiPolygon
from shapely.ops import unary_union

from routing.water_graph import (
    WaterPolygonGraph,
    GraphNode,
    GraphEdge,
    build_graph_from_water_polygons,
)


# ============================================================================
# Fixtures — Simple synthetic geography for testing
# ============================================================================


def make_simple_island():
    """Create a simple circular island polygon."""
    return Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])


def make_water_with_island():
    """Water area with a rectangular island in the middle.
    
    Water = large rectangle minus island hole.
    This simulates a simple island scenario.
    """
    outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])
    island = make_simple_island()
    # Water is the outer polygon with the island as a hole
    water = Polygon(outer.exterior, [island.exterior])
    return MultiPolygon([water]), island


def make_narrow_channel():
    """Two land masses with a narrow channel between them.
    
    Land left: x < -10
    Land right: x > 10
    Channel: -10 < x < 10 (width = 20m)
    """
    land_left = Polygon([(-100, -100), (-10, -100), (-10, 100), (-100, 100)])
    land_right = Polygon([(10, -100), (100, -100), (100, 100), (10, 100)])
    
    # Water is everything between the two land masses
    water = Polygon([(-10, -100), (10, -100), (10, 100), (-10, 100)])
    
    return MultiPolygon([water]), unary_union([land_left, land_right])


def make_breakwater():
    """A breakwater that blocks direct passage.

    Simulates a harbor wall — water on both sides but no crossing through the wall.

    Regression note: this used to make the breakwater only ``y in [-100, 100]``
    while the water body extends to ``y=200`` — a genuine 100-unit-wide gap
    of open water above the wall's tip. The coarse routing graph missed that
    gap (grid nodes exactly on the water polygon's y=200 boundary edge were
    rejected by the strict-interior `_is_in_water` check, and no interior
    grid line at the default 100-unit spacing landed inside the gap either),
    so ``shortest_path`` happened to return ``None`` — but only because of
    that sampling gap, not because the geometry was actually impassable.
    Once local refinement (CLAUDE.md Section 7.2/11) started finding real
    navigable water the coarse grid missed, it correctly found a route
    around the wall's open top. The fixture now spans the full water height
    so "no route exists" is actually true, matching this function's own
    docstring intent.
    """
    # Outer water boundary
    outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])

    # Breakwater as a line-like polygon (thin rectangle) — full height of the
    # water body, so there is genuinely no way around it.
    breakwater = Polygon([(50, -100), (60, -100), (60, 200), (50, 200)])

    land = Polygon([(-100, -100), (50, -100), (50, 200), (-100, 200)])

    # Water split into two regions by breakwater
    water_left = Polygon(outer.exterior, [breakwater.exterior])
    water_right = Polygon([(60, -100), (200, -100), (200, 200), (60, 200)])

    return MultiPolygon([water_left, water_right]), breakwater, land


class TestBuildGraphFromWaterPolygons:
    """Test graph construction from water polygon geometry."""

    def test_graph_has_nodes(self):
        """Graph constructed from valid water polygons has nodes."""
        outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])
        water = MultiPolygon([outer])
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        assert len(graph.nodes) > 0

    def test_graph_has_edges(self):
        """Graph constructed from valid water polygons has edges."""
        outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])
        water = MultiPolygon([outer])
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        assert len(graph.edges) > 0

    def test_empty_water_no_nodes(self):
        """Empty water polygon produces empty graph."""
        water = MultiPolygon()
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        assert len(graph.nodes) == 0
        assert len(graph.edges) == 0

    def test_clearance_reduces_navigable_area(self):
        """Higher clearance reduces the navigable area."""
        outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])
        water = MultiPolygon([outer])
        
        graph_tight = build_graph_from_water_polygons(water, clearance_m=2)
        graph_wide = build_graph_from_water_polygons(water, clearance_m=50)
        
        # Tighter clearance should have fewer nodes (smaller navigable area)
        assert len(graph_tight.nodes) >= len(graph_wide.nodes)


class TestEdgeValidation:
    """Test that edges crossing land are rejected."""

    def test_edge_in_open_water_valid(self):
        """An edge entirely within open water is valid."""
        outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])
        water = MultiPolygon([outer])
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        # All edges should be valid (no land to cross)
        for edge in graph.edges:
            assert edge.is_valid is True

    def test_edge_crossing_island_rejected(self):
        """An edge crossing an island must be rejected."""
        water, island = make_water_with_island()
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        # No edge should cross the island
        for edge in graph.edges:
            assert not edge.geometry.intersects(island.buffer(0.1)), \
                f"Edge {edge.geometry} crosses island"

    def test_no_edge_crosses_land_buffer(self):
        """No valid edge should intersect a land buffer."""
        water, island = make_water_with_island()
        
        land_buffer = island.buffer(5)  # 5m clearance
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        for edge in graph.edges:
            assert not edge.geometry.intersects(land_buffer), \
                f"Edge {edge.geometry} intersects land buffer"


class TestIslandShadow:
    """Test that paths go around islands, not through them."""

    def test_path_wraps_island(self):
        """A route between points on opposite sides of an island wraps around it."""
        water, island = make_water_with_island()
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        # Origin: left of island
        origin = Point(-50, 5)
        # Destination: right of island  
        destination = Point(150, 5)
        
        path = graph.shortest_path(origin, destination)
        
        assert path is not None
        
        # The path should NOT go through the island
        for coord in path.coords:
            assert not island.contains(Point(coord)), \
                f"Path goes through island at {coord}"


class TestNarrowChannel:
    """Test narrow channel passability."""

    def test_channel_is_passable(self):
        """A valid narrow channel allows passage."""
        water, land = make_narrow_channel()
        
        graph = build_graph_from_water_polygons(water, clearance_m=2)
        
        origin = Point(0, -50)
        destination = Point(0, 50)
        
        path = graph.shortest_path(origin, destination)
        
        assert path is not None
        
        # Path should stay within the channel (x between -8 and 8 with clearance)
        for coord in path.coords:
            assert -8 <= coord[0] <= 8, \
                f"Path exits channel at x={coord[0]}"


class TestBreakwaterBlocking:
    """Test that breakwaters block direct passage."""

    def test_breakwater_blocks_direct_path(self):
        """A breakwater prevents a straight-line path through it."""
        water, breakwater, land = make_breakwater()
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        # Origin: left of breakwater
        origin = Point(0, 0)
        # Destination: right of breakwater (unreachable directly)
        destination = Point(150, 0)
        
        path = graph.shortest_path(origin, destination)
        
        # Path should be None — no valid route through breakwater
        assert path is None


class TestGraphEdgeCost:
    """Test edge cost calculation."""

    def test_edge_distance_is_correct(self):
        """Edge distance matches the geometric length of the segment."""
        outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])
        water = MultiPolygon([outer])
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        for edge in graph.edges:
            expected_dist = edge.geometry.length
            assert edge.distance_m == pytest.approx(expected_dist, rel=1e-6)

    def test_edge_cost_includes_fuel_factor(self):
        """Edge cost is distance * fuel consumption rate."""
        outer = Polygon([(-100, -100), (200, -100), (200, 200), (-100, 200)])
        water = MultiPolygon([outer])
        
        graph = build_graph_from_water_polygons(water, clearance_m=5)
        
        lpnm = 2.19  # L/nm at 4000 RPM
        graph.set_fuel_rate(lpnm)
        
        for edge in graph.edges:
            expected_cost = (edge.distance_m / 1852) * lpnm  # convert m to nm
            assert edge.fuel_cost_l == pytest.approx(expected_cost, rel=1e-3)


class TestExactQueryNode:
    """A valid off-grid origin must join the routed water component."""

    def test_off_grid_origin_is_inserted_and_connected(self):
        water = MultiPolygon([
            Polygon([(-50, -50), (450, -50), (450, 450), (-50, 450)])
        ])
        graph = build_graph_from_water_polygons(
            water,
            clearance_m=0,
            grid_resolution_m=100,
            enable_refinement=False,
        )

        origin = Point(37, 63)
        node = graph.add_query_node(origin, max_connection_distance_m=250)

        assert node is not None
        assert (node.x, node.y) == pytest.approx((37, 63))
        assert graph._adjacency[node.node_id]
        assert all(
            graph._segment_is_clear(
                (node.x, node.y),
                (graph.nodes[neighbor].x, graph.nodes[neighbor].y),
            )
            for neighbor in graph._adjacency[node.node_id]
        )
