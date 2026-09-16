"""Tests for /api/v1/range/preview and /api/v1/range/full."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

WATER_ORIGIN = {"lat": 36.94, "lon": 27.30}  # channel, EMODnet depth ~2.6 m
LAND_ORIGIN = {"lat": 37.10, "lon": 27.40}  # inside the synthetic Bodrum landmass
OUTSIDE_REGION_ORIGIN = {"lat": 55.0, "lon": 10.0}  # North Sea — outside every
# registered region's bbox, including east-med-real's (6.5-42 lon, 34.5-46.5
# lat). (41.0, 29.0) — the old value — used to be "nowhere near" any region
# back when only the small Bodrum/Kos fixtures existed, but it's inside
# east-med-real's much wider bbox now (near Istanbul/Marmara).


def _payload(**overrides):
    base = {
        "origin": WATER_ORIGIN,
        "fuel": {"mode": "percent", "value": 100},
        "reservePct": 20,
        "speedKn": 20,
        "seaState": "calm",
        "loadState": "normal",
        "clearanceM": 50,
        "rangeMode": "one_way",
    }
    base.update(overrides)
    return base


class TestRangePreview:
    def test_returns_100_percent_band(self):
        r = client.post("/api/v1/range/preview", json=_payload())
        assert r.status_code == 200
        body = r.json()
        assert set(body["bands"].keys()) == {"100"}
        assert body["quality"] == "preview"

    def test_max_range_matches_usable_fuel_over_lpnm(self):
        r = client.post("/api/v1/range/preview", json=_payload())
        body = r.json()
        # 205.6 L usable (257 * 0.8) / 2.19 L/nm at 20kn
        assert body["maxRangeNm"] == pytest.approx(205.6 / 2.19, rel=1e-3)

    def test_confidence_is_never_overstated(self):
        """Synthetic demo data must never claim HIGH/chart-grade confidence."""
        r = client.post("/api/v1/range/preview", json=_payload())
        body = r.json()
        assert body["confidence"] == "LOW"
        assert body["confidenceReasons"]

    def test_conservative_depth_metadata_is_returned(self):
        r = client.post("/api/v1/range/preview", json=_payload())
        assert r.status_code == 200
        depth = r.json()["depth"]
        assert depth["policy"] == "conservative"
        assert depth["minimumSafeDepthM"] == pytest.approx(1.35)
        assert depth["source"] == "EMODnet Bathymetry Digital Terrain Model"

    def test_disclaimer_present_in_warnings(self):
        r = client.post("/api/v1/range/preview", json=_payload())
        body = r.json()
        assert any("planning purposes only" in w for w in body["warnings"])


class TestRangeFull:
    def test_returns_all_four_bands(self):
        r = client.post("/api/v1/range/full", json=_payload())
        assert r.status_code == 200
        body = r.json()
        assert set(body["bands"].keys()) == {"25", "50", "75", "100"}
        assert body["allBandsValid"] is True

    def test_round_trip_area_smaller_than_one_way(self):
        # A modest fuel load, well short of covering the whole compute
        # extent — with a huge tank/full fuel both modes saturate to "every
        # bit of water in the box" and the areas coincide for an unrelated
        # reason (region size, not range mode).
        modest_fuel = {"mode": "liters", "value": 75}
        one_way = client.post(
            "/api/v1/range/full", json=_payload(rangeMode="one_way", fuel=modest_fuel)
        ).json()
        round_trip = client.post(
            "/api/v1/range/full", json=_payload(rangeMode="round_trip", fuel=modest_fuel)
        ).json()
        assert round_trip["bands"]["100"]["areaM2"] < one_way["bands"]["100"]["areaM2"]

    def test_band_geometry_is_wgs84_not_projected_meters(self):
        """Regression guard: band polygons live in the engine's projected
        local CRS (meters) internally — the API must reproject to WGS84
        before returning GeoJSON, or every coordinate silently comes back
        as a huge meter value nowhere near the actual lon/lat of the region."""
        r = client.post("/api/v1/range/preview", json=_payload())
        body = r.json()
        coords = body["bands"]["100"]["geometry"]["coordinates"]

        def flatten(c):
            if isinstance(c[0], (int, float)):
                yield c
            else:
                for sub in c:
                    yield from flatten(sub)

        for lon, lat in flatten(coords):
            assert -180 <= lon <= 180, f"longitude {lon} is not WGS84 — looks like raw projected meters"
            assert -90 <= lat <= 90, f"latitude {lat} is not WGS84 — looks like raw projected meters"

    def test_bands_never_report_land_intersection(self):
        r = client.post("/api/v1/range/full", json=_payload())
        body = r.json()
        for band in body["bands"].values():
            assert band["validation"]["boundary_land_touches"] == 0
            assert band["validation"]["interior_outside_water"] == 0

    def test_origin_without_bathymetry_fails_closed(self):
        """Regression guard: _compute_and_serialize used to call
        calculate_range() without passing region.projection, silently
        falling back to calculate_range's own default (the Bodrum-centered
        LocalProjection). That was invisible for bodrum-kos-real/-demo,
        which happen to use that exact same default — but east-med-real
        uses a different reference point (lat_ref=40, lon_ref=24), so any
        origin far from Bodrum got projected to local meters using the
        WRONG reference frame relative to where the region's actual water/
        land geometry lives, landing nowhere near real water and failing
        with a confusing "Graph not built" or "origin not navigable" error
        even though the point genuinely is water. This origin — near
        Thessaloniki, Greece — is real, verified navigable water, nowhere
        near Bodrum/Kos, so it only passes if the correct region-specific
        projection is actually used end to end."""
        r = client.post(
            "/api/v1/range/preview",
            json=_payload(origin={"lat": 40.7701, "lon": 24.2500}),
        )
        assert r.status_code == 422
        assert "Unknown depth is blocked" in r.json()["detail"]

    def test_developer_can_explicitly_compare_permissive_depth(self):
        r = client.post(
            "/api/v1/range/preview",
            json=_payload(
                origin={"lat": 40.7701, "lon": 24.2500},
                depthPolicy="permissive",
                developerMode=True,
            ),
        )
        assert r.status_code == 200
        body = r.json()
        assert body["region"]["id"] == "east-med-real"
        assert any("Developer override active" in warning for warning in body["warnings"])


class TestRangeFailClosed:
    def test_permissive_depth_requires_developer_mode(self):
        r = client.post(
            "/api/v1/range/preview", json=_payload(depthPolicy="permissive")
        )
        assert r.status_code == 422

    def test_origin_outside_covered_region_returns_422(self):
        r = client.post("/api/v1/range/preview", json=_payload(origin=OUTSIDE_REGION_ORIGIN))
        assert r.status_code == 422

    def test_origin_on_land_returns_422(self):
        r = client.post("/api/v1/range/preview", json=_payload(origin=LAND_ORIGIN))
        assert r.status_code == 422

    def test_unknown_vessel_returns_404(self):
        r = client.post("/api/v1/range/preview", json=_payload(vesselProfileId="does-not-exist"))
        assert r.status_code == 404

    def test_speed_outside_curve_range_returns_422(self):
        r = client.post("/api/v1/range/preview", json=_payload(speedKn=100.0))
        assert r.status_code == 422

    def test_zero_fuel_returns_422(self):
        r = client.post("/api/v1/range/preview", json=_payload(fuel={"mode": "liters", "value": 0}))
        assert r.status_code == 422

    def test_full_reserve_returns_422(self):
        """100% reserve leaves zero usable fuel — must fail closed, not
        silently compute a zero-radius/degenerate range."""
        r = client.post("/api/v1/range/preview", json=_payload(reservePct=100))
        assert r.status_code == 422
