"""Regression fixtures for gaps absent from the explicit obstacle layer."""

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, box

from routing.water_graph import build_graph_from_water_polygons


@pytest.mark.parametrize("gap", [20.0, 0.5])
@pytest.mark.parametrize("refinement", [False, True])
def test_disconnected_water_never_gets_a_cross_gap_edge(gap, refinement):
    water = MultiPolygon([
        box(-50, -50, 50 - gap / 2, 250),
        box(50 + gap / 2, -50, 250, 250),
    ])
    graph = build_graph_from_water_polygons(
        water, clearance_m=0, grid_resolution_m=100,
        enable_refinement=refinement,
    )
    assert graph.nodes
    assert graph.edges
    assert all(water.covers(edge.geometry) for edge in graph.edges)
    assert graph.shortest_path(Point(0, 100), Point(200, 100)) is None


def test_refinement_stitch_rejects_gap_without_registering_edge():
    water = MultiPolygon([box(-50, -50, 40, 150), box(60, -50, 250, 150)])
    graph = build_graph_from_water_polygons(
        water, clearance_m=0, grid_resolution_m=100, enable_refinement=False,
    )
    left = graph.nearest_node(Point(0, 0)).node_id
    right = graph.nearest_node(Point(100, 0)).node_id
    assert not graph._add_validated_edge(left, right)
    assert right not in graph._adjacency[left]
    assert left not in graph._adjacency[right]


def test_simplification_must_not_bridge_sub_meter_gap():
    water = MultiPolygon([box(-50, -50, 49.75, 150), box(50.25, -50, 250, 150)])
    graph = build_graph_from_water_polygons(
        water, clearance_m=0, grid_resolution_m=100, enable_refinement=False,
    )
    assert not graph._segment_is_clear((0, 0), (100, 0))


def test_open_water_route_keeps_exact_endpoints():
    water = MultiPolygon([box(-50, -50, 250, 250)])
    graph = build_graph_from_water_polygons(
        water, clearance_m=0, grid_resolution_m=100, enable_refinement=False,
    )
    route = graph.shortest_path(Point(10, 20), Point(180, 190))
    assert route.equals(LineString([(10, 20), (180, 190)]))
    assert water.covers(route)
