"""Fetch and normalize Bodrum/Kos marinas, harbours, and marine fuel docks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import Request, urlopen

from shapely.geometry import Point, mapping
from shapely.wkt import loads as load_wkt

BBOX = (36.60, 26.85, 37.25, 27.85)  # south, west, north, east
SOURCE_VERSION = "qlever-osm-planet-query-2026-09-16"
QLEVER_ENDPOINT = "https://qlever.dev/api/osm-planet"
TAG_QUERIES = (
    ("leisure", "marina"),
    ("seamark:type", "harbour"),
    ("waterway", "fuel"),
    ("seamark:small_craft_facility:category", "fuel_station"),
)


def _tag_iri(key: str) -> str:
    return f"<https://www.openstreetmap.org/wiki/Key:{key}>"


def _query(key: str, value: str) -> str:
    south, west, north, east = BBOX
    return f"""
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX geof: <http://www.opengis.net/def/function/geosparql/>
SELECT DISTINCT ?element ?geometry ?name ?seamarkName ?leisure ?seamarkType
                ?harbourCategory ?waterway ?facilityCategory WHERE {{
  ?element {_tag_iri(key)} {json.dumps(value)} ;
           geo:hasGeometry/geo:asWKT ?geometry .
  BIND(geof:centroid(?geometry) AS ?center)
  BIND(geof:latitude(?center) AS ?lat)
  BIND(geof:longitude(?center) AS ?lon)
  FILTER(?lat >= {south} && ?lat <= {north} && ?lon >= {west} && ?lon <= {east})
  OPTIONAL {{ ?element {_tag_iri('name')} ?name }}
  OPTIONAL {{ ?element {_tag_iri('seamark:name')} ?seamarkName }}
  OPTIONAL {{ ?element {_tag_iri('leisure')} ?leisure }}
  OPTIONAL {{ ?element {_tag_iri('seamark:type')} ?seamarkType }}
  OPTIONAL {{ ?element {_tag_iri('seamark:harbour:category')} ?harbourCategory }}
  OPTIONAL {{ ?element {_tag_iri('waterway')} ?waterway }}
  OPTIONAL {{ ?element {_tag_iri('seamark:small_craft_facility:category')} ?facilityCategory }}
}}
"""


def _fetch_query(query: str) -> dict:
    request = Request(
        QLEVER_ENDPOINT,
        data=query.encode("utf-8"),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/sparql-query",
            "User-Agent": "AxoparRangePlanner/1.0 (research)",
        },
        method="POST",
    )
    with urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch() -> dict:
    elements: dict[tuple[str, int], dict] = {}
    field_to_tag = {
        "name": "name", "seamarkName": "seamark:name", "leisure": "leisure",
        "seamarkType": "seamark:type", "harbourCategory": "seamark:harbour:category",
        "waterway": "waterway",
        "facilityCategory": "seamark:small_craft_facility:category",
    }
    for key, value in TAG_QUERIES:
        document = _fetch_query(_query(key, value))
        for row in document.get("results", {}).get("bindings", []):
            uri = row["element"]["value"]
            element_type, element_id = uri.rstrip("/").rsplit("/", 2)[-2:]
            center = load_wkt(row["geometry"]["value"]).centroid
            element_key = (element_type, int(element_id))
            element = elements.setdefault(element_key, {
                "type": element_type, "id": int(element_id),
                "center": {"lon": center.x, "lat": center.y}, "tags": {},
            })
            element["tags"][key] = value
            for field, tag in field_to_tag.items():
                if field in row:
                    element["tags"][tag] = row[field]["value"]
    return {
        "version": 1, "generator": "QLever OSM planet SPARQL tag queries",
        "endpoint": QLEVER_ENDPOINT, "sourceVersion": SOURCE_VERSION,
        "elements": list(elements.values()),
    }


def _coordinate(element: dict) -> tuple[float, float] | None:
    if element["type"] == "node" and "lon" in element and "lat" in element:
        return element["lon"], element["lat"]
    center = element.get("center")
    return (center.get("lon"), center.get("lat")) if center else None


def normalize(document: dict) -> dict:
    features = []
    for element in document.get("elements", []):
        coordinate = _coordinate(element)
        if not coordinate or None in coordinate:
            continue
        tags = element.get("tags", {})
        is_fuel = tags.get("waterway") == "fuel" or tags.get(
            "seamark:small_craft_facility:category"
        ) == "fuel_station"
        is_marina = tags.get("leisure") == "marina" or tags.get(
            "seamark:harbour:category"
        ) in {"marina", "marina_no_facilities"}
        is_harbour = tags.get("seamark:type") == "harbour"
        categories = [name for name, present in (
            ("fuel_dock", is_fuel), ("marina", is_marina), ("harbour", is_harbour)
        ) if present]
        if not categories:
            continue
        poi_type = "fuel_dock" if is_fuel else "marina" if is_marina else "harbour"
        name = tags.get("name") or tags.get("seamark:name") or "Unnamed marine facility"
        features.append({
            "type": "Feature",
            "properties": {
                "source_id": f"osm:{element['type']}/{element['id']}",
                "source_name": "OpenStreetMap / OpenSeaMap via QLever",
                "source_version": SOURCE_VERSION,
                "poi_type": poi_type, "categories": categories, "name": name,
                "confidence": "MEDIUM" if is_fuel or is_marina else "LOW",
                "classification_rule": (
                    "marine fuel tag" if is_fuel else
                    "leisure=marina or marina harbour category" if is_marina else
                    "seamark:type=harbour"
                ),
                "tags": tags,
            },
            "geometry": mapping(Point(*coordinate)),
        })
    features.sort(key=lambda item: item["properties"]["source_id"])
    return {"type": "FeatureCollection", "features": features}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("data/fixtures/bodrum_kos_real"))
    parser.add_argument("--from-existing", action="store_true")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.out_dir / "marine-pois-osm-raw.json"
    document = json.loads(raw_path.read_text(encoding="utf-8")) if args.from_existing else fetch()
    if not args.from_existing:
        raw_path.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    output = args.out_dir / "marine-pois.geojson"
    normalized = normalize(document)
    output.write_text(json.dumps(normalized, separators=(",", ":")), encoding="utf-8")
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    counts: dict[str, int] = {}
    for feature in normalized["features"]:
        key = feature["properties"]["poi_type"]
        counts[key] = counts.get(key, 0) + 1
    print(f"wrote {len(normalized['features'])} POIs {counts}; sha256={checksum}")


if __name__ == "__main__":
    main()
