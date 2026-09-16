"""Fetch the EMODnet DTM subset covering the Aegean routing region."""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge


BBOX = (24.75, 35.25, 30.25, 39.25)
# About 206 m north/south: finer than the current 600 m routing grid while
# remaining below EMODnet WCS's 97.66 MB per-request read limit for this AOI.
RESOLUTION_DEGREES = 1.0 / 540.0
WCS_ENDPOINT = "https://ows.emodnet-bathymetry.eu/wcs"
OUTPUT = Path("data/fixtures/aegean_real/depth-emodnet-2024.tif")


def build_url(bbox: tuple[float, float, float, float]) -> str:
    params = {
        "service": "wcs",
        "version": "1.0.0",
        "request": "getcoverage",
        "coverage": "emodnet:mean",
        "crs": "EPSG:4326",
        "BBOX": ",".join(map(str, bbox)),
        "format": "image/tiff",
        "interpolation": "nearest",
        "resx": f"{RESOLUTION_DEGREES:.10f}",
        "resy": f"{RESOLUTION_DEGREES:.10f}",
    }
    return f"{WCS_ENDPOINT}?{urllib.parse.urlencode(params)}"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    west, south, east, north = BBOX
    middle = (west + east) / 2.0
    tile_paths = [OUTPUT.with_name("depth-west.tif"), OUTPUT.with_name("depth-east.tif")]
    tile_bounds = [(west, south, middle, north), (middle, south, east, north)]
    for bounds, path in zip(tile_bounds, tile_paths):
        urllib.request.urlretrieve(build_url(bounds), path)

    datasets = [rasterio.open(path) for path in tile_paths]
    try:
        mosaic, transform = merge(datasets)
        profile = datasets[0].profile.copy()
        profile.update(
            width=mosaic.shape[2],
            height=mosaic.shape[1],
            transform=transform,
            compress="deflate",
            predictor=3,
        )
        with rasterio.open(OUTPUT, "w", **profile) as target:
            target.write(mosaic)
    finally:
        for dataset in datasets:
            dataset.close()
        for path in tile_paths:
            path.unlink(missing_ok=True)

    with rasterio.open(OUTPUT) as dataset:
        values = dataset.read(1, masked=True).filled(np.nan)
        if dataset.crs is None or dataset.crs.to_epsg() != 4326:
            raise ValueError(f"unexpected CRS: {dataset.crs}")
        if not np.isfinite(values).any() or float(np.nanmin(values)) >= 0:
            raise ValueError("download does not contain valid seabed elevations")
        print(f"coverage={dataset.bounds} size={dataset.width}x{dataset.height}")
    print(f"sha256={hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
