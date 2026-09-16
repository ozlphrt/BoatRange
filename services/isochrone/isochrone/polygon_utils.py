"""Reachable-node set -> validated water-only polygon.

Per CLAUDE.md Section 10 (Reachable Polygon Generation), Section 10.1 (Cleanup)
and Section 12 (Independent Verifier).

The strategy is deliberately conservative and proof-oriented:

1. Each reachable grid node becomes a cell square (half-width = grid/2).
2. The cell squares are unioned and then **clipped against the navigable
   water mask**. Clipping guarantees the final polygon is a strict subset of
   navigable water, so it can never intersect land, islands or obstacles.
3. Only *artifactual* geometry is removed: sub-cell slivers and numerical
   holes. Real islands remain as holes (the polygon wraps around them) and
   legitimate reachable regions are preserved.

Decorative spline smoothing is deliberately NOT applied here (CLAUDE.md
Section 10.2: "never use decorative spline smoothing"). Navigation-aware
smoothing is a later phase.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List

from shapely.geometry import Point, Polygon, MultiPolygon
from shapely.ops import unary_union


@dataclass
class PolygonValidation:
    """Result of validating a reachable polygon against the navigability mask."""

    ok: bool
    land_leaks: int = 0
    boundary_land_touches: int = 0
    interior_outside_water: int = 0
    clearance_violations: int = 0
    origin_inside: bool = False
    n_boundary_samples: int = 0
    n_interior_samples: int = 0
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "land_leaks": self.land_leaks,
            "boundary_land_touches": self.boundary_land_touches,
            "interior_outside_water": self.interior_outside_water,
            "clearance_violations": self.clearance_violations,
            "origin_inside": self.origin_inside,
            "n_boundary_samples": self.n_boundary_samples,
            "n_interior_samples": self.n_interior_samples,
            "notes": list(self.notes),
        }


def build_region_polygon(
    reachable_node_ids,
    graph,
    water_mask: MultiPolygon,
    *,
    min_component_area_factor: float = 0.5,
) -> MultiPolygon:
    """Turn a set of reachable node IDs into a water-clipped MultiPolygon.

    Args:
        reachable_node_ids: Iterable of node IDs that are within fuel budget.
        graph: Built :class:`WaterPolygonGraph` (provides node coordinates and
            grid resolution).
        water_mask: Navigable-water geometry the polygon must stay within.
        min_component_area_factor: Components smaller than this fraction of one
            grid cell are dropped as clipping artifacts.
    """
    if not reachable_node_ids:
        return MultiPolygon()

    resolution = graph.grid_resolution_m
    half = resolution / 2.0
    min_component_area = min_component_area_factor * resolution * resolution

    cells: List[Polygon] = []
    for node_id in reachable_node_ids:
        node = graph.nodes[node_id]
        x, y = node.x, node.y
        cells.append(
            Polygon(
                [
                    (x - half, y - half),
                    (x + half, y - half),
                    (x + half, y + half),
                    (x - half, y + half),
                ]
            )
        )

    union = unary_union(cells)

    # Clip against navigable water. This is the leak guarantee: the polygon
    # can only ever contain water, never land/island/obstacle.
    clipped = union.intersection(water_mask)

    cleaned = cleanup_polygon(
        clipped,
        min_component_area=min_component_area,
    )

    if cleaned.is_empty:
        return MultiPolygon()
    if isinstance(cleaned, Polygon):
        return MultiPolygon([cleaned])
    return cleaned


def cleanup_polygon(
    geometry: Polygon | MultiPolygon,
    *,
    min_component_area: float,
    max_hole_fill_area: float = 100.0,
) -> Polygon | MultiPolygon:
    """Remove only geometric artifacts; preserve real islands and corridors.

    * Drops disconnected components smaller than ``min_component_area``
      (clipping slivers / single-cell noise).
    * Fills holes smaller than ``max_hole_fill_area`` (numerical artifacts).

    Holes larger than ``max_hole_fill_area`` are treated as legitimate land
    islands and preserved as polygon holes so the reachable area wraps around
    them. No area is eroded, so valid narrow corridors are never removed.
    """
    geoms = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]

    kept: List[Polygon] = []
    for poly in geoms:
        if poly.area <= min_component_area:
            continue
        kept.append(_fill_small_holes(poly, max_hole_fill_area))

    if not kept:
        return MultiPolygon()
    if len(kept) == 1:
        return kept[0]
    return MultiPolygon(kept)


def _fill_small_holes(
    polygon: Polygon, max_hole_fill_area: float
) -> Polygon:
    """Return a copy of ``polygon`` with sub-threshold holes filled."""
    shell = polygon.exterior
    kept_interiors = []
    for hole in polygon.interiors:
        if Polygon(hole).area > max_hole_fill_area:
            kept_interiors.append(hole)
    return Polygon(shell, kept_interiors)


def validate_polygon(
    geometry: Polygon | MultiPolygon,
    region,
    origin_point: Point,
    *,
    n_boundary_samples: int = 60,
    n_interior_samples: int = 60,
    seed: int = 1337,
) -> PolygonValidation:
    """Independently verify a polygon does not leak through blocked terrain.

    Checks (CLAUDE.md Section 12):
      1. boundary samples must not cross raw land,
      2. random interior samples must stay inside navigable water,
      3. the origin must be covered by the polygon.
      4. the polygon must not encroach into the safety-clearance buffer.

    Land-intrusion (1) is checked against raw land plus a small numerical
    tolerance, independent of ``region.clearance_m`` — a polygon boundary that
    sits exactly *on* the clearance line (which is expected: the engine clips
    to the clearance-aware mask) must not be reported as a leak. Clearance
    compliance is checked separately (4), by measuring distance to raw land
    rather than testing containment in a buffer whose edge the polygon is
    supposed to touch.

    A previous version buffered land by the *full* ``clearance_m`` for the
    land-intrusion check too — the same buffer the engine now clips against
    (see ``NavigabilityRegion.safe_water_polygons``) — so every boundary
    sample sitting correctly right at the clearance line was flagged as
    "touching the land buffer", even though it wasn't near land at all.
    """
    notes: List[str] = []
    land_leaks = 0
    boundary_land_touches = 0
    interior_outside_water = 0
    clearance_violations = 0

    numerical_tolerance_m = 1.0
    # Cached on the region (NavigabilityRegion.land_raw_union) rather than
    # recomputed here — this function runs once per fuel band (4 per
    # request), and rebuilding a buffer+union over every land polygon each
    # time was expensive enough on a region with hundreds of real islands
    # to matter (see that property's docstring).
    land_raw_union = getattr(region, "land_raw_union", None)
    if land_raw_union is None and region.land_geometries:
        land_raw_union = unary_union(
            [g.buffer(numerical_tolerance_m) for g in region.land_geometries]
        )
    # Prepared version for the intersects() checks below — see
    # NavigabilityRegion.land_raw_union_prepared's docstring. Fall back to
    # the unprepared geometry itself (still correct, just slower) if region
    # doesn't expose the cached prepared version.
    land_raw_union_prepared = getattr(region, "land_raw_union_prepared", None) or land_raw_union
    # Same tolerance below the declared clearance: legitimately touching the
    # clearance line is fine, sitting measurably inside it is not.
    clearance_check_m = max(region.clearance_m - numerical_tolerance_m, 0.0)

    # 1. Boundary sampling — every ring (shell + holes) sampled, none on land.
    #
    # Regression note: this used to call
    # ``ring.interpolate(i * length / n_boundary_samples, normalized=True)`` —
    # passing an *absolute* distance while also asking Shapely to treat it as
    # *normalized* (0..1). Every sample beyond i=1 landed past the end of the
    # ring and clamped to the same point, so all n_boundary_samples samples on
    # every ring were actually the same single coordinate. The boundary check
    # was silently sampling ~1 point per polygon instead of ~60.
    rings = list(_iter_rings(geometry))
    for ring in rings:
        length = ring.length
        if length <= 0:
            continue
        land_tree = getattr(region, "land_strtree", None) if region.land_geometries else None
        for i in range(n_boundary_samples):
            pt = ring.interpolate(i / n_boundary_samples, normalized=True)
            if land_raw_union_prepared is not None and land_raw_union_prepared.intersects(pt):
                boundary_land_touches += 1
            if region.land_geometries and clearance_check_m > 0:
                # A linear scan over every land polygon per sample point is
                # fine for a handful of curated islands, but a region with
                # hundreds of real islands turns this into tens of millions
                # of distance() calls (found by profiling east-med-real: one
                # validate_polygon call took over a minute). The spatial
                # index narrows this to the actual nearest candidates.
                if land_tree is not None:
                    # Most boundary samples on a large, sprawling reachable
                    # polygon are nowhere near the clearance-violation
                    # threshold — cheaply ask the tree "is anything even
                    # within clearance_check_m of this point" first (a bbox
                    # query, not an exact distance computation) and only
                    # pay for the precise nearest-distance call when that
                    # comes back non-empty. On a region with hundreds of
                    # islands this skips the expensive path for the large
                    # majority of samples.
                    candidates = land_tree.query(pt.buffer(clearance_check_m))
                    if len(candidates) == 0:
                        nearest_land_m = clearance_check_m  # provably >= threshold
                    else:
                        nearest_land_m = min(
                            region.land_geometries[idx].distance(pt) for idx in candidates
                        )
                else:
                    nearest_land_m = min(g.distance(pt) for g in region.land_geometries)
                if nearest_land_m < clearance_check_m:
                    clearance_violations += 1

    # 2. Interior random sampling — must stay inside navigable water.
    rng = random.Random(seed)
    bounds = geometry.bounds
    for _ in range(n_interior_samples):
        inside = False
        attempts = 0
        while not inside and attempts < 5:
            px = rng.uniform(bounds[0], bounds[2])
            py = rng.uniform(bounds[1], bounds[3])
            pt = Point(px, py)
            if geometry.contains(pt):
                inside = True
            attempts += 1
        if inside and not region.is_navigable_point(pt):
            interior_outside_water += 1

    # 3. Origin must be covered by the reachable polygon.
    origin_inside = geometry.covers(origin_point) if not geometry.is_empty else False

    if boundary_land_touches > 0:
        notes.append(
            f"{boundary_land_touches} boundary samples fall on/inside raw land "
            f"— leak through blocked terrain"
        )
    if clearance_violations > 0:
        notes.append(
            f"{clearance_violations} boundary samples are closer to land than "
            f"the {region.clearance_m}m safety clearance"
        )

    ok = (
        boundary_land_touches == 0
        and clearance_violations == 0
        and interior_outside_water == 0
        and origin_inside
    )

    return PolygonValidation(
        ok=ok,
        land_leaks=land_leaks,
        boundary_land_touches=boundary_land_touches,
        interior_outside_water=interior_outside_water,
        clearance_violations=clearance_violations,
        origin_inside=origin_inside,
        # Regression note: this used to call ``len(list(rings))`` here, after
        # ``rings`` (a generator) had already been fully consumed by the loop
        # above — an exhausted generator reports length 0, so every
        # validation result claimed "0 boundary samples" regardless of how
        # many were actually taken.
        n_boundary_samples=len(rings) * n_boundary_samples,
        n_interior_samples=n_interior_samples,
        notes=notes,
    )


def _iter_rings(geometry: Polygon | MultiPolygon):
    """Yield every LinearRing (shells and holes) of a polygon/multipolygon."""
    geoms = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    for poly in geoms:
        yield poly.exterior
        for hole in poly.interiors:
            yield hole
