"""Build the Aegean routing coastline from the existing OSM source layers.

The wider East Mediterranean land-polygons dataset supplies regional context;
the higher-detail Bodrum/Kos dataset is unioned on top so its small islands are
preserved. Output stays WGS84 GeoJSON and is clipped to the Aegean compute box.
"""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import Polygon, mapping, shape
from shapely.ops import unary_union


BBOX = (24.75, 35.25, 30.25, 39.25)
ROOT = Path(__file__).resolve().parents[2]
EAST_MED = ROOT / "data" / "fixtures" / "east_med_real" / "land.geojson"
BODRUM = ROOT / "data" / "fixtures" / "bodrum_kos_real" / "land.geojson"
OUTPUT = ROOT / "data" / "fixtures" / "aegean_real" / "land.geojson"


def load_features(path: Path):
    document = json.loads(path.read_text(encoding="utf-8"))
    return [shape(feature["geometry"]) for feature in document["features"]]


def main() -> None:
    west, south, east, north = BBOX
    clip = Polygon.from_bounds(west, south, east, north)
    candidates = [
        geometry.intersection(clip)
        for geometry in [*load_features(EAST_MED), *load_features(BODRUM)]
        if geometry.intersects(clip)
    ]
    merged = unary_union(candidates)
    if not merged.is_valid:
        merged = merged.buffer(0)
    polygons = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    features = [
        {"type": "Feature", "properties": {}, "geometry": mapping(polygon)}
        for polygon in polygons
        if not polygon.is_empty
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT} with {len(features)} land polygons")


if __name__ == "__main__":
    main()
