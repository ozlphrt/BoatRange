"""Water polygon graph — vector-first routing backbone.

Builds a navigable graph directly from water polygon geometry.
Every edge is validated against land using exact Shapely intersection checks.

This is the core of our approach: routes never cross land because the graph
is constructed FROM water polygons, not FROM raster cells that might misclassify.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from heapq import heappush, heappop
from typing import Callable, Iterator

import numpy as np
from shapely.geometry import Point, LineString, Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree


# ============================================================================
# Graph Nodes and Edges
# ============================================================================


@dataclass(frozen=True)
class GraphNode:
    """A node in the water polygon graph.

    Represents a point in navigable water where a route can turn.
    """

    x: float  # meters in local CRS (Web Mercator or projected)
    y: float
    node_id: int = -1


@dataclass
class GraphEdge:
    """An edge connecting two graph nodes.

    Attributes:
        from_node: Source node
        to_node: Destination node
        geometry: LineString representing the edge path
        distance_m: Geometric length in meters
        fuel_cost_l: Fuel cost in liters (distance_nm * L/nm)
        is_valid: Whether this edge passes land/obstacle checks
    """

    from_node: GraphNode
    to_node: GraphNode
    geometry: LineString
    distance_m: float
    fuel_cost_l: float = 0.0
    is_valid: bool = True


# ============================================================================
# WaterPolygonGraph
# ============================================================================


class WaterPolygonGraph:
    """Navigable graph built from water polygon geometry.

    The graph is constructed by:
    1. Sampling points within water polygons on a grid
    2. Filtering out points that fall on land (exact intersection check)
    3. Connecting adjacent water points with edges
    4. Validating each edge against land buffers (exact geometry check)

    This ensures NO edge in the graph crosses land or obstacles.
    """

    def __init__(
        self,
        water_polygons: MultiPolygon,
        land_geometries: list[Polygon] | None = None,
        clearance_m: float = 50.0,
        grid_resolution_m: float = 100.0,
        enable_refinement: bool = True,
        refined_resolution_m: float | None = None,
        max_refined_nodes: int = 4000,
        max_refined_components: int = 20,
        progress_callback: Callable[[int, str], None] | None = None,
    ):
        """Initialize the water polygon graph.

        Args:
            water_polygons: Pre-computed navigable water geometry
            land_geometries: List of land/obstacle polygons for edge validation
            clearance_m: Safety clearance from land in meters
            grid_resolution_m: Grid spacing for node placement
            enable_refinement: Run local adaptive refinement after the coarse
                grid is built (CLAUDE.md Section 7.2 / Section 11). A passage
                narrower than ``grid_resolution_m`` can fall entirely between
                coarse grid columns — no coarse node lands inside it — making
                a real, navigable corridor invisible to the graph. Refinement
                finds every such "missed" pocket of navigable water and lays
                a local fine grid inside it.
            refined_resolution_m: Node spacing for refined patches. Defaults
                to ``max(20.0, grid_resolution_m / 4)``, inside the 20-50m
                range CLAUDE.md Section 7.2 calls for.
            max_refined_nodes: Hard cap on total nodes added by refinement
                (CLAUDE.md Section 26: every refinement pass needs a budget).
            max_refined_components: Hard cap on how many missed corridors get
                refined in one build — protects against a pathological
                fixture with thousands of tiny slivers.
        """
        self.water_polygons = water_polygons
        self.clearance_m = clearance_m
        self.grid_resolution_m = grid_resolution_m
        self.enable_refinement = enable_refinement
        self.refined_resolution_m = refined_resolution_m or max(20.0, grid_resolution_m / 4.0)
        self.max_refined_nodes = max_refined_nodes
        self.max_refined_components = max_refined_components
        self.refined_node_count = 0
        self.refined_component_count = 0
        self.progress_callback = progress_callback or (lambda _percent, _stage: None)

        # The graph only understands MultiPolygon (it reads .geoms for island
        # holes). Coerce a single Polygon / GeometryCollection up to one so the
        # factory is robust to cropped/derived water geometry.
        if not isinstance(self.water_polygons, MultiPolygon):
            if self.water_polygons.is_empty:
                self.water_polygons = MultiPolygon()
            elif isinstance(self.water_polygons, Polygon):
                self.water_polygons = MultiPolygon([self.water_polygons])
            else:
                polys = [g for g in self.water_polygons.geoms if isinstance(g, Polygon)]
                self.water_polygons = MultiPolygon(polys) if polys else MultiPolygon()

        # Build combined obstacle geometry: land buffer + polygon holes
        obstacles: list[Polygon] = []

        # Add explicit land geometries with clearance buffer
        if land_geometries:
            land_union = unary_union(land_geometries)
            obstacles.append(land_union.buffer(clearance_m))

        # Add water polygon holes (islands, peninsulas cut out of water)
        for geom in self.water_polygons.geoms:
            if isinstance(geom, Polygon):
                for hole in geom.interiors:
                    hole_poly = Polygon(hole)
                    obstacles.append(hole_poly.buffer(clearance_m))

        self.obstacle_union = unary_union(obstacles) if obstacles else None
        # `prepared` builds an internal spatial index over obstacle_union's
        # rings once, so repeated intersects()/contains() calls against the
        # same static geometry (one per candidate edge, of which there can be
        # 100k+) are indexed lookups instead of a fresh full geometric test
        # each time. With a real multi-island region (100+ land pieces) the
        # unprepared per-edge intersects() calls dominated build time.
        self._obstacle_union_prepared = (
            prep(self.obstacle_union) if self.obstacle_union is not None else None
        )

        # Prepare the exact classified water once for all segment checks.
        # Expanding this mask, even by one meter, can bridge blocked or
        # unmapped gaps that are absent from the explicit obstacle layer.
        self._water_containment_mask = self.water_polygons
        self._water_containment_mask_prepared = prep(self._water_containment_mask)

        # Graph data structures
        self.nodes: list[GraphNode] = []
        self.edges: list[GraphEdge] = []
        self._node_index: dict[tuple[float, float], int] = {}  # (x,y) -> node_id
        self._adjacency: dict[int, list[int]] = {}  # node_id -> neighbor node_ids
        # (from_id, to_id) -> fuel_cost_l, rebuilt whenever set_fuel_rate runs.
        # Regression note: shortest_path used to look this up by scanning the
        # entire self.edges list per neighbor (O(edges) per Dijkstra step, so
        # O(nodes*edges) overall) — fine at 4-connectivity, painfully slow
        # once 8-connectivity roughly doubled the edge count.
        self._edge_cost: dict[tuple[int, int], float] = {}

        self._built = False

    def build(self) -> "WaterPolygonGraph":
        """Construct the graph from water polygon geometry.

        Returns:
            Self for method chaining.

        The build process:
        1. Determine bounding box of water polygons
        2. Place grid nodes within the bounding box
        3. Filter: keep only nodes inside water (not on land)
        4. Connect adjacent water nodes with edges
        5. Validate each edge against land buffer
        """
        if self._built:
            return self

        # Handle empty water polygons
        if self.water_polygons.is_empty:
            self.nodes = []
            self.edges = []
            self._adjacency = {}
            self._built = True
            return self

        self.progress_callback(18, "Sampling navigable-water grid nodes")
        # Step 1: Get bounding box
        bounds = self.water_polygons.bounds  # (minx, miny, maxx, maxy)

        # Step 2: Place grid nodes
        x_start = math.floor(bounds[0] / self.grid_resolution_m) * self.grid_resolution_m
        y_start = math.floor(bounds[1] / self.grid_resolution_m) * self.grid_resolution_m
        x_end = math.ceil(bounds[2] / self.grid_resolution_m) * self.grid_resolution_m
        y_end = math.ceil(bounds[3] / self.grid_resolution_m) * self.grid_resolution_m

        node_id = 0
        for x in np.arange(x_start, x_end + self.grid_resolution_m, self.grid_resolution_m):
            for y in np.arange(y_start, y_end + self.grid_resolution_m, self.grid_resolution_m):
                point = Point(x, y)

                # Step 3: Check if point is inside water and not on land
                if self._is_in_water(point) and self._not_on_land(point):
                    node = GraphNode(x=x, y=y, node_id=node_id)
                    self.nodes.append(node)
                    self._node_index[(round(x, 2), round(y, 2))] = node_id
                    self._adjacency[node_id] = []
                    node_id += 1

        # Step 4: Connect adjacent nodes
        self.progress_callback(26, "Connecting neighboring water cells")
        self._connect_adjacent_nodes()

        # Step 5: Validate edges
        self.progress_callback(30, "Checking route edges against coastline and obstacles")
        self._validate_edges()

        # Step 6: Local adaptive refinement (CLAUDE.md Section 7.2 / 11) —
        # find navigable water the coarse grid missed entirely and patch it
        # in with a local fine grid, stitched back into the main graph.
        if self.enable_refinement:
            self.progress_callback(54, "Refining narrow coastal passages")
            self._refine_missed_corridors()

        self._built = True
        return self

    def _is_in_water(self, point: Point) -> bool:
        """Check if a point is strictly inside the water polygon geometry."""
        if self.water_polygons.is_empty:
            return False
        # Boundary points (including island edges) stay excluded. Use the
        # prepared geometry created at initialization: this predicate runs for
        # every candidate grid point and the unprepared MultiPolygon check was
        # the dominant cost for regional graphs.
        return self._water_containment_mask_prepared.contains(point)

    def _not_on_land(self, point: Point) -> bool:
        """Check if a point is not on any obstacle (land buffer or island holes)."""
        if self._obstacle_union_prepared is None:
            return True
        return not self._obstacle_union_prepared.contains(point) and not self._obstacle_union_prepared.touches(point)

    def _connect_adjacent_nodes(self) -> None:
        """Connect each node to its 8 adjacent neighbors in the grid.

        Regression note: this used to be 4-connected (N/S/E/W only). On a
        uniform-cost grid every monotone staircase between two points has the
        *same* total length, so Dijkstra had no reason to prefer a diagonal-
        looking path over one that runs all the way along one axis before
        turning — it just returned whichever staircase order the heap
        happened to explore first. Real open water produced ugly right-angle
        routes that hugged one bearing before turning 90 degrees, and every
        route was up to ~41% longer than the true distance (a diagonal cell
        cost 2*res through two cardinal moves instead of res*sqrt(2)). Adding
        the four diagonal neighbors lets Dijkstra actually choose the shorter
        diagonal hop, which is what fixes both problems at once.
        """
        res = self.grid_resolution_m
        offsets = [
            (res, 0), (-res, 0), (0, res), (0, -res),
            (res, res), (res, -res), (-res, res), (-res, -res),
        ]
        for node in self.nodes:
            x, y = node.x, node.y
            for dx, dy in offsets:
                key = (round(x + dx, 2), round(y + dy, 2))
                if key in self._node_index:
                    neighbor_id = self._node_index[key]
                    if neighbor_id not in self._adjacency.get(node.node_id, []):
                        self._adjacency.setdefault(node.node_id, []).append(neighbor_id)

    def _validate_edges(self) -> None:
        """Validate every undirected edge once and reject invalid edges.

        ``_connect_adjacent_nodes`` records both A→B and B→A. The previous
        implementation performed the same expensive Shapely segment checks
        twice and stored two GraphEdge objects, then ``adjacency_with_costs``
        symmetrized those duplicates again. Regional calculations spent most
        of their time here. One validated edge plus bidirectional adjacency is
        equivalent and cuts that work roughly in half.
        """
        candidate_pairs = {
            (min(from_id, to_id), max(from_id, to_id))
            for from_id, neighbor_ids in self._adjacency.items()
            for to_id in neighbor_ids
            if from_id != to_id
        }
        valid_adjacency: dict[int, list[int]] = {
            node.node_id: [] for node in self.nodes
        }

        total_pairs = max(len(candidate_pairs), 1)
        for index, (from_id, to_id) in enumerate(candidate_pairs):
            if index % 2000 == 0:
                self.progress_callback(
                    30 + round(23 * index / total_pairs),
                    "Checking route edges against coastline and obstacles",
                )
            from_node = self.nodes[from_id]
            to_node = self.nodes[to_id]
            geometry = LineString([(from_node.x, from_node.y), (to_node.x, to_node.y)])

            if not self._segment_is_clear(
                (from_node.x, from_node.y), (to_node.x, to_node.y)
            ):
                continue

            distance_m = geometry.length
            self.edges.append(GraphEdge(
                from_node=from_node,
                to_node=to_node,
                geometry=geometry,
                distance_m=distance_m,
                fuel_cost_l=0.0,
                is_valid=True,
            ))
            valid_adjacency[from_id].append(to_id)
            valid_adjacency[to_id].append(from_id)
            self._edge_cost[(from_id, to_id)] = distance_m
            self._edge_cost[(to_id, from_id)] = distance_m

        self._adjacency = valid_adjacency

    def _refine_missed_corridors(self) -> None:
        """Find water narrower than the coarse grid can reliably sample, and
        patch it in with a local fine grid.

        The safe-to-navigate region is exactly ``water_polygons`` minus
        ``obstacle_union`` — the same test :meth:`_is_in_water` /
        :meth:`_not_on_land` use for placing every coarse node.

        An earlier version of this looked for whole *connected components*
        of that region with zero coarse nodes anywhere in them — which only
        catches a corridor that is entirely isolated from every other body
        of water. In practice a narrow corridor almost always connects two
        much larger basins that already have plenty of coarse nodes, so the
        connected component as a whole was never "missed"; only the thin
        neck joining the two basins was, and the old check skipped straight
        past it because *some* node existed somewhere in the same component.

        This uses a morphological opening instead — erode the safe region by
        half the coarse grid spacing, then dilate it back by the same
        amount. That operation deletes (and does not restore) any part of
        the region narrower than roughly ``grid_resolution_m``, while
        leaving wide open water intact. Subtracting the opening from the
        original safe region leaves exactly the narrow parts — independent
        of how well-sampled the wider water around them is. This matches
        CLAUDE.md Section 7.2's trigger list directly: "corridors whose
        width is near safety-clearance threshold", "suspicious single-cell
        gaps".
        """
        if self.obstacle_union is not None:
            try:
                safe_region = self.water_polygons.difference(self.obstacle_union)
            except Exception:
                # Invalid input geometry (self-intersections etc.) can make
                # even a repaired difference() raise a GEOSException. The
                # coarse grid is already built and validated at this point —
                # refinement is an enhancement, not a correctness dependency,
                # so skip it rather than take the whole graph build down.
                try:
                    safe_region = self.water_polygons.buffer(0).difference(
                        self.obstacle_union.buffer(0)
                    )
                except Exception:
                    return
        else:
            safe_region = self.water_polygons

        if safe_region.is_empty:
            return

        # Buffering obstacle_union's land polygons by clearance_m approximates
        # every original vertex's corner with several arc segments, so a
        # region with hundreds of real, detailed islands (as opposed to a
        # handful of curated ones) can turn safe_region into a geometry with
        # hundreds of thousands of vertices. The two buffer() calls below are
        # then dominated by that vertex count. Refinement only cares about
        # *roughly where* a corridor narrower than the coarse grid might be —
        # not the exact coastline shape, which every other validation layer
        # (edge collision checks, the independent verifier) still checks
        # against the real, unsimplified obstacle geometry — so simplifying
        # here first is safe and keeps this detection step from scaling with
        # how detailed the underlying coastline data happens to be.
        simplify_tolerance = min(50.0, self.grid_resolution_m / 20.0)
        safe_region = safe_region.simplify(simplify_tolerance, preserve_topology=True)
        if not safe_region.is_valid:
            safe_region = safe_region.buffer(0)

        half = self.grid_resolution_m / 2.0
        try:
            opening = safe_region.buffer(-half).buffer(half)
            narrow_parts = safe_region.difference(opening)
        except Exception:
            return

        if narrow_parts.is_empty:
            return

        components = list(narrow_parts.geoms) if hasattr(narrow_parts, "geoms") else [narrow_parts]

        # Skip components too small to matter: pure numerical slivers left
        # over from the buffer round-trip, not a real corridor.
        min_area = max(self.clearance_m, 1.0) * self.refined_resolution_m * 0.25

        for component in components:
            if self.refined_component_count >= self.max_refined_components:
                break
            if self.refined_node_count >= self.max_refined_nodes:
                break
            if component.is_empty or component.area < min_area:
                continue

            self._refine_component(component)

    def _refine_component(self, component) -> None:
        """Lay a local fine grid over one missed-corridor component and
        stitch it into the existing graph."""
        self.refined_component_count += 1
        res = self.refined_resolution_m
        minx, miny, maxx, maxy = component.bounds
        # Pad by one fine cell so nodes right at the component's edge, and
        # the stitching edges out of it, have room.
        minx -= res
        miny -= res
        maxx += res
        maxy += res

        new_node_ids: list[int] = []

        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                if self.refined_node_count >= self.max_refined_nodes:
                    break
                point = Point(x, y)
                if self._is_in_water(point) and self._not_on_land(point):
                    key = (round(x, 2), round(y, 2))
                    if key not in self._node_index:
                        node_id = len(self.nodes)
                        node = GraphNode(x=x, y=y, node_id=node_id)
                        self.nodes.append(node)
                        self._node_index[key] = node_id
                        self._adjacency[node_id] = []
                        new_node_ids.append(node_id)
                        self.refined_node_count += 1
                y += res
            x += res

        if not new_node_ids:
            return

        # Connect the new fine nodes to each other (8-connectivity at the
        # fine spacing) and validate those edges exactly like the coarse grid.
        offsets = [
            (res, 0), (-res, 0), (0, res), (0, -res),
            (res, res), (res, -res), (-res, res), (-res, -res),
        ]
        for node_id in new_node_ids:
            node = self.nodes[node_id]
            for dx, dy in offsets:
                key = (round(node.x + dx, 2), round(node.y + dy, 2))
                neighbor_id = self._node_index.get(key)
                if neighbor_id is not None and neighbor_id != node_id:
                    self._add_validated_edge(node_id, neighbor_id)

        # Stitch: connect each new fine node to nearby pre-existing nodes
        # (coarse grid, or a previously refined patch) so this component
        # isn't left floating disconnected from the rest of the graph.
        #
        # Regression note: this used to build the candidate list with
        # ``n.node_id not in new_node_ids`` (a linear scan of a *list* per
        # node) and then, for every new node, linearly scan every other node
        # in the whole graph to measure distance — O(new_nodes * all_nodes)
        # per component. On the real Bodrum/Kos fixture (13k+ nodes, a dozen+
        # refined components) that alone pushed a single API request from
        # ~1.3s to ~4.8s, right at CLAUDE.md Section 25's 5s budget. An
        # STRtree spatial index turns each node's neighbor search into a
        # bounding-box query instead of a full scan.
        stitch_radius = max(self.grid_resolution_m, res) * 1.6
        new_node_id_set = set(new_node_ids)
        existing_ids = [n.node_id for n in self.nodes if n.node_id not in new_node_id_set]
        if existing_ids:
            existing_points = [Point(self.nodes[i].x, self.nodes[i].y) for i in existing_ids]
            tree = STRtree(existing_points)
            for node_id in new_node_ids:
                node = self.nodes[node_id]
                query_area = Point(node.x, node.y).buffer(stitch_radius)
                for idx in tree.query(query_area):
                    other_id = existing_ids[int(idx)]
                    other = self.nodes[other_id]
                    dist = math.hypot(other.x - node.x, other.y - node.y)
                    if dist <= stitch_radius:
                        self._add_validated_edge(node_id, other_id)

    def _add_validated_edge(self, from_id: int, to_id: int) -> bool:
        """Validate and register an edge symmetrically in both directions.

        Mirrors :meth:`_validate_edges`'s per-edge logic for edges added
        after the initial bulk pass (refinement stitching). Must add BOTH
        directions to ``self._adjacency``/``self._edge_cost`` — those are
        what ``shortest_path``'s own Dijkstra walks directly, unlike
        :meth:`adjacency_with_costs` (used by the isochrone cost field),
        which symmetrizes automatically at read time. A one-way stitch here
        would silently make a refined corridor usable outbound but not on
        the return leg.
        """
        from_node = self.nodes[from_id]
        to_node = self.nodes[to_id]
        geometry = LineString([(from_node.x, from_node.y), (to_node.x, to_node.y)])

        if not self._segment_is_clear(
            (from_node.x, from_node.y), (to_node.x, to_node.y)
        ):
            return False

        if to_id in self._adjacency[from_id]:
            return True

        distance_m = geometry.length
        self.edges.append(GraphEdge(
            from_node=from_node,
            to_node=to_node,
            geometry=geometry,
            distance_m=distance_m,
            fuel_cost_l=0.0,
            is_valid=True,
        ))
        self._adjacency[from_id].append(to_id)
        self._adjacency[to_id].append(from_id)
        self._edge_cost[(from_id, to_id)] = distance_m
        self._edge_cost[(to_id, from_id)] = distance_m
        return True

    def set_fuel_rate(self, lpnm: float) -> None:
        """Set the fuel consumption rate and recalculate edge costs.

        Args:
            lpnm: Liters per nautical mile.
        """
        self._edge_cost = {}
        for edge in self.edges:
            distance_nm = edge.distance_m / 1852.0
            edge.fuel_cost_l = distance_nm * lpnm
            self._edge_cost[(edge.from_node.node_id, edge.to_node.node_id)] = edge.fuel_cost_l
            self._edge_cost[(edge.to_node.node_id, edge.from_node.node_id)] = edge.fuel_cost_l

    def _is_point_navigable(self, point: Point) -> bool:
        """True if ``point`` is strictly in water and clear of every obstacle.

        Mirrors :meth:`_is_in_water` / :meth:`_not_on_land` — used to fail
        closed on a query point rather than silently snapping it to whatever
        node happens to be nearest (CLAUDE.md Section 43).
        """
        return self._is_in_water(point) and self._not_on_land(point)

    def _snap_endpoint(self, point: Point) -> GraphNode | None:
        """Find the graph node to route from/to a requested point, or refuse.

        Returns ``None`` (fail closed) when:
          * the graph has no nodes,
          * the point itself is not navigable water, or
          * the nearest node is farther away than 1.5 grid cells — which means
            the point is outside the water extent, not just off-grid.
        """
        if not self.nodes:
            return None
        if not self._is_point_navigable(point):
            return None

        node = self._find_nearest_node(point)
        if node is None:
            return None
        gap_m = math.hypot(node.x - point.x, node.y - point.y)
        if gap_m > 1.5 * self.grid_resolution_m:
            return None
        return node

    def _segment_is_clear(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        """True if the straight segment ``a -> b`` stays in navigable water.

        Checks both that it crosses no obstacle *and* that it never leaves
        the mapped water polygon — the second half matters once
        :meth:`_line_of_sight_simplify` starts drawing shortcuts the original
        Dijkstra path never took; an obstacle-free straight line could still
        cut outside the compute extent or across an unmapped gap without this.
        """
        segment = LineString([a, b])
        if self._obstacle_union_prepared is not None and self._obstacle_union_prepared.intersects(segment):
            return False
        if not self._water_containment_mask_prepared.contains(segment):
            return False
        return True

    def _line_of_sight_simplify(
        self, coords: list[tuple[float, float]]
    ) -> list[tuple[float, float]]:
        """Collapse a grid-constrained path to its minimal collision-free shape.

        Regression note: 8-connectivity fixed distance accuracy but not path
        *shape* — Dijkstra is blind to which of several equal-cost paths it
        returns, so a route whose start/end offset isn't a multiple of 45°
        (almost all of them) could come back as an arbitrary strand of
        diagonal-then-cardinal-then-diagonal moves: a visible zigzag/"S" even
        in open water with no obstacle anywhere nearby.

        This is the same navigation-aware line-of-sight simplification
        CLAUDE.md Section 10.2 asks for on polygon boundaries, applied to
        route lines instead: greedily connect each point directly to the
        farthest later point still reachable by a collision-free straight
        segment, dropping everything in between. It never makes the route
        cross anything the original path didn't already avoid — every
        replacement segment is checked with the same :meth:`_segment_is_clear`
        the graph itself is built from — and it strictly shortens or leaves
        unchanged the path length, since every kept vertex is one the raw
        path already visited in order. A fully open, unobstructed origin ->
        destination now collapses to a single straight segment, as it should.
        """
        if len(coords) <= 2:
            return coords

        simplified = [coords[0]]
        i = 0
        n = len(coords)
        while i < n - 1:
            j = n - 1
            while j > i + 1 and not self._segment_is_clear(coords[i], coords[j]):
                j -= 1
            simplified.append(coords[j])
            i = j
        return simplified

    def shortest_path(
        self, origin: Point, destination: Point
    ) -> LineString | None:
        """Find the shortest navigable path between two points.

        Uses Dijkstra's algorithm on the water polygon graph, with the exact
        requested origin/destination coordinates as the path's first/last
        vertex (not the snapped grid node) whenever that connecting segment is
        itself collision-free.

        Args:
            origin: Start point (in same CRS as graph nodes)
            destination: End point (in same CRS as graph nodes)

        Returns:
            LineString representing the route, or None if no path exists —
            including when ``origin`` or ``destination`` is not itself
            navigable water (CLAUDE.md Section 43: fail closed, never route
            from/to a point that isn't actually reachable).
        """
        # Find nearest nodes to origin and destination — fails closed if either
        # requested point is not navigable water or too far from the grid.
        origin_node = self._snap_endpoint(origin)
        dest_node = self._snap_endpoint(destination)

        if origin_node is None or dest_node is None:
            return None

        origin_xy = (origin.x, origin.y)
        dest_xy = (destination.x, destination.y)

        if origin_node.node_id == dest_node.node_id:
            # Sub-grid-cell hop — still must be checked against obstacles, a
            # narrow spit of land can sit inside a single cell.
            if not self._segment_is_clear(origin_xy, dest_xy):
                return None
            return LineString([origin_xy, dest_xy])

        # Dijkstra's algorithm
        dist = {node.node_id: float("inf") for node in self.nodes}
        dist[origin_node.node_id] = 0
        prev: dict[int, int | None] = {node.node_id: None for node in self.nodes}
        visited: set[int] = set()
        heap = [(0, origin_node.node_id)]

        while heap:
            current_dist, current_id = heappop(heap)

            if current_id in visited:
                continue
            visited.add(current_id)

            if current_id == dest_node.node_id:
                break

            for neighbor_id in self._adjacency.get(current_id, []):
                if neighbor_id in visited:
                    continue

                # Find edge cost
                edge_cost = self._get_edge_cost(current_id, neighbor_id)
                new_dist = current_dist + edge_cost

                if new_dist < dist[neighbor_id]:
                    dist[neighbor_id] = new_dist
                    prev[neighbor_id] = current_id
                    heappush(heap, (new_dist, neighbor_id))

        # Reconstruct path
        if dist[dest_node.node_id] == float("inf"):
            return None  # No path found

        path_ids: list[int | None] = []
        current: int | None = dest_node.node_id
        while current is not None:
            path_ids.append(current)
            current = prev[current]

        path_ids.reverse()

        # Convert node IDs to coordinates
        coords = [
            (self.nodes[i].x, self.nodes[i].y) for i in path_ids if i is not None
        ]

        # Splice in the exact requested endpoints when the connecting segment
        # to the first/last grid node is itself collision-free. Otherwise keep
        # the grid node — never silently draw a segment that might cross land.
        if coords and self._segment_is_clear(origin_xy, coords[0]):
            coords = [origin_xy] + coords
        if coords and self._segment_is_clear(coords[-1], dest_xy):
            coords = coords + [dest_xy]

        # Collapse the grid-constrained zigzag to the shortest collision-free
        # shape (CLAUDE.md Section 10.2, applied to the route line — see
        # _line_of_sight_simplify). Every kept vertex was already on the
        # validated Dijkstra path, so this can only shorten the route, never
        # introduce a crossing the raw path didn't already avoid.
        coords = self._line_of_sight_simplify(coords)

        return LineString(coords)

    def _find_nearest_node(self, point: Point) -> GraphNode | None:
        """Find the nearest graph node to a given point."""
        if not self.nodes:
            return None

        best = min(self.nodes, key=lambda n: math.hypot(n.x - point.x, n.y - point.y))
        return best

    def nearest_node(self, point: Point) -> GraphNode | None:
        """Public: find the nearest graph node to a given point.

        Args:
            point: Point in the same projected CRS as the graph nodes.

        Returns:
            The nearest GraphNode, or None if the graph has no nodes.
        """
        return self._find_nearest_node(point)

    def add_query_node(
        self,
        point: Point,
        *,
        max_connection_distance_m: float | None = None,
        max_connections: int = 16,
    ) -> GraphNode | None:
        """Insert an exact navigable endpoint and stitch it into the graph.

        A coarse grid can place the nearest node in a tiny refined shoreline
        component even when the requested point has clear line of sight to the
        main water graph. Starting Dijkstra from that isolated node produces
        an empty isochrone. Query endpoints therefore become real graph nodes
        and are connected only by the same exact water/obstacle segment check
        used for every other edge.
        """
        if not self._built or not self._is_point_navigable(point):
            return None

        if not self.nodes:
            return None

        # Label current connected components. Refined shoreline patches can
        # contain the closest/exact node while remaining disconnected from
        # the useful open-water graph. Prefer candidates from the largest
        # safely visible component instead of whichever node happens to be
        # closest in Euclidean distance.
        component_for: dict[int, int] = {}
        component_sizes: list[int] = []
        for seed in range(len(self.nodes)):
            if seed in component_for:
                continue
            component_id = len(component_sizes)
            stack = [seed]
            component_for[seed] = component_id
            size = 0
            while stack:
                current = stack.pop()
                size += 1
                for neighbor in self._adjacency.get(current, []):
                    if neighbor not in component_for:
                        component_for[neighbor] = component_id
                        stack.append(neighbor)
            component_sizes.append(size)

        key = (round(point.x, 2), round(point.y, 2))
        existing_id = self._node_index.get(key)
        existing_component = component_for.get(existing_id) if existing_id is not None else None
        largest_size = max(component_sizes)
        if existing_component is not None and component_sizes[existing_component] == largest_size:
            return self.nodes[existing_id]

        candidates = [node for node in self.nodes if node.node_id != existing_id]

        radius = max_connection_distance_m or (2.5 * self.grid_resolution_m)
        nearby = sorted(
            (
                (
                    -component_sizes[component_for[node.node_id]],
                    math.hypot(node.x - point.x, node.y - point.y),
                    node.node_id,
                )
                for node in candidates
                if abs(node.x - point.x) <= radius and abs(node.y - point.y) <= radius
            ),
            key=lambda item: (item[0], item[1]),
        )

        created = existing_id is None
        if created:
            node_id = len(self.nodes)
            node = GraphNode(x=float(point.x), y=float(point.y), node_id=node_id)
            self.nodes.append(node)
            self._node_index[key] = node_id
            self._adjacency[node_id] = []
        else:
            node_id = existing_id
            node = self.nodes[node_id]

        connected = 0
        for _negative_component_size, distance, other_id in nearby:
            if distance > radius:
                continue
            if existing_component is not None and component_for[other_id] == existing_component:
                continue
            if self._add_validated_edge(node_id, other_id):
                connected += 1
                if connected >= max_connections:
                    break

        if connected == 0:
            if created:
                # Keep node IDs contiguous/list-indexable by rolling back the
                # append. No edge could prove a safe connection, so fail closed.
                self.nodes.pop()
                self._node_index.pop(key, None)
                self._adjacency.pop(node_id, None)
            return None

        return node

    def adjacency_with_costs(self) -> dict[int, list[tuple[int, float]]]:
        """Build a bidirectional adjacency list with fuel costs per edge.

        Returns:
            node_id -> [(neighbor_id, edge_fuel_cost_l), ...] for every node.

        Each undirected edge in ``self.edges`` is emitted in both directions with
        the same cost, so the resulting graph is symmetric (required for the
        one-way isochrone and for a symmetric round-trip model).
        """
        adjacency: dict[int, list[tuple[int, float]]] = {
            node.node_id: [] for node in self.nodes
        }
        for edge in self.edges:
            if not edge.is_valid:
                continue
            from_id = edge.from_node.node_id
            to_id = edge.to_node.node_id
            cost = edge.fuel_cost_l
            adjacency[from_id].append((to_id, cost))
            adjacency[to_id].append((from_id, cost))
        return adjacency

    def node_point(self, node_id: int) -> Point:
        """Return the Point for a node ID."""
        node = self.nodes[node_id]
        return Point(node.x, node.y)

    def _get_edge_cost(self, from_id: int, to_id: int) -> float:
        """Get the routing cost between two adjacent nodes: O(1) dict lookup.

        Fuel cost if ``set_fuel_rate`` has been called, geometric distance
        otherwise (see the comment in ``_validate_edges``) — never the linear
        scan over ``self.edges`` this used to do, which made every Dijkstra
        expansion O(nodes * edges).
        """
        return self._edge_cost.get((from_id, to_id), float("inf"))


# ============================================================================
# Factory Function
# ============================================================================


def build_graph_from_water_polygons(
    water_polygons: MultiPolygon,
    land_geometries: list[Polygon] | None = None,
    clearance_m: float = 50.0,
    grid_resolution_m: float = 100.0,
    enable_refinement: bool = True,
    refined_resolution_m: float | None = None,
    max_refined_nodes: int = 4000,
    max_refined_components: int = 20,
    progress_callback: Callable[[int, str], None] | None = None,
) -> WaterPolygonGraph:
    """Build a navigable graph from water polygon geometry.

    This is the main entry point for creating a routing graph. It encapsulates
    the full vector-first approach: graph topology is derived from water shape,
    not raster cells.

    Args:
        water_polygons: Pre-computed navigable water geometry (MultiPolygon).
        land_geometries: Land/obstacle polygons for edge validation.
        clearance_m: Safety clearance from land in meters.
        grid_resolution_m: Node placement grid spacing in meters.
        enable_refinement: Locally refine narrow corridors the coarse grid
            missed (CLAUDE.md Section 7.2/11) — see
            :meth:`WaterPolygonGraph._refine_missed_corridors`.
        refined_resolution_m: Node spacing inside refined patches (defaults
            to ``max(20.0, grid_resolution_m / 4)``).
        max_refined_nodes: Hard cap on nodes added by refinement.
        max_refined_components: Hard cap on how many corridors get refined.

    Returns:
        A fully constructed WaterPolygonGraph ready for routing queries.
    """
    graph = WaterPolygonGraph(
        water_polygons=water_polygons,
        land_geometries=land_geometries or [],
        clearance_m=clearance_m,
        grid_resolution_m=grid_resolution_m,
        enable_refinement=enable_refinement,
        refined_resolution_m=refined_resolution_m,
        max_refined_nodes=max_refined_nodes,
        max_refined_components=max_refined_components,
        progress_callback=progress_callback,
    )
    return graph.build()
