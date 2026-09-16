"""Ingest real, full-detail OSM coastline for Italy/Greece/Cyprus/Turkey.

Replaces the earlier Natural Earth-based approximation with OSM's own
pre-built "land polygons" dataset (osmdata.openstreetmap.de) — the same
underlying data quality as bodrum_kos_real, clipped to a much bigger bbox.
See data/manifests/east_med_real.json for full provenance/limitations.

Why the pre-built land-polygons file instead of raw Overpass natural=
coastline queries: this AOI's coastline includes essentially all of the
Greek archipelago (thousands of islands) — a raw Overpass query over an area
this size risks enormous, unreliable responses and repeated timeouts.
osmdata.openstreetmap.de publishes the *entire world's* land polygons,
already validated and assembled from OSM's coastline data, as a single
~900MB download; fetching that once and clipping locally is far more
reliable than querying Overpass for a region this size.

Pipeline (CLAUDE.md Section 16):
    1. fetch source        -- download land-polygons-split-4326.zip
    2. checksum/version     -- manifest records byte size + fetch date
    3. normalize CRS        -- already WGS84 (4326); local-meter projection
                               applied at load time (marine_data.east_med_real)
    4-5. validate/repair    -- .buffer(0) after every union operation
    6. clip by region        -- bbox-prefilter then exact intersection with AOI
    7. classify features     -- land vs water via difference from the AOI box
    8. simplify only where safe -- sliver filter (1 ha) + Douglas-Peucker
                               (~30m tolerance, preserve_topology=True)
    9-12. (PostGIS/tiles/indexes/topology tests) -- not yet wired up, V1
        keeps this in-memory (see apps/api/app/regions.py)
    13. generate data manifest -- data/manifests/east_med_real.json
    14. invalidate affected caches -- no cache layer exists yet

Usage:
    pip install pyshp   # into whichever venv runs this
    python scripts/ingest/fetch_east_med_osm.py [--out-dir data/fixtures/east_med_real]

Downloads ~900MB and takes a few minutes end to end (download-dominated;
the actual clip/union/simplify is well under a minute). Safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import shapefile
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

LAND_POLYGONS_URL = "https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip"

# Covers Italy, Greece, Cyprus, and Turkey's coastlines. A rectangular AOI
# this size inevitably sweeps in neighboring countries' coastlines too
# (Albania, Croatia, Slovenia, Bosnia, Montenegro, Malta, Bulgaria, parts of
# the Levant) at the same data quality — incidental, not a scope decision.
AOI_BOUNDS = (6.5, 34.5, 42.0, 46.5)  # (min_lon, min_lat, max_lon, max_lat)
AOI = box(*AOI_BOUNDS)

SLIVER_FILTER_MIN_AREA_M2 = 10_000  # 1 hectare
SIMPLIFY_TOLERANCE_DEG = 0.0003  # ~30 m at this latitude
_DEG2_TO_M2 = 111_320.0 * (111_320.0 * 0.766)  # cos(40 deg), area-filter estimate only


def _bbox_intersects(a: tuple, b: tuple) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0)


def download_and_clip() -> "unary_union":
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / "land-polygons-split-4326.zip"
        print(f"downloading {LAND_POLYGONS_URL} (~900MB)...", file=sys.stderr)
        urllib.request.urlretrieve(LAND_POLYGONS_URL, zip_path)  # noqa: S310 - fixed, trusted URL

        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp_path)
        shp_path = next(tmp_path.rglob("land_polygons.shp"))

        sf = shapefile.Reader(str(shp_path))
        print(f"total world land-polygon records: {len(sf)}", file=sys.stderr)

        # Fast bbox prefilter before building any shapely geometry, since a
        # world dataset has ~870k records and only a tiny fraction touch
        # our AOI.
        candidate_indices = [
            i for i, shp in enumerate(sf.iterShapes())
            if shp.bbox and _bbox_intersects(shp.bbox, AOI_BOUNDS)
        ]
        print(f"AOI-intersecting records: {len(candidate_indices)}", file=sys.stderr)

        pieces = []
        for idx in candidate_indices:
            geom = shape(sf.shape(idx).__geo_interface__)
            if geom.is_empty:
                continue
            clipped = geom.intersection(AOI)
            if not clipped.is_empty:
                pieces.append(clipped)

    land = unary_union(pieces)
    if not land.is_valid:
        land = land.buffer(0)
    return land


def filter_and_simplify(land):
    pieces = list(land.geoms) if hasattr(land, "geoms") else [land]
    kept = [p for p in pieces if p.area * _DEG2_TO_M2 > SLIVER_FILTER_MIN_AREA_M2]
    merged = unary_union(kept).simplify(SIMPLIFY_TOLERANCE_DEG, preserve_topology=True)
    if not merged.is_valid:
        merged = merged.buffer(0)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir", default="data/fixtures/east_med_real",
        help="Directory to write land.geojson into",
    )
    args = parser.parse_args()

    land = filter_and_simplify(download_and_clip())
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
        "remember to update data/manifests/east_med_real.json's fetched_at "
        "and piece-count fields after a re-run",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
