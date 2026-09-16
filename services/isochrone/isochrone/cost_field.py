"""Cost field computation — Dijkstra expansion from origin.

Per CLAUDE.md Section 8: "A cell is reachable if minimum_fuel_cost(origin -> cell) <= usable_fuel"

This module computes the fuel cost from the origin to every node in the graph,
using Dijkstra's algorithm with fuel cost as the edge weight.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class CostField:
    """Fuel cost from origin to every node in the graph.

    Attributes:
        costs: Mapping of node_id → minimum fuel cost from origin
        predecessors: For path reconstruction — node_id → (predecessor_id, edge_cost)
        origin_id: The origin node ID
    """

    costs: Dict[int, float] = field(default_factory=dict)
    predecessors: Dict[int, Tuple[int, float]] = field(default_factory=dict)
    origin_id: int = 0


def compute_cost_field(
    origin_id: int,
    adjacency: Dict[int, List[Tuple[int, float]]],
    all_node_ids: List[int],
) -> CostField:
    """Compute fuel cost from origin to every node using Dijkstra.

    Args:
        origin_id: ID of the origin node
        adjacency: node_id → [(neighbor_id, edge_fuel_cost), ...]
        all_node_ids: All valid node IDs in the graph

    Returns:
        CostField with costs and predecessors for path reconstruction.

    Per CLAUDE.md Section 7.4: "compute fuel cost rather than only geometric distance"
    """
    costs: Dict[int, float] = {nid: float("inf") for nid in all_node_ids}
    predecessors: Dict[int, Tuple[int, float]] = {}
    costs[origin_id] = 0.0

    # Min-heap: (cost, node_id)
    heap: List[Tuple[float, int]] = [(0.0, origin_id)]
    visited: set[int] = set()

    while heap:
        current_cost, current_id = heapq.heappop(heap)

        if current_id in visited:
            continue
        visited.add(current_id)

        # Explore neighbors
        for neighbor_id, edge_cost in adjacency.get(current_id, []):
            new_cost = current_cost + edge_cost

            if new_cost < costs[neighbor_id]:
                costs[neighbor_id] = new_cost
                predecessors[neighbor_id] = (current_id, edge_cost)
                heapq.heappush(heap, (new_cost, neighbor_id))

    return CostField(
        costs=costs,
        predecessors=predecessors,
        origin_id=origin_id,
    )


def reverse_adjacency(
    adjacency: Dict[int, List[Tuple[int, float]]],
) -> Dict[int, List[Tuple[int, float]]]:
    """Transpose a directed adjacency list: edge (u -> v, cost) becomes (v -> u, cost).

    Standard trick for single-destination shortest paths: the distance from
    ``origin`` computed on the *transposed* graph equals the distance *to*
    ``origin`` on the original graph. Used by :func:`compute_return_cost_field`
    to build the return leg as a genuinely separate Dijkstra run rather than
    reusing the outbound field — see that function's docstring for why this
    matters even though today's edge costs are symmetric.
    """
    reversed_adj: Dict[int, List[Tuple[int, float]]] = {nid: [] for nid in adjacency}
    for from_id, edges in adjacency.items():
        for to_id, cost in edges:
            reversed_adj.setdefault(to_id, []).append((from_id, cost))
    return reversed_adj


def compute_return_cost_field(
    origin_id: int,
    adjacency: Dict[int, List[Tuple[int, float]]],
    all_node_ids: List[int],
) -> CostField:
    """Compute the fuel cost of the return leg: from every node back to origin.

    Per CLAUDE.md Section 9: "Even with symmetric costs, keep the two-cost
    architecture because future directional conditions will make outbound
    and return costs different." This runs Dijkstra from ``origin_id`` over
    the *transposed* graph (:func:`reverse_adjacency`) — a real, independent
    shortest-path computation, not ``compute_cost_field`` called twice on the
    same adjacency and not the outbound field copied or halved. With today's
    symmetric edge costs the transposed graph happens to equal the original
    one, so the numbers currently match the outbound field exactly; the day
    an edge's cost depends on the heading being travelled (wind, current —
    CLAUDE.md Section 7.6's ``cost(edge, direction, environment)``), this
    function is the one that changes, with the round-trip combination logic
    in the isochrone engine untouched.
    """
    return compute_cost_field(origin_id, reverse_adjacency(adjacency), all_node_ids)


def reconstruct_path(
    cost_field: CostField,
    destination_id: int,
) -> List[int] | None:
    """Reconstruct the shortest path from origin to destination.

    Args:
        cost_field: The computed cost field
        destination_id: Target node ID

    Returns:
        List of node IDs from origin to destination, or None if unreachable.
    """
    if destination_id not in cost_field.costs or cost_field.costs[destination_id] == float("inf"):
        return None

    path: List[int] = []
    current: int | None = destination_id

    while current is not None:
        path.append(current)
        current = cost_field.predecessors.get(current, (None, 0.0))[0]

    path.reverse()
    return path if path[0] == cost_field.origin_id else None


def get_reachable_nodes(
    cost_field: CostField,
    usable_fuel: float,
) -> set[int]:
    """Get all nodes reachable within the fuel budget.

    Args:
        cost_field: The computed cost field
        usable_fuel: Maximum fuel available (after reserve)

    Returns:
        Set of node IDs reachable within the fuel budget.

    Per CLAUDE.md Section 8: "minimum_fuel_cost(origin -> cell) <= usable_fuel"
    """
    return {
        nid for nid, cost in cost_field.costs.items()
        if cost <= usable_fuel
    }


def get_threshold_nodes(
    cost_field: CostField,
    threshold_fraction: float,
    max_cost: float,
) -> set[int]:
    """Get nodes reachable at a specific fuel percentage of the theoretical maximum.

    Args:
        cost_field: The computed cost field
        threshold_fraction: Fraction of max cost (0.25, 0.50, 0.75, 1.0)
        max_cost: Maximum cost in the field (theoretical range)

    Returns:
        Set of node IDs reachable at this threshold.
    """
    threshold = max_cost * threshold_fraction
    return {nid for nid, cost in cost_field.costs.items() if cost <= threshold}
