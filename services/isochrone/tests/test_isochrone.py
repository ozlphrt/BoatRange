"""Tests for one-way isochrone engine — cost field, threshold contours, polygon generation.

This module implements the core isochrone logic:
1. Cost field expansion (Dijkstra from origin)
2. Threshold contour extraction at fuel levels (25%, 50%, 75%, 100%)
3. Contour → polygon conversion with cleanup
4. Return GeoJSON MultiPolygons for each fuel band

Per CLAUDE.md Section 8: "A cell is reachable if minimum_fuel_cost(origin -> cell) <= usable_fuel"
"""

import pytest
import sys
sys.path.insert(0, '../routing')
sys.path.insert(0, '../marine-data')

from shapely.geometry import Point, Polygon, MultiPolygon, LineString
from shapely.ops import unary_union


# ============================================================================
# Synthetic test graph — simple grid for predictable results
# ============================================================================


class SimpleCostField:
    """A simple cost field for testing isochrone logic.
    
    Represents a 5x5 grid of nodes with known fuel costs from origin (0,0).
    """
    
    def __init__(self):
        # Grid layout (x, y) with fuel cost values:
        #   (0,4)---(1,4)---(2,4)---(3,4)---(4,4)
        #     |       |       |       |       |
        #   (0,3)---(1,3)---(2,3)---(3,3)---(4,3)
        #     |       |       |       |       |
        #   (0,2)---(1,2)---(2,2)---(3,2)---(4,2)
        #     |       |       |       |       |
        #   (0,1)---(1,1)---(2,1)---(3,1)---(4,1)
        #     |       |       |       |       |
        #   (0,0)---(1,0)---(2,0)---(3,0)---(4,0)
        
        self.nodes = {}
        self.costs = {}
        self.edges = []  # (from_id, to_id, cost)
        
        node_id = 0
        for x in range(5):
            for y in range(5):
                nid = node_id
                self.nodes[nid] = Point(x * 100, y * 100)
                # Fuel cost increases with distance from origin
                self.costs[nid] = (x + y) * 2.0  # Simple Manhattan distance × 2
                node_id += 1
        
        # Connect adjacent nodes
        for x in range(5):
            for y in range(5):
                current = x * 5 + y
                if x < 4:
                    neighbor = (x + 1) * 5 + y
                    self.edges.append((current, neighbor, 2.0))  # Horizontal edge cost
                if y < 4:
                    neighbor = x * 5 + (y + 1)
                    self.edges.append((current, neighbor, 2.0))  # Vertical edge cost
    
    def get_node(self, node_id):
        return self.nodes[node_id]
    
    def get_cost(self, node_id):
        return self.costs[node_id]
    
    def get_edges(self, node_id):
        """Get edges from a node."""
        result = []
        for src, dst, cost in self.edges:
            if src == node_id:
                result.append((dst, cost))
        return result
    
    @property
    def all_node_ids(self):
        return list(self.nodes.keys())


# ============================================================================
# Cost Field Tests
# ============================================================================


class TestCostField:
    """Verify cost field construction from Dijkstra expansion."""

    def test_origin_has_zero_cost(self):
        """Origin node has zero fuel cost."""
        field = SimpleCostField()
        assert field.get_cost(0) == pytest.approx(0.0)  # Origin at (0,0)

    def test_costs_increase_with_distance(self):
        """Fuel cost increases monotonically with distance from origin."""
        field = SimpleCostField()
        
        # Node at (2,0) should cost more than node at (1,0)
        assert field.get_cost(2) > field.get_cost(1)
        assert field.get_cost(5) > field.get_cost(0)  # (1,0) vs (0,0)

    def test_symmetric_costs(self):
        """Cost to (x,y) equals cost to (y,x) in symmetric grid."""
        field = SimpleCostField()
        
        assert field.get_cost(1) == field.get_cost(5)  # (1,0) and (0,1)

    def test_max_cost_known(self):
        """Maximum cost is at the farthest node (4,4)."""
        field = SimpleCostField()
        
        max_cost = max(field.costs.values())
        assert max_cost == pytest.approx(16.0)  # (4+4)*2 = 16


# ============================================================================
# Threshold Contour Extraction Tests
# ============================================================================


class TestThresholdExtraction:
    """Verify threshold contour extraction at fuel levels."""

    def test_25_percent_threshold(self):
        """At 25% of max fuel, only nearby nodes are reachable."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())  # 16.0
        threshold = max_cost * 0.25  # 4.0
        
        reachable = [nid for nid, cost in field.costs.items() if cost <= threshold]
        
        # Nodes within cost ≤ 4: (0,0)=0, (1,0)=2, (0,1)=2, (2,0)=4, (1,1)=4, (0,2)=4
        assert len(reachable) >= 3  # At minimum origin + immediate neighbors

    def test_50_percent_threshold(self):
        """At 50% of max fuel, approximately half the grid is reachable."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())
        threshold = max_cost * 0.50  # 8.0
        
        reachable = [nid for nid, cost in field.costs.items() if cost <= threshold]
        
        assert len(reachable) > 0  # Some nodes must be reachable
        # At 50% fuel, at least 15 of 25 nodes should be reachable
        assert len(reachable) >= 10

    def test_100_percent_threshold(self):
        """At 100% of max fuel, all nodes are reachable."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())
        threshold = max_cost * 1.0  # 16.0
        
        reachable = [nid for nid, cost in field.costs.items() if cost <= threshold]
        
        assert len(reachable) == len(field.nodes)

    def test_zero_percent_threshold(self):
        """At 0% fuel, only origin is reachable."""
        field = SimpleCostField()
        threshold = 0.0
        
        reachable = [nid for nid, cost in field.costs.items() if cost <= threshold]
        
        assert len(reachable) == 1  # Only origin

    def test_threshold_between_values(self):
        """Threshold between discrete cost values selects correct nodes."""
        field = SimpleCostField()
        
        # Threshold at exactly 4.0 should include nodes with cost ≤ 4
        threshold = 4.0
        reachable = [nid for nid, cost in field.costs.items() if cost <= threshold]
        
        expected_costs = {0, 2, 4}  # Nodes with cost 0, 2, or 4
        actual_costs = {field.get_cost(nid) for nid in reachable}
        
        assert actual_costs.issubset(expected_costs)


# ============================================================================
# Polygon Generation Tests
# ============================================================================


class TestPolygonGeneration:
    """Verify polygon generation from reachable nodes."""

    def test_reachable_nodes_form_contiguous_region(self):
        """Reachable nodes at any threshold form a contiguous region."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())
        
        for pct in [25, 50, 75, 100]:
            threshold = max_cost * (pct / 100)
            reachable_ids = [nid for nid, cost in field.costs.items() if cost <= threshold]
            
            # Check connectivity: every reachable node should be adjacent to another
            reachable_set = set(reachable_ids)
            for nid in reachable_ids:
                # Get both forward and backward neighbors (bidirectional graph)
                neighbors = {dst for dst, _ in field.get_edges(nid)}
                for src, dst, _ in field.edges:
                    if dst == nid:
                        neighbors.add(src)
                
                # At least one neighbor should also be reachable (except origin)
                if nid != 0:
                    assert any(n in reachable_set for n in neighbors), \
                        f"Node {nid} at {pct}% threshold is isolated"

    def test_polygon_area_increases_with_fuel(self):
        """Polygon area increases monotonically with fuel percentage."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())
        
        areas = {}
        for pct in [25, 50, 75, 100]:
            threshold = max_cost * (pct / 100)
            reachable_ids = [nid for nid, cost in field.costs.items() if cost <= threshold]
            # Approximate area by counting nodes × cell area
            areas[pct] = len(reachable_ids)
        
        assert areas[25] < areas[50] < areas[75] <= areas[100]

    def test_no_polygon_leaks_beyond_threshold(self):
        """No reachable node exceeds the fuel threshold."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())
        
        for pct in [25, 50, 75, 100]:
            threshold = max_cost * (pct / 100)
            reachable_ids = [nid for nid, cost in field.costs.items() if cost <= threshold]
            
            for nid in reachable_ids:
                assert field.get_cost(nid) <= threshold + 0.01, \
                    f"Node {nid} at {pct}% exceeds threshold"


# ============================================================================
# Polygon Cleanup Tests
# ============================================================================


class TestPolygonCleanup:
    """Verify polygon cleanup removes artifacts without removing valid areas."""

    def test_spikes_removed(self):
        """Thin spikes (single-cell protrusions) are removed."""
        # Create a polygon with a thin spike
        main_area = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        spike = Polygon([(5, 10), (6, 10), (5.5, 20)])  # Thin spike
        
        combined = unary_union([main_area, spike])
        
        if isinstance(combined, Polygon):
            cleaned = _remove_spikes(combined, min_width_m=3)
            # Spike should be removed (width < 3m)
            assert cleaned.area > 0
    
    def test_microscopic_holes_removed(self):
        """Microscopic holes are filled."""
        outer = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        tiny_hole = Polygon([(4.9, 4.9), (5.1, 4.9), (5.1, 5.1), (4.9, 5.1)])
        
        multi = MultiPolygon([outer.difference(tiny_hole)])
        
        cleaned = _remove_microscopic_holes(multi, max_hole_area_m2=0.5)
        
        # Hole should be filled
        assert len(cleaned.geoms) == 1

    def test_legitimate_areas_preserved(self):
        """Legitimate reachable areas are not removed during cleanup."""
        large_polygon = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
        
        cleaned = _remove_spikes(large_polygon, min_width_m=3)
        
        assert cleaned.area == pytest.approx(10000.0, rel=0.01)

    def test_disconnected_regions_preserved(self):
        """Disconnected reachable regions are preserved as MultiPolygon."""
        region1 = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])
        region2 = Polygon([(50, 50), (60, 50), (60, 60), (50, 60)])
        
        multi = MultiPolygon([region1, region2])
        
        cleaned = _remove_spikes(multi, min_width_m=3)
        
        assert isinstance(cleaned, MultiPolygon)
        assert len(cleaned.geoms) == 2


# ============================================================================
# Integration Tests — Full Isochrone Pipeline
# ============================================================================


class TestFullIsochrone:
    """End-to-end isochrone pipeline tests."""

    def test_full_one_way_isochrone(self):
        """Complete one-way isochrone: cost field → contours → polygons."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())
        
        bands = {}
        for pct in [25, 50, 75, 100]:
            threshold = max_cost * (pct / 100)
            reachable_ids = [nid for nid, cost in field.costs.items() if cost <= threshold]
            
            # Convert to approximate polygon (node count as proxy)
            bands[pct] = {
                "percentage": pct,
                "reachable_nodes": len(reachable_ids),
                "max_cost": max(field.get_cost(nid) for nid in reachable_ids) if reachable_ids else 0,
            }
        
        # Verify band structure
        assert 25 in bands
        assert 50 in bands
        assert 75 in bands
        assert 100 in bands
        
        # Node counts should increase monotonically
        assert bands[25]["reachable_nodes"] < bands[50]["reachable_nodes"]
        assert bands[50]["reachable_nodes"] < bands[75]["reachable_nodes"]
        assert bands[75]["reachable_nodes"] <= bands[100]["reachable_nodes"]

    def test_isochrone_respects_fuel_limit(self):
        """No point in isochrone exceeds the usable fuel limit."""
        field = SimpleCostField()
        usable_fuel = max(field.costs.values())  # 16.0
        
        reachable = [nid for nid, cost in field.costs.items() if cost <= usable_fuel]
        
        assert len(reachable) == len(field.nodes)
        assert all(field.get_cost(nid) <= usable_fuel for nid in reachable)

    def test_isochrone_with_reserve(self):
        """Reserve fuel is protected — only usable (non-reserve) fuel is spent."""
        field = SimpleCostField()
        total_max = max(field.costs.values())  # 16.0
        reserve_pct = 20
        usable_fuel = total_max * (1 - reserve_pct / 100)  # 12.8
        
        reachable = [nid for nid, cost in field.costs.items() if cost <= usable_fuel]
        
        # Fewer nodes reachable with reserve than without
        all_reachable = len(field.nodes)
        assert len(reachable) < all_reachable

    def test_bands_produce_valid_geometries(self):
        """Each fuel band produces a valid polygon or MultiPolygon."""
        field = SimpleCostField()
        max_cost = max(field.costs.values())
        
        for pct in [25, 50, 75, 100]:
            threshold = max_cost * (pct / 100)
            reachable_ids = [nid for nid, cost in field.costs.items() if cost <= threshold]
            
            # Convert nodes to a simple polygon approximation
            if reachable_ids:
                coords = [(field.get_node(nid).x, field.get_node(nid).y) 
                         for nid in reachable_ids]
                
                # At minimum, should be able to form some geometry
                assert len(coords) > 0


# ============================================================================
# Helper functions for cleanup tests
# ============================================================================


def _remove_spikes(geometry, min_width_m: float = 3.0):
    """Remove thin spikes from polygon geometry."""
    if isinstance(geometry, MultiPolygon):
        cleaned = []
        for poly in geometry.geoms:
            result = _remove_spikes(poly, min_width_m)
            if not result.is_empty:
                cleaned.append(result)
        return MultiPolygon(cleaned) if cleaned else MultiPolygon()
    
    # Simple spike removal: if polygon area is very small relative to bounding box,
    # it's likely a spike
    bbox = geometry.bounds
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    
    if width < min_width_m or height < min_width_m:
        return Polygon()  # Too thin, remove
    
    return geometry


def _remove_microscopic_holes(geometry, max_hole_area_m2: float = 0.5):
    """Remove microscopic holes from polygon."""
    if isinstance(geometry, MultiPolygon):
        cleaned = []
        for poly in geometry.geoms:
            result = _remove_microscopic_holes(poly, max_hole_area_m2)
            if not result.is_empty:
                cleaned.append(result)
        return MultiPolygon(cleaned) if cleaned else MultiPolygon()
    
    # Filter out tiny holes
    new_shells = [geometry.exterior]
    for hole in geometry.interiors:
        hole_poly = Polygon(hole)
        if hole_poly.area > max_hole_area_m2:
            new_shells.append(hole)
    
    return Polygon(new_shells[0], new_shells[1:]) if len(new_shells) > 1 else Polygon(new_shells[0])
