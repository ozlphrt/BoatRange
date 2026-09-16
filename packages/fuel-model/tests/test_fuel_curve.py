"""Tests for FuelCurve — Axopar 28 cleaned data and derived calculations."""

import pytest
from fuel_model.curve import AmbiguousInversionError, FuelCurve, CLEANED_FUEL_DATA


class TestCleanedFuelData:
    """Verify the cleaned v1 anchor data matches CLAUDE.md spec section 4.2."""

    def test_point_count(self):
        assert len(CLEANED_FUEL_DATA) == 9

    def test_rpm_values_match_spec(self):
        rpms = [pt["rpm"] for pt in CLEANED_FUEL_DATA]
        expected = [1000, 1500, 2000, 3200, 3500, 4000, 4500, 5000, 5400]
        assert rpms == expected

    def test_speed_values_match_spec(self):
        speeds = [pt["speed_kn"] for pt in CLEANED_FUEL_DATA]
        expected = [5.0, 6.0, 8.0, 13.0, 16.9, 20.0, 26.0, 31.0, 35.0]
        assert speeds == expected

    def test_fuel_lph_values_match_spec(self):
        lph = [pt["fuel_lph"] for pt in CLEANED_FUEL_DATA]
        expected = [6.0, 8.0, 13.0, 27.0, 38.0, 43.8, 60.5, 75.5, 87.2]
        assert lph == expected

    def test_derived_lpnm_is_correct(self):
        """L/nm = L/h / knots. Verify each point (allow rounding to 4dp)."""
        for pt in CLEANED_FUEL_DATA:
            derived = pt["fuel_lph"] / pt["speed_kn"]
            assert pt["derived_lpnm"] == pytest.approx(derived, rel=1e-3)

    def test_3500_rpm_reconciliation(self):
        """3500 RPM reconciled from 38 L/h and 2.25 L/nm: 38/2.25 = 16.89 kn."""
        pt = next(p for p in CLEANED_FUEL_DATA if p["rpm"] == 3500)
        assert pt["fuel_lph"] == 38.0
        expected_speed = 38.0 / 2.25
        # Spec says 16.9 kn (rounded from 16.888...)
        assert pt["speed_kn"] == pytest.approx(expected_speed, abs=0.02)

    def test_4000_rpm_inconsistency_flagged(self):
        """4000 RPM: 43.8/20 = 2.19 L/nm, NOT 2.33 as printed in original graphic."""
        pt = next(p for p in CLEANED_FUEL_DATA if p["rpm"] == 4000)
        assert pt["derived_lpnm"] == pytest.approx(2.19, abs=0.01)


class TestFuelCurve:
    """Test FuelCurve class behavior."""

    @pytest.fixture
    def curve(self):
        return FuelCurve.from_data(CLEANED_FUEL_DATA)

    def test_speed_at_rpm(self, curve):
        """Get interpolated speed at a given RPM."""
        speed = curve.speed_at_rpm(4000)
        assert speed == pytest.approx(20.0, abs=0.01)

    def test_fuel_flow_at_rpm(self, curve):
        """Get interpolated fuel flow (L/h) at a given RPM."""
        lph = curve.fuel_flow_at_rpm(4000)
        assert lph == pytest.approx(43.8, abs=0.01)

    def test_lpnm_at_rpm(self, curve):
        """Get derived L/nm at a given RPM."""
        lpnm = curve.lpnm_at_rpm(4000)
        assert lpnm == pytest.approx(2.19, abs=0.01)

    def test_speed_at_rpm_extrapolation_raises(self, curve):
        """Must not extrapolate beyond measured RPM range."""
        with pytest.raises(ValueError):
            curve.speed_at_rpm(500)  # below min RPM

        with pytest.raises(ValueError):
            curve.speed_at_rpm(6000)  # above max RPM

    def test_speed_to_rpm_inversion(self, curve):
        """Bidirectional: speed -> RPM mapping."""
        rpm = curve.rpm_at_speed(20.0)
        assert rpm == pytest.approx(4000, abs=10)

    def test_speed_to_rpm_round_trips_through_the_same_pchip_curve(self, curve):
        """speed_at_rpm(rpm_at_speed(v)) must recover v almost exactly.

        Regression guard: the previous inversion used a separate LINEAR
        interpolation between raw anchor points, independent of the PCHIP
        curve speed_at_rpm actually evaluates — consistent only exactly at
        the anchors, off by a real amount everywhere the curve bends away
        from a straight line between them.
        """
        for target_kn in [6.0, 8.0, 13.0, 16.9, 20.0, 26.0, 31.0, 21.5, 9.5]:
            rpm = curve.rpm_at_speed(target_kn)
            recovered = curve.speed_at_rpm(rpm)
            assert recovered == pytest.approx(target_kn, abs=0.05), (
                f"round-trip failed for {target_kn} kn: rpm={rpm}, recovered={recovered}"
            )

    def test_speed_to_rpm_respects_load_state(self, curve):
        """Regression guard: rpm_at_speed used to ignore load_state entirely.

        A heavy load slows the boat at every RPM, so reaching the same target
        speed under heavy load requires MORE rpm than under normal load.
        """
        rpm_normal = curve.rpm_at_speed(20.0, load_state="normal")
        rpm_heavy = curve.rpm_at_speed(20.0, load_state="heavy")
        assert rpm_heavy > rpm_normal
        # And the inverted RPM must actually reproduce the target under that
        # same load state.
        assert curve.speed_at_rpm(rpm_heavy, load_state="heavy") == pytest.approx(20.0, abs=0.05)

    def test_ambiguous_inversion_raises_with_candidates(self):
        """CLAUDE.md Section 4.4: a non-monotonic region must surface every
        candidate RPM, never silently pick one."""
        non_monotonic = FuelCurve(
            rpm_data=[1000, 2000, 3000, 4000, 5000],
            speed_data=[5.0, 15.0, 10.0, 20.0, 30.0],  # rises, dips, rises
            lph_data=[6.0, 13.0, 20.0, 40.0, 70.0],
        )
        with pytest.raises(AmbiguousInversionError) as exc_info:
            non_monotonic.rpm_at_speed(12.0)
        assert len(exc_info.value.candidates) >= 2

    def test_speed_to_rpm_extrapolation_raises(self, curve):
        """Must not extrapolate beyond measured speed range."""
        with pytest.raises(ValueError):
            curve.rpm_at_speed(2.0)  # below min speed

        with pytest.raises(ValueError):
            curve.rpm_at_speed(40.0)  # above max speed

    def test_range_at_rpm(self, curve):
        """Range = usable_fuel / lpnm."""
        range_nm = curve.range_at_rpm(4000, usable_fuel_l=200)
        # At 4000 RPM: lpnm ~2.19, so range ~200/2.19 = ~91.3 nm
        assert range_nm == pytest.approx(91.3, abs=0.5)

    def test_endurance_at_rpm(self, curve):
        """Endurance (hours) = usable_fuel / fuel_lph."""
        endurance = curve.endurance_at_rpm(4000, usable_fuel_l=200)
        # At 4000 RPM: lph ~43.8, so endurance ~200/43.8 = ~4.57 h
        assert endurance == pytest.approx(4.57, abs=0.01)

    def test_load_factor_applied(self, curve):
        """Load factor adjusts speed and fuel flow."""
        # Heavy load should reduce speed slightly
        speed_normal = curve.speed_at_rpm(3000, load_state="normal")
        speed_heavy = curve.speed_at_rpm(3000, load_state="heavy")
        assert speed_heavy <= speed_normal

    def test_sea_state_factor_applied(self, curve):
        """Sea state factor adjusts fuel flow (penalty for rough seas)."""
        lph_calm = curve.fuel_flow_at_rpm(3000, sea_state="calm")
        lph_rough = curve.fuel_flow_at_rpm(3000, sea_state="rough")
        assert lph_rough > lph_calm

    def test_inefficient_zone_marked(self, curve):
        """The hump region ~2000-3200 RPM is marked inefficient."""
        for rpm in [1500, 2500, 3000]:
            op = curve.operating_point(rpm)
            if 2000 <= rpm <= 3200:
                assert op.is_inefficient is True

    def test_operating_point_at_boundary(self, curve):
        """Operating point at exact control RPM returns exact values."""
        op = curve.operating_point(3500)
        assert op.rpm == 3500
        assert op.speed_kn == pytest.approx(16.9, abs=0.01)
        assert op.fuel_lph == pytest.approx(38.0, abs=0.01)
        assert op.lpnm == pytest.approx(2.25, abs=0.01)
