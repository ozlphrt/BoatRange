"""Fuel calculations shared by the /range and /route endpoints.

Thin wiring over ``packages/fuel-model`` — CLAUDE.md Section 49.8: "Keep the
fuel model independent from marine routing." No fuel-curve math happens here,
only usable-fuel bookkeeping and operating-point resolution.

Multi-vessel: every vessel has its OWN fuel curve (see
``fuel_model.catalog``), not one shared global curve — a request must always
say which vessel it means, and the curve used must match that vessel exactly.
"""

from __future__ import annotations

from typing import Optional

from fuel_model.catalog import VesselCatalogEntry, get_vessel_entry, list_vessels
from fuel_model.curve import AmbiguousInversionError, FuelCurve, OperatingPoint
from fuel_model.vessel import VesselProfile

# Preferred cruise speed when a request specifies neither speed nor RPM.
# Not every vessel's fuel curve actually covers this speed (see
# resolve_operating_point), so it is a starting *preference*, not a guarantee.
PREFERRED_DEFAULT_CRUISE_KN = 20.0


def get_vessel(vessel_profile_id: str) -> VesselProfile:
    return get_vessel_entry(vessel_profile_id).profile


def get_vessel_catalog_entry(vessel_profile_id: str) -> VesselCatalogEntry:
    return get_vessel_entry(vessel_profile_id)


def list_vessel_entries() -> list[VesselCatalogEntry]:
    return list_vessels()


def usable_fuel_liters(vessel: VesselProfile, fuel_mode: str, fuel_value: float, reserve_pct: float) -> float:
    """Total usable fuel in liters after reserve, per CLAUDE.md Section 3.6/4.6."""
    if fuel_mode == "full":
        total = vessel.tank_capacity_l
    elif fuel_mode == "percent":
        total = vessel.tank_capacity_l * (fuel_value / 100.0)
    elif fuel_mode == "liters":
        total = fuel_value
    else:
        raise ValueError(f"Unknown fuel mode: {fuel_mode!r}")

    total = max(0.0, min(total, vessel.tank_capacity_l))
    return total * (1 - reserve_pct / 100.0)


def resolve_operating_point(
    fuel_curve: FuelCurve,
    speed_kn: Optional[float],
    rpm: Optional[float],
    load_state: str,
    sea_state: str,
) -> OperatingPoint:
    """Resolve the requested speed/RPM into a full operating point on
    ``fuel_curve`` — the specific vessel's own curve, never a shared default.

    Raises:
        AmbiguousInversionError: if the requested speed maps to more than one
            RPM (CLAUDE.md Section 4.4) — the API surfaces this as a 409, it
            is not resolved silently here.
        ValueError: if speed/RPM is outside this vessel's measured curve
            range (which can be much narrower than another vessel's — see
            the Dusky 233 catalog entry's notes).
    """
    if rpm is not None:
        return fuel_curve.operating_point(rpm, load_state, sea_state)
    if speed_kn is not None:
        resolved_rpm = fuel_curve.rpm_at_speed(speed_kn, load_state)
        return fuel_curve.operating_point(resolved_rpm, load_state, sea_state)

    # No speed/RPM given: try the shared preferred cruise speed, but fall
    # back to this vessel's own minimum logged point if that preference
    # falls outside its measured range (e.g. the Dusky 233's curve starts
    # at ~25 kn) rather than raising on every default-settings request.
    try:
        resolved_rpm = fuel_curve.rpm_at_speed(PREFERRED_DEFAULT_CRUISE_KN, load_state)
    except ValueError:
        resolved_rpm = fuel_curve.min_rpm
    return fuel_curve.operating_point(resolved_rpm, load_state, sea_state)
