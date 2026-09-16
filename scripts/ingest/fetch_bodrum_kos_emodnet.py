"""Fetch and validate the EMODnet DTM subset used by Bodrum/Kos.

The WCS URL follows EMODnet's official Web Coverage Service documentation.
The output is source data: classification happens in ``marine_data.depth``.
"""

from __future__ import annotations

import argparse
import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import rasterio

BBOX = (26.85, 36.60, 27.85, 37.25)
RESOLUTION_DEGREES = 1.0 / 960.0  # 1/16 arc minute, approximately 115 m
WCS_ENDPOINT = "https://ows.emodnet-bathymetry.eu/wcs"


def build_url() -> str:
    params = {
        "service": "wcs",
        "version": "1.0.0",
        "request": "getcoverage",
        "coverage": "emodnet:mean",
        "crs": "EPSG:4326",
        "BBOX": ",".join(map(str, BBOX)),
        "format": "image/tiff",
        "interpolation": "nearest",
        "resx": f"{RESOLUTION_DEGREES:.10f}",
        "resy": f"{RESOLUTION_DEGREES:.10f}",
    }
    return f"{WCS_ENDPOINT}?{urllib.parse.urlencode(params)}"


def validate(path: Path) -> None:
    with rasterio.open(path) as dataset:
        if dataset.crs is None or dataset.crs.to_epsg() != 4326:
            raise ValueError(f"unexpected CRS: {dataset.crs}")
        values = dataset.read(1, masked=True).filled(np.nan)
        if not np.isfinite(values).any():
            raise ValueError("download contains no finite elevation values")
        if float(np.nanmin(values)) >= 0:
            raise ValueError("download contains no below-sea-level elevations")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/fixtures/bodrum_kos_real/depth-emodnet-2024.tif"),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(build_url(), args.output)
    validate(args.output)
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(f"wrote {args.output} sha256={digest}")


if __name__ == "__main__":
    main()

