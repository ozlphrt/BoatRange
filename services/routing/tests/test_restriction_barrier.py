"""Hard legal polygons use the same exact collision path as physical land."""

from shapely.geometry import LineString, MultiPolygon, Polygon

from routing.water_graph import build_graph_from_water_polygons


def test_hard_restriction_polygon_blocks_every_candidate_edge():
    water = MultiPolygon([Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])])
    restriction = Polygon([(450, 0), (550, 0), (550, 1000), (450, 1000)])
    graph = build_graph_from_water_polygons(
        water_polygons=water,
        land_geometries=[restriction],
        clearance_m=25,
        grid_resolution_m=100,
    )
    blocked = restriction.buffer(25)
    assert graph.edges
    assert all(not edge.geometry.intersects(blocked) for edge in graph.edges)
    assert all(not LineString([(edge.from_node.x, edge.from_node.y), (edge.to_node.x, edge.to_node.y)]).intersects(blocked) for edge in graph.edges)
