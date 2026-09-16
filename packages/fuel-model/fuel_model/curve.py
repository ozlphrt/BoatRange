"""Fuel curve data and FuelCurve class for the Axopar 28 Range Planner.

Contains the cleaned v1 anchor dataset from CLAUDE.md section 4.2 and
provides interpolation-based lookup for speed, fuel flow, and derived L/nm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .interpolation import pchip_interpolate


class AmbiguousInversionError(ValueError):
    """Raised when speed -> RPM inversion has more than one valid answer.

    CLAUDE.md Section 4.4: "If inversion becomes ambiguous due to a
    non-monotonic region, do not silently choose a branch. Return: ambiguity
    warning, valid candidate operating points." ``candidates`` carries every
    RPM at which the curve reaches the requested speed, sorted ascending, so
    the caller can present the choice rather than have one picked for them.
    """

    def __init__(self, speed_kn: float, candidates: list[float]) -> None:
        self.speed_kn = speed_kn
        self.candidates = candidates
        super().__init__(
            f"Speed {speed_kn} kn is reachable at {len(candidates)} different "
            f"RPM values in a non-monotonic region of the curve: {candidates}. "
            f"Refusing to silently pick one — inspect `.candidates`."
        )


# ============================================================================
# Cleaned v1 Anchor Data (CLAUDE.md Section 4.2)
# ============================================================================
# Source-of-truth fields: RPM, speed_kn, fuel_lph
# Derived field: lpnm = fuel_lph / speed_kn
# The original performance graphic contained internal contradictions;
# these values have been reconciled.

CLEANED_FUEL_DATA: list[dict] = [
    {"rpm": 1000, "speed_kn": 5.0, "fuel_lph": 6.0, "source": "axopar_performance_graphic_reconciled", "confidence": 0.95},
    {"rpm": 1500, "speed_kn": 6.0, "fuel_lph": 8.0, "source": "axopar_performance_graphic_reconciled", "confidence": 0.95},
    {"rpm": 2000, "speed_kn": 8.0, "fuel_lph": 13.0, "source": "axopar_performance_graphic_reconciled", "confidence": 0.90},
    {"rpm": 3200, "speed_kn": 13.0, "fuel_lph": 27.0, "source": "axopar_performance_graphic_reconciled", "confidence": 0.90},
    {"rpm": 3500, "speed_kn": 16.9, "fuel_lph": 38.0, "source": "axopar_performance_graphic_reconciled_3500_rpm", "confidence": 0.92},
    {"rpm": 4000, "speed_kn": 20.0, "fuel_lph": 43.8, "source": "axopar_performance_graphic_reconciled_4000_rpm", "confidence": 0.92},
    {"rpm": 4500, "speed_kn": 26.0, "fuel_lph": 60.5, "source": "axopar_performance_graphic_reconciled", "confidence": 0.90},
    {"rpm": 5000, "speed_kn": 31.0, "fuel_lph": 75.5, "source": "axopar_performance_graphic_reconciled", "confidence": 0.90},
    {"rpm": 5400, "speed_kn": 35.0, "fuel_lph": 87.2, "source": "axopar_performance_graphic_reconciled", "confidence": 0.88},
]

# Compute derived L/nm for each point
for pt in CLEANED_FUEL_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)


# ============================================================================
# Root finding — speed -> RPM inversion support
# ============================================================================


def _find_all_roots(f, lo: float, hi: float, n_scan: int = 400, tol: float = 1e-6) -> list[float]:
    """Find every root of ``f`` in ``[lo, hi]`` by scan-and-bisect.

    ``f`` need not be monotonic: this samples ``n_scan`` points across the
    range, bisects within every bracket where ``f`` changes sign (or lands on
    an exact zero), and returns all roots found. Used to invert the fuel
    curve without assuming in advance that it is monotonic — CLAUDE.md
    Section 4.4 requires ambiguity in a non-monotonic region to surface as
    multiple candidates, not be silently resolved by whichever branch a
    simpler method happens to pick.
    """
    xs = [lo + (hi - lo) * i / n_scan for i in range(n_scan + 1)]
    ys = [f(x) for x in xs]

    roots: list[float] = []
    for i in range(n_scan):
        y0, y1 = ys[i], ys[i + 1]
        if y0 == 0.0:
            roots.append(xs[i])
            continue
        if y0 * y1 < 0.0:
            roots.append(_bisect(f, xs[i], xs[i + 1], tol))
    if ys[-1] == 0.0:
        roots.append(xs[-1])

    # De-duplicate roots that landed within tolerance of each other (can
    # happen at a scan-cell boundary that is itself very close to a root).
    deduped: list[float] = []
    for r in sorted(roots):
        if not deduped or r - deduped[-1] > tol * 10:
            deduped.append(r)
    return deduped


def _bisect(f, lo: float, hi: float, tol: float) -> float:
    """Standard bisection root-find, assuming ``f(lo)`` and ``f(hi)`` differ in sign."""
    f_lo = f(lo)
    while hi - lo > tol:
        mid = (lo + hi) / 2
        f_mid = f(mid)
        if f_mid == 0.0:
            return mid
        if (f_mid < 0) == (f_lo < 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


@dataclass(frozen=True)
class SpeedInversion:
    """Result of inverting the fuel curve from speed to RPM.

    Attributes:
        rpm: The single answer, or ``None`` if zero or multiple candidates.
        candidates: Every RPM at which the curve reaches the requested speed.
        ambiguous: True if more than one candidate was found — the caller
            must choose (CLAUDE.md Section 4.4), never has one chosen for it.
    """

    rpm: float | None
    candidates: list[float]
    ambiguous: bool


# ============================================================================
# FuelCurve Class
# ============================================================================

@dataclass(frozen=True)
class OperatingPoint:
    """Represents a single operating point on the fuel curve.

    Attributes:
        rpm: Engine RPM
        speed_kn: Boat speed in nautical miles per hour
        fuel_lph: Fuel flow in liters per hour
        lpnm: Fuel consumption in liters per nautical mile (derived)
        is_inefficient: Whether this point falls in an inefficient operating zone
    """

    rpm: float
    speed_kn: float
    fuel_lph: float
    lpnm: float
    is_inefficient: bool = False


# Inefficient operating zone — the "hump" region around 2000-3200 RPM
# where fuel efficiency drops significantly.
INEFFICIENT_RPM_RANGE = (2000, 3200)

# Load and sea state adjustment factors
LOAD_FACTORS = {
    "light": {"speed": 1.02, "fuel": 0.95},
    "normal": {"speed": 1.0, "fuel": 1.0},
    "heavy": {"speed": 0.96, "fuel": 1.08},
}

SEA_STATE_FACTORS = {
    "calm": {"fuel": 1.0},
    "moderate": {"fuel": 1.05},
    "rough": {"fuel": 1.12},
}


class FuelCurve:
    """Fuel curve engine with PCHIP interpolation.

    Interpolates independently:
      - RPM -> speed (knots)
      - RPM -> fuel flow (L/h)

    Then derives:
      - L/nm = L/h / knots
      - Endurance = usable_fuel / L/h
      - Range = usable_fuel / (L/h / knots)

    Extrapolation beyond measured RPM range is NOT allowed.
    """

    def __init__(self, rpm_data: list[float], speed_data: list[float], lph_data: list[float]) -> None:
        """Initialize with sorted arrays of control points.

        Args:
            rpm_data: Engine RPM values (must be sorted ascending)
            speed_data: Corresponding boat speeds in knots
            lph_data: Corresponding fuel flow in liters per hour
        """
        if len(rpm_data) != len(speed_data) or len(rpm_data) != len(lph_data):
            raise ValueError("All data arrays must have the same length")

        self._rpm = sorted(rpm_data)
        self._speed = [v for _, v in sorted(zip(self._rpm, speed_data))]
        self._lph = [v for _, v in sorted(zip(self._rpm, lph_data))]

    @classmethod
    def from_data(cls, data: list[dict]) -> "FuelCurve":
        """Create a FuelCurve from the cleaned anchor data.

        Args:
            data: List of dicts with keys 'rpm', 'speed_kn', 'fuel_lph'

        Returns:
            A new FuelCurve instance.
        """
        rpm = [pt["rpm"] for pt in data]
        speed = [pt["speed_kn"] for pt in data]
        lph = [pt["fuel_lph"] for pt in data]
        return cls(rpm, speed, lph)

    @property
    def min_rpm(self) -> float:
        return self._rpm[0]

    @property
    def max_rpm(self) -> float:
        return self._rpm[-1]

    def _check_range(self, rpm: float) -> None:
        """Raise ValueError if RPM is outside measured range."""
        if rpm < self._rpm[0] or rpm > self._rpm[-1]:
            raise ValueError(
                f"RPM {rpm} is outside measured range [{self._rpm[0]}, {self._rpm[-1]}]. "
                "Do not extrapolate the fuel curve."
            )

    def speed_at_rpm(self, rpm: float, load_state: str = "normal") -> float:
        """Get interpolated boat speed at a given RPM.

        Args:
            rpm: Engine RPM (must be within measured range)
            load_state: Light / Normal / Heavy load factor

        Returns:
            Boat speed in knots.
        """
        self._check_range(rpm)
        speed = pchip_interpolate(self._rpm, self._speed, rpm)
        factor = LOAD_FACTORS.get(load_state, LOAD_FACTORS["normal"])
        return round(speed * factor["speed"], 2)

    def fuel_flow_at_rpm(self, rpm: float, load_state: str = "normal", sea_state: str = "calm") -> float:
        """Get interpolated fuel flow at a given RPM.

        Args:
            rpm: Engine RPM (must be within measured range)
            load_state: Light / Normal / Heavy load factor
            sea_state: Calm / Moderate / Rough sea state factor

        Returns:
            Fuel flow in liters per hour.
        """
        self._check_range(rpm)
        lph = pchip_interpolate(self._rpm, self._lph, rpm)
        load_factor = LOAD_FACTORS.get(load_state, LOAD_FACTORS["normal"])["fuel"]
        sea_factor = SEA_STATE_FACTORS.get(sea_state, SEA_STATE_FACTORS["calm"])["fuel"]
        return round(lph * load_factor * sea_factor, 2)

    def lpnm_at_rpm(self, rpm: float, load_state: str = "normal", sea_state: str = "calm") -> float:
        """Get derived L/nm at a given RPM.

        Derived as: fuel_lph / speed_kn
        """
        speed = self.speed_at_rpm(rpm, load_state)
        lph = self.fuel_flow_at_rpm(rpm, load_state, sea_state)
        if speed <= 0:
            return float("inf")
        return round(lph / speed, 4)

    def rpm_at_speed(self, speed_kn: float, load_state: str = "normal") -> float:
        """Invert the curve: find the RPM that produces a given boat speed.

        Args:
            speed_kn: Desired boat speed in knots.
            load_state: Light / Normal / Heavy — must match whatever
                ``load_state`` the caller intends to use with ``speed_at_rpm``,
                since load scales achievable speed at every RPM.

        Returns:
            Required RPM.

        Raises:
            ValueError: If speed is outside the achievable range for this
                load state.
            AmbiguousInversionError: If more than one RPM produces this speed
                (a non-monotonic region) — never silently returns one of them.

        Regression note: this used to invert with a separate linear
        interpolation between the raw control points, independent of the
        PCHIP curve ``speed_at_rpm`` actually evaluates — so
        ``speed_at_rpm(rpm_at_speed(v))`` was only approximately ``v``
        wherever the real curve bends away from a straight line between
        anchors, and ``load_state`` was silently ignored altogether (heavy
        load slows the boat at every RPM, so the correct answer for the same
        target speed is measurably higher).
        """
        result = self.invert_speed(speed_kn, load_state)
        if result.ambiguous:
            raise AmbiguousInversionError(speed_kn, result.candidates)
        if result.rpm is None:
            raise ValueError(f"No RPM found producing {speed_kn} kn at load '{load_state}'.")
        return result.rpm

    def invert_speed(self, speed_kn: float, load_state: str = "normal") -> "SpeedInversion":
        """Invert the curve, returning every candidate RPM (never guessing).

        Root-finds directly against the same PCHIP curve ``speed_at_rpm``
        evaluates (bisection on a fine sign-change scan), so the two stay
        consistent, and applies the load factor before searching so the
        answer reflects the requested load state.
        """
        factor = LOAD_FACTORS.get(load_state, LOAD_FACTORS["normal"])["speed"]
        scaled_min = self._speed[0] * factor
        scaled_max = self._speed[-1] * factor
        if speed_kn < scaled_min or speed_kn > scaled_max:
            raise ValueError(
                f"Speed {speed_kn} kn is outside the achievable range "
                f"[{scaled_min:.2f}, {scaled_max:.2f}] kn at load '{load_state}'."
            )

        # Search on the raw (unscaled) curve for the raw target; load scales
        # speed uniformly so this is equivalent to searching the scaled curve.
        target = speed_kn / factor

        def f(rpm: float) -> float:
            return pchip_interpolate(self._rpm, self._speed, rpm) - target

        candidates = _find_all_roots(f, self._rpm[0], self._rpm[-1])
        candidates = [round(c, 0) for c in candidates]

        if not candidates:
            return SpeedInversion(rpm=None, candidates=[], ambiguous=False)
        if len(candidates) == 1:
            return SpeedInversion(rpm=candidates[0], candidates=candidates, ambiguous=False)
        return SpeedInversion(rpm=None, candidates=sorted(set(candidates)), ambiguous=True)

    def range_at_rpm(self, rpm: float, usable_fuel_l: float, load_state: str = "normal", sea_state: str = "calm") -> float:
        """Calculate fuel-limited range in nautical miles.

        Args:
            rpm: Engine RPM
            usable_fuel_l: Available fuel in liters (after reserve)
            load_state: Load factor
            sea_state: Sea state factor

        Returns:
            Range in nautical miles.
        """
        lpnm = self.lpnm_at_rpm(rpm, load_state, sea_state)
        if lpnm <= 0:
            return 0.0
        return round(usable_fuel_l / lpnm, 2)

    def endurance_at_rpm(self, rpm: float, usable_fuel_l: float, load_state: str = "normal", sea_state: str = "calm") -> float:
        """Calculate endurance in hours.

        Args:
            rpm: Engine RPM
            usable_fuel_l: Available fuel in liters (after reserve)
            load_state: Load factor
            sea_state: Sea state factor

        Returns:
            Endurance in hours.
        """
        lph = self.fuel_flow_at_rpm(rpm, load_state, sea_state)
        if lph <= 0:
            return 0.0
        return round(usable_fuel_l / lph, 2)

    def operating_point(self, rpm: float, load_state: str = "normal", sea_state: str = "calm") -> OperatingPoint:
        """Get a complete operating point at a given RPM.

        Args:
            rpm: Engine RPM
            load_state: Load factor
            sea_state: Sea state factor

        Returns:
            OperatingPoint dataclass with all computed values.
        """
        speed = self.speed_at_rpm(rpm, load_state)
        lph = self.fuel_flow_at_rpm(rpm, load_state, sea_state)
        lpnm = self.lpnm_at_rpm(rpm, load_state, sea_state)

        in_inefficient_zone = INEFFICIENT_RPM_RANGE[0] <= rpm <= INEFFICIENT_RPM_RANGE[1]

        return OperatingPoint(
            rpm=rpm,
            speed_kn=speed,
            fuel_lph=lph,
            lpnm=lpnm,
            is_inefficient=in_inefficient_zone,
        )


# ============================================================================
# Default v1 Fuel Curve (Axopar 28)
# ============================================================================

default_fuel_curve = FuelCurve.from_data(CLEANED_FUEL_DATA)


# ============================================================================
# Second vessel: Dusky 233, single Evinrude E-TEC 300 H.P.
# ============================================================================
# Source: a single published boat-test summary (RPM, speed, fuel burn,
# 2-stroke 300 HP outboard on a 4,600 lb test boat). Per CLAUDE.md Section
# 4.1 ("never trust published L/nm blindly... automatically flag
# inconsistent source values") this is deliberately NOT treated the way the
# Axopar table is:
#
#   * Only 3 points, all at mid-to-high RPM (3500/5500/5650) — no idle or
#     low-speed data was published, so this curve's usable range starts at
#     3500 RPM / ~25 kn and cannot answer a slow-cruise query at all. That
#     is a real data gap, not something to interpolate around.
#   * The 5500 RPM fuel-burn figure was not printed directly in the source;
#     it is back-derived from the published MPG (speed_mph / mpg), and the
#     result (26.2 GPH) is suspiciously IDENTICAL to the WOT figure two rows
#     below — almost certainly a copy/rounding artifact in the source table,
#     not two genuinely independent readings. Flagged via ``confidence`` and
#     ``source`` below rather than silently trusted.
#
# speed_mph -> speed_kn: * 0.868976. fuel_gph -> fuel_lph: * 3.78541.
DUSKY_233_EVINRUDE_300_DATA: list[dict] = [
    {
        "rpm": 3500,
        "speed_kn": round(28.8 * 0.868976, 2),
        "fuel_lph": round(9.3 * 3.78541, 2),
        "source": "boattest.com Evinrude E-TEC 300 H.P. review (Dusky 233, 4600 lb test weight)",
        "confidence": 0.6,
    },
    {
        "rpm": 5500,
        "speed_kn": round(50.3 * 0.868976, 2),
        # Back-derived from published 1.92 MPG; matches WOT GPH exactly —
        # flagged as a likely source-data artifact, kept for curve shape but
        # at low confidence rather than dropped silently.
        "fuel_lph": round((50.3 / 1.92) * 3.78541, 2),
        "source": "boattest.com (fuel burn back-derived from MPG; suspicious match to WOT row)",
        "confidence": 0.3,
    },
    {
        "rpm": 5650,
        "speed_kn": round(51.7 * 0.868976, 2),
        "fuel_lph": round(26.2 * 3.78541, 2),
        "source": "boattest.com Evinrude E-TEC 300 H.P. review (Dusky 233, wide open throttle)",
        "confidence": 0.6,
    },
]
for pt in DUSKY_233_EVINRUDE_300_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)

dusky_233_fuel_curve = FuelCurve.from_data(DUSKY_233_EVINRUDE_300_DATA)


# ============================================================================
# Vessels 3-9: independently tested single-outboard boats (boattest.com)
# ============================================================================
# Each dataset below is the FULL published RPM/Knots/GPH "Test Results" table
# for that specific boat+engine combination — not a 2-3 point summary. Unit
# conversion: fuel_lph = GPH * 3.78541; speed_kn is the table's own Knots
# column (no mph->kn conversion needed). See fuel_model.vessel for the
# matching VesselProfile of each.

ROBALO_R180_YAMAHA_115_DATA: list[dict] = [
    {"rpm": 700, "speed_kn": 2.3, "fuel_lph": 0.95, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 1000, "speed_kn": 3.3, "fuel_lph": 1.89, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 1500, "speed_kn": 4.8, "fuel_lph": 3.03, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 2000, "speed_kn": 5.9, "fuel_lph": 5.11, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 2500, "speed_kn": 6.6, "fuel_lph": 8.33, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 3000, "speed_kn": 7.7, "fuel_lph": 13.63, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 3500, "speed_kn": 17.4, "fuel_lph": 14.38, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 4000, "speed_kn": 22.1, "fuel_lph": 18.55, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 4500, "speed_kn": 26.0, "fuel_lph": 23.66, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 5000, "speed_kn": 29.4, "fuel_lph": 28.2, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 5500, "speed_kn": 32.9, "fuel_lph": 34.83, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 6000, "speed_kn": 35.7, "fuel_lph": 39.37, "source": 'boattest.com Robalo R180 (2019) test — 1x115-hp Yamaha 4-stroke', "confidence": 0.75},
]
for pt in ROBALO_R180_YAMAHA_115_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)


BOSTON_WHALER_210_DAUNTLESS_DATA: list[dict] = [
    {"rpm": 680, "speed_kn": 2.3, "fuel_lph": 1.51, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 1000, "speed_kn": 3.7, "fuel_lph": 2.65, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 1500, "speed_kn": 5.4, "fuel_lph": 4.16, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 2000, "speed_kn": 6.3, "fuel_lph": 7.19, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 2500, "speed_kn": 7.3, "fuel_lph": 11.73, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 3000, "speed_kn": 10.2, "fuel_lph": 17.79, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 3500, "speed_kn": 17.5, "fuel_lph": 18.17, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 4000, "speed_kn": 23.2, "fuel_lph": 22.71, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 4500, "speed_kn": 27.2, "fuel_lph": 30.28, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 5000, "speed_kn": 31.0, "fuel_lph": 39.37, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 5500, "speed_kn": 34.9, "fuel_lph": 51.86, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 6230, "speed_kn": 40.2, "fuel_lph": 75.33, "source": 'boattest.com Boston Whaler 210 Dauntless (2019) test — 1x200-hp Mercury Verado', "confidence": 0.75},
]
for pt in BOSTON_WHALER_210_DAUNTLESS_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)


AXOPAR_25_CROSS_TOP_DATA: list[dict] = [
    {"rpm": 630, "speed_kn": 3.0, "fuel_lph": 3.03, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 1000, "speed_kn": 4.7, "fuel_lph": 4.92, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 1500, "speed_kn": 6.3, "fuel_lph": 7.95, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 2000, "speed_kn": 7.6, "fuel_lph": 12.11, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 2500, "speed_kn": 9.5, "fuel_lph": 14.38, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 3000, "speed_kn": 15.6, "fuel_lph": 20.82, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 3500, "speed_kn": 21.3, "fuel_lph": 26.5, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 4000, "speed_kn": 27.0, "fuel_lph": 38.23, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 4500, "speed_kn": 30.7, "fuel_lph": 46.18, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 5000, "speed_kn": 34.4, "fuel_lph": 58.67, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 5500, "speed_kn": 38.8, "fuel_lph": 69.27, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
    {"rpm": 6000, "speed_kn": 42.6, "fuel_lph": 82.52, "source": 'boattest.com Axopar 25 Cross Top (2023) test — 1x250-hp Mercury Verado V6', "confidence": 0.75},
]
for pt in AXOPAR_25_CROSS_TOP_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)


AXOPAR_22_T_TOP_DATA: list[dict] = [
    {"rpm": 600, "speed_kn": 2.7, "fuel_lph": 2.27, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 1000, "speed_kn": 4.4, "fuel_lph": 4.16, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 1500, "speed_kn": 6.3, "fuel_lph": 7.19, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 2000, "speed_kn": 7.6, "fuel_lph": 12.11, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 2500, "speed_kn": 11.5, "fuel_lph": 16.66, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 3000, "speed_kn": 17.7, "fuel_lph": 19.31, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 3500, "speed_kn": 23.6, "fuel_lph": 23.09, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 4000, "speed_kn": 28.7, "fuel_lph": 32.93, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 4500, "speed_kn": 32.1, "fuel_lph": 40.5, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 5000, "speed_kn": 36.7, "fuel_lph": 56.02, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
    {"rpm": 5400, "speed_kn": 39.5, "fuel_lph": 64.73, "source": 'boattest.com Axopar 22 T-Top (2022) test — 1x200-hp Mercury Verado', "confidence": 0.75},
]
for pt in AXOPAR_22_T_TOP_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)


BOSTON_WHALER_190_MONTAUK_DATA: list[dict] = [
    {"rpm": 650, "speed_kn": 2.5, "fuel_lph": 1.89, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 1000, "speed_kn": 3.9, "fuel_lph": 3.03, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 1500, "speed_kn": 5.5, "fuel_lph": 4.92, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 2000, "speed_kn": 6.8, "fuel_lph": 7.95, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 2500, "speed_kn": 8.5, "fuel_lph": 12.49, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 3000, "speed_kn": 17.1, "fuel_lph": 15.14, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 3500, "speed_kn": 22.5, "fuel_lph": 18.55, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 4000, "speed_kn": 27.6, "fuel_lph": 24.98, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 4500, "speed_kn": 31.1, "fuel_lph": 32.93, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 5000, "speed_kn": 34.3, "fuel_lph": 43.15, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 5500, "speed_kn": 39.2, "fuel_lph": 52.24, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    {"rpm": 5650, "speed_kn": 39.7, "fuel_lph": 56.02, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — 1x150-hp Mercury 4-stroke', "confidence": 0.75},
    # Source quirk: fuel burn at WOT (5900 rpm) is reported LOWER than at
    # 5650 rpm even though speed increased — flagged, kept at reduced
    # confidence rather than silently smoothed over.
    {"rpm": 5900, "speed_kn": 40.8, "fuel_lph": 55.65, "source": 'boattest.com Boston Whaler 190 Montauk (2019) test — fuel burn dips vs. previous point, flagged', "confidence": 0.4},
]
for pt in BOSTON_WHALER_190_MONTAUK_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)


ROBALO_R160_YAMAHA_70_DATA: list[dict] = [
    {"rpm": 600, "speed_kn": 2.6, "fuel_lph": 1.14, "source": 'boattest.com Robalo R160 (2019) test — 1x70-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 1500, "speed_kn": 4.4, "fuel_lph": 2.08, "source": 'boattest.com Robalo R160 (2019) test — 1x70-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 2500, "speed_kn": 6.0, "fuel_lph": 5.11, "source": 'boattest.com Robalo R160 (2019) test — 1x70-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 3500, "speed_kn": 14.1, "fuel_lph": 8.9, "source": 'boattest.com Robalo R160 (2019) test — 1x70-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 4500, "speed_kn": 21.7, "fuel_lph": 12.3, "source": 'boattest.com Robalo R160 (2019) test — 1x70-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 5500, "speed_kn": 27.4, "fuel_lph": 17.6, "source": 'boattest.com Robalo R160 (2019) test — 1x70-hp Yamaha 4-stroke', "confidence": 0.75},
    {"rpm": 6100, "speed_kn": 30.5, "fuel_lph": 23.28, "source": 'boattest.com Robalo R160 (2019) test — 1x70-hp Yamaha 4-stroke', "confidence": 0.75},
]
for pt in ROBALO_R160_YAMAHA_70_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)


GRADY_WHITE_FISHERMAN_236_DATA: list[dict] = [
    {"rpm": 500, "speed_kn": 2.6, "fuel_lph": 2.46, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 1000, "speed_kn": 5.2, "fuel_lph": 4.54, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 1500, "speed_kn": 7.0, "fuel_lph": 7.57, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 2000, "speed_kn": 8.7, "fuel_lph": 12.87, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 2500, "speed_kn": 11.3, "fuel_lph": 20.25, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 3000, "speed_kn": 14.8, "fuel_lph": 26.31, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 3500, "speed_kn": 23.5, "fuel_lph": 34.45, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 4000, "speed_kn": 27.8, "fuel_lph": 46.18, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 4500, "speed_kn": 32.2, "fuel_lph": 58.67, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 5000, "speed_kn": 35.7, "fuel_lph": 80.63, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 5500, "speed_kn": 40.4, "fuel_lph": 99.37, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
    {"rpm": 5700, "speed_kn": 42.2, "fuel_lph": 108.07, "source": 'boattest.com Grady-White Fisherman 236 (2016) test — 1x300-hp Yamaha F300', "confidence": 0.75},
]
for pt in GRADY_WHITE_FISHERMAN_236_DATA:
    pt["derived_lpnm"] = round(pt["fuel_lph"] / pt["speed_kn"], 4)



robalo_r180_fuel_curve = FuelCurve.from_data(ROBALO_R180_YAMAHA_115_DATA)
boston_whaler_210_dauntless_fuel_curve = FuelCurve.from_data(BOSTON_WHALER_210_DAUNTLESS_DATA)
axopar_25_cross_top_fuel_curve = FuelCurve.from_data(AXOPAR_25_CROSS_TOP_DATA)
axopar_22_t_top_fuel_curve = FuelCurve.from_data(AXOPAR_22_T_TOP_DATA)
boston_whaler_190_montauk_fuel_curve = FuelCurve.from_data(BOSTON_WHALER_190_MONTAUK_DATA)
robalo_r160_fuel_curve = FuelCurve.from_data(ROBALO_R160_YAMAHA_70_DATA)
grady_white_fisherman_236_fuel_curve = FuelCurve.from_data(GRADY_WHITE_FISHERMAN_236_DATA)
