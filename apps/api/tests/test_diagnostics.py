"""Developer diagnostic export, point reasoning, and re-verification."""

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
BASE = {
    "origin": {"lat": 36.94, "lon": 27.30},
    "fuel": {"mode": "liters", "value": 40},
    "reservePct": 20,
    "speedKn": 20,
    "seaState": "calm",
    "loadState": "normal",
    "clearanceM": 50,
    "rangeMode": "one_way",
    "developerMode": True,
}


def test_developer_range_exports_reproducible_snapshot():
    first = client.post("/api/v1/range/preview", json=BASE)
    second = client.post("/api/v1/range/preview", json=BASE)
    assert first.status_code == second.status_code == 200
    one = first.json()["diagnostics"]
    two = second.json()["diagnostics"]
    assert one["requestHash"] == two["requestHash"]
    assert one["sourceLayers"] and one["sourceFeatures"]
    assert any(item.get("sourceId") == "hnhs-pilot-d-206-2014" for item in one["sourceFeatures"])
    assert any("GR4210008" in str(item.get("sourceId")) for item in one["sourceFeatures"])
    assert "refined_node_count" in one["grid"]
    assert one["polygons"]["100"] is not None


def test_normal_range_omits_diagnostic_payload():
    payload = {**BASE, "developerMode": False}
    assert "diagnostics" not in client.post("/api/v1/range/preview", json=payload).json()


def test_verify_accepts_engine_output_and_rejects_land_polygon():
    result = client.post("/api/v1/range/preview", json=BASE).json()
    good = client.post("/api/v1/verify", json={**BASE, "geometry": result["bands"]["100"]["geometry"]})
    assert good.status_code == 200
    assert good.json()["accepted"] is True

    land_box = {
        "type": "Polygon",
        "coordinates": [[[27.39, 37.09], [27.41, 37.09], [27.41, 37.11], [27.39, 37.11], [27.39, 37.09]]],
    }
    bad = client.post("/api/v1/verify", json={**BASE, "geometry": land_box})
    assert bad.status_code == 200
    assert bad.json()["accepted"] is False
    assert bad.json()["rawObstacleIntersection"] is True


def test_verify_and_point_diagnostics_require_developer_mode():
    geometry = {"type": "Polygon", "coordinates": [[[27.29, 36.93], [27.31, 36.93], [27.31, 36.95], [27.29, 36.95], [27.29, 36.93]]]}
    assert client.post("/api/v1/verify", json={**BASE, "developerMode": False, "geometry": geometry}).status_code == 422
    assert client.post("/api/v1/diagnostics/classify-point", json={**BASE, "developerMode": False, "point": BASE["origin"]}).status_code == 422


def test_point_diagnostic_explains_depth_and_final_decision():
    response = client.post(
        "/api/v1/diagnostics/classify-point", json={**BASE, "point": BASE["origin"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["navigable"] is True
    assert {item["sourceId"] for item in body["contributions"]} >= {"coastline", "bathymetry"}


def test_segment_diagnostic_reports_exact_mask_rejection():
    response = client.post(
        "/api/v1/diagnostics/classify-segment",
        json={**BASE, "end": {"lat": 37.10, "lon": 27.40}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["navigable"] is False
    assert any(item["sourceId"] == "classified-water-mask" for item in body["rejections"])
