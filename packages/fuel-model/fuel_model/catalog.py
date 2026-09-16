"""Multi-vessel catalog — ties a VesselProfile to its matching FuelCurve.

CLAUDE.md Section 1.1: "Boat profile schema must remain generic enough for
future vessels... Do not expose a generic multi-boat product in v1" was the
original v1 scope call; the user has since explicitly asked for vessel
selection, so this module is the seam that makes it real: every consumer
(API, future UI) should go through ``VESSEL_CATALOG`` / :func:`get_vessel_entry`
rather than importing ``default_fuel_curve`` directly.

Nine vessels, single outboard: Axopar's own three tested sizes (22/25/28),
Boston Whaler's two Dauntless/Montauk sizes, Robalo's two sizes, a Dusky and
a Grady-White. Seven of the nine (everything but Axopar 28 and the Dusky)
come from a full published boattest.com "Test Results" table — every RPM
step they measured, not a 2-3 point summary — via the ``scrapling`` MCP
server. See each entry's ``notes`` for what's still uncertain.
"""

from __future__ import annotations

from dataclasses import dataclass

from .curve import (
    FuelCurve,
    axopar_22_t_top_fuel_curve,
    axopar_25_cross_top_fuel_curve,
    boston_whaler_190_montauk_fuel_curve,
    boston_whaler_210_dauntless_fuel_curve,
    default_fuel_curve,
    dusky_233_fuel_curve,
    grady_white_fisherman_236_fuel_curve,
    robalo_r160_fuel_curve,
    robalo_r180_fuel_curve,
)
from .vessel import (
    Axopar22TTop,
    Axopar25CrossTop,
    Axopar28V1,
    BostonWhaler190Montauk,
    BostonWhaler210Dauntless,
    Dusky233Evinrude300,
    GradyWhiteFisherman236,
    RobaloR160,
    RobaloR180,
    VesselProfile,
)


@dataclass(frozen=True)
class VesselCatalogEntry:
    """A vessel profile paired with the fuel curve that drives its range math.

    Attributes:
        profile: The vessel's identity/tank/draft data.
        fuel_curve: RPM -> speed/L-h curve for this specific vessel.
        data_confidence: 0-1 rough indicator of how well-attested the fuel
            curve is (anchor point count, source quality, cross-checks) —
            NOT the same as the per-runtime-request "confidence" field the
            API returns (that also folds in marine-data quality); this one
            is about the fuel model alone.
        notes: Human-readable caveats surfaced in the UI/API — CLAUDE.md
            Section 49.11: "If data quality is insufficient, say so in
            code/UI rather than inventing confidence."
    """

    profile: VesselProfile
    fuel_curve: FuelCurve
    data_confidence: float
    notes: list[str]


_BOATTEST_NOTE = (
    "Fuel curve and tank/draft specs are the full published RPM/knots/GPH "
    "table from an independent boattest.com sea trial, not manufacturer "
    "marketing figures."
)

VESSEL_CATALOG: dict[str, VesselCatalogEntry] = {
    Axopar28V1.id: VesselCatalogEntry(
        profile=Axopar28V1,
        fuel_curve=default_fuel_curve,
        data_confidence=0.85,
        notes=[
            "Fuel curve: 9 reconciled anchor points across the full RPM range "
            "(CLAUDE.md Section 4.2) — the most complete curve in this catalog.",
            "tank_capacity_l (257 L) and hull_draft_m (0.80 m) were corrected "
            "using an independent boattest.com test of the Axopar 28 Cabin "
            "(https://boattest.com/boats/axopar/28-cabin-2019) — same hull "
            "and tank, but that test boat runs TWIN 200-hp Mercury engines, "
            "not the single 300-hp this profile's fuel curve models, so its "
            "performance numbers were not used to touch the curve itself.",
        ],
    ),
    Dusky233Evinrude300.id: VesselCatalogEntry(
        profile=Dusky233Evinrude300,
        fuel_curve=dusky_233_fuel_curve,
        data_confidence=0.35,
        notes=[
            "Fuel curve: only 3 anchor points, all at 3500 RPM and above — "
            "no idle/low-speed data was published, so this vessel cannot "
            "answer a slow-cruise query (minimum usable speed is ~25 kn).",
            "The 5500 RPM fuel-burn figure is back-derived from a published "
            "MPG value and happens to exactly match the wide-open-throttle "
            "figure — likely a source data artifact, not verified.",
            "Vessel spec (tank capacity, draft) is from a different-year "
            "Dusky 233 listing than the tested boat; may not match exactly.",
        ],
    ),
    RobaloR180.id: VesselCatalogEntry(
        profile=RobaloR180,
        fuel_curve=robalo_r180_fuel_curve,
        data_confidence=0.8,
        notes=[
            _BOATTEST_NOTE,
            "12-point RPM sweep from idle (700 rpm) to WOT (6000 rpm) — the "
            "densest curve in this catalog besides the Axopar 28.",
        ],
    ),
    BostonWhaler210Dauntless.id: VesselCatalogEntry(
        profile=BostonWhaler210Dauntless,
        fuel_curve=boston_whaler_210_dauntless_fuel_curve,
        data_confidence=0.78,
        notes=[
            _BOATTEST_NOTE,
            "Source publishes a single 'Draft' figure (14 in), not separate "
            "hull/propulsion-down values — both fields use the same number.",
        ],
    ),
    Axopar25CrossTop.id: VesselCatalogEntry(
        profile=Axopar25CrossTop,
        fuel_curve=axopar_25_cross_top_fuel_curve,
        data_confidence=0.78,
        notes=[
            _BOATTEST_NOTE,
            "Source publishes a single 'Draft' figure (2'9\"), not separate "
            "hull/propulsion-down values — both fields use the same number.",
        ],
    ),
    Axopar22TTop.id: VesselCatalogEntry(
        profile=Axopar22TTop,
        fuel_curve=axopar_22_t_top_fuel_curve,
        data_confidence=0.78,
        notes=[
            _BOATTEST_NOTE,
            "Smallest single-engine Axopar in the catalog (23'7\", 200-hp "
            "tested / 115-hp standard power).",
            "Source publishes a single 'Draft' figure (2'8\"), not separate "
            "hull/propulsion-down values — both fields use the same number.",
        ],
    ),
    BostonWhaler190Montauk.id: VesselCatalogEntry(
        profile=BostonWhaler190Montauk,
        fuel_curve=boston_whaler_190_montauk_fuel_curve,
        data_confidence=0.7,
        notes=[
            _BOATTEST_NOTE,
            "The published table's last two points (5650 and 5900 rpm) show "
            "fuel burn DECREASING as speed increases — almost certainly a "
            "source measurement/rounding quirk near wide-open-throttle, "
            "kept at reduced confidence rather than smoothed over.",
            "Source publishes a single 'Draft' figure (13 in), not separate "
            "hull/propulsion-down values — both fields use the same number.",
        ],
    ),
    RobaloR160.id: VesselCatalogEntry(
        profile=RobaloR160,
        fuel_curve=robalo_r160_fuel_curve,
        data_confidence=0.7,
        notes=[
            _BOATTEST_NOTE,
            "Only 7 anchor points (500 rpm steps rather than the usual "
            "500-1000), and the smallest tank in this catalog (12 US gal / "
            "45 L) — expect a short theoretical range even at full tank.",
        ],
    ),
    GradyWhiteFisherman236.id: VesselCatalogEntry(
        profile=GradyWhiteFisherman236,
        fuel_curve=grady_white_fisherman_236_fuel_curve,
        data_confidence=0.8,
        notes=[
            _BOATTEST_NOTE,
            "Largest boat and largest tank in this catalog (115 US gal / 435 L).",
            "Source publishes a single 'Draft' figure (18.5 in), not separate "
            "hull/propulsion-down values — both fields use the same number.",
        ],
    ),
}


def get_vessel_entry(vessel_id: str) -> VesselCatalogEntry:
    """Look up a vessel's full catalog entry.

    Raises:
        KeyError: if ``vessel_id`` isn't in the catalog.
    """
    return VESSEL_CATALOG[vessel_id]


def list_vessels() -> list[VesselCatalogEntry]:
    return list(VESSEL_CATALOG.values())
