"""Fetch and normalize OSM marine obstacles/restrictions for Bodrum/Kos."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon, mapping

from marine_data.features import classify_osm_tags

BBOX = "36.60,26.85,37.25,27.85"
SOURCE_VERSION = "osm-snapshot-2026-09-13"
MIRRORS = (
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
)
OBSTACLE_QUERY = f'''[out:json][timeout:60];(
way["man_made"="breakwater"]({BBOX});
way["man_made"="groyne"]({BBOX});
);out tags geom;'''
SEAMARK_QUERY = f'''[out:json][timeout:60];(
node["seamark:type"~"^(rock|wreck|obstruction)$"]({BBOX});
way["seamark:type"~"^(rock|wreck|obstruction|restricted_area)$"]({BBOX});
);out tags geom;'''


def fetch(query: str) -> dict:
    for endpoint in MIRRORS:
        result = subprocess.run(
            [
                "curl", "--max-time", "90", "-sS", "-A",
                "AxoparRangePlanner/1.0 (research)", "--data-binary", query, endpoint,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.lstrip().startswith("{"):
            return json.loads(result.stdout)
    raise RuntimeError("all Overpass mirrors failed")


def geometry_for(element: dict):
    if element["type"] == "node":
        return Point(element["lon"], element["lat"])
    coordinates = [(point["lon"], point["lat"]) for point in element.get("geometry", [])]
    if len(coordinates) < 2:
        raise ValueError(f"OSM {element['type']}/{element['id']} has no usable geometry")
    if coordinates[0] == coordinates[-1] and len(coordinates) >= 4:
        return Polygon(coordinates)
    return LineString(coordinates)


def normalize(documents: list[dict]) -> dict:
    features = []
    seen = set()
    for document in documents:
        for element in document["elements"]:
            source_id = f"osm:{element['type']}/{element['id']}"
            if source_id in seen:
                continue
            seen.add(source_id)
            tags = element.get("tags", {})
            feature_type, restriction_class, rule = classify_osm_tags(tags)
            features.append({
                "type": "Feature",
                "properties": {
                    "source_id": source_id,
                    "source_name": "OpenStreetMap / OpenSeaMap seamark tags",
                    "source_version": SOURCE_VERSION,
                    "feature_type": feature_type,
                    "restriction_class": restriction_class.value,
                    "classification_rule": rule,
                    "tags": tags,
                },
                "geometry": mapping(geometry_for(element)),
            })
    features.sort(key=lambda feature: feature["properties"]["source_id"])
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("data/fixtures/bodrum_kos_real"))
    parser.add_argument("--from-existing", action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    obstacle_path = args.out_dir / "marine-features-osm.json"
    seamark_path = args.out_dir / "seamarks-osm.json"
    if args.from_existing:
        documents = [json.loads(obstacle_path.read_text()), json.loads(seamark_path.read_text())]
    else:
        documents = [fetch(OBSTACLE_QUERY), fetch(SEAMARK_QUERY)]
        obstacle_path.write_text(json.dumps(documents[0]))
        seamark_path.write_text(json.dumps(documents[1]))
    output = args.out_dir / "marine-features.geojson"
    output.write_text(json.dumps(normalize(documents), separators=(",", ":")))
    print(f"wrote {output}; sha256={hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
