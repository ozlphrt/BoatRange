"""Build compact, GitHub-Pages-compatible marine routing assets.

The browser uses an implicit regular graph. Every stored edge retains the
minimum exact vector-obstacle clearance and sampled minimum depth, so runtime
settings can fail closed without requiring a Python server.
"""

from __future__ import annotations

import base64
import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
import shapely
from shapely.geometry import LineString
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
for path in (
    ROOT / "apps" / "api",
    ROOT / "packages" / "fuel-model",
    ROOT / "services" / "routing",
    ROOT / "services" / "isochrone",
    ROOT / "services" / "marine-data",
    ROOT / "services" / "diagnostics",
):
    sys.path.insert(0, str(path))

from app import main  # noqa: E402
from app.regions import AEGEAN_REAL  # noqa: E402
from fuel_model.catalog import list_vessels  # noqa: E402

OUTPUT = ROOT / "apps" / "web" / "public" / "browser-data"
RESOLUTION_M = 1500.0
UINT16_MAX = np.iinfo(np.uint16).max


def encode_u16(values: np.ndarray) -> str:
    clipped = np.clip(np.rint(values), 0, UINT16_MAX).astype("<u2", copy=False)
    return base64.b64encode(clipped.tobytes()).decode("ascii")


def nearest_distances(tree: STRtree, geometries: np.ndarray, batch_size: int = 20_000) -> np.ndarray:
    result = np.empty(len(geometries), dtype=np.float64)
    for start in range(0, len(geometries), batch_size):
        stop = min(start + batch_size, len(geometries))
        _indices, distances = tree.query_nearest(
            geometries[start:stop], return_distance=True, all_matches=False
        )
        result[start:stop] = distances
        print(f"clearance {stop:,}/{len(geometries):,}", flush=True)
    return result


def raster_depth_sampler(path: Path):
    dataset = rasterio.open(path)
    values = dataset.read(1, masked=True)
    array = np.asarray(values.filled(np.nan), dtype=np.float32)
    transform = dataset.transform

    def sample(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        lon = x / AEGEAN_REAL.projection.m_per_deg_lon + AEGEAN_REAL.projection.lon_ref
        lat = y / AEGEAN_REAL.projection.m_per_deg_lat + AEGEAN_REAL.projection.lat_ref
        col = np.floor((lon - transform.c) / transform.a).astype(np.int64)
        row = np.floor((lat - transform.f) / transform.e).astype(np.int64)
        valid = (row >= 0) & (row < array.shape[0]) & (col >= 0) & (col < array.shape[1])
        depth = np.zeros(len(x), dtype=np.float32)
        sampled = np.full(len(x), np.nan, dtype=np.float32)
        sampled[valid] = array[row[valid], col[valid]]
        known_water = np.isfinite(sampled) & (sampled < 0)
        depth[known_water] = -sampled[known_water]
        return depth

    return dataset, sample


def edge_metrics(
    tree: STRtree,
    sample_depth,
    x1: np.ndarray,
    y1: np.ndarray,
    x2: np.ndarray,
    y2: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    lines = shapely.linestrings(np.stack([
        np.stack([x1, y1], axis=1),
        np.stack([x2, y2], axis=1),
    ], axis=1))
    clearance = nearest_distances(tree, lines)
    minimum_depth = np.full(len(lines), np.inf, dtype=np.float32)
    for fraction in np.linspace(0.0, 1.0, 9):
        depth = sample_depth(x1 + (x2 - x1) * fraction, y1 + (y2 - y1) * fraction)
        minimum_depth = np.minimum(minimum_depth, depth)
    minimum_depth[~np.isfinite(minimum_depth)] = 0
    return clearance, minimum_depth * 100.0


def build_grid() -> dict:
    region = AEGEAN_REAL
    west, south, east, north = region.bbox_wgs84
    corners = [region.projection.to_projected(lon, lat) for lon, lat in (
        (west, south), (east, south), (west, north), (east, north)
    )]
    min_x = min(point[0] for point in corners)
    max_x = max(point[0] for point in corners)
    min_y = min(point[1] for point in corners)
    max_y = max(point[1] for point in corners)
    width = math.floor((max_x - min_x) / RESOLUTION_M) + 1
    height = math.floor((max_y - min_y) / RESOLUTION_M) + 1
    xs = min_x + np.arange(width, dtype=np.float64) * RESOLUTION_M
    ys = min_y + np.arange(height, dtype=np.float64) * RESOLUTION_M
    grid_x, grid_y = np.meshgrid(xs, ys)
    flat_x, flat_y = grid_x.ravel(), grid_y.ravel()

    blockers = list(region.blocking_geometries)
    tree = STRtree(blockers)
    points = shapely.points(flat_x, flat_y)
    point_clearance = nearest_distances(tree, points)

    dataset, sample_depth = raster_depth_sampler(region.depth_layer.verified_path)
    point_depth_cm = sample_depth(flat_x, flat_y) * 100.0

    index = np.arange(width * height, dtype=np.int64).reshape(height, width)
    definitions = {
        "east": (index[:, :-1].ravel(), index[:, 1:].ravel()),
        "north": (index[:-1, :].ravel(), index[1:, :].ravel()),
        "northEast": (index[:-1, :-1].ravel(), index[1:, 1:].ravel()),
        "northWest": (index[:-1, 1:].ravel(), index[1:, :-1].ravel()),
    }
    edges = {}
    for name, (start, end) in definitions.items():
        print(f"building {name} edges", flush=True)
        clearance, depth_cm = edge_metrics(
            tree, sample_depth,
            flat_x[start], flat_y[start], flat_x[end], flat_y[end]
        )
        edges[name] = {
            "clearanceM": encode_u16(clearance),
            "minimumDepthCm": encode_u16(depth_cm),
        }

    dataset.close()
    return {
        "version": 1,
        "regionId": region.id,
        "regionName": region.name,
        "marineDataVersion": region.marine_data_version,
        "width": width,
        "height": height,
        "resolutionM": RESOLUTION_M,
        "originXM": min_x,
        "originYM": min_y,
        "projection": {
            "latRef": region.projection.lat_ref,
            "lonRef": region.projection.lon_ref,
            "metersPerDegreeLat": region.projection.m_per_deg_lat,
            "metersPerDegreeLon": region.projection.m_per_deg_lon,
        },
        "pointClearanceM": encode_u16(point_clearance),
        "pointDepthCm": encode_u16(point_depth_cm),
        "edges": edges,
    }


def build_vessels() -> list[dict]:
    vessels = []
    for entry in list_vessels():
        body = main._vessel_summary_dict(entry)
        body.update({
            "curve": {
                "rpm": entry.fuel_curve._rpm,
                "speedKn": entry.fuel_curve._speed,
                "fuelLph": entry.fuel_curve._lph,
            },
        })
        vessels.append(body)
    return vessels


def main_build() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "grid.json").write_text(json.dumps(build_grid(), separators=(",", ":")))
    (OUTPUT / "vessels.json").write_text(json.dumps(build_vessels(), separators=(",", ":")))
    (OUTPUT / "region.json").write_text(
        json.dumps(main.region_geometry(AEGEAN_REAL.id), separators=(",", ":"))
    )
    (OUTPUT / "regions.json").write_text(json.dumps(main.list_regions(), separators=(",", ":")))
    print(f"Browser assets written to {OUTPUT}")


if __name__ == "__main__":
    main_build()
