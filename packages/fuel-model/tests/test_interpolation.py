"""Tests for PCHIP monotonic interpolation — fuel model core."""

import pytest
from fuel_model.interpolation import pchip_interpolate, monotonic_cubic_interp


class TestPCHIPInterpolate:
    """Verify shape-preserving monotonic cubic interpolation."""

    def test_exact_point_retrieval(self):
        """Given known control points, interpolation at those points is exact."""
        x = [1000, 2000, 3000, 4000]
        y = [5.0, 8.0, 13.0, 27.0]

        assert pchip_interpolate(x, y, 1000) == pytest.approx(5.0)
        assert pchip_interpolate(x, y, 2000) == pytest.approx(8.0)
        assert pchip_interpolate(x, y, 3000) == pytest.approx(13.0)
        assert pchip_interpolate(x, y, 4000) == pytest.approx(27.0)

    def test_monotonic_preservation_increasing(self):
        """If data is monotonically increasing, output should be too."""
        x = [1, 2, 3, 4, 5]
        y = [10, 20, 30, 40, 50]

        results = [pchip_interpolate(x, y, xi) for xi in [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]]
        # Each successive value should be >= previous (monotonic)
        for i in range(1, len(results)):
            assert results[i] >= results[i - 1], f"Non-monotonic at {results}"

    def test_no_overshoot_between_sparse_points(self):
        """PCHIP must not overshoot between control points.
        
        This is the key difference from cubic splines — unrestricted splines
        can produce values outside the range [y_min, y_max] between two points.
        """
        x = [1000, 2000, 3000]
        y = [5.0, 15.0, 10.0]  # peak at middle point

        for xi in [1001, 1500, 1999]:
            yi = pchip_interpolate(x, y, xi)
            assert yi <= max(y), f"Overshoot: {yi} > {max(y)} at x={xi}"
            assert yi >= min(y), f"Undershoot: {yi} < {min(y)} at x={xi}"

    def test_linear_region_preserved(self):
        """If data is linear between two points, interpolation should be linear."""
        x = [0, 10]
        y = [0, 100]

        assert pchip_interpolate(x, y, 5) == pytest.approx(50.0)
        assert pchip_interpolate(x, y, 7) == pytest.approx(70.0)

    def test_no_overshoot_with_same_sign_unequal_secants(self):
        """Regression guard: weighted ARITHMETIC mean (the old bug) overshoots
        here even though both adjacent secants have the SAME sign, which is
        exactly the case the sign-flip zeroing branch does not catch.

        x=[0,1,2,3], y=[0,0.1,0.2,10.0]: the last segment's secant (9.8/unit)
        swamped the interior node's arithmetic-mean slope at x=2, and the
        curve dipped to -0.45 on the much shallower [1,2] segment — below the
        segment's own y_min of 0.1. The correct Fritsch-Carlson weighted
        HARMONIC mean does not do this.
        """
        x = [0, 1, 2, 3]
        y = [0, 0.1, 0.2, 10.0]

        segment_values = [pchip_interpolate(x, y, 1 + i / 100) for i in range(101)]
        assert min(segment_values) >= 0.1 - 1e-9, (
            f"undershoot on [1,2]: min={min(segment_values)}, segment data is [0.1, 0.2]"
        )
        assert max(segment_values) <= 0.2 + 1e-9, (
            f"overshoot on [1,2]: max={max(segment_values)}, segment data is [0.1, 0.2]"
        )

    def test_matches_scipy_pchip_interpolator(self):
        """Cross-check against scipy's reference PCHIP implementation.

        Not a hard dependency of this package — skipped if scipy isn't
        installed — but whenever it's available this is the strongest
        available proof the Fritsch-Carlson formula (interior weighted
        harmonic mean AND the one-sided endpoint derivative) is implemented
        correctly rather than merely "monotonic and plausible".
        """
        scipy_interp = pytest.importorskip("scipy.interpolate")
        import numpy as np

        rpm = [1000, 1500, 2000, 3200, 3500, 4000, 4500, 5000, 5400]
        speed = [5.0, 6.0, 8.0, 13.0, 16.9, 20.0, 26.0, 31.0, 35.0]
        lph = [6.0, 8.0, 13.0, 27.0, 38.0, 43.8, 60.5, 75.5, 87.2]

        for y in (speed, lph):
            reference = scipy_interp.PchipInterpolator(rpm, y)
            for rpm_query in np.linspace(rpm[0], rpm[-1], 500):
                ours = pchip_interpolate(rpm, y, float(rpm_query))
                theirs = float(reference(rpm_query))
                assert ours == pytest.approx(theirs, abs=1e-9), (
                    f"mismatch at rpm={rpm_query}: ours={ours}, scipy={theirs}"
                )

    def test_extrapolation_raises(self):
        """Extrapolation beyond measured range must NOT be allowed."""
        x = [1000, 2000, 3000]
        y = [5.0, 8.0, 13.0]

        with pytest.raises(ValueError):
            pchip_interpolate(x, y, 500)  # below minimum x

        with pytest.raises(ValueError):
            pchip_interpolate(x, y, 4000)  # above maximum x


class TestMonotonicCubicInterp:
    """Test the full-array version of monotonic cubic interpolation."""

    def test_returns_correct_length(self):
        x = [1000, 2000, 3000]
        y = [5.0, 8.0, 13.0]
        x_new = [1000, 1500, 2000, 2500, 3000]

        result = monotonic_cubic_interp(x, y, x_new)
        assert len(result) == len(x_new)

    def test_endpoints_preserved(self):
        x = [1000, 2000, 3000]
        y = [5.0, 8.0, 13.0]
        x_new = [1000, 2000, 3000]

        result = monotonic_cubic_interp(x, y, x_new)
        assert result[0] == pytest.approx(5.0)
        assert result[1] == pytest.approx(8.0)
        assert result[2] == pytest.approx(13.0)

    def test_fuel_curve_interpolation(self):
        """Interpolate RPM -> speed for Axopar data."""
        rpm = [1000, 1500, 2000, 3200, 3500, 4000, 4500, 5000, 5400]
        speed = [5.0, 6.0, 8.0, 13.0, 16.9, 20.0, 26.0, 31.0, 35.0]

        # At 4000 RPM, speed should be exactly 20.0
        assert pchip_interpolate(rpm, speed, 4000) == pytest.approx(20.0, abs=0.01)
        
        # At 3500 RPM, speed should be ~16.9 (reconciled value)
        assert pchip_interpolate(rpm, speed, 3500) == pytest.approx(16.9, abs=0.01)
        
        # Interpolated value at 4200 RPM should be between 20.0 and 26.0
        v = pchip_interpolate(rpm, speed, 4200)
        assert 20.0 < v < 26.0
