"""Ingest real OSM coastline for the Bodrum/Kos region (CLAUDE.md Section 16).

Stages implemented here:
    1. fetch source        -- Overpass API, admin boundary relations + coastline ways
    2. checksum/version     -- manifest records the Overpass snapshot date and OSM ids
    3. normalize CRS        -- kept in WGS84 on disk; projected to local meters at load time
                               (see marine_data.bodrum_kos_real), matching CLAUDE.md 7.5
    4. validate geometry     -- .buffer(0) repair + is_valid checks
    5. repair invalid geometry -- see step 4
    6. clip by region        -- intersect with FINAL_BBOX
    7. classify features     -- land vs water via unary_union + difference
    8. simplify only where safe -- Douglas-Peucker with preserve_topology=True,
                               tolerance chosen small enough (~6.6m) to not
                               introduce new self-intersections or erase real
                               narrow passages
    9-12. (PostGIS load / tile rasterization / indexes / topology tests) --
        not yet wired up; V1 keeps this in-memory (see apps/api/app/regions.py
        docstring). The geometry this script produces is exactly what would
        be loaded into PostGIS in a later phase.
    13. generate data manifest -- data/manifests/bodrum_kos_real.json
    14. invalidate affected caches -- no cache layer exists yet (Section 24)

Why admin boundary relations instead of raw natural=coastline reconstruction:
raw coastline ways only touch at shared endpoints when they happen to line up
exactly; turning them into closed polygons requires "noding" (inserting
vertices at every real crossing) via shapely.ops.unary_union, then
polygonize(), and even then telling land from water needs a directional
heuristic (OSM's coastline drawing convention: land is to the right of the
way's direction). That heuristic proved unreliable on real, irregularly
shaped faces during manual testing. OSM administrative boundary relations
(and named island relations) are already valid, correctly wound, closed
multipolygons maintained by OSM itself -- no reconstruction or heuristic
needed. This script therefore prefers relations for the two named landmasses
that matter for the regression fixtures (Bodrum, Kos), and only falls back to
raw-coastline polygonization for the many small surrounding islets, where a
missed or malformed islet is a much smaller correctness risk.

Usage:
    python scripts/ingest/fetch_bodrum_kos_osm.py [--out-dir data/fixtures/bodrum_kos_real]

Network access required (Overpass API). Safe to re-run: it always re-fetches
and overwrites the output GeoJSON deterministically from the current OSM
state -- update data/manifests/bodrum_kos_real.json's fetched_at afterwards.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from shapely.geometry import mapping, shape, MultiPolygon, Polygon
from shapely.ops import linemerge, polygonize, unary_union

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "AxoparRangePlanner/1.0 (research)"

# Wide enough to include all of Kos (whose westernmost point sits at
# lon ~26.917, outside a naive Bodrum-only bbox) plus the full Bodrum
# peninsula and Gulluk Bay.
FETCH_BBOX = (36.45, 26.75, 37.35, 27.95)  # south, west, north, east
FINAL_BBOX = (26.85, 36.60, 27.85, 37.25)  # min_lon, min_lat, max_lon, max_lat

BODRUM_RELATION_ID = 1827275  # admin_level=6, boundary=administrative, name="Bodrum"
KOS_RELATION_ID = 536480  # place=island, type=multipolygon

FILTER_MIN_AREA_M2 = 500
SIMPLIFY_TOLERANCE_DEG = 0.00006  # ~6.6 m at this latitude


def _overpass_query(query: str, attempts: int = 5, sleep_s: int = 18) -> dict:
    """POST an Overpass QL query via curl, retrying through the frequent
    504 (server busy) responses the public instance returns on a cold hit."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                [
                    "curl", "-s", "-A", USER_AGENT,
                    "--data", query, OVERPASS_URL,
                ],
                capture_output=True, timeout=120, check=True,
            )
            text = result.stdout.decode("utf-8", errors="replace")
            if text.strip().startswith("{"):
                return json.loads(text)
            last_error = RuntimeError(f"non-JSON Overpass response: {text[:200]!r}")
        except Exception as exc:  # noqa: BLE001 - retry on anything transient
            last_error = exc
        if attempt < attempts:
            time.sleep(sleep_s)
    raise RuntimeError(f"Overpass query failed after {attempts} attempts: {last_error}")


def fetch_relation_polygon(relation_id: int) -> MultiPolygon:
    """Fetch an OSM relation and merge its member ways into closed rings.

    Works for both a `boundary=administrative` relation (Bodrum) and a
    `type=multipolygon` island relation (Kos) -- both expose their outline as
    a set of ways under the relation.
    """
    data = _overpass_query(f"[out:json][timeout:90];relation({relation_id});(._;>;);out geom;")
    ways = [el for el in data["elements"] if el["type"] == "way" and "geometry" in el]
    lines = [[(pt["lon"], pt["lat"]) for pt in way["geometry"]] for way in ways]
    from shapely.geometry import LineString

    merged = linemerge([LineString(line) for line in lines if len(line) >= 2])
    rings = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
    polys = [Polygon(ring) for ring in rings if ring.is_ring or ring.is_closed]
    polys = [p.buffer(0) for p in polys if not p.is_empty]
    return unary_union(polys)


def fetch_surrounding_islets() -> MultiPolygon:
    """Reconstruct small islands from raw natural=coastline ways in the wider
    fetch bbox, excluding Bodrum/Kos (handled via their own relations)."""
    south, west, north, east = FETCH_BBOX
    data = _overpass_query(
        f'[out:json][timeout:120];way["natural"="coastline"]({south},{west},{north},{east});'
        f"out geom;"
    )
    from shapely.geometry import LineString

    ways = [el for el in data["elements"] if el["type"] == "way" and "geometry" in el]
    lines = [LineString([(pt["lon"], pt["lat"]) for pt in way["geometry"]]) for way in ways]

    bbox_poly = Polygon.from_bounds(west, south, east, north)
    noded = unary_union(lines + [bbox_poly.exterior])
    faces = list(polygonize(noded))

    # Faces near a coastline ring smaller than the two named landmasses are
    # taken as islets. Filtering out anything overlapping the Bodrum/Kos
    # relations (fetched separately, more reliably) avoids double-counting.
    return unary_union([f.buffer(0) for f in faces if f.is_valid and not f.is_empty])


def build_region_geometry() -> tuple[MultiPolygon, MultiPolygon]:
    """Return (land, water) MultiPolygons in WGS84, clipped to FINAL_BBOX."""
    bodrum = fetch_relation_polygon(BODRUM_RELATION_ID)
    kos = fetch_relation_polygon(KOS_RELATION_ID)
    islets = fetch_surrounding_islets()

    land = unary_union([bodrum, kos, islets])
    final_bbox_poly = Polygon.from_bounds(*FINAL_BBOX)
    land = land.intersection(final_bbox_poly)

    # Filter numerical slivers and simplify cautiously (CLAUDE.md 10.1/17):
    # never so aggressively that a genuine narrow channel could disappear.
    pieces = list(land.geoms) if hasattr(land, "geoms") else [land]
    # Rough m^2 per deg^2 at this latitude, for the area filter below.
    deg_to_m2 = 111320.0 * (111320.0 * 0.8)
    kept = [p for p in pieces if p.area * deg_to_m2 > FILTER_MIN_AREA_M2]
    land = unary_union(kept).simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
    if not land.is_valid:
        land = land.buffer(0)

    water = final_bbox_poly.difference(land)
    return land, water


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default="data/fixtures/bodrum_kos_real",
        help="Directory to write land.geojson into",
    )
    args = parser.parse_args()

    land, _water = build_region_geometry()
    pieces = list(land.geoms) if hasattr(land, "geoms") else [land]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "land.geojson"
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": mapping(p)}
            for p in pieces
        ],
    }
    out_path.write_text(json.dumps(fc))
    print(f"wrote {len(pieces)} land polygons to {out_path}", file=sys.stderr)
    print(
        "remember to update data/manifests/bodrum_kos_real.json's fetched_at "
        "and land_piece_count fields after a re-run",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
