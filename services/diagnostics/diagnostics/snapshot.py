"""Build deterministic, portable diagnostic snapshots.

The request hash deliberately excludes request IDs and timings so the same
calculation state always has the same identity. Runtime measurements remain in
the exported snapshot for performance diagnosis.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def stable_request_hash(request_state: Mapping[str, Any]) -> str:
    """Return the SHA-256 identity of a normalized calculation state."""
    return hashlib.sha256(_canonical_json(request_state)).hexdigest()


def build_diagnostic_snapshot(
    *,
    request_state: Mapping[str, Any],
    response: Mapping[str, Any],
    source_layers: list[dict[str, Any]],
    source_features: list[dict[str, Any]],
    random_seed: int = 1337,
) -> dict[str, Any]:
    """Create the complete JSON artifact required by developer mode."""
    validations = {
        key: band.get("validation", {})
        for key, band in response.get("bands", {}).items()
    }
    failed = [key for key, item in validations.items() if not item.get("ok", False)]
    return {
        "schemaVersion": "1.0.0",
        "requestHash": stable_request_hash(request_state),
        "requestState": dict(request_state),
        "origin": request_state.get("origin"),
        "region": response.get("region"),
        "vessel": response.get("vessel"),
        "fuelSettings": {
            key: request_state.get(key)
            for key in ("fuel", "reservePct", "speedKn", "rpm", "seaState", "loadState")
        },
        "routingParameters": {
            key: request_state.get(key)
            for key in ("clearanceM", "rangeMode", "depthPolicy", "depthSafetyMarginM")
        },
        "versions": response.get("versions", {}),
        "sourceLayers": source_layers,
        "sourceFeatures": source_features,
        "grid": response.get("metrics", {}),
        "polygons": {
            key: band.get("geometry") for key, band in response.get("bands", {}).items()
        },
        "verifier": {
            "accepted": bool(response.get("allBandsValid")) and not response.get("anomalies"),
            "bandResults": validations,
            "failedBands": failed,
            "anomalies": list(response.get("anomalies", [])),
            "randomSeed": random_seed,
        },
        "timings": {"computeMs": response.get("metrics", {}).get("computeMs")},
        "warnings": list(response.get("warnings", [])),
    }
