"""Shared test fixtures."""

import pytest


@pytest.fixture
def sample_origin():
    """Bodrum marina area — default test origin."""
    return {"lat": 37.0134, "lon": 27.4583}


@pytest.fixture
def sample_vessel_profile():
    """Axopar 28 2019 Verado 300 vessel profile."""
    return {
        "id": "axopar-28-2019-verado-300",
        "name": "Axopar 28",
        "manufacturer": "Axopar",
        "model": "28",
        "year": 2019,
        "propulsion_type": "outboard",
        "engine_count": 1,
        "tank_capacity_l": 257,
        "hull_draft_m": 0.80,
        "propulsion_depth_m": 0.85,
        "default_reserve_pct": 20,
    }
