"""One-way isochrone engine — fuel cost-field expansion to reachable polygons.

This is the real Phase-5 engine (CLAUDE.md Section 5, Section 7, Section 8).

It ties the three independent subsystems together:

    marine data  ->  NavigabilityRegion  (what is passable water)
    fuel model   ->  effective L/nm      (cost per nautical mile)
    routing      ->  WaterPolygonGraph   (water-only graph, no land crossings)

and then:

    1. crops the water to a compute extent around the origin,
    2. builds the water graph and validates every edge against land,
    3. expands a fuel cost field from the origin with Dijkstra
       (CLAUDE.md Section 8: "minimum_fuel_cost(origin -> cell) <= usable_fuel"),
    4. extracts the 25/50/75/100% fuel bands,
    5. turns each band into a water-clipped polygon,
    6. validates every polygon (no land leak, origin inside) and runs an
       anomaly check before returning.

Per CLAUDE.md Section 44 the result is NEVER a circle, great-circle radius,
convex hull or land-clipped-after-the-fact approximation — it is the exact
set of grid cells reachable along water within the fuel budget, clipped to
water.
"""

from __future__ import annotations

import time
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from shapely.geometry import Point, Polygon, MultiPolygon

from .navigability import NavigabilityRegion
from .polygon_utils import build_region_polygon, validate_polygon, PolygonValidation
from .cost_field import compute_cost_field, compute_return_cost_field
from routing.water_graph import build_graph_from_water_polygons

# Fuel bands requested per CLAUDE.md Section 3.4 / Section 8.
THRESHOLD_FRACTIONS: List[float] = [0.25, 0.50, 0.75, 1.0]

# CLAUDE.md Section 3.3 / Section 9.
RANGE_MODES = ("one_way", "round_trip")

# Default hard limits (CLAUDE.md Section 26). Kept generous for v1; the engine
# exposes them so callers can tighten budgets.
DEFAULT_MAX_CELL_COUNT = 400_000
# Covers the Axopar profile's full ~94 nm one-way envelope with a small graph
# margin. The former 60 km cap visibly cut otherwise reachable water into a
# square even when the fuel budget and marine data extended much farther.
DEFAULT_MAX_EXTENT_M = 200_000.0


def _as_multipolygon(geom) -> MultiPolygon:
    """Coerce any geometry to a MultiPolygon, preserving island holes.

    Cropping water with ``intersection`` can return a single ``Polygon`` (or a
    ``GeometryCollection``); the routing graph only understands
    ``MultiPolygon``. Holes (islands) are preserved through the conversion.
    """
    if geom.is_empty:
        return MultiPolygon()
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    polys = [g for g in geom.geoms if isinstance(g, Polygon)]
    return MultiPolygon(polys) if polys else MultiPolygon()


@dataclass
class BandResult:
    """One fuel band: fraction, reachable node count, polygon and cost ceiling."""

    fraction: float
    node_count: int
    max_cost: float
    polygon: MultiPolygon
    validation: PolygonValidation

    def to_dict(self) -> dict:
        return {
            "percentage": int(self.fraction * 100),
            "node_count": self.node_count,
            "max_fuel_cost_l": round(self.max_cost, 4),
            "area_m2": round(self.polygon.area, 2),
            "valid": self.validation.ok,
            "validation": self.validation.to_dict(),
        }


@dataclass
class IsochroneResult:
    """Full isochrone result — one-way or round-trip (see ``range_mode``)."""

    origin_projected: tuple[float, float]
    origin_wgs84: Optional[tuple[float, float]]
    bands: Dict[float, BandResult]
    usable_fuel_l: float
    lpnm: float
    operating_point: Optional[dict]
    max_range_nm: float
    metrics: dict
    warnings: List[str]
    anomalies: List[str]
    all_polygons_valid: bool
    range_mode: str = "one_way"
    debug_grid: Optional[list[dict]] = None

    def to_dict(self) -> dict:
        return {
            "origin_projected": list(self.origin_projected),
            "origin_wgs84": list(self.origin_wgs84) if self.origin_wgs84 else None,
            "range_mode": self.range_mode,
            "usable_fuel_l": round(self.usable_fuel_l, 3),
            "lpnm": round(self.lpnm, 4),
            "operating_point": self.operating_point,
            "max_range_nm": round(self.max_range_nm, 3),
            "bands": {str(k): v.to_dict() for k, v in sorted(self.bands.items())},
            "all_polygons_valid": self.all_polygons_valid,
            "anomalies": list(self.anomalies),
            "warnings": list(self.warnings),
            "metrics": self.metrics,
            "debug_grid": self.debug_grid,
        }


class IsochroneEngine:
    """One-way fuel-limited reachable-area engine.

    Args:
        region: Classified navigable-water geometry for the compute extent.
        origin_x / origin_y: Origin in the same projected CRS as ``region``.
        lpnm: Effective liters-per-nautical-mile cost (already includes load and
            sea-state factors).
        resolution_m: Base grid resolution for the routing graph.
        clearance_m: Safety clearance (defaults to ``region.clearance_m``).
    """

    def __init__(
        self,
        region: NavigabilityRegion,
        origin_x: float,
        origin_y: float,
        lpnm: float,
        resolution_m: float = 1000.0,
        clearance_m: Optional[float] = None,
        max_extent_m: float = DEFAULT_MAX_EXTENT_M,
    ) -> None:
        if lpnm <= 0:
            raise ValueError("lpnm must be positive")

        self.region = region
        self.origin_x = float(origin_x)
        self.origin_y = float(origin_y)
        self.lpnm = float(lpnm)
        self.resolution_m = float(resolution_m)
        self.clearance_m = clearance_m if clearance_m is not None else region.clearance_m
        self.max_extent_m = float(max_extent_m)

        self._graph = None
        self._cost_field: Optional["object"] = None  # set in compute()
        self._origin_id: Optional[int] = None

    # ------------------------------------------------------------------ graph

    def _compute_extent(self, max_range_m: float) -> Polygon:
        """Bounding box around the origin covering the full fuel range.

        The navigable range can never exceed the straight-line fuel range, so a
        box of ``max_range_m`` (capped by ``max_extent_m``) around the origin
        provably contains the entire reachable set. Cropping the water to this
        box bounds the graph size (CLAUDE.md Section 26).
        """
        radius = min(max_range_m * 1.05, self.max_extent_m)
        minx = self.origin_x - radius
        maxx = self.origin_x + radius
        miny = self.origin_y - radius
        maxy = self.origin_y + radius
        return Polygon(
            [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
        )

    def build_graph(
        self,
        max_range_m: float,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ):
        """Crop water to the compute extent and build the water graph."""
        extent = self._compute_extent(max_range_m)
        water_cropped = _as_multipolygon(
            self.region.water_polygons.intersection(extent)
        )

        # Only keep land/obstacle geometry that could actually affect an edge
        # inside the compute extent. This used to pass every land polygon in
        # the whole region regardless of crop size — fine for the small
        # synthetic fixture (5 pieces), but a real OSM-derived region can
        # have 100+ islands, most of them nowhere near the current origin;
        # buffering and unioning all of them on every request was the
        # dominant cost of building the graph. A small margin (2x clearance)
        # keeps edges right at the crop boundary correctly validated.
        margin = self.clearance_m * 2.0
        extent_with_margin = extent.buffer(margin)
        nearby_land = [
            g for g in self.region.land_geometries
            if g.intersects(extent_with_margin)
        ]

        graph = build_graph_from_water_polygons(
            water_polygons=water_cropped,
            land_geometries=nearby_land,
            clearance_m=self.clearance_m,
            grid_resolution_m=self.resolution_m,
            progress_callback=progress_callback,
        )
        self._graph = graph
        return graph

    def origin_node(self) -> int:
        """Return the graph node nearest the origin (fail closed if none).

        Per CLAUDE.md Section 43 the engine must fail closed — never silently
        route from a distant node when the requested origin is not actually in
        navigable water. Because grid nodes are spaced ``resolution_m`` apart,
        a valid in-water origin always has a nearest node within roughly one
        cell. A larger gap means the origin is outside the water extent.
        """
        if self._graph is None:
            # Programmer error (calculate_range() always calls build_graph()
            # first) — distinct from the "no water at all in the compute
            # extent" case below, which is a normal, user-facing scenario
            # (origin far inland, nowhere near the coast) and deserves a
            # clearer message than this one.
            raise RuntimeError("Graph not built; call build_graph() first")
        if not self._graph.nodes:
            raise RuntimeError(
                "No navigable water found near this origin — it may be on "
                "land or far from the coast."
            )

        origin_point = Point(self.origin_x, self.origin_y)
        if not self.region.is_navigable_point(origin_point):
            raise RuntimeError("Origin point is not in navigable water")

        # Use the requested point itself as the Dijkstra source. Snapping to
        # the nearest coarse/refined node can select an isolated shoreline
        # fragment at one resolution even though the origin has a clear,
        # validated connection to the main water graph. That failure mode
        # yielded a visible preview followed by four empty full-result bands.
        node = self._graph.add_query_node(
            origin_point,
            max_connection_distance_m=2.5 * self.resolution_m,
        )
        if node is None:
            raise RuntimeError(
                "Origin is navigable but has no collision-free connection to "
                "the routing graph at this resolution."
            )

        self._origin_id = node.node_id
        return node.node_id

    # --------------------------------------------------------------- compute

    def compute(
        self,
        usable_fuel_l: float,
        fractions: Sequence[float] = THRESHOLD_FRACTIONS,
        *,
        operating_point: Optional[dict] = None,
        max_range_m: Optional[float] = None,
        range_mode: str = "one_way",
        collect_diagnostics: bool = False,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        band_callback: Optional[Callable[[float, BandResult], None]] = None,
    ) -> IsochroneResult:
        """Run the full isochrone and return validated bands.

        Args:
            range_mode: ``"one_way"`` (CLAUDE.md Section 8) or ``"round_trip"``
                (Section 9). Round-trip reachability is
                ``outbound_cost[node] + return_cost[node] <= usable_fuel_l`` —
                computed from two independent Dijkstra runs (see
                :func:`isochrone.cost_field.compute_return_cost_field`), never
                as half of the one-way range (CLAUDE.md Section 44.10).
        """
        if usable_fuel_l <= 0:
            raise ValueError("usable_fuel_l must be positive")
        if range_mode not in RANGE_MODES:
            raise ValueError(f"range_mode must be one of {RANGE_MODES}, got {range_mode!r}")

        start = time.perf_counter()
        report = progress_callback or (lambda _percent, _stage: None)
        warnings: List[str] = []
        anomalies: List[str] = []

        lpnm = self.lpnm
        max_range_nm = usable_fuel_l / lpnm
        if max_range_m is None:
            # A round-trip reachable node always satisfies
            # outbound_cost <= outbound_cost + return_cost <= usable_fuel_l
            # (return cost is never negative), so the one-way max range is
            # still a valid — if conservative — bound on the compute extent
            # for round-trip mode too. No separate sizing needed.
            max_range_m = max_range_nm * 1852.0

        # 1. Build graph and cost field(s).
        report(15, "Building navigable-water graph")
        graph_started = time.perf_counter()
        self.build_graph(max_range_m, report)
        graph_seconds = time.perf_counter() - graph_started
        report(58, "Connecting the origin to navigable water")
        origin_id = self.origin_node()
        self._graph.set_fuel_rate(lpnm)

        adjacency = self._graph.adjacency_with_costs()
        all_node_ids = [node.node_id for node in self._graph.nodes]

        if len(all_node_ids) > DEFAULT_MAX_CELL_COUNT:
            raise RuntimeError(
                f"Compute extent too large: {len(all_node_ids)} nodes exceeds "
                f"budget of {DEFAULT_MAX_CELL_COUNT}. Narrow the region, fuel or "
                f"resolution."
            )

        field_started = time.perf_counter()
        report(63, "Propagating fuel costs through water routes")
        outbound = compute_cost_field(origin_id, adjacency, all_node_ids)

        if range_mode == "round_trip":
            return_field = compute_return_cost_field(origin_id, adjacency, all_node_ids)
            effective_costs: Dict[int, float] = {
                nid: outbound.costs[nid] + return_field.costs[nid]
                for nid in all_node_ids
            }
        else:
            return_field = None
            effective_costs = outbound.costs
        field_seconds = time.perf_counter() - field_started

        # 2. Extract fuel bands.
        bands_started = time.perf_counter()
        bands: Dict[float, BandResult] = {}
        polygon_timings: dict[str, float] = {}
        validation_timings: dict[str, float] = {}
        all_valid = True
        for index, frac in enumerate(fractions):
            band_start_pct = 68 + round(index * 27 / max(len(fractions), 1))
            report(band_start_pct, f"Generating and verifying the {int(frac * 100)}% fuel band")
            threshold = usable_fuel_l * frac
            reachable = {
                nid for nid, cost in effective_costs.items()
                if cost <= threshold + 1e-9
            }
            # Clip against the clearance-aware mask, not raw water — otherwise
            # a cell whose node sits just outside the safety buffer can still
            # paint area inside it (see NavigabilityRegion.safe_water_polygons).
            polygon_started = time.perf_counter()
            polygon = build_region_polygon(
                reachable, self._graph, self.region.safe_water_polygons
            )
            polygon_timings[str(int(frac * 100))] = round(time.perf_counter() - polygon_started, 4)
            origin_point = Point(self.origin_x, self.origin_y)
            validation_started = time.perf_counter()
            # Sample every shell and island ring, but avoid the old 60 points
            # *per ring*. A regional outer band can contain hundreds of rings,
            # making the independent verifier slower than graph construction
            # even though the polygon was already exactly clipped to safe
            # water. Sixteen samples per ring plus interior samples preserves
            # broad independent coverage without tens of thousands of Python/
            # GEOS point-distance calls.
            validation = validate_polygon(
                polygon,
                self.region,
                origin_point,
                n_boundary_samples=16,
                n_interior_samples=40,
            )
            validation_timings[str(int(frac * 100))] = round(time.perf_counter() - validation_started, 4)

            # 3. Anomaly detection (CLAUDE.md Section 13).
            anomalies.extend(_detect_anomalies(polygon))

            if not validation.ok:
                all_valid = False
                warnings.append(
                    f"{int(frac * 100)}% band failed verification: "
                    f"{validation.to_dict()}"
                )
            bands[frac] = BandResult(
                fraction=frac,
                node_count=len(reachable),
                max_cost=threshold,
                polygon=polygon,
                validation=validation,
            )
            if band_callback:
                band_callback(frac, bands[frac])
        bands_seconds = time.perf_counter() - bands_started
        report(96, "Finalizing verified range bands")

        elapsed_s = time.perf_counter() - start

        metrics = {
            "n_nodes": len(all_node_ids),
            "n_edges": len(self._graph.edges),
            "refined_node_count": self._graph.refined_node_count,
            "refined_component_count": self._graph.refined_component_count,
            "resolution_m": self.resolution_m,
            "clearance_m": self.clearance_m,
            "max_range_nm": round(max_range_nm, 3),
            "usable_fuel_l": round(usable_fuel_l, 3),
            "lpnm": round(lpnm, 4),
            "range_mode": range_mode,
            "compute_seconds": round(elapsed_s, 4),
            "graph_seconds": round(graph_seconds, 4),
            "cost_field_seconds": round(field_seconds, 4),
            "band_seconds": round(bands_seconds, 4),
            "polygon_seconds_by_band": polygon_timings,
            "validation_seconds_by_band": validation_timings,
        }

        debug_grid = None
        if collect_diagnostics:
            # Bound response size while preserving a deterministic sample over
            # the whole graph. Refined nodes are always appended after coarse
            # construction, so the suffix boundary identifies them exactly.
            max_points = 5000
            stride = max(1, math.ceil(len(self._graph.nodes) / max_points))
            refined_from = len(self._graph.nodes) - self._graph.refined_node_count
            debug_grid = [
                {
                    "x": round(node.x, 2),
                    "y": round(node.y, 2),
                    "costL": round(effective_costs[node.node_id], 4),
                    "refined": node.node_id >= refined_from,
                }
                for node in self._graph.nodes[::stride]
                if math.isfinite(effective_costs[node.node_id])
            ]

        return IsochroneResult(
            origin_projected=(self.origin_x, self.origin_y),
            origin_wgs84=None,
            bands=bands,
            usable_fuel_l=usable_fuel_l,
            lpnm=lpnm,
            operating_point=operating_point,
            max_range_nm=max_range_nm,
            metrics=metrics,
            warnings=warnings,
            anomalies=anomalies,
            all_polygons_valid=all_valid,
            range_mode=range_mode,
            debug_grid=debug_grid,
        )


def _detect_anomalies(polygon: MultiPolygon) -> List[str]:
    """Claw-check a band for the failure modes in CLAUDE.md Section 13.

    Returns a list of anomaly strings (empty when the band looks healthy).
    """
    anomalies: List[str] = []

    # Extreme perimeter/area ratio or very thin components often indicate a
    # tendril leaking through a narrow gap. Report (do not silently remove) so
    # a later refinement phase can decide.
    for geom in polygon.geoms:
        if geom.area < 1e-6:
            continue
        perim = geom.length
        ratio = perim / (geom.area ** 0.5)
        if ratio > 8.0:
            anomalies.append(
                f"suspicious component with perimeter/area ratio {ratio:.2f} "
                f"(possible thin tendril)"
            )

    return anomalies


# ============================================================================
# High-level convenience: fuel model -> engine
# ============================================================================


def calculate_range(
    origin_wgs84,
    lpnm: float,
    usable_fuel_l: float,
    *,
    resolution_m: float = 1000.0,
    clearance_m: float = 50.0,
    water: MultiPolygon,
    land: List[Polygon],
    projection=None,
    fractions: Sequence[float] = THRESHOLD_FRACTIONS,
    operating_point: Optional[dict] = None,
    max_extent_m: float = DEFAULT_MAX_EXTENT_M,
    range_mode: str = "one_way",
    collect_diagnostics: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
    band_callback: Optional[Callable[[float, BandResult], None]] = None,
) -> IsochroneResult:
    """End-to-end isochrone from a WGS84 origin — one-way or round-trip.

    Args:
        origin_wgs84: ``(lon, lat)`` start point.
        lpnm: Effective liters-per-nautical-mile cost.
        usable_fuel_l: Fuel available after reserve.
        water: Navigable-water MultiPolygon (bbox minus land).
        land: Raw land/obstacle polygons.
        projection: Optional :class:`LocalProjection` (defaults to the Bodrum
            reference frame).
        fractions: Fuel band fractions to compute.
        operating_point: Optional metadata for reporting.
        max_extent_m: Hard cap on the compute box radius.
        range_mode: ``"one_way"`` or ``"round_trip"`` (CLAUDE.md Section 3.3).

    Returns:
        An :class:`IsochroneResult` with validated bands.
    """
    from .projection import LocalProjection

    proj = projection or LocalProjection()
    x, y = proj.to_projected(*origin_wgs84)

    # Every land piece anywhere in the region — not just ones near this
    # origin — used to flow straight into NavigabilityRegion, whose
    # obstacle_union/land_raw_union/land_strtree all buffer or index the
    # *entire* list. For a small curated region that's cheap; for a region
    # with hundreds of real islands (east-med-real) it meant every one of
    # those derived properties did work proportional to the whole region's
    # land count on every request, regardless of how far away most of it
    # was. IsochroneEngine.build_graph() already does this filtering for the
    # routing graph itself — this applies the same idea one level up, using
    # max_extent_m (known upfront, unlike the actual fuel-limited range)
    # as a safe upper bound on how far any of this request's geometry work
    # could possibly need to look.
    extent_radius = max_extent_m * 1.1
    origin_extent = Point(x, y).buffer(extent_radius)
    nearby_land = [g for g in land if g.intersects(origin_extent)]

    region = NavigabilityRegion(
        water_polygons=water,
        land_geometries=nearby_land,
        clearance_m=clearance_m,
        resolution_m=resolution_m,
    )

    engine = IsochroneEngine(
        region=region,
        origin_x=x,
        origin_y=y,
        lpnm=lpnm,
        resolution_m=resolution_m,
        clearance_m=clearance_m,
        max_extent_m=max_extent_m,
    )
    result = engine.compute(
        usable_fuel_l,
        fractions=fractions,
        operating_point=operating_point,
        range_mode=range_mode,
        collect_diagnostics=collect_diagnostics,
        progress_callback=progress_callback,
        band_callback=band_callback,
    )
    result.origin_wgs84 = origin_wgs84
    return result


def calculate_one_way_range(*args, **kwargs) -> IsochroneResult:
    """Backwards-compatible one-way alias for :func:`calculate_range`."""
    kwargs.pop("range_mode", None)
    return calculate_range(*args, range_mode="one_way", **kwargs)


def calculate_round_trip_range(*args, **kwargs) -> IsochroneResult:
    """Round-trip isochrone (CLAUDE.md Section 9): ``calculate_range`` with
    ``range_mode="round_trip"``.

    Never implemented as ``one_way_range / 2`` (CLAUDE.md Section 44.10) —
    every band is built from ``outbound_cost + return_cost`` computed as two
    independent Dijkstra runs; see
    :func:`isochrone.cost_field.compute_return_cost_field`.
    """
    kwargs.pop("range_mode", None)
    return calculate_range(*args, range_mode="round_trip", **kwargs)
