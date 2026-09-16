"""Vessel profile definitions for the Axopar 28 Range Planner.

All vessel profiles are defined as dataclass instances. The Axopar 28 2019
with single Mercury Verado 300 is the default v1 vessel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FuelType(Enum):
    GASOLINE = "gasoline"
    DIESEL = "diesel"
    ELECTRIC = "electric"


@dataclass(frozen=True)
class VesselProfile:
    """Generic vessel profile — schema designed for future boat types.

    Attributes:
        id: Unique identifier (e.g., "axopar-28-2019-verado-300")
        name: Display name (e.g., "Axopar 28")
        manufacturer: Boat manufacturer
        model: Model designation
        year: Model year
        propulsion_type: Type of propulsion system
        engine_count: Number of engines
        fuel_type: Fuel type
        tank_capacity_l: Total fuel tank capacity in liters
        hull_draft_m: Hull draft in meters
        propulsion_depth_m: Propulsion system depth (propellers, shafts) in meters
                       Must be >= hull_draft_m
        default_reserve_pct: Default fuel reserve percentage (0-100)
    """

    id: str
    name: str
    manufacturer: str
    model: str
    year: int
    propulsion_type: str  # "outboard", "inboard", etc.
    engine_count: int
    fuel_type: FuelType | str = FuelType.GASOLINE
    tank_capacity_l: float = 0.0
    hull_draft_m: float = 0.0
    propulsion_depth_m: float = 0.0
    default_reserve_pct: float = 20.0

    def __post_init__(self) -> None:
        """Validate vessel profile constraints."""
        if self.tank_capacity_l <= 0:
            raise ValueError("tank_capacity_l must be positive")
        if self.hull_draft_m <= 0:
            raise ValueError("hull_draft_m must be positive")
        if self.propulsion_depth_m < self.hull_draft_m:
            raise ValueError(
                "propulsion_depth_m must be >= hull_draft_m"
            )
        if not (0 <= self.default_reserve_pct < 100):
            raise ValueError("default_reserve_pct must be between 0 and 100")

        # Validate fuel_type
        valid_fuel_types = {ft.value for ft in FuelType} | {"gasoline", "diesel", "electric"}
        if isinstance(self.fuel_type, FuelType):
            pass  # already valid enum
        elif isinstance(self.fuel_type, str) and self.fuel_type.lower() in valid_fuel_types:
            object.__setattr__(self, "fuel_type", FuelType(self.fuel_type.lower()))
        else:
            raise ValueError(
                f"Invalid fuel_type: {self.fuel_type!r}. "
                f"Must be one of: {sorted(valid_fuel_types)}"
            )

    def usable_fuel_liters(self, reserve_pct: float | None = None) -> float:
        """Calculate usable fuel after reserving the safety margin.

        Args:
            reserve_pct: Override default reserve percentage.

        Returns:
            Usable fuel in liters (total - reserve).
        """
        pct = reserve_pct if reserve_pct is not None else self.default_reserve_pct
        return self.tank_capacity_l * (1 - pct / 100)

    def min_safe_depth(self, safety_margin_m: float = 0.5) -> float:
        """Calculate minimum safe water depth.

        Args:
            safety_margin_m: Additional safety margin in meters.

        Returns:
            Minimum safe depth = max(hull_draft, propulsion_depth) + margin.
        """
        return max(self.hull_draft_m, self.propulsion_depth_m) + safety_margin_m


# ============================================================================
# V1 Default Vessel: Axopar 28 2019 with Mercury Verado 300
# ============================================================================

Axopar28V1 = VesselProfile(
    id="axopar-28-2019-verado-300",
    name="Axopar 28",
    manufacturer="Axopar",
    model="28",
    year=2019,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=257,  # 68 US gal — boattest.com "Axopar 28 Cabin" test, see note below
    hull_draft_m=0.80,    # 2'8" — same source
    propulsion_depth_m=0.85,  # small margin over hull draft; source gives one "Draft" figure only
    default_reserve_pct=20,
)
# CORRECTED (was 750 L / 1.35 m, flagged as implausible and unverified):
# boattest.com independently tested an "Axopar 28 Cabin" —
# https://boattest.com/boats/axopar/28-cabin-2019 — and reports Fuel Capacity
# 68 gal (257 L) and Draft 2'8" (.80 m). That test boat runs TWIN 200-hp
# Mercury Verados (not the single 300-hp this profile models), so its
# performance numbers are not comparable to CLEANED_FUEL_DATA below — but
# hull tank capacity and draft are the same regardless of engine count, so
# those two figures are corrected here. The fuel curve itself is untouched:
# no independently tested single-Verado-300 Axopar 28 was found.


# ============================================================================
# Second V1 Vessel: Dusky 233, single Evinrude E-TEC 300 H.P.
# ============================================================================
# Added to prove the vessel/fuel-model architecture actually supports more
# than one boat (CLAUDE.md Section 1.1: "Boat profile schema must remain
# generic enough for future vessels"). Specs below are sourced from public
# listings for the Dusky 233 FAC variant — draft, beam, fuel tank capacity —
# which may not exactly match the specific 233 hull used in the linked
# performance test. See fuel_model.curve for the matching (sparse,
# lower-confidence) fuel curve and its own caveats.

Dusky233Evinrude300 = VesselProfile(
    id="dusky-233-evinrude-etec-300",
    name="Dusky 233",
    manufacturer="Dusky Marine",
    model="233",
    year=1995,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=378.5,  # 100 US gal, Dusky 233 FAC listing
    hull_draft_m=0.33,  # 1'1", Dusky 233 FAC listing
    propulsion_depth_m=0.85,  # engineering estimate, outboard trimmed down — not sourced
    default_reserve_pct=20,
)


# ============================================================================
# Vessels 3-9: single-outboard boats independently tested by boattest.com
# ============================================================================
# All specs (tank capacity, draft) and the matching fuel curves in
# fuel_model.curve come straight from each boat's own boattest.com "Test
# Results" table — full RPM/knots/GPH data, not the sparse 2-3-point summaries
# elsewhere in this file. Where the source gives only one "Draft" figure
# (not separate up/down), the same value is used for both hull_draft_m and
# propulsion_depth_m and that is noted in the catalog entry.

RobaloR180 = VesselProfile(
    id="robalo-r180-2019-yamaha-115",
    name="Robalo R180",
    manufacturer="Robalo",
    model="R180",
    year=2019,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=189,       # 50 US gal
    hull_draft_m=0.28,         # 11" draft up
    propulsion_depth_m=0.69,   # 27" draft down
    default_reserve_pct=20,
)

BostonWhaler210Dauntless = VesselProfile(
    id="boston-whaler-210-dauntless-2019-verado-200",
    name="Boston Whaler 210 Dauntless",
    manufacturer="Boston Whaler",
    model="210 Dauntless",
    year=2019,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=284,       # 75 US gal
    hull_draft_m=0.36,         # 14" — single "Draft" figure, used for both
    propulsion_depth_m=0.36,
    default_reserve_pct=20,
)

Axopar25CrossTop = VesselProfile(
    id="axopar-25-cross-top-2023-verado-250",
    name="Axopar 25 Cross Top",
    manufacturer="Axopar",
    model="25 Cross Top",
    year=2023,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=227,       # 60 US gal
    hull_draft_m=0.85,         # 2'9" — single "Draft" figure, used for both
    propulsion_depth_m=0.85,
    default_reserve_pct=20,
)

Axopar22TTop = VesselProfile(
    id="axopar-22-t-top-2022-verado-200",
    name="Axopar 22 T-Top",
    manufacturer="Axopar",
    model="22 T-Top",
    year=2022,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=227,       # 60 US gal
    hull_draft_m=0.80,         # 2'8" — single "Draft" figure, used for both
    propulsion_depth_m=0.80,
    default_reserve_pct=20,
)

BostonWhaler190Montauk = VesselProfile(
    id="boston-whaler-190-montauk-2019-mercury-150",
    name="Boston Whaler 190 Montauk",
    manufacturer="Boston Whaler",
    model="190 Montauk",
    year=2019,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=227,       # 60 US gal
    hull_draft_m=0.33,         # 13" — single "Draft" figure, used for both
    propulsion_depth_m=0.33,
    default_reserve_pct=20,
)

RobaloR160 = VesselProfile(
    id="robalo-r160-2019-yamaha-70",
    name="Robalo R160",
    manufacturer="Robalo",
    model="R160",
    year=2019,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=45.4,      # 12 US gal — the smallest tank in this catalog
    hull_draft_m=0.25,         # 10" draft up
    propulsion_depth_m=0.69,   # 27" draft down
    default_reserve_pct=20,
)

GradyWhiteFisherman236 = VesselProfile(
    id="grady-white-fisherman-236-2016-yamaha-f300",
    name="Grady-White Fisherman 236",
    manufacturer="Grady-White",
    model="Fisherman 236",
    year=2016,
    propulsion_type="outboard",
    engine_count=1,
    fuel_type=FuelType.GASOLINE,
    tank_capacity_l=435,       # 115 US gal — the largest tank in this catalog
    hull_draft_m=0.47,         # 18.5" — single "Draft" figure, used for both
    propulsion_depth_m=0.47,
    default_reserve_pct=20,
)
