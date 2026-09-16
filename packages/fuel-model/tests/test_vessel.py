"""Tests for vessel profile definitions."""

import pytest
from fuel_model.vessel import VesselProfile, Axopar28V1


class TestAxopar28V1:
    """Verify the Axopar 28 v1 vessel profile matches spec requirements."""

    def test_id_matches_spec(self):
        assert Axopar28V1.id == "axopar-28-2019-verado-300"

    def test_engine_count_is_single(self):
        """v1 only supports single outboard."""
        assert Axopar28V1.engine_count == 1
        assert Axopar28V1.propulsion_type == "outboard"

    def test_tank_capacity(self):
        """Axopar 28 2019 has a 257L (68 US gal) fuel tank.

        Corrected from an earlier, unverified 750 L figure using an
        independent boattest.com sea trial of the Axopar 28 Cabin
        (https://boattest.com/boats/axopar/28-cabin-2019) — see
        fuel_model.catalog for the full citation.
        """
        assert Axopar28V1.tank_capacity_l == 257

    def test_draft_values(self):
        """Hull draft and propulsion depth are stored separately."""
        assert Axopar28V1.hull_draft_m > 0
        assert Axopar28V1.propulsion_depth_m >= Axopar28V1.hull_draft_m

    def test_default_reserve_is_20_pct(self):
        """Default fuel reserve is 20% per spec section 3.6."""
        assert Axopar28V1.default_reserve_pct == 20

    def test_fuel_type_is_gasoline(self):
        from fuel_model.vessel import FuelType
        assert Axopar28V1.fuel_type == FuelType.GASOLINE


class TestVesselProfile:
    """Test VesselProfile dataclass behavior."""

    def test_minimal_creation(self):
        profile = VesselProfile(
            id="test-1",
            name="Test Boat",
            manufacturer="Test",
            model="T1",
            year=2024,
            propulsion_type="outboard",
            engine_count=1,
            tank_capacity_l=200,
            hull_draft_m=0.8,
            propulsion_depth_m=1.0,
            default_reserve_pct=15,
        )
        assert profile.id == "test-1"
        from fuel_model.vessel import FuelType
        assert profile.fuel_type == FuelType.GASOLINE  # default

    def test_fuel_type_validation(self):
        from fuel_model.vessel import FuelType
        with pytest.raises(ValueError):
            VesselProfile(
                id="bad", name="Bad", manufacturer="X", model="Y", year=2024,
                propulsion_type="outboard", engine_count=1,
                tank_capacity_l=100, hull_draft_m=0.5, propulsion_depth_m=0.6,
                default_reserve_pct=20, fuel_type="invalid_fuel_type",
            )

    def test_usable_fuel_calculation(self):
        """Usable fuel = total * (1 - reserve_pct/100)."""
        profile = VesselProfile(
            id="test-2", name="T", manufacturer="X", model="Y", year=2024,
            propulsion_type="outboard", engine_count=1,
            tank_capacity_l=500, hull_draft_m=0.8, propulsion_depth_m=1.0,
            default_reserve_pct=20,
        )
        usable = profile.usable_fuel_liters()
        assert usable == 400.0  # 500 * 0.8

    def test_usable_fuel_with_custom_reserve(self):
        profile = VesselProfile(
            id="test-3", name="T", manufacturer="X", model="Y", year=2024,
            propulsion_type="outboard", engine_count=1,
            tank_capacity_l=500, hull_draft_m=0.8, propulsion_depth_m=1.0,
            default_reserve_pct=20,
        )
        usable = profile.usable_fuel_liters(reserve_pct=30)
        assert usable == 350.0

    def test_min_safe_depth(self):
        """Min safe depth = max(hull_draft, propulsion_depth) + safety_margin."""
        profile = VesselProfile(
            id="test-4", name="T", manufacturer="X", model="Y", year=2024,
            propulsion_type="outboard", engine_count=1,
            tank_capacity_l=500, hull_draft_m=0.8, propulsion_depth_m=1.0,
            default_reserve_pct=20,
        )
        depth = profile.min_safe_depth(safety_margin_m=0.5)
        assert depth == 1.5  # max(0.8, 1.0) + 0.5
