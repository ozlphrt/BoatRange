from types import SimpleNamespace

from fastapi.testclient import TestClient
from shapely.geometry import Point, box

from app.main import _reachable_fuel_stop_ids, app

client = TestClient(app)


def test_region_geometry_exposes_versioned_marinas_and_fuel_docks():
    response = client.get("/api/v1/regions/bodrum-kos-real/geometry")
    assert response.status_code == 200
    body = response.json()
    assert body["marinePoiCount"] == 33
    assert body["fuelDockCount"] == 2
    assert len(body["marinePois"]["features"]) == 33
    fuel = [
        item for item in body["marinePois"]["features"]
        if item["properties"]["poiType"] == "fuel_dock"
    ]
    assert len(fuel) == 2
    assert all(item["properties"]["sourceId"].startswith("osm:") for item in fuel)


def test_reachable_fuel_stops_require_valid_outer_polygon_coverage():
    region = SimpleNamespace(marine_pois=[
        SimpleNamespace(source_id="inside", poi_type="fuel_dock", geometry=Point(1, 1)),
        SimpleNamespace(source_id="outside", poi_type="fuel_dock", geometry=Point(5, 5)),
        SimpleNamespace(source_id="marina", poi_type="marina", geometry=Point(1, 1)),
    ])
    valid = SimpleNamespace(ok=True)
    outer = SimpleNamespace(fraction=1.0, polygon=box(0, 0, 2, 2), validation=valid)
    inner = SimpleNamespace(fraction=0.5, polygon=box(0, 0, 1, 1), validation=valid)
    assert _reachable_fuel_stop_ids(region, {0.5: inner, 1.0: outer}) == ["inside"]

    outer.validation = SimpleNamespace(ok=False)
    assert _reachable_fuel_stop_ids(region, {1.0: outer}) == []


def test_range_response_always_includes_reachable_fuel_stop_ids():
    response = client.post("/api/v1/range/preview", json={
        "origin": {"lat": 36.94, "lon": 27.30},
        "fuel": {"mode": "percent", "value": 100},
        "reservePct": 20,
        "speedKn": 20,
        "seaState": "calm",
        "loadState": "normal",
        "clearanceM": 50,
        "rangeMode": "one_way",
    })
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["reachableFuelStopIds"], list)
    assert set(body["reachableFuelStopIds"]) <= {
        "osm:node/2422088590", "osm:node/497504262"
    }
