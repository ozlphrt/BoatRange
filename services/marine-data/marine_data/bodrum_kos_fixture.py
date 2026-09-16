"""Bodrum/Kos regression fixture — synthetic but topologically honest coastline.

This geometry is **synthetic**. It is deliberately NOT presented as chart data
and must never be used for navigation or for demo screenshots that imply real
coverage (CLAUDE.md Section 44.17). Its only job is to be a fixed, fully
controlled world in which the routing and isochrone engines can be *proved*
not to cross land (Section 35, Section 50 steps 9-14).

Real ingestion (OSM coastline -> PostGIS -> water polygons) is Phase 2 and is
still outstanding; this fixture is the stand-in that lets Phases 4-5 be
verified in the meantime.

Geographic layout (simplified from the real Aegean, but with the *topology*
that matters preserved):

    * Bodrum peninsula   — a single landmass in the NORTH, whose southern
                           coast runs near lat 37.00, with a cape reaching
                           south to ~36.955 and Güllük Bay indenting from the
                           east.
    * Kos island         — a SEPARATE landmass to the south-west, elongated
                           NE-SW, north-east tip near (27.34, 36.90).
    * The channel        — the open water between them, ~7.5 km at its
                           narrowest. A route from one side to the other must
                           pass *through* it, not over land.
    * Karaada            — a small island sitting inside the channel, so a
                           correct reachable area shows a shadow behind it.
    * Two breakwaters    — thin walls attached to the shore that block a
                           direct crossing but leave a navigable gap.

The properties above are asserted in ``tests/test_bodrum_kos_fixture.py``. If
you edit the coordinates, those tests are what stop you from silently
reintroducing a merged-landmass fixture (which is exactly the bug this file
replaced: the previous mainland polygon swallowed Kos, so the channel did not
exist and every "channel is passable" test was passing against open sea west
of the peninsula instead).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union


# ============================================================================
# Local planar projection
# ============================================================================
# Kept deliberately simple and dependency-free. It matches
# ``isochrone.projection.LocalProjection`` so fixture coordinates and engine
# coordinates are directly comparable.
#
# NOTE: this is a *fixture-local* reference frame, not a general CRS. Anything
# outside the Bodrum/Kos box needs its own reference (see
# ``isochrone.projection.choose_local_projection``).

FIXTURE_LAT_REF = 37.0
FIXTURE_LON_REF = 27.5
_METERS_PER_DEG_LAT = 111320.0
_METERS_PER_DEG_LON = _METERS_PER_DEG_LAT * math.cos(math.radians(FIXTURE_LAT_REF))


def _wgs84_to_local(lon: float, lat: float) -> tuple[float, float]:
    """Project a WGS84 ``(lon, lat)`` to fixture-local meters ``(x, y)``."""
    return (
        (lon - FIXTURE_LON_REF) * _METERS_PER_DEG_LON,
        (lat - FIXTURE_LAT_REF) * _METERS_PER_DEG_LAT,
    )


def _local_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Inverse of :func:`_wgs84_to_local`."""
    return (
        x / _METERS_PER_DEG_LON + FIXTURE_LON_REF,
        y / _METERS_PER_DEG_LAT + FIXTURE_LAT_REF,
    )


def _project_coords(coords: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Project a ring of WGS84 ``(lon, lat)`` pairs to local meters."""
    return [_wgs84_to_local(lon, lat) for lon, lat in coords]


def _polygon(coords: Sequence[tuple[float, float]]) -> Polygon:
    """Build a projected polygon and refuse to return invalid geometry.

    Failing here is the whole point: an invalid fixture ring is a silent source
    of "water leaks" downstream (CLAUDE.md Section 17), so it must not be
    constructible.
    """
    poly = Polygon(_project_coords(coords))
    if not poly.is_valid:
        raise ValueError(f"fixture ring is not a valid polygon: {poly.is_valid_reason}")
    if poly.is_empty or poly.area <= 0:
        raise ValueError("fixture ring has no area")
    return poly


# ============================================================================
# Land geometry
# ============================================================================


def bodrum_mainland() -> Polygon:
    """Bodrum peninsula — the northern landmass.

    Southern coast runs near lat 37.00-37.04. A cape (Krio burnumu analogue)
    reaches south to ~36.955 at lon ~27.40. Güllük Bay indents from the east
    around (27.50, 37.03), so the bay is only reachable through its entrance.
    """
    return _polygon([
        # Northern edge and NE corner (connection to the wider mainland)
        (27.10, 37.20), (27.75, 37.20), (27.75, 37.05),
        # East coast, Güllük side
        (27.66, 37.04), (27.62, 37.00), (27.60, 36.99),
        # Güllük Bay indentation — water intrudes into the land here
        (27.58, 37.03), (27.50, 37.04), (27.46, 37.02),
        # Southern cape
        (27.43, 36.99), (27.41, 36.96), (27.39, 36.955),
        # Cape west flank
        (27.36, 36.97), (27.34, 37.00),
        # South-west coast running back north
        (27.25, 37.01), (27.16, 37.02), (27.10, 37.04),
        (27.10, 37.20),
    ])


def kos_island() -> Polygon:
    """Kos island — a separate landmass south-west of the peninsula.

    Elongated NE-SW. The north-east tip near (27.34, 36.90) is the point that
    forms the narrow part of the channel with the Bodrum cape.
    """
    return _polygon([
        (27.34, 36.90), (27.30, 36.91), (27.22, 36.90),
        (27.10, 36.85), (26.98, 36.78), (26.95, 36.74),
        (27.00, 36.72), (27.10, 36.75), (27.22, 36.82),
        (27.32, 36.87), (27.36, 36.885), (27.34, 36.90),
    ])


def small_islands() -> list[Polygon]:
    """Islands sitting inside the channel.

    Karaada is placed in open water between the peninsula and Kos so that a
    correct reachable area has a visible shadow behind it. A range polygon that
    covers this island is leaking.
    """
    karaada = _polygon([
        (27.44, 36.93), (27.48, 36.935), (27.49, 36.915),
        (27.45, 36.91), (27.44, 36.93),
    ])
    return [karaada]


def bodrum_marina_breakwater() -> Polygon:
    """Thin breakwater wall hanging south from the Bodrum shore.

    Blocks a direct east-west crossing at lat ~37.00 but leaves a navigable gap
    to its west, so "must go around the wall" is testable.
    """
    return _polygon([
        (27.316, 37.010), (27.320, 37.010),
        (27.320, 36.988), (27.316, 36.988),
        (27.316, 37.010),
    ])


def kos_harbor_breakwater() -> Polygon:
    """Thin breakwater wall on the north coast of Kos."""
    return _polygon([
        (27.300, 36.912), (27.304, 36.912),
        (27.304, 36.896), (27.300, 36.896),
        (27.300, 36.912),
    ])


def land_geometries() -> list[Polygon]:
    """Every blocking land/obstacle polygon in the fixture, unbuffered."""
    return [
        bodrum_mainland(),
        kos_island(),
        *small_islands(),
        bodrum_marina_breakwater(),
        kos_harbor_breakwater(),
    ]


# ============================================================================
# Water generation
# ============================================================================

# Compute box for the fixture, in WGS84 degrees.
FIXTURE_BBOX_WGS84 = (27.00, 36.60, 27.80, 37.18)  # (min_lon, min_lat, max_lon, max_lat)


def fixture_bbox() -> Polygon:
    """The projected compute box the fixture water is cut from."""
    min_lon, min_lat, max_lon, max_lat = FIXTURE_BBOX_WGS84
    return _polygon([
        (min_lon, min_lat), (max_lon, min_lat),
        (max_lon, max_lat), (min_lon, max_lat),
        (min_lon, min_lat),
    ])


def unify_land(land_geoms: Sequence[Polygon]) -> Polygon | MultiPolygon:
    """Union land polygons, preserving **every** disjoint piece.

    The previous implementation returned ``max(result.geoms, key=area)`` when
    the union came out as a MultiPolygon — silently discarding Kos, the islands
    and both breakwaters, which then became navigable water. That is a
    fail-*open* data bug of exactly the kind CLAUDE.md Section 43 forbids, so
    this function now refuses to drop anything and raises instead of guessing.
    """
    if not land_geoms:
        return Polygon()

    repaired: list[Polygon] = []
    for geom in land_geoms:
        if geom.is_empty:
            continue
        if not geom.is_valid:
            # Section 17: repair rather than propagate invalid rings.
            geom = geom.buffer(0)
        if geom.is_empty:
            raise ValueError("land geometry collapsed to empty during repair")
        repaired.append(geom)

    union = unary_union(repaired)
    if union.is_empty:
        raise ValueError("land union is empty — refusing to treat everything as water")
    if union.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError(f"unexpected land union type: {union.geom_type}")
    return union


def generate_bodrum_kos_water() -> tuple[MultiPolygon, list[Polygon]]:
    """Return ``(water, land)`` for the fixture region.

    ``water`` is the compute box minus **raw, unbuffered** land. Safety
    clearance is a user setting applied by the routing engine
    (CLAUDE.md Section 6.5), not something baked into the dataset — the old
    fixture buffered land by 50 m here *and* the graph buffered again, so
    clearance was silently applied twice.

    Islands enclosed by the box appear as holes in the water polygon, which is
    what makes the reachable-area shadow behind them possible.
    """
    land_geoms = land_geometries()
    land_union = unify_land(land_geoms)

    water_geom = fixture_bbox().difference(land_union)
    if water_geom.is_empty:
        raise ValueError("fixture produced no navigable water")

    if isinstance(water_geom, Polygon):
        water_mp = MultiPolygon([water_geom])
    elif isinstance(water_geom, MultiPolygon):
        water_mp = water_geom
    else:
        raise ValueError(f"unexpected water geometry type: {water_geom.geom_type}")

    return water_mp, land_geoms


# ============================================================================
# Regression assertions (CLAUDE.md Section 35)
# ============================================================================


@dataclass(frozen=True)
class RegressionAssertion:
    """One named, checkable geographic claim about the fixture.

    Unlike the previous version, these are not inert metadata: every entry is
    exercised by the routing regression suite, which routes origin -> destination
    on the real graph and checks the outcome against ``expectation``.
    """

    name: str
    description: str
    origin_wgs84: tuple[float, float]       # (lon, lat)
    destination_wgs84: tuple[float, float]  # (lon, lat)
    expectation: str = "reachable"          # "reachable" | "unreachable"
    # Straight line origin->destination is expected to cross land, so a valid
    # route must be strictly longer than the direct distance.
    direct_line_crosses_land: bool = False
    notes: str = ""

    def projected_origin(self) -> tuple[float, float]:
        return _wgs84_to_local(*self.origin_wgs84)

    def projected_destination(self) -> tuple[float, float]:
        return _wgs84_to_local(*self.destination_wgs84)


REGRESSION_FIXTURES: list[RegressionAssertion] = [
    RegressionAssertion(
        name="bodrum-to-kos-channel",
        description=(
            "Cross the open channel between the Bodrum peninsula and Kos. "
            "Both endpoints are in the channel, so a direct route exists."
        ),
        origin_wgs84=(27.30, 36.98),
        destination_wgs84=(27.20, 36.94),
        expectation="reachable",
        direct_line_crosses_land=False,
    ),
    RegressionAssertion(
        name="around-bodrum-cape",
        description=(
            "West of the southern cape to east of it. The straight line cuts "
            "through the cape, so the route must bend around its tip."
        ),
        origin_wgs84=(27.34, 36.985),
        destination_wgs84=(27.44, 36.985),
        expectation="reachable",
        direct_line_crosses_land=True,
    ),
    RegressionAssertion(
        name="kos-north-to-south",
        description=(
            "North coast of Kos to its south coast. The straight line crosses "
            "the island; the route must wrap around one of its ends."
        ),
        origin_wgs84=(27.15, 36.92),
        destination_wgs84=(27.15, 36.74),
        expectation="reachable",
        direct_line_crosses_land=True,
    ),
    RegressionAssertion(
        name="gulluk-bay-entrance",
        description=(
            "Open water into Güllük Bay. The bay is only reachable through its "
            "entrance, never over the surrounding land."
        ),
        origin_wgs84=(27.56, 36.96),
        destination_wgs84=(27.52, 37.02),
        expectation="reachable",
        direct_line_crosses_land=False,
    ),
    RegressionAssertion(
        name="breakwater-must-be-rounded",
        description=(
            "Straight across the Bodrum breakwater wall. The wall blocks the "
            "direct line; the route must use the gap to its west."
        ),
        origin_wgs84=(27.310, 36.999),
        destination_wgs84=(27.330, 36.999),
        expectation="reachable",
        direct_line_crosses_land=True,
    ),
    RegressionAssertion(
        name="inland-destination-is-unreachable",
        description=(
            "A destination in the middle of the Bodrum landmass must never be "
            "reachable. This is the fail-closed case."
        ),
        origin_wgs84=(27.30, 36.98),
        destination_wgs84=(27.40, 37.12),
        expectation="unreachable",
        notes="Destination is inland; the engine must refuse rather than route to a nearby coastal node.",
    ),
]


__all__ = [
    "FIXTURE_BBOX_WGS84",
    "FIXTURE_LAT_REF",
    "FIXTURE_LON_REF",
    "REGRESSION_FIXTURES",
    "RegressionAssertion",
    "bodrum_mainland",
    "bodrum_marina_breakwater",
    "fixture_bbox",
    "generate_bodrum_kos_water",
    "kos_harbor_breakwater",
    "kos_island",
    "land_geometries",
    "small_islands",
    "unify_land",
    "_wgs84_to_local",
    "_local_to_wgs84",
]
