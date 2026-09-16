"""Local planar projection for cost-field computation.

Per CLAUDE.md Section 7.5:

    Display:  WGS84 / Web Mercator as appropriate
    Compute:  local projected CRS suitable for the region
    Do not perform cost-field geometry directly in Web Mercator.

This module provides a lightweight East-North-East style planar projection
centered on a reference coordinate. It is intentionally simple and dependency
free (only the standard library) so the isochrone engine stays decoupled from
any specific chart/CRS provider. It matches the projection used by the
Bodrum/Kos regression fixture so results are directly comparable.
"""

from __future__ import annotations

import math

# Meters per degree of latitude (approx, WGS84).
_METERS_PER_DEG_LAT = 111320.0


class LocalProjection:
    """Planar projection centered at ``(lat_ref, lon_ref)``.

    Coordinates flow:
        WGS84 (lon, lat)  ->  projected meters (x east, y north)
        projected meters  ->  WGS84 (lon, lat)

    Distances are accurate to first order near the reference coordinate, which
    is exactly what a localized compute extent around a single origin needs.
    """

    def __init__(self, lat_ref: float = 37.0, lon_ref: float = 27.5) -> None:
        self.lat_ref = lat_ref
        self.lon_ref = lon_ref
        self.m_per_deg_lon = _METERS_PER_DEG_LAT * math.cos(math.radians(lat_ref))
        self.m_per_deg_lat = _METERS_PER_DEG_LAT

    def to_projected(self, lon: float, lat: float) -> tuple[float, float]:
        """Project a WGS84 (lon, lat) coordinate to local meters (x, y)."""
        x = (lon - self.lon_ref) * self.m_per_deg_lon
        y = (lat - self.lat_ref) * self.m_per_deg_lat
        return x, y

    def to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        """Project local meters (x, y) back to WGS84 (lon, lat)."""
        lon = x / self.m_per_deg_lon + self.lon_ref
        lat = y / self.m_per_deg_lat + self.lat_ref
        return lon, lat
