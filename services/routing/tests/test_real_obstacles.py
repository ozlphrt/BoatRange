"""Real OSM obstacle snapshot integration with graph edge validation."""

from shapely.geometry import MultiPolygon, box

from marine_data.features import RestrictionClass, load_features_projected
from routing.water_graph import build_graph_from_water_polygons


def _identity(x, y):
    return x, y


def test_real_breakwater_blocks_every_intersecting_candidate_edge():
    features = load_features_projected(_identity)
    breakwater = max(
        (
            feature.geometry
            for feature in features
            if feature.feature_type == "breakwater"
            and feature.restriction_class is RestrictionClass.HARD_NO_GO
        ),
        key=lambda geometry: geometry.length,
    )
    minx, miny, maxx, maxy = breakwater.bounds
    pad = max(maxx - minx, maxy - miny, 0.002)
    water = MultiPolygon([box(minx - pad, miny - pad, maxx + pad, maxy + pad)])
    clearance = pad / 20
    graph = build_graph_from_water_polygons(
        water,
        land_geometries=[breakwater],
        clearance_m=clearance,
        grid_resolution_m=pad / 10,
        enable_refinement=False,
    )
    blocked = breakwater.buffer(clearance)
    assert graph.edges
    assert all(not edge.geometry.intersects(blocked) for edge in graph.edges)

