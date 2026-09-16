"""Tests for /api/v1/route."""

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, shape
from shapely.ops import transform, unary_union

from app.main import app
from isochrone.projection import LocalProjection
from marine_data.bodrum_kos_real import generate_bodrum_kos_real_water

client = TestClient(app)

# The bathymetry-backed Bodrum/Kos region is preferred for this area.
CHANNEL_ORIGIN = {"lat": 36.94, "lon": 27.30}  # EMODnet depth ~2.6 m
CHANNEL_DEST = {"lat": 36.94, "lon": 27.20}
LAND_DEST = {"lat": 37.10, "lon": 27.40}

# On opposite sides of the Karaburun peninsula (Izmir, Turkey) — a real
# route between them must detour around the peninsula tip, not cut through
# it. Both verified as real navigable water.
GULF_OF_IZMIR_ORIGIN = {"lat": 38.45, "lon": 26.90}
KARABURUN_WEST_DEST = {"lat": 38.65, "lon": 26.35}


def _payload(**overrides):
    base = {
        "origin": CHANNEL_ORIGIN,
        "destination": CHANNEL_DEST,
        "fuel": {"mode": "percent", "value": 100},
        "reservePct": 20,
        "speedKn": 20,
        "seaState": "calm",
        "loadState": "normal",
        "clearanceM": 50,
    }
    base.update(overrides)
    return base


class TestRoute:
    def test_returns_a_route_with_expected_fields(self):
        r = client.post("/api/v1/route", json=_payload())
        assert r.status_code == 200
        body = r.json()
        for key in ("distanceNm", "etaMinutes", "fuelUsedL", "fuelRemainingL", "routeGeojson", "confidence"):
            assert key in body

    def test_route_never_crosses_land(self):
        r = client.post("/api/v1/route", json=_payload())
        body = r.json()
        # routeGeojson comes back in WGS84 (the API reprojects it — see
        # main.py's route_wgs84_coords); the region's own land geometry is
        # in its LOCAL METER frame. Comparing them directly without
        # reprojecting first is a scale mismatch that made this assertion
        # vacuously true regardless of whether the route actually crossed
        # land (a WGS84-scale line essentially never numerically overlaps a
        # thousands-of-meters-scale polygon by coincidence).
        line = shape(body["routeGeojson"])
        _, land = generate_bodrum_kos_real_water()
        proj = LocalProjection()
        land_union_wgs84 = transform(lambda x, y: proj.to_wgs84(x, y), unary_union(land))
        assert not line.intersects(land_union_wgs84.buffer(-1e-5))

    def test_fuel_used_matches_distance_times_lpnm(self):
        r = client.post("/api/v1/route", json=_payload())
        body = r.json()
        expected = body["distanceNm"] * body["operatingPoint"]["lpnm"]
        assert body["fuelUsedL"] == pytest.approx(expected, rel=1e-3)

    def test_unreachable_destination_returns_422(self):
        r = client.post("/api/v1/route", json=_payload(destination=LAND_DEST))
        assert r.status_code == 422

    def test_low_confidence_always_reported(self):
        r = client.post("/api/v1/route", json=_payload())
        assert r.json()["confidence"] == "LOW"

    def test_insufficient_fuel_flagged_but_route_still_returned(self):
        """A route that costs more than usable fuel is still geometrically
        valid — the API should return it with a clear warning, not hide it."""
        r = client.post("/api/v1/route", json=_payload(fuel={"mode": "liters", "value": 0.5}, reservePct=0))
        assert r.status_code == 200
        body = r.json()
        assert body["reachableWithCurrentFuel"] is False
        assert any("exceeds usable fuel" in w for w in body["warnings"])

    def test_route_around_a_peninsula_is_found(self):
        """Regression guard: the route graph used to be cropped to the
        origin/destination bounding box plus a fixed 5km margin. That's
        fine when the real route is roughly a straight line, but when
        origin and destination sit on opposite sides of a peninsula (like
        Karaburun here), the real route has to detour around the tip —
        and a 5km margin gave nowhere near enough room for that detour, so
        the cropped graph had no path even though the destination was
        genuinely reachable by water. shortest_path() correctly reported
        "unreachable" for the (too-small) graph it was given; the bug was
        the crop, not the pathfinding. Margin now scales with the direct
        origin-destination distance instead of a fixed 5km."""
        r = client.post(
            "/api/v1/route",
            json=_payload(
                origin=GULF_OF_IZMIR_ORIGIN,
                destination=KARABURUN_WEST_DEST,
                depthPolicy="permissive",
                developerMode=True,
            ),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["distanceNm"] > 0
