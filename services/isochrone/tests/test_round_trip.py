"""Round-trip isochrone tests — CLAUDE.md Section 9.

    "For every candidate point: outbound_min_cost + return_min_cost <=
    usable_fuel. Use true navigable costs. Do not implement:
    round_trip_range = one_way_range / 2. Even with symmetric costs, keep the
    two-cost architecture because future directional conditions will make
    outbound and return costs different."

This file checks both halves of that requirement:

1. the two-cost-field ARCHITECTURE is real — ``compute_return_cost_field``
   is a genuinely independent computation that gives different answers than
   the outbound field on an asymmetric graph (proving it isn't secretly
   reusing/copying the outbound field);
2. the round-trip RESULT is correct on the actual engine — smaller than the
   one-way area, never leaking through land, and (as a derived consequence
   of today's symmetric edge costs, not as the implementation strategy)
   numerically identical to a one-way run at half the fuel.
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")
sys.path.insert(0, "../routing")
sys.path.insert(0, "../marine-data")

import pytest

from marine_data.bodrum_kos_fixture import generate_bodrum_kos_water
from isochrone.cost_field import (
    compute_cost_field,
    compute_return_cost_field,
    reverse_adjacency,
)
from isochrone.engine import calculate_range, RANGE_MODES

ORIGIN_WGS84 = (27.30, 36.95)
LPNM = 2.19
RESOLUTION_M = 800.0


# ============================================================================
# Unit level: the return cost field is a real, independent computation
# ============================================================================


class TestReverseAdjacencyArchitecture:
    """Prove compute_return_cost_field isn't just compute_cost_field twice."""

    def test_reverse_adjacency_transposes_directed_edges(self):
        # A one-way street: 0 -> 1 costs 5, but there is no 1 -> 0 edge at all.
        adjacency = {0: [(1, 5.0)], 1: []}
        reversed_adj = reverse_adjacency(adjacency)
        assert reversed_adj[1] == [(0, 5.0)]
        assert reversed_adj[0] == []

    def test_return_cost_field_differs_from_outbound_on_asymmetric_graph(self):
        """The whole point of Section 9's two-cost architecture: when costs
        genuinely differ by direction, outbound and return fields must too.

        Graph: 0 -> 1 costs 1 (cheap downstream), 1 -> 0 costs 100 (expensive
        upstream) — e.g. a strong current. Reaching node 1 from origin 0 is
        cheap; RETURNING from node 1 to origin 0 is expensive.
        """
        adjacency = {0: [(1, 1.0)], 1: [(0, 100.0)]}
        all_nodes = [0, 1]

        outbound = compute_cost_field(0, adjacency, all_nodes)
        assert outbound.costs[1] == pytest.approx(1.0)

        return_field = compute_return_cost_field(0, adjacency, all_nodes)
        # Cost of getting FROM node 1 BACK TO origin 0 must use the 1->0 edge
        # (100), not the 0->1 edge (1) — a naive "just reuse outbound" bug
        # would silently return 1.0 here instead of 100.0.
        assert return_field.costs[1] == pytest.approx(100.0)
        assert return_field.costs[1] != outbound.costs[1]

    def test_return_cost_field_matches_outbound_on_symmetric_graph(self):
        """Today's real edge costs ARE symmetric, so the two fields should
        currently agree exactly — this is what makes the emergent
        one_way(F/2) equivalence in TestRoundTripEngine correct."""
        adjacency = {0: [(1, 3.0)], 1: [(0, 3.0), (2, 4.0)], 2: [(1, 4.0)]}
        all_nodes = [0, 1, 2]
        outbound = compute_cost_field(0, adjacency, all_nodes)
        return_field = compute_return_cost_field(0, adjacency, all_nodes)
        assert outbound.costs == return_field.costs


# ============================================================================
# Engine level: real geography, real fuel model, real validation
# ============================================================================


@pytest.fixture(scope="module")
def geometry():
    water, land = generate_bodrum_kos_water()
    return {"water": water, "land": land}


class TestRoundTripEngine:
    def test_invalid_range_mode_rejected(self, geometry):
        with pytest.raises(ValueError):
            calculate_range(
                origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=60.0,
                resolution_m=RESOLUTION_M, clearance_m=50.0,
                water=geometry["water"], land=geometry["land"],
                range_mode="there_and_back_again",
            )

    def test_round_trip_result_reports_its_mode(self, geometry):
        result = calculate_range(
            origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=60.0,
            resolution_m=RESOLUTION_M, clearance_m=50.0,
            water=geometry["water"], land=geometry["land"],
            range_mode="round_trip",
        )
        assert result.range_mode == "round_trip"
        assert result.to_dict()["range_mode"] == "round_trip"

    def test_round_trip_area_is_smaller_than_one_way(self, geometry):
        """Paying for the return leg must shrink the reachable set — a round
        trip range computed as if it were one-way (or as half of it without
        actually pricing the return leg) would get this wrong."""
        one_way = calculate_range(
            origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=60.0,
            resolution_m=RESOLUTION_M, clearance_m=50.0,
            water=geometry["water"], land=geometry["land"], range_mode="one_way",
        )
        round_trip = calculate_range(
            origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=60.0,
            resolution_m=RESOLUTION_M, clearance_m=50.0,
            water=geometry["water"], land=geometry["land"], range_mode="round_trip",
        )
        assert round_trip.bands[1.0].polygon.area < one_way.bands[1.0].polygon.area

    def test_round_trip_equals_half_fuel_one_way_under_symmetric_costs(self, geometry):
        """Derived-property check, NOT the implementation: with today's
        symmetric edge costs, outbound_cost == return_cost at every node, so
        "outbound + return <= F" is mathematically equivalent to
        "outbound <= F/2" — i.e. round_trip(fuel=F) must come out IDENTICAL
        to one_way(fuel=F/2).

        This looks like the forbidden "round_trip = one_way / 2" shortcut
        (CLAUDE.md Section 44.10) but isn't: the engine never divides
        usable_fuel_l by two anywhere (see IsochroneEngine.compute) — it
        always runs two independent Dijkstra expansions and sums them. The
        equivalence only holds because of the current data, and
        TestReverseAdjacencyArchitecture above proves the two fields would
        diverge the moment edge costs stop being symmetric.
        """
        round_trip = calculate_range(
            origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=60.0,
            resolution_m=RESOLUTION_M, clearance_m=50.0,
            water=geometry["water"], land=geometry["land"], range_mode="round_trip",
        )
        half_fuel_one_way = calculate_range(
            origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=30.0,
            resolution_m=RESOLUTION_M, clearance_m=50.0,
            water=geometry["water"], land=geometry["land"], range_mode="one_way",
        )
        a = round_trip.bands[1.0].polygon
        b = half_fuel_one_way.bands[1.0].polygon
        assert a.symmetric_difference(b).area < 1.0  # equal up to raster/clip noise

    def test_round_trip_bands_pass_independent_verification(self, geometry):
        """The same land-leak / clearance verifier applies to round-trip
        bands as to one-way bands — round-trip mode must not bypass it."""
        result = calculate_range(
            origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=60.0,
            resolution_m=RESOLUTION_M, clearance_m=50.0,
            water=geometry["water"], land=geometry["land"], range_mode="round_trip",
        )
        assert result.all_polygons_valid
        for frac, band in result.bands.items():
            assert band.validation.ok, f"{int(frac*100)}% round-trip band failed: {band.validation.to_dict()}"

    def test_round_trip_bands_grow_monotonically(self, geometry):
        result = calculate_range(
            origin_wgs84=ORIGIN_WGS84, lpnm=LPNM, usable_fuel_l=60.0,
            resolution_m=RESOLUTION_M, clearance_m=50.0,
            water=geometry["water"], land=geometry["land"], range_mode="round_trip",
        )
        areas = [result.bands[f].polygon.area for f in (0.25, 0.50, 0.75, 1.00)]
        for a, b in zip(areas, areas[1:]):
            assert b >= a - 1.0
