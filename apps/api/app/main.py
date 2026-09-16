"""Axopar 28 Range Planner API — entry point.

V1 scope: one demo region (Bodrum/Kos synthetic fixture, see
``app.regions``), no PostGIS/scenarios/sharing yet. Every response carries an
honest confidence + data-note field rather than implying chart-grade
accuracy (CLAUDE.md Section 14, Section 44.20).
"""

from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, mapping, shape
from shapely.ops import transform as shapely_transform, unary_union

from diagnostics.snapshot import build_diagnostic_snapshot, stable_request_hash
from fuel_model.curve import AmbiguousInversionError
from isochrone.engine import DEFAULT_MAX_EXTENT_M, calculate_range
from isochrone.navigability import NavigabilityRegion
from isochrone.polygon_utils import validate_polygon
from routing.water_graph import build_graph_from_water_polygons

from .fuel import get_vessel_catalog_entry, list_vessel_entries, resolve_operating_point, usable_fuel_liters
from .regions import get_region_for
from .schemas import OriginModel, PointDiagnosticRequest, RangeRequest, RouteRequest, SegmentDiagnosticRequest, VerifyRequest

app = FastAPI(
    title="Axopar 28 Range Planner",
    description="Water-constrained reachable area planner for marine navigation planning.",
    version="0.1.0",
)

# Local dev only — the Vite dev server proxies /api itself, but CORS is kept
# permissive here too so the API can be hit directly (docs UI, curl, a future
# non-proxied frontend host) without surprises.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DISCLAIMER = (
    "Range estimates are for planning purposes only. They depend on "
    "fuel-model assumptions, environmental conditions, vessel load, "
    "chart-data quality, and user inputs. Always use official navigation "
    "charts and normal seamanship when operating the vessel."
)

APP_VERSION = "0.1.0"
FUEL_MODEL_VERSION = "1.0.0"
ROUTING_ENGINE_VERSION = "1.0.1"
VALIDATION_ENGINE_VERSION = "1.0.0"
DIAGNOSTIC_SCHEMA_VERSION = "1.0.0"

# Full regional isochrones are CPU-heavy. Run them outside the request thread
# and expose stage updates for the UI. A single worker prevents two accidental
# clicks/tabs from competing for the same CPU and making both jobs slower.
_range_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="range-full")
_range_jobs: dict[str, dict] = {}
_range_jobs_lock = Lock()


def _set_range_job(job_id: str, **changes) -> None:
    with _range_jobs_lock:
        if job_id in _range_jobs:
            _range_jobs[job_id].update(changes)


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/v1/regions")
def list_regions():
    from .regions import REGIONS

    return [
        {
            "id": r.id,
            "name": r.name,
            "bboxWgs84": list(r.bbox_wgs84),
            "dataNote": r.data_note,
            "marineDataVersion": r.marine_data_version,
            "hasBathymetry": r.depth_layer is not None,
            "marineFeatureCount": len(r.marine_features),
            "marinePoiCount": len(r.marine_pois),
            "fuelDockCount": sum(p.poi_type == "fuel_dock" for p in r.marine_pois),
            "unresolvedRestrictionNoticeCount": len(r.unresolved_restriction_notices),
        }
        for r in REGIONS
    ]


@app.get("/api/v1/regions/{region_id}/geometry")
def region_geometry(region_id: str):
    """Land geometry for the map basemap — the coastline this engine actually
    routes against, not a real chart (see the region's ``dataNote``)."""
    from .regions import REGIONS

    region = next((r for r in REGIONS if r.id == region_id), None)
    if region is None:
        raise HTTPException(status_code=404, detail=f"Unknown region: {region_id}")

    from shapely.ops import transform as shapely_transform

    def to_wgs84(x, y):
        return region.projection.to_wgs84(x, y)

    return {
        "id": region.id,
        "name": region.name,
        "bboxWgs84": list(region.bbox_wgs84),
        "dataNote": region.data_note,
        "marineDataVersion": region.marine_data_version,
        "hasBathymetry": region.depth_layer is not None,
        "marineFeatureCount": len(region.marine_features),
        "marinePoiCount": len(region.marine_pois),
        "fuelDockCount": sum(p.poi_type == "fuel_dock" for p in region.marine_pois),
        "unresolvedRestrictionNoticeCount": len(region.unresolved_restriction_notices),
        # Land geometry is stored in projected local meters (see
        # marine_data.bodrum_kos_fixture) — GeoJSON must be WGS84.
        "land": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": mapping(shapely_transform(to_wgs84, geom)),
                }
                for geom in region.land
            ],
        },
        "marineFeatures": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "sourceId": feature.source_id,
                        "featureType": feature.feature_type,
                        "restrictionClass": feature.restriction_class.value,
                        "classificationRule": feature.classification_rule,
                        "sourceName": feature.source_name,
                        "sourceVersion": feature.source_version,
                    },
                    "geometry": mapping(
                        shapely_transform(to_wgs84, feature.geometry)
                    ),
                }
                for feature in region.marine_features
            ],
        },
        "marinePois": {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "sourceId": poi.source_id,
                        "name": poi.name,
                        "poiType": poi.poi_type,
                        "categories": list(poi.categories),
                        "confidence": poi.confidence,
                        "classificationRule": poi.classification_rule,
                        "sourceName": poi.source_name,
                        "sourceVersion": poi.source_version,
                    },
                    "geometry": mapping(shapely_transform(to_wgs84, poi.geometry)),
                }
                for poi in region.marine_pois
            ],
        },
        "unresolvedRestrictionNotices": region.unresolved_restriction_notices,
    }


def _vessel_summary_dict(entry) -> dict:
    vessel = entry.profile
    curve = entry.fuel_curve
    return {
        "id": vessel.id,
        "name": vessel.name,
        "manufacturer": vessel.manufacturer,
        "model": vessel.model,
        "year": vessel.year,
        "propulsionType": vessel.propulsion_type,
        "engineCount": vessel.engine_count,
        "fuelType": vessel.fuel_type.value,
        "tankCapacityL": vessel.tank_capacity_l,
        "hullDraftM": vessel.hull_draft_m,
        "propulsionDepthM": vessel.propulsion_depth_m,
        "defaultReservePct": vessel.default_reserve_pct,
        "minRpm": curve.min_rpm,
        "maxRpm": curve.max_rpm,
        "minSpeedKn": curve.speed_at_rpm(curve.min_rpm),
        "maxSpeedKn": curve.speed_at_rpm(curve.max_rpm),
        "dataConfidence": entry.data_confidence,
        "notes": entry.notes,
    }


@app.get("/api/v1/vessels")
def list_vessels_endpoint():
    return [_vessel_summary_dict(entry) for entry in list_vessel_entries()]


@app.get("/api/v1/vessels/{vessel_id}")
def get_vessel_profile(vessel_id: str):
    try:
        entry = get_vessel_catalog_entry(vessel_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown vessel profile: {vessel_id}")

    body = _vessel_summary_dict(entry)
    body["fuelCurve"] = [
        {k: v for k, v in pt.items()} for pt in _fuel_curve_source_data(vessel_id)
    ]
    body["fuelModelVersion"] = FUEL_MODEL_VERSION
    return body


def _fuel_curve_source_data(vessel_id: str) -> list[dict]:
    from fuel_model.curve import CLEANED_FUEL_DATA, DUSKY_233_EVINRUDE_300_DATA

    return {
        "axopar-28-2019-verado-300": CLEANED_FUEL_DATA,
        "dusky-233-evinrude-etec-300": DUSKY_233_EVINRUDE_300_DATA,
    }.get(vessel_id, [])


def _region_or_422(origin: OriginModel):
    region = get_region_for(origin.lon, origin.lat)
    if region is None:
        # CLAUDE.md Section 27: hard-required data unavailable -> fail closed,
        # never silently compute against the wrong/no data.
        raise HTTPException(
            status_code=422,
            detail=(
                f"No marine data available at ({origin.lat}, {origin.lon}). "
                f"V1 only covers the Bodrum/Kos demo region."
            ),
        )
    return region


def _resolve_request_common(req):
    try:
        vessel_entry = get_vessel_catalog_entry(req.vesselProfileId)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown vessel profile: {req.vesselProfileId}")
    vessel = vessel_entry.profile

    region = _region_or_422(req.origin)

    try:
        operating_point = resolve_operating_point(
            vessel_entry.fuel_curve, req.speedKn, req.rpm, req.loadState, req.seaState
        )
    except AmbiguousInversionError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "candidates": exc.candidates,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    usable_fuel = usable_fuel_liters(vessel, req.fuel.mode, req.fuel.value, req.reservePct)
    if usable_fuel <= 0:
        raise HTTPException(status_code=422, detail="Usable fuel (after reserve) must be positive.")

    return vessel_entry, region, operating_point, usable_fuel


def _classified_water(req, vessel, region):
    minimum_safe_depth_m = vessel.min_safe_depth(req.depthSafetyMarginM)
    try:
        water = region.water_for_depth(minimum_safe_depth_m, req.depthPolicy)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if water.is_empty:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No water satisfying the {minimum_safe_depth_m:.2f} m minimum "
                "safe depth is available for this request."
            ),
        )
    return water, minimum_safe_depth_m


def _band_to_geojson(band, projection) -> dict:
    """Serialize one fuel band, reprojecting the polygon to WGS84.

    ``band.polygon`` lives in the engine's local projected CRS (meters) — see
    IsochroneEngine. GeoJSON is WGS84 by convention (RFC 7946); returning the
    raw projected coordinates would silently place the polygon at the wrong
    place on any standard map consumer.
    """
    from shapely.ops import transform as shapely_transform

    def to_wgs84(x, y):
        return projection.to_wgs84(x, y)

    geometry = None
    if not band.polygon.is_empty:
        geometry = mapping(shapely_transform(to_wgs84, band.polygon))

    return {
        "percentage": int(band.fraction * 100),
        "geometry": geometry,
        "areaM2": round(band.polygon.area, 1),
        "maxFuelCostL": round(band.max_cost, 3),
        "valid": band.validation.ok,
        "validation": band.validation.to_dict(),
    }


def _reachable_fuel_stop_ids(region, bands) -> list[str]:
    """Return fuel POIs covered by the validated outer reachable polygon."""
    if not bands:
        return []
    outer = max(bands.values(), key=lambda band: band.fraction)
    if not outer.validation.ok or outer.polygon.is_empty:
        return []
    return sorted(
        poi.source_id
        for poi in region.marine_pois
        if poi.poi_type == "fuel_dock" and outer.polygon.covers(poi.geometry)
    )


def _compute_and_serialize(
    req: RangeRequest,
    *,
    resolution_m: float,
    fractions,
    quality: str,
    progress_callback=None,
    partial_band_callback=None,
):
    if progress_callback:
        progress_callback(3, "Loading vessel and marine data")
    vessel_entry, region, operating_point, usable_fuel = _resolve_request_common(req)
    vessel = vessel_entry.profile
    if progress_callback:
        progress_callback(8, "Applying depth and safety-clearance rules")
    classified_water, minimum_safe_depth_m = _classified_water(req, vessel, region)

    warnings: list[str] = [DISCLAIMER]
    warnings.extend(region.data_freshness_warnings)
    if operating_point.is_inefficient:
        warnings.append(
            "Selected RPM falls in the inefficient operating zone "
            "(~2000-3200 RPM) — not recommended as a cruising speed."
        )

    request_id = str(uuid.uuid4())
    start = time.perf_counter()
    try:
        result = calculate_range(
            origin_wgs84=(req.origin.lon, req.origin.lat),
            lpnm=operating_point.lpnm,
            usable_fuel_l=usable_fuel,
            resolution_m=resolution_m,
            clearance_m=req.clearanceM,
            water=classified_water,
            land=region.blocking_geometries,
            projection=region.projection,
            fractions=fractions,
            range_mode=req.rangeMode,
            max_extent_m=DEFAULT_MAX_EXTENT_M,
            collect_diagnostics=req.developerMode,
            progress_callback=progress_callback,
            band_callback=(
                lambda fraction, band: partial_band_callback(
                    str(int(fraction * 100)),
                    _band_to_geojson(band, region.projection),
                )
                if partial_band_callback else None
            ),
        )
    except RuntimeError as exc:
        # Origin not navigable, or the compute budget was exceeded — fail
        # closed rather than degrading to an unsafe shortcut (Section 43/26).
        raise HTTPException(status_code=422, detail=str(exc))

    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)

    if not result.all_polygons_valid:
        warnings.extend(result.warnings)

    confidence = "LOW"
    confidence_reasons = [
        region.data_note,
        (
            f"Depth policy: {req.depthPolicy}; minimum safe depth "
            f"{minimum_safe_depth_m:.2f} m."
        ),
        *vessel_entry.notes,
        *region.data_freshness_warnings,
    ]
    if region.caution_features:
        warnings.append(
            f"{len(region.caution_features)} caution/conditional marine restriction(s) "
            "exist in this region; inspect their source details before travel."
        )
    if region.unresolved_restriction_notices:
        warnings.append(
            f"{len(region.unresolved_restriction_notices)} official restriction notice(s) "
            "lack authoritative machine-readable boundaries; they are not silently "
            "converted into guessed route blockers. Inspect developer diagnostics."
        )
        confidence_reasons.append("Official legal notices have unresolved geometry coverage.")
    if req.depthPolicy == "permissive":
        warnings.append(
            "Developer override active: unknown depth is treated as passable."
        )
        confidence_reasons.append("Unknown depth accepted by developer override.")
    if result.anomalies:
        confidence_reasons.append("Anomaly detector flagged one or more bands.")

    response = {
        "requestId": request_id,
        "quality": quality,
        "rangeMode": req.rangeMode,
        "region": {"id": region.id, "name": region.name},
        "vessel": {"id": vessel.id, "name": vessel.name, "dataConfidence": vessel_entry.data_confidence},
        "operatingPoint": {
            "rpm": operating_point.rpm,
            "speedKn": operating_point.speed_kn,
            "fuelLph": operating_point.fuel_lph,
            "lpnm": operating_point.lpnm,
            "isInefficient": operating_point.is_inefficient,
        },
        "usableFuelL": round(usable_fuel, 2),
        "maxRangeNm": round(result.max_range_nm, 2),
        "depth": {
            "policy": req.depthPolicy,
            "minimumSafeDepthM": round(minimum_safe_depth_m, 2),
            "source": region.depth_layer.source_name if region.depth_layer else None,
            "verticalDatum": region.depth_layer.vertical_datum if region.depth_layer else None,
        },
        "bands": {
            str(int(f * 100)): _band_to_geojson(b, region.projection)
            for f, b in result.bands.items()
        },
        "reachableFuelStopIds": _reachable_fuel_stop_ids(region, result.bands),
        "allBandsValid": result.all_polygons_valid,
        "anomalies": result.anomalies,
        "warnings": warnings,
        "confidence": confidence,
        "confidenceReasons": confidence_reasons,
        "metrics": {**result.metrics, "computeMs": elapsed_ms},
        "versions": {
            "appVersion": APP_VERSION,
            "fuelModelVersion": FUEL_MODEL_VERSION,
            "routingEngineVersion": ROUTING_ENGINE_VERSION,
            "validationEngineVersion": VALIDATION_ENGINE_VERSION,
            "marineDataVersion": region.marine_data_version,
            "diagnosticSchemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        },
    }
    if req.developerMode:
        request_state = {
            **req.model_dump(mode="json"),
            "resolvedRegionId": region.id,
            "quality": quality,
            "resolutionM": resolution_m,
            "marineDataVersion": region.marine_data_version,
        }
        source_layers = [
            {
                "type": "coastline",
                "source": "OpenStreetMap",
                "version": region.marine_data_version,
                "required": True,
            },
            {
                "type": "bathymetry",
                "source": region.depth_layer.source_name if region.depth_layer else None,
                "version": region.depth_layer.source_version if region.depth_layer else None,
                "checksumSha256": region.depth_layer.checksum_sha256 if region.depth_layer else None,
                "required": req.depthPolicy == "conservative",
            },
        ]
        source_features = [
            {
                "sourceId": item.source_id,
                "featureType": item.feature_type,
                "restrictionClass": item.restriction_class.value,
                "classificationRule": item.classification_rule,
                "sourceName": item.source_name,
                "sourceVersion": item.source_version,
            }
            for item in region.marine_features
        ]
        source_features.extend(region.unresolved_restriction_notices)
        source_features.extend(
            {
                "sourceId": item.source_id,
                "featureType": "marine_poi",
                "poiType": item.poi_type,
                "classificationRule": item.classification_rule,
                "sourceName": item.source_name,
                "sourceVersion": item.source_version,
            }
            for item in region.marine_pois
        )
        ox, oy = region.projection.to_projected(req.origin.lon, req.origin.lat)
        radius_m = min(result.max_range_nm * 1852.0 * 1.05, DEFAULT_MAX_EXTENT_M)
        diagnostic_extent = Point(ox, oy).buffer(radius_m).envelope
        safe_water_crop = classified_water.intersection(diagnostic_extent)
        blockers = unary_union(region.blocking_geometries).buffer(req.clearanceM).intersection(diagnostic_extent)

        def projected_to_wgs84(geometry):
            return mapping(shapely_transform(region.projection.to_wgs84, geometry)) if not geometry.is_empty else None

        cost_features = []
        for sample in result.debug_grid or []:
            lon, lat = region.projection.to_wgs84(sample["x"], sample["y"])
            cost_features.append({
                "type": "Feature",
                "properties": {"costL": sample["costL"], "refined": sample["refined"]},
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
            })
        overlays = {
            "computeExtent": projected_to_wgs84(diagnostic_extent),
            "classifiedSafeWater": projected_to_wgs84(safe_water_crop),
            "clearanceBuffer": projected_to_wgs84(blockers),
            "costField": {"type": "FeatureCollection", "features": cost_features},
        }
        response["diagnostics"] = build_diagnostic_snapshot(
            request_state=request_state,
            response=response,
            source_layers=source_layers,
            source_features=source_features,
        )
        response["diagnostics"]["overlays"] = overlays
    if progress_callback:
        progress_callback(100, "Range calculation complete")
    return response


@app.post("/api/v1/range/preview")
def range_preview(req: RangeRequest):
    """Fast, coarse-resolution preview (CLAUDE.md Section 22)."""
    return _compute_and_serialize(req, resolution_m=6000.0, fractions=[1.0], quality="preview")


@app.post("/api/v1/range/full")
def range_full(req: RangeRequest):
    """Full validated isochrone with all fuel bands."""
    return _compute_and_serialize(
        req, resolution_m=2400.0, fractions=[0.25, 0.50, 0.75, 1.0], quality="full"
    )


def _run_range_job(job_id: str, req: RangeRequest) -> None:
    def progress(percent: int, stage: str) -> None:
        _set_range_job(job_id, progress=percent, stage=stage)

    def publish_band(key: str, band: dict) -> None:
        with _range_jobs_lock:
            if job_id not in _range_jobs:
                return
            partial = dict(_range_jobs[job_id].get("partialBands", {}))
            partial[key] = band
            _range_jobs[job_id]["partialBands"] = partial

    _set_range_job(job_id, status="running")
    try:
        result = _compute_and_serialize(
            req,
            resolution_m=2400.0,
            fractions=[0.25, 0.50, 0.75, 1.0],
            quality="full",
            progress_callback=progress,
            partial_band_callback=publish_band,
        )
        _set_range_job(
            job_id,
            status="complete",
            progress=100,
            stage="Range calculation complete",
            result=result,
        )
    except HTTPException as exc:
        detail = exc.detail
        message = detail if isinstance(detail, str) else detail.get("message", str(detail))
        _set_range_job(job_id, status="failed", stage="Calculation failed", error=message)
    except Exception as exc:
        _set_range_job(job_id, status="failed", stage="Calculation failed", error=str(exc))


@app.post("/api/v1/range/full/jobs", status_code=202)
def start_range_full_job(req: RangeRequest):
    """Start a full calculation and return immediately for progress polling."""
    job_id = str(uuid.uuid4())
    with _range_jobs_lock:
        # Keep bounded completed history; active jobs are never discarded.
        completed = [
            key for key, value in _range_jobs.items()
            if value["status"] in ("complete", "failed")
        ]
        for key in completed[:-20]:
            _range_jobs.pop(key, None)
        _range_jobs[job_id] = {
            "jobId": job_id,
            "status": "queued",
            "progress": 0,
            "stage": "Queued for calculation",
            "result": None,
            "error": None,
            "partialBands": {},
        }
    _range_executor.submit(_run_range_job, job_id, req)
    return {"jobId": job_id, "status": "queued", "progress": 0, "stage": "Queued for calculation"}


@app.get("/api/v1/range/full/jobs/{job_id}")
def get_range_full_job(job_id: str):
    with _range_jobs_lock:
        job = _range_jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown or expired range job")
        return dict(job)


def _projected_geometry(geometry: dict, region):
    try:
        candidate = shape(geometry)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid GeoJSON geometry: {exc}")
    if not isinstance(candidate, (Polygon, MultiPolygon)) or candidate.is_empty:
        raise HTTPException(status_code=422, detail="geometry must be a non-empty Polygon or MultiPolygon")
    return shapely_transform(region.projection.to_projected, candidate)


@app.post("/api/v1/verify")
def verify_result(req: VerifyRequest):
    """Independently re-check supplied WGS84 result geometry and fail closed."""
    vessel_entry, region, _operating_point, _usable_fuel = _resolve_request_common(req)
    classified_water, minimum_safe_depth_m = _classified_water(req, vessel_entry.profile, region)
    candidate = _projected_geometry(req.geometry, region)
    nav = NavigabilityRegion(
        water_polygons=classified_water,
        land_geometries=region.blocking_geometries,
        clearance_m=req.clearanceM,
        resolution_m=600.0,
    )
    ox, oy = region.projection.to_projected(req.origin.lon, req.origin.lat)
    sampled = validate_polygon(candidate, nav, Point(ox, oy))
    raw_obstacle_intersection = bool(
        nav.land_raw_union is not None and candidate.intersects(nav.land_raw_union)
    )
    outside_safe_water_area_m2 = float(candidate.difference(nav.safe_water_polygons).area)
    accepted = (
        candidate.is_valid
        and sampled.ok
        and not raw_obstacle_intersection
        and outside_safe_water_area_m2 <= 1.0
    )
    request_state = {
        **req.model_dump(mode="json", exclude={"geometry"}),
        "geometry": req.geometry,
        "resolvedRegionId": region.id,
        "marineDataVersion": region.marine_data_version,
    }
    return {
        "accepted": accepted,
        "requestHash": stable_request_hash(request_state),
        "geometryValid": candidate.is_valid,
        "rawObstacleIntersection": raw_obstacle_intersection,
        "outsideSafeWaterAreaM2": round(outside_safe_water_area_m2, 2),
        "minimumSafeDepthM": round(minimum_safe_depth_m, 2),
        "sampledVerification": sampled.to_dict(),
        "versions": {
            "marineDataVersion": region.marine_data_version,
            "routingEngineVersion": ROUTING_ENGINE_VERSION,
            "validationEngineVersion": VALIDATION_ENGINE_VERSION,
        },
    }


@app.post("/api/v1/diagnostics/classify-point")
def classify_point(req: PointDiagnosticRequest):
    """Explain every available source contribution to one point decision."""
    vessel_entry, region, _operating_point, _usable_fuel = _resolve_request_common(req)
    classified_water, minimum_safe_depth_m = _classified_water(req, vessel_entry.profile, region)
    px, py = region.projection.to_projected(req.point.lon, req.point.lat)
    point = Point(px, py)
    elevation = region.depth_layer.elevation_at(req.point.lon, req.point.lat) if region.depth_layer else None
    contributions = [
        {
            "sourceId": "coastline",
            "sourceName": "OpenStreetMap",
            "rule": "point must be in classified water",
            "decision": "PASS" if region.water.contains(point) else "BLOCK",
        },
        {
            "sourceId": "bathymetry",
            "sourceName": region.depth_layer.source_name if region.depth_layer else None,
            "sourceVersion": region.depth_layer.source_version if region.depth_layer else None,
            "elevationM": elevation,
            "rule": f"known depth must be at least {minimum_safe_depth_m:.2f} m",
            "decision": (
                "PASS" if elevation is not None and elevation <= -minimum_safe_depth_m
                else "PASS_OVERRIDE" if req.depthPolicy == "permissive"
                else "BLOCK"
            ),
        },
    ]
    nearby = []
    for feature in region.marine_features:
        distance = feature.geometry.distance(point)
        if distance <= req.clearanceM:
            item = {
                "sourceId": feature.source_id,
                "sourceName": feature.source_name,
                "sourceVersion": feature.source_version,
                "featureType": feature.feature_type,
                "restrictionClass": feature.restriction_class.value,
                "classificationRule": feature.classification_rule,
                "distanceM": round(distance, 2),
            }
            nearby.append(item)
            contributions.append({**item, "decision": "BLOCK"})
    # A single-point decision does not need a regional graph or a unioned,
    # buffered obstacle mask. Building those on every marker drag made the UI
    # wait many seconds. These direct predicates are equivalent for a point:
    # it must be inside classified safe water and farther than the requested
    # clearance from every hard blocker.
    inside_classified_water = classified_water.contains(point)
    clears_blockers = all(
        blocker.distance(point) > req.clearanceM
        for blocker in region.blocking_geometries
    )
    return {
        "point": req.point.model_dump(mode="json"),
        "navigable": inside_classified_water and clears_blockers,
        "minimumSafeDepthM": round(minimum_safe_depth_m, 2),
        "contributions": contributions,
        "nearbyHardFeatures": nearby,
        "requestHash": stable_request_hash(req.model_dump(mode="json")),
    }


@app.post("/api/v1/diagnostics/classify-segment")
def classify_segment(req: SegmentDiagnosticRequest):
    """Return exact rejection reasons for one candidate route edge."""
    vessel_entry, region, _operating_point, _usable_fuel = _resolve_request_common(req)
    classified_water, minimum_safe_depth_m = _classified_water(req, vessel_entry.profile, region)
    start_xy = region.projection.to_projected(req.origin.lon, req.origin.lat)
    end_xy = region.projection.to_projected(req.end.lon, req.end.lat)
    segment = LineString([start_xy, end_xy])
    rejections: list[dict] = []
    safe_depth_and_coast = classified_water
    if not safe_depth_and_coast.covers(segment):
        rejections.append({
            "sourceId": "classified-water-mask",
            "sourceName": region.depth_layer.source_name if region.depth_layer else "OpenStreetMap coastline",
            "rule": (
                f"entire segment must remain in water with known depth >= "
                f"{minimum_safe_depth_m:.2f} m"
            ),
            "reason": "segment leaves the classified safe-water mask",
        })
    for feature in region.marine_features:
        if segment.intersects(feature.geometry.buffer(req.clearanceM)):
            rejections.append({
                "sourceId": feature.source_id,
                "sourceName": feature.source_name,
                "sourceVersion": feature.source_version,
                "featureType": feature.feature_type,
                "restrictionClass": feature.restriction_class.value,
                "classificationRule": feature.classification_rule,
                "reason": f"segment violates {req.clearanceM:.0f} m obstacle clearance",
            })
    return {
        "start": req.origin.model_dump(mode="json"),
        "end": req.end.model_dump(mode="json"),
        "navigable": not rejections,
        "rejections": rejections,
        "minimumSafeDepthM": round(minimum_safe_depth_m, 2),
        "requestHash": stable_request_hash(req.model_dump(mode="json")),
    }


@app.post("/api/v1/route")
def route(req: RouteRequest):
    """Shortest navigable-water route from origin to destination."""
    vessel_entry, region, operating_point, usable_fuel = _resolve_request_common(req)
    vessel = vessel_entry.profile
    classified_water, minimum_safe_depth_m = _classified_water(req, vessel, region)

    dest_region = _region_or_422(req.destination)
    if dest_region.id != region.id:
        raise HTTPException(status_code=422, detail="Origin and destination are in different regions.")

    proj = region.projection
    ox, oy = proj.to_projected(req.origin.lon, req.origin.lat)
    dx, dy = proj.to_projected(req.destination.lon, req.destination.lat)

    # Crop water to a generous box around both points for a fast, focused
    # route graph (not the full-region isochrone extent).
    #
    # Regression note: a fixed 5km margin around the straight-line
    # origin/destination bounding box assumes the real route roughly
    # follows that line. It doesn't when origin and destination sit on
    # opposite sides of a peninsula or headland — the real route has to
    # detour around it, and a 5km margin isn't nearly enough room for that
    # detour, so the cropped graph had genuinely no path even though open
    # water existed just outside the box. shortest_path() correctly
    # reported "unreachable" for the graph it was given — the graph itself
    # was too small. Scaling the margin with the direct distance gives
    # realistic detours room without ballooning every short hop's graph.
    from shapely.geometry import Polygon as ShapelyPolygon

    direct_distance_m = ((dx - ox) ** 2 + (dy - oy) ** 2) ** 0.5
    margin = max(20_000.0, min(direct_distance_m, DEFAULT_MAX_EXTENT_M))
    minx, maxx = sorted([ox, dx])
    miny, maxy = sorted([oy, dy])
    extent = ShapelyPolygon(
        [
            (minx - margin, miny - margin),
            (maxx + margin, miny - margin),
            (maxx + margin, maxy + margin),
            (minx - margin, maxy + margin),
        ]
    )
    from isochrone.engine import _as_multipolygon

    water_cropped = _as_multipolygon(classified_water.intersection(extent))
    # Same reasoning as IsochroneEngine.build_graph(): only land near the
    # crop extent can actually affect an edge inside it. region.land is the
    # whole region's list (2000+ pieces for east-med-real) — buffering and
    # unioning all of it for every route request was needless work on top
    # of the correctness bug above.
    extent_with_margin = extent.buffer(req.clearanceM * 2.0)
    nearby_land = [
        g for g in region.blocking_geometries if g.intersects(extent_with_margin)
    ]

    graph = build_graph_from_water_polygons(
        water_polygons=water_cropped,
        land_geometries=nearby_land,
        clearance_m=req.clearanceM,
        grid_resolution_m=250.0,
    )
    graph.set_fuel_rate(operating_point.lpnm)

    path = graph.shortest_path(Point(ox, oy), Point(dx, dy))
    if path is None:
        raise HTTPException(
            status_code=422,
            detail="No navigable route found — destination may be unreachable, on land, "
            "or outside the mapped water extent.",
        )

    distance_nm = path.length / 1852.0
    fuel_used_l = distance_nm * operating_point.lpnm
    speed_kn = operating_point.speed_kn or 1e-6
    eta_minutes = (distance_nm / speed_kn) * 60.0

    warnings = [DISCLAIMER]
    if req.depthPolicy == "permissive":
        warnings.append("Developer override active: unknown depth is treated as passable.")
    if fuel_used_l > usable_fuel:
        warnings.append(
            f"This route ({fuel_used_l:.1f} L) exceeds usable fuel "
            f"({usable_fuel:.1f} L) — not reachable with the current fuel/reserve settings."
        )

    route_wgs84_coords = [proj.to_wgs84(x, y) for x, y in path.coords]

    return {
        "region": {"id": region.id, "name": region.name},
        "distanceNm": round(distance_nm, 3),
        "etaMinutes": round(eta_minutes, 1),
        "fuelUsedL": round(fuel_used_l, 2),
        "usableFuelL": round(usable_fuel, 2),
        "fuelRemainingL": round(usable_fuel - fuel_used_l, 2),
        "reachableWithCurrentFuel": fuel_used_l <= usable_fuel,
        "operatingPoint": {
            "rpm": operating_point.rpm,
            "speedKn": operating_point.speed_kn,
            "lpnm": operating_point.lpnm,
        },
        "routeGeojson": {
            "type": "LineString",
            "coordinates": [[lon, lat] for lon, lat in route_wgs84_coords],
        },
        "confidence": "LOW",
        "confidenceReasons": [
            region.data_note,
            (
                f"Depth policy: {req.depthPolicy}; minimum safe depth "
                f"{minimum_safe_depth_m:.2f} m."
            ),
            *vessel_entry.notes,
        ],
        "depth": {
            "policy": req.depthPolicy,
            "minimumSafeDepthM": round(minimum_safe_depth_m, 2),
            "source": region.depth_layer.source_name if region.depth_layer else None,
            "verticalDatum": region.depth_layer.vertical_datum if region.depth_layer else None,
        },
        "warnings": warnings,
    }
