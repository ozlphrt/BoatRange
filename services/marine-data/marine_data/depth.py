"""Raster bathymetry classification with conservative unknown-depth handling."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property, lru_cache
import hashlib
from pathlib import Path
from typing import Callable

import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.ops import transform, unary_union


def _as_multipolygon(geometry) -> MultiPolygon:
    if geometry.is_empty:
        return MultiPolygon()
    if isinstance(geometry, Polygon):
        return MultiPolygon([geometry])
    if isinstance(geometry, MultiPolygon):
        return geometry
    polygons = [part for part in geometry.geoms if isinstance(part, Polygon)]
    return MultiPolygon(polygons) if polygons else MultiPolygon()


@dataclass(frozen=True)
class RasterDepthLayer:
    """A versioned depth raster whose elevations use metres relative to LAT.

    EMODnet stores seabed elevations as negative values, so a location is
    deep enough when ``elevation <= -minimum_safe_depth``. Pixels without a
    finite value are unknown and remain blocked.
    """

    path: Path
    source_name: str
    source_version: str
    fetched_at: str
    checksum_sha256: str
    vertical_datum: str = "Lowest Astronomical Tide (LAT)"

    @cached_property
    def verified_path(self) -> Path:
        if not self.path.exists():
            raise FileNotFoundError(f"required depth raster is missing: {self.path}")
        actual = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if actual.lower() != self.checksum_sha256.lower():
            raise ValueError(
                f"depth raster checksum mismatch for {self.path}: "
                f"expected {self.checksum_sha256}, got {actual}"
            )
        return self.path

    @lru_cache(maxsize=16)
    def safe_water_wgs84(self, minimum_safe_depth_m: float) -> MultiPolygon:
        if minimum_safe_depth_m <= 0:
            raise ValueError("minimum_safe_depth_m must be positive")
        with rasterio.open(self.verified_path) as dataset:
            if dataset.crs is None or dataset.crs.to_epsg() != 4326:
                raise ValueError("depth raster must use EPSG:4326")
            elevation = dataset.read(1, masked=True)
            values = np.asarray(elevation.filled(np.nan), dtype=float)
            known = np.isfinite(values)
            if np.ma.is_masked(elevation):
                known &= ~np.ma.getmaskarray(elevation)
            safe = known & (values <= -float(minimum_safe_depth_m))
            polygons = [
                shape(geometry)
                for geometry, value in shapes(
                    safe.astype("uint8"), mask=safe, transform=dataset.transform
                )
                if value == 1
            ]

        if not polygons:
            return MultiPolygon()
        merged = unary_union(polygons)
        if not merged.is_valid:
            merged = merged.buffer(0)
        return _as_multipolygon(merged)

    def safe_water_projected(
        self,
        minimum_safe_depth_m: float,
        project: Callable[[float, float], tuple[float, float]],
    ) -> MultiPolygon:
        return _as_multipolygon(
            transform(project, self.safe_water_wgs84(minimum_safe_depth_m))
        )

    def elevation_at(self, lon: float, lat: float) -> float | None:
        """Sample one WGS84 point; return ``None`` for outside/nodata depth."""
        with rasterio.open(self.verified_path) as dataset:
            bounds = dataset.bounds
            if not (bounds.left <= lon <= bounds.right and bounds.bottom <= lat <= bounds.top):
                return None
            value = next(dataset.sample([(lon, lat)], masked=True))[0]
            if np.ma.is_masked(value) or not np.isfinite(float(value)):
                return None
            return float(value)
