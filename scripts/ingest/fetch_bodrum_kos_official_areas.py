"""Fetch official/EU-harmonized managed marine areas for Bodrum/Kos.

EMODnet Human Activities is operated for the European Commission's DG MARE
and exposes source provenance through WFS. A protected area is not
automatically a navigation prohibition: Natura 2000 polygons are retained as
INFORMATIONAL. Military areas are HARD_NO_GO only when their published status
explicitly says active/prohibited; unknown status remains conditional.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from shapely.geometry import box, mapping, shape

from marine_data.official_areas import classify_managed_area

BBOX = (26.85, 36.60, 27.85, 37.25)
WFS_URL = "https://ows.emodnet-humanactivities.eu/wfs"
FETCHED_AT = "2026-09-14T00:00:00Z"
LAYERS = {
    "emodnet:militaryareaspoly": "military_area",
    "emodnet:natura2000areas": "protected_area",
}


def fetch_layer(type_name: str) -> dict:
    query = urlencode({
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": type_name, "outputFormat": "application/json",
        "srsName": "EPSG:4326", "bbox": ",".join(map(str, BBOX)) + ",EPSG:4326",
    })
    request = Request(f"{WFS_URL}?{query}", headers={"User-Agent": "AxoparRangePlanner/1.0"})
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def classify(layer: str, properties: dict) -> tuple[str, str]:
    restriction_class, rule = classify_managed_area(layer, properties)
    return restriction_class.value, rule


def normalize(raw_layers: dict[str, dict]) -> dict:
    region_clip = box(*BBOX)
    features = []
    for layer, document in raw_layers.items():
        feature_type = LAYERS[layer]
        for index, item in enumerate(document.get("features", [])):
            geometry = shape(item["geometry"])
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            geometry = geometry.intersection(region_clip)
            if geometry.is_empty:
                continue
            properties = item.get("properties", {})
            restriction_class, rule = classify(layer, properties)
            identity = properties.get("sitecode") or properties.get("gid") or index
            features.append({
                "type": "Feature",
                "properties": {
                    "source_id": f"emodnet:{layer.split(':', 1)[1]}/{identity}",
                    "source_name": "EMODnet Human Activities (European Commission DG MARE)",
                    "source_version": properties.get("release_da") or FETCHED_AT[:10],
                    "feature_type": feature_type,
                    "restriction_class": restriction_class,
                    "classification_rule": rule,
                    "upstream_layer": layer,
                    "resource": properties.get("resource"),
                    "properties": properties,
                },
                "geometry": mapping(geometry),
            })
    features.sort(key=lambda feature: feature["properties"]["source_id"])
    return {"type": "FeatureCollection", "features": features}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture_dir = root / "data" / "fixtures" / "bodrum_kos_real"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    raw_layers = {}
    raw_checksums = {}
    for layer in LAYERS:
        document = fetch_layer(layer)
        raw_layers[layer] = document
        raw_path = fixture_dir / f"official-{layer.split(':', 1)[1]}-raw.json"
        raw_path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        raw_checksums[raw_path.name] = sha256(raw_path)

    output = fixture_dir / "official-managed-areas.geojson"
    normalized = normalize(raw_layers)
    output.write_text(json.dumps(normalized, separators=(",", ":")), encoding="utf-8")

    # These official pilot amendments are relevant but lack machine-readable
    # boundaries. They remain non-blocking conditional records until an
    # authoritative geometry is available; no guessed polygon is created.
    notices = {
        "source": "Hellenic Navy Hydrographic Service, Pilot D amendments",
        "sourceUrl": "https://hnhs.gr/wp-content/uploads/2025/08/plohgos-d-1.pdf",
        "fetchedAt": FETCHED_AT,
        "notices": [
            {
                "sourceId": "hnhs-pilot-d-206-2014",
                "area": "Kos east coast, Cape Louros",
                "restrictionClass": "CAUTION_CONDITIONAL",
                "validMonths": [5, 6, 7, 8, 9, 10],
                "rule": "official text prohibits vessel transit close to Cape Louros during the stated season",
                "geometryStatus": "MISSING_AUTHORITATIVE_BOUNDARY",
            },
            {
                "sourceId": "hnhs-pilot-d-157-2019",
                "area": "Nisyros north coast abandoned mining structures",
                "restrictionClass": "CAUTION_CONDITIONAL",
                "bufferM": 100,
                "rule": "official text prohibits sailing, anchoring and fishing within 100 m of structures",
                "geometryStatus": "MISSING_AUTHORITATIVE_STRUCTURE_GEOMETRY",
            },
        ],
    }
    notice_path = fixture_dir / "official-unresolved-notices.json"
    notice_path.write_text(json.dumps(notices, indent=2), encoding="utf-8")

    counts = {"HARD_NO_GO": 0, "CAUTION_CONDITIONAL": 0, "INFORMATIONAL": 0}
    for feature in normalized["features"]:
        counts[feature["properties"]["restriction_class"]] += 1
    manifest = {
        "layerType": "official_managed_and_restricted_areas",
        "regionId": "bodrum-kos-real",
        "fetchedAt": FETCHED_AT,
        "regionBboxWgs84": list(BBOX),
        "wfsEndpoint": WFS_URL,
        "upstreamLayers": list(LAYERS),
        "normalizedFile": str(output.relative_to(root)).replace("\\", "/"),
        "normalizedSha256": sha256(output),
        "unresolvedNoticesFile": str(notice_path.relative_to(root)).replace("\\", "/"),
        "unresolvedNoticesSha256": sha256(notice_path),
        "rawChecksums": raw_checksums,
        "counts": {**counts, "unresolvedNotices": len(notices["notices"])},
        "coverageFinding": "No EMODnet military polygons intersected the Bodrum/Kos bbox on the fetch date.",
        "classificationPolicy": {
            "explicitActiveMilitaryArea": "HARD_NO_GO",
            "unknownStatusMilitaryArea": "CAUTION_CONDITIONAL",
            "Natura2000": "INFORMATIONAL",
            "officialNoticeWithoutBoundary": "CAUTION_CONDITIONAL and never converted to guessed geometry",
        },
    }
    manifest_path = root / "data" / "manifests" / "bodrum_kos_official_areas.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"wrote {len(normalized['features'])} features; sha256={sha256(output)}")


if __name__ == "__main__":
    main()
