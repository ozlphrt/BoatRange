from diagnostics.snapshot import build_diagnostic_snapshot, stable_request_hash


def test_request_hash_is_stable_across_key_order_and_changes_with_state():
    assert stable_request_hash({"b": 2, "a": 1}) == stable_request_hash({"a": 1, "b": 2})
    assert stable_request_hash({"a": 1}) != stable_request_hash({"a": 2})


def test_snapshot_records_failed_verification_and_geometry():
    response = {
        "region": {"id": "test"},
        "vessel": {"id": "boat"},
        "versions": {"routingEngineVersion": "1"},
        "metrics": {"n_nodes": 4, "computeMs": 12.5},
        "bands": {"100": {"geometry": {"type": "Polygon", "coordinates": []}, "validation": {"ok": False}}},
        "allBandsValid": False,
        "anomalies": ["bad"],
        "warnings": [],
    }
    snapshot = build_diagnostic_snapshot(
        request_state={"origin": {"lat": 1, "lon": 2}}, response=response,
        source_layers=[], source_features=[]
    )
    assert len(snapshot["requestHash"]) == 64
    assert snapshot["verifier"]["accepted"] is False
    assert snapshot["verifier"]["failedBands"] == ["100"]
    assert snapshot["polygons"]["100"]["type"] == "Polygon"
