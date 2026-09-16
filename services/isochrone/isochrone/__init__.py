"""Isochrone engine — fuel-limited reachable-area computation.

Implements the core isochrone pipeline per CLAUDE.md Sections 5, 7, 8, 9, 10, 12:

1. Build a water-only routing graph (from services/routing) whose edges never
   cross land, islands, breakwaters or hard obstacles.
2. Expand a fuel cost field from the origin with Dijkstra
   ("minimum_fuel_cost(origin -> cell) <= usable_fuel") — one-way — or two
   independent cost fields (outbound + return) combined as
   "outbound_cost + return_cost <= usable_fuel" — round-trip.
3. Extract the 25/50/75/100% fuel bands.
4. Turn each band into a water-clipped polygon that wraps around islands and
   peninsulas (never a circle, great-circle radius or convex hull).
5. Validate every polygon (no land leak, origin inside) and run an anomaly
   check before returning.

Public API:

    IsochroneEngine            — low-level, works on projected meters
    calculate_range            — end-to-end from a WGS84 origin, one_way or round_trip
    calculate_one_way_range    — convenience alias, range_mode="one_way"
    calculate_round_trip_range — convenience alias, range_mode="round_trip"
    IsochroneResult / BandResult — typed results
    NavigabilityRegion         — provider-agnostic passable-water geometry
    LocalProjection            — WGS84 <-> local meters
"""

from .cost_field import (
    CostField,
    compute_cost_field,
    compute_return_cost_field,
    reverse_adjacency,
    reconstruct_path,
    get_reachable_nodes,
)
from .navigability import NavigabilityRegion
from .projection import LocalProjection
from .polygon_utils import (
    build_region_polygon,
    cleanup_polygon,
    validate_polygon,
    PolygonValidation,
)
from .engine import (
    IsochroneEngine,
    IsochroneResult,
    BandResult,
    calculate_range,
    calculate_one_way_range,
    calculate_round_trip_range,
    THRESHOLD_FRACTIONS,
    RANGE_MODES,
)

__all__ = [
    "CostField",
    "compute_cost_field",
    "compute_return_cost_field",
    "reverse_adjacency",
    "reconstruct_path",
    "get_reachable_nodes",
    "NavigabilityRegion",
    "LocalProjection",
    "build_region_polygon",
    "cleanup_polygon",
    "validate_polygon",
    "PolygonValidation",
    "IsochroneEngine",
    "IsochroneResult",
    "BandResult",
    "calculate_range",
    "calculate_one_way_range",
    "calculate_round_trip_range",
    "THRESHOLD_FRACTIONS",
    "RANGE_MODES",
]
