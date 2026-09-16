"""In-memory region registry.

Three regions are registered. The bathymetry-backed Bodrum/Kos region is
checked first; the wider coastline-only region handles origins outside it:

1. ``bodrum-kos-real`` (live default in its bbox): detailed OSM coastline
   plus EMODnet DTM 2024 bathymetry. Conservative requests classify water by
   the vessel-specific minimum safe depth.
2. ``east-med-real``: real, full-detail OSM coastline
   covering Italy, Greece, Cyprus, and Turkey — see
   ``services/marine-data/marine_data/east_med_real.py`` and
   ``data/manifests/east_med_real.json``. It uses its own ``LocalProjection``
   reference point (see its module); the manifest
   documents a real, non-trivial distance/fuel accuracy caveat for origins
   far from that point (a region spanning 12 degrees of latitude can't have
   uniformly accurate distances from one fixed reference under this
   project's current projection model).
   It has no depth layer, so conservative requests fail closed. An explicit
   developer-only permissive request can exercise it for diagnostics.
3. ``bodrum-kos-demo``: the original hand-built *synthetic* fixture from
   ``marine_data.bodrum_kos_fixture``, kept only because
   ``services/marine-data/tests``, ``services/routing/tests`` and
   ``services/isochrone/tests`` import it directly as a fully-controlled
   world for proving routing/isochrone correctness (CLAUDE.md Section 35).
   It remains unreachable through the live API because the real region has
   the same coverage.

CLAUDE.md Section 27 (hard-required data): if a requested origin falls
outside every region we actually have data for, the API must fail closed
with a clear message — never silently extrapolate a fixture, and never
pretend a real chart exists where it doesn't.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Literal, Optional

from shapely.geometry import MultiPolygon, Polygon

from marine_data.bodrum_kos_fixture import FIXTURE_BBOX_WGS84, generate_bodrum_kos_water
from marine_data.bodrum_kos_real import REAL_BBOX_WGS84, generate_bodrum_kos_real_water
from marine_data.aegean_real import AEGEAN_BBOX_WGS84, generate_aegean_water
from marine_data.east_med_real import EAST_MED_BBOX_WGS84, generate_east_med_water
from marine_data.depth import RasterDepthLayer
from marine_data.features import MarineFeature, RestrictionClass, load_features_projected
from marine_data.official_areas import load_official_areas_projected, load_unresolved_official_notices
from marine_data.pois import MarinePoi, load_marine_pois_projected
from isochrone.projection import LocalProjection


@dataclass
class Region:
    id: str
    name: str
    water: MultiPolygon
    land: List[Polygon]
    bbox_wgs84: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    projection: LocalProjection
    data_note: str
    marine_data_version: str
    depth_layer: Optional[RasterDepthLayer] = None
    marine_features: list[MarineFeature] = field(default_factory=list)
    marine_pois: list[MarinePoi] = field(default_factory=list)
    unresolved_restriction_notices: list[dict] = field(default_factory=list)
    data_freshness_warnings: list[str] = field(default_factory=list)
    _depth_water_cache: dict[float, MultiPolygon] = field(
        default_factory=dict, init=False, repr=False
    )

    def contains(self, lon: float, lat: float) -> bool:
        min_lon, min_lat, max_lon, max_lat = self.bbox_wgs84
        return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat

    def water_for_depth(
        self, minimum_safe_depth_m: float, policy: Literal["conservative", "permissive"]
    ) -> MultiPolygon:
        if policy == "permissive":
            return self.water
        if self.depth_layer is None:
            raise RuntimeError(
                f"Conservative depth policy requires bathymetry, but region {self.id!r} "
                "has no depth layer. Unknown depth is blocked."
            )
        threshold = round(float(minimum_safe_depth_m), 3)
        if threshold not in self._depth_water_cache:
            safe_depth = self.depth_layer.safe_water_projected(
                threshold, self.projection.to_projected
            )
            classified = self.water.intersection(safe_depth)
            if isinstance(classified, Polygon):
                classified = MultiPolygon([classified])
            elif not isinstance(classified, MultiPolygon):
                classified = MultiPolygon(
                    [g for g in classified.geoms if isinstance(g, Polygon)]
                )
            self._depth_water_cache[threshold] = classified
        return self._depth_water_cache[threshold]

    @property
    def blocking_geometries(self) -> list:
        return [
            *self.land,
            *(
                feature.geometry
                for feature in self.marine_features
                if feature.restriction_class is RestrictionClass.HARD_NO_GO
            ),
        ]

    @property
    def caution_features(self) -> list[MarineFeature]:
        return [
            feature
            for feature in self.marine_features
            if feature.restriction_class is RestrictionClass.CAUTION_CONDITIONAL
        ]


_real_water, _real_land = generate_bodrum_kos_real_water()

_DEPTH_PATH = (
    Path(__file__).resolve().parents[3]
    / "data" / "fixtures" / "bodrum_kos_real" / "depth-emodnet-2024.tif"
)
_bodrum_depth = RasterDepthLayer(
    path=_DEPTH_PATH,
    source_name="EMODnet Bathymetry Digital Terrain Model",
    source_version="DTM-2024",
    fetched_at="2026-09-13T00:00:00Z",
    checksum_sha256="c311e5b7d5e18e41bb94fd9f541f08c24f90041f67cf84fb1314162a4669973e",
)
_bodrum_projection = LocalProjection()
_bodrum_features = [
    *load_features_projected(_bodrum_projection.to_projected),
    *load_official_areas_projected(_bodrum_projection.to_projected),
]
_bodrum_pois = load_marine_pois_projected(_bodrum_projection.to_projected)

_aegean_water, _aegean_land = generate_aegean_water()
_AEGEAN_DEPTH_PATH = (
    Path(__file__).resolve().parents[3]
    / "data" / "fixtures" / "aegean_real" / "depth-emodnet-2024.tif"
)
_aegean_depth = RasterDepthLayer(
    path=_AEGEAN_DEPTH_PATH,
    source_name="EMODnet Bathymetry Digital Terrain Model",
    source_version="DTM-2024-aegean-206m",
    fetched_at="2026-09-16T00:00:00Z",
    checksum_sha256="ba94eecf8ae3202ed400c0dc8382836c90d73b326ceb82ba54528c3ea54db8c5",
)

AEGEAN_REAL = Region(
    id="aegean-real",
    name="Aegean Sea (OSM coastline + EMODnet depth)",
    water=_aegean_water,
    land=_aegean_land,
    bbox_wgs84=AEGEAN_BBOX_WGS84,
    projection=_bodrum_projection,
    data_note=(
        "Regional OpenStreetMap coastline covering the Aegean Sea, merged with "
        "the higher-detail Bodrum/Kos coastline. EMODnet DTM 2024 bathymetry "
        "covers the same extent at approximately 206 m resolution. Local "
        "Bodrum/Kos obstacles, restrictions, marinas and fuel docks are retained; "
        "equivalent feature coverage elsewhere in the region remains incomplete."
    ),
    marine_data_version="aegean-osm-2026-09-09+emodnet-dtm-2024-206m+bodrum-detail-2026-09-16",
    depth_layer=_aegean_depth,
    marine_features=_bodrum_features,
    marine_pois=_bodrum_pois,
    unresolved_restriction_notices=list(load_unresolved_official_notices()),
    data_freshness_warnings=[
        "Regional obstacle and restriction coverage outside Bodrum/Kos is incomplete.",
        "Marina and fuel-dock POIs are community-maintained; confirm availability directly with the operator.",
    ],
)

BODRUM_KOS_REAL = Region(
    id="bodrum-kos-real",
    name="Bodrum / Kos (real OSM coastline)",
    water=_real_water,
    land=_real_land,
    bbox_wgs84=REAL_BBOX_WGS84,
    projection=_bodrum_projection,
    data_note=(
        "Real OpenStreetMap coastline (Bodrum peninsula, Kos island, ~470 "
        "surrounding islets) — see data/manifests/bodrum_kos_real.json for "
        "source relations and fetch date. EMODnet DTM 2024 bathymetry is "
        "available at approximately 115 m resolution. OSM/OpenSeaMap adds "
        "57 breakwater/groyne features and 111 rock/wreck/obstruction hazards. "
        "Eight official EMODnet Natura 2000 polygons are retained as informational; "
        "the official WFS returned no military polygons in this bbox. Two HNHS "
        "prohibitions have no authoritative machine-readable boundary and remain "
        "explicit unresolved cautions. The OSM/OpenSeaMap snapshot adds 29 marinas, "
        "two harbours, and two explicitly tagged marine fuel docks. POIs may be "
        "incomplete or stale, so this still does not qualify as navigation-grade data."
    ),
    marine_data_version="bodrum-kos-osm-2026-09-09+emodnet-dtm-2024+osm-seamarks-2026-09-13+official-areas-2026-09-14+osm-pois-2026-09-16",
    depth_layer=_bodrum_depth,
    marine_features=_bodrum_features,
    marine_pois=_bodrum_pois,
    unresolved_restriction_notices=list(load_unresolved_official_notices()),
    data_freshness_warnings=[
        "EMODnet Natura 2000 upstream release date is 2020-06-30; confirm current legal status before travel.",
        "Marina and fuel-dock POIs are community-maintained; confirm fuel availability and access directly with the operator.",
    ],
)

_water, _land = generate_bodrum_kos_water()

BODRUM_KOS = Region(
    id="bodrum-kos-demo",
    name="Bodrum / Kos (synthetic demo region)",
    water=_water,
    land=_land,
    bbox_wgs84=FIXTURE_BBOX_WGS84,
    projection=LocalProjection(),
    data_note=(
        "Synthetic regression-fixture coastline, not sourced from real "
        "charts or OSM data. Shapes and positions are simplified/approximate. "
        "For engine demonstration only — never use for real navigation."
    ),
    marine_data_version="bodrum-kos-synthetic-v1",
)

_east_med_water, _east_med_land = generate_east_med_water()

EAST_MED = Region(
    id="east-med-real",
    name="Italy / Greece / Cyprus / Turkey (real OSM coastline)",
    water=_east_med_water,
    land=_east_med_land,
    bbox_wgs84=EAST_MED_BBOX_WGS84,
    projection=LocalProjection(lat_ref=40.0, lon_ref=24.0),
    data_note=(
        "Real OpenStreetMap coastline (full detail down to 1-hectare "
        "islets) covering Italy, Greece, Cyprus, and Turkey — see "
        "data/manifests/east_med_real.json. Smaller islets/rocks below that "
        "size aren't represented outside the higher-detail Bodrum/Kos area, "
        "and distance/fuel accuracy degrades for origins far from this "
        "region's projection reference point (documented in the manifest). "
        "No bathymetry, restriction zones, or marina/fuel-dock data."
    ),
    marine_data_version="east-med-osm-2026-09-09",
)

# Prefer the bathymetry-backed, higher-detail region where it is available.
# Requests outside it fall through to the wider coastline-only region, where
# conservative mode fails closed because depth is unknown.
REGIONS: List[Region] = [AEGEAN_REAL, BODRUM_KOS_REAL, EAST_MED, BODRUM_KOS]


def get_region_for(lon: float, lat: float) -> Optional[Region]:
    """Return the first region whose compute extent covers (lon, lat)."""
    for region in REGIONS:
        if region.contains(lon, lat):
            return region
    return None
