"""Marine data module — ingestion, validation, and water polygon generation.

This package handles:
1. Loading coastline/obstacle/restriction data from various sources
2. Validating geometry topology (no self-intersections, valid rings)
3. Generating pre-computed water polygons for routing
4. Ingesting data into PostGIS marine_features table

The Bodrum/Kos production region currently loads versioned OSM coastline,
OSM/OpenSeaMap obstacles and POIs, EMODnet bathymetry and managed areas, plus
unresolved official notices. Wider regional coverage remains less complete.
"""

from .depth import RasterDepthLayer
from .features import MarineFeature, RestrictionClass
from .official_areas import load_official_areas_wgs84, load_unresolved_official_notices
from .pois import MarinePoi, load_marine_pois_wgs84

__all__ = [
    "MarineFeature", "RasterDepthLayer", "RestrictionClass",
    "load_official_areas_wgs84", "load_unresolved_official_notices",
    "MarinePoi", "load_marine_pois_wgs84",
]

from .bodrum_kos_fixture import (
    FIXTURE_BBOX_WGS84,
    FIXTURE_LAT_REF,
    FIXTURE_LON_REF,
    REGRESSION_FIXTURES,
    RegressionAssertion,
    bodrum_mainland,
    bodrum_marina_breakwater,
    fixture_bbox,
    generate_bodrum_kos_water,
    kos_harbor_breakwater,
    kos_island,
    land_geometries,
    small_islands,
    unify_land,
    _wgs84_to_local,
    _local_to_wgs84,
)

__all__ = [
    "FIXTURE_BBOX_WGS84",
    "FIXTURE_LAT_REF",
    "FIXTURE_LON_REF",
    "REGRESSION_FIXTURES",
    "RegressionAssertion",
    "bodrum_mainland",
    "bodrum_marina_breakwater",
    "fixture_bbox",
    "generate_bodrum_kos_water",
    "kos_harbor_breakwater",
    "kos_island",
    "land_geometries",
    "small_islands",
    "unify_land",
    "_wgs84_to_local",
    "_local_to_wgs84",
]
