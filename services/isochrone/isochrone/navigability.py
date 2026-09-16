"""Navigability region abstraction for the isochrone engine.

Per CLAUDE.md Section 6 (Marine Navigability Model) and Section 7 (Routing
Architecture). A :class:`NavigabilityRegion` is the minimal, provider-agnostic
description of "what is passable water" for a single compute extent:

    * ``water_polygons``  — the navigable water (bbox minus land/obstacles),
    * ``land_geometries`` — the raw land / obstacle / hard-restriction polygons
                            (unbuffered; the source-of-truth geometry),
    * ``clearance_m``     — safety clearance applied around every obstacle,
    * ``resolution_m``    — base grid resolution for the routing graph.

All navigability *decisions* (depth policy, restrictions, user overrides,
provenance) are upstream of this object. This object only carries the
already-classified pass/fail geometry so the routing/isochrone engine stays
independent of the marine-data ingestion pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import List

from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.prepared import PreparedGeometry, prep
from shapely.strtree import STRtree


@dataclass
class NavigabilityRegion:
    """Classified passable-water geometry for one compute extent."""

    water_polygons: MultiPolygon
    land_geometries: List[Polygon] = field(default_factory=list)
    clearance_m: float = 50.0
    resolution_m: float = 100.0

    #: Matches the numerical tolerance ``polygon_utils.validate_polygon`` uses
    #: for its land-intrusion check — buffering land by a hair so a boundary
    #: sample sitting exactly on the coastline isn't missed by floating-point
    #: noise, independent of the (much larger) safety clearance buffer.
    _VERIFIER_TOLERANCE_M = 1.0

    @cached_property
    def land_raw_union(self) -> Polygon | MultiPolygon | None:
        """Land geometry buffered by a small numerical tolerance and unioned
        — used by the independent verifier's land-intrusion check.

        The verifier runs once per fuel band (4 per request), and used to
        recompute this from scratch every time. Buffering and unioning every
        land polygon in a region with hundreds of real islands (east-med-real)
        is expensive enough that redoing it 4x over was a real, measurable
        chunk of a slow request — caching it here means it costs that once
        per region load, not once per band.
        """
        if not self.land_geometries:
            return None
        buffered = [g.buffer(self._VERIFIER_TOLERANCE_M) for g in self.land_geometries]
        return unary_union(buffered)

    @cached_property
    def land_raw_union_prepared(self) -> PreparedGeometry | None:
        """Prepared version of :attr:`land_raw_union`.

        An unprepared ``.intersects()`` test against a region with hundreds
        of real islands still does a full geometric test from scratch on
        every call; the verifier calls this once per boundary sample (tens
        of thousands per request). Preparing it once builds the internal
        index that makes each of those calls a fast indexed lookup instead —
        the same fix already applied to obstacle collision checks in
        ``routing.water_graph`` for the same reason.
        """
        if self.land_raw_union is None:
            return None
        return prep(self.land_raw_union)

    @cached_property
    def obstacle_union(self) -> Polygon | MultiPolygon | None:
        """Land/obstacle geometry buffered by the safety clearance.

        This is the geometry every graph edge and node is checked against, and
        the geometry the final reachable polygon must also be clipped against
        (see :attr:`safe_water_polygons`) — otherwise a band can visually
        extend inside the declared safety clearance even though no routing
        node was ever placed there.

        Cached for real this time: the previous implementation was a plain
        ``@property`` that recomputed the full buffer+union on every access,
        despite the docstring claiming it was cached and every caller in the
        cost-field expansion relying on that.
        """
        if not self.land_geometries:
            return None
        buffered = [g.buffer(self.clearance_m) for g in self.land_geometries]
        return unary_union(buffered)

    @cached_property
    def obstacle_union_prepared(self) -> PreparedGeometry | None:
        """Prepared version of :attr:`obstacle_union`, for the same reason as
        :attr:`land_raw_union_prepared` — ``is_navigable_point`` runs a
        contains/touches test against this on every interior-sample check,
        and an unprepared test against hundreds of real islands is
        measurably slower than the indexed version."""
        if self.obstacle_union is None:
            return None
        return prep(self.obstacle_union)

    @cached_property
    def water_polygons_prepared(self) -> PreparedGeometry:
        """Prepared version of ``water_polygons`` for the same reason."""
        return prep(self.water_polygons)

    @cached_property
    def safe_water_polygons(self) -> MultiPolygon:
        """Navigable water with the safety-clearance buffer already removed.

        ``water_polygons`` is raw water (compute bbox minus *unbuffered*
        land) — good enough for placing/validating graph nodes, which already
        check against ``obstacle_union`` individually. But the final reachable
        polygon (CLAUDE.md Section 10) is built by clipping cell squares
        against a water mask; clipping against raw ``water_polygons`` let a
        cell whose node sits just outside the clearance buffer still paint
        area *inside* that buffer, so the displayed "safe" range could extend
        closer to the coast than the clearance setting promises. Clipping
        against this clearance-aware mask instead closes that gap.
        """
        if self.obstacle_union is None:
            return self.water_polygons
        safe = self.water_polygons.difference(self.obstacle_union)
        if safe.is_empty:
            return MultiPolygon()
        if isinstance(safe, Polygon):
            return MultiPolygon([safe])
        if isinstance(safe, MultiPolygon):
            return safe
        polys = [g for g in safe.geoms if isinstance(g, Polygon)]
        return MultiPolygon(polys) if polys else MultiPolygon()

    @cached_property
    def land_strtree(self) -> STRtree | None:
        """Spatial index over ``land_geometries``, for nearest-distance
        queries (the independent verifier's clearance check).

        A region with a handful of curated islands can get away with a
        linear scan over every land piece per sample point; a region with
        hundreds of real islands (east-med-real) cannot — that scan, run for
        every one of ~60 boundary samples per ring of a polygon with one
        hole per nearby island, is exactly what turned one verification pass
        into tens of millions of ``Polygon.distance()`` calls. The tree
        turns "nearest land piece to this point" into a real spatial query
        instead of checking every piece.
        """
        if not self.land_geometries:
            return None
        return STRtree(self.land_geometries)

    def is_navigable_point(self, point) -> bool:
        """True if a point is strictly in navigable water (not on any obstacle)."""
        if not self.water_polygons_prepared.contains(point):
            return False
        obstacle = self.obstacle_union_prepared
        if obstacle is not None and (obstacle.contains(point) or obstacle.touches(point)):
            return False
        return True
