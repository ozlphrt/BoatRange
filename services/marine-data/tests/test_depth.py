"""Depth classification and conservative missing-data regression tests."""

from pathlib import Path
import hashlib

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import Point

from marine_data.depth import RasterDepthLayer


def _layer(tmp_path: Path) -> RasterDepthLayer:
    path = tmp_path / "depth.tif"
    values = np.array([[-5.0, -0.5, -9999.0]], dtype="float32")
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=3,
        height=1,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(0, 1, 1, 1),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(values, 1)
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    return RasterDepthLayer(path, "fixture", "v1", "2026-09-13", checksum)


def test_conservative_mask_accepts_only_known_safe_depth(tmp_path):
    safe = _layer(tmp_path).safe_water_wgs84(1.35)
    assert safe.contains(Point(0.5, 0.5))
    assert not safe.intersects(Point(1.5, 0.5))  # known shallow
    assert not safe.intersects(Point(2.5, 0.5))  # nodata / unknown


def test_higher_minimum_depth_can_only_reduce_safe_water(tmp_path):
    layer = _layer(tmp_path)
    shallow_threshold = layer.safe_water_wgs84(0.25)
    deep_threshold = layer.safe_water_wgs84(1.35)
    assert deep_threshold.within(shallow_threshold)
    assert deep_threshold.area < shallow_threshold.area


def test_missing_required_raster_fails_closed(tmp_path):
    layer = RasterDepthLayer(
        tmp_path / "missing.tif", "fixture", "v1", "2026-09-13", "missing"
    )
    try:
        layer.safe_water_wgs84(1.35)
    except FileNotFoundError as exc:
        assert "required depth raster is missing" in str(exc)
    else:
        raise AssertionError("missing depth raster was treated as safe")


def test_checksum_mismatch_fails_closed(tmp_path):
    layer = _layer(tmp_path)
    corrupted = RasterDepthLayer(
        layer.path, "fixture", "v1", "2026-09-13", "0" * 64
    )
    try:
        corrupted.safe_water_wgs84(1.35)
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("corrupted depth raster was accepted")
