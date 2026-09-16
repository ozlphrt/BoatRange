"""Tests for the multi-vessel catalog — CLAUDE.md Section 1.1 generic vessel schema."""

import pytest

from fuel_model.catalog import VESSEL_CATALOG, get_vessel_entry, list_vessels
from fuel_model.vessel import Axopar28V1, Dusky233Evinrude300


class TestCatalogContainsBothVessels:
    def test_axopar_is_registered(self):
        assert Axopar28V1.id in VESSEL_CATALOG

    def test_dusky_is_registered(self):
        assert Dusky233Evinrude300.id in VESSEL_CATALOG

    def test_list_vessels_returns_at_least_two(self):
        assert len(list_vessels()) >= 2

    def test_unknown_vessel_raises_key_error(self):
        with pytest.raises(KeyError):
            get_vessel_entry("not-a-real-vessel")


class TestEachEntryIsSelfConsistent:
    @pytest.mark.parametrize("vessel_id", list(VESSEL_CATALOG.keys()))
    def test_fuel_curve_matches_vessel_profile(self, vessel_id):
        entry = get_vessel_entry(vessel_id)
        assert entry.profile.id == vessel_id
        # The curve must be usable: at least the min RPM must interpolate
        # without raising.
        speed = entry.fuel_curve.speed_at_rpm(entry.fuel_curve.min_rpm)
        assert speed > 0

    @pytest.mark.parametrize("vessel_id", list(VESSEL_CATALOG.keys()))
    def test_data_confidence_in_range(self, vessel_id):
        entry = get_vessel_entry(vessel_id)
        assert 0.0 <= entry.data_confidence <= 1.0

    @pytest.mark.parametrize("vessel_id", list(VESSEL_CATALOG.keys()))
    def test_notes_are_non_empty(self, vessel_id):
        """CLAUDE.md Section 49.11: data quality caveats must be surfaced,
        never silently assumed away."""
        entry = get_vessel_entry(vessel_id)
        assert len(entry.notes) > 0


class TestDuskyCurveHonestlyReflectsSparseData:
    """The Dusky curve was deliberately built from a sparse, partly-flagged
    source (see fuel_model.curve.DUSKY_233_EVINRUDE_300_DATA docstring)."""

    def test_curve_has_only_three_anchor_points(self):
        entry = get_vessel_entry(Dusky233Evinrude300.id)
        assert len(entry.fuel_curve._rpm) == 3

    def test_curve_cannot_answer_low_speed_queries(self):
        """No idle/low-RPM data exists for this vessel — the curve must
        refuse rather than guess at low speed, exactly like it refuses
        RPM extrapolation."""
        entry = get_vessel_entry(Dusky233Evinrude300.id)
        with pytest.raises(ValueError):
            entry.fuel_curve.rpm_at_speed(10.0)  # below this boat's min logged speed

    def test_confidence_lower_than_axopar(self):
        axopar = get_vessel_entry(Axopar28V1.id)
        dusky = get_vessel_entry(Dusky233Evinrude300.id)
        assert dusky.data_confidence < axopar.data_confidence
