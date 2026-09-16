"""Vector-first water polygon graph routing engine.

This module implements the core routing approach: instead of raster cells,
we build a graph directly from pre-computed water polygons. Every edge is
validated against land geometry using exact intersection checks.

Key principle: if an edge intersects land or obstacles, it is rejected.
This guarantees that routes never cross blocked terrain.
"""

from .water_graph import (
    GraphNode,
    GraphEdge,
    WaterPolygonGraph,
    build_graph_from_water_polygons,
)

__all__ = [
    "GraphNode",
    "GraphEdge",
    "WaterPolygonGraph",
    "build_graph_from_water_polygons",
]
