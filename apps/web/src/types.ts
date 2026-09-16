/** Shared TypeScript types for the Axopar 28 Range Planner web UI.
 *
 * Loosely mirrors the API response shapes in apps/api/app/main.py. Not the
 * full packages/shared-contracts schema yet — v1 demo scope.
 */

export type RangeMode = "one_way" | "round_trip";
export type SeaState = "calm" | "moderate" | "rough";
export type LoadState = "light" | "normal" | "heavy";
export type FuelMode = "full" | "liters" | "percent";
export type DepthPolicy = "conservative" | "permissive";

export interface LatLon {
  lat: number;
  lon: number;
}

export interface GeoJSONGeometry {
  type: string;
  coordinates: unknown;
}

export interface RegionSummary {
  id: string;
  name: string;
  bboxWgs84: [number, number, number, number];
  dataNote: string;
  marineDataVersion: string;
  hasBathymetry: boolean;
  marineFeatureCount: number;
  marinePoiCount: number;
  fuelDockCount: number;
  unresolvedRestrictionNoticeCount: number;
}

export interface VesselSummary {
  id: string;
  name: string;
  manufacturer: string;
  model: string;
  year: number;
  propulsionType: string;
  engineCount: number;
  fuelType: string;
  tankCapacityL: number;
  hullDraftM: number;
  propulsionDepthM: number;
  defaultReservePct: number;
  minRpm: number;
  maxRpm: number;
  minSpeedKn: number;
  maxSpeedKn: number;
  dataConfidence: number;
  notes: string[];
}

export interface RegionGeometry extends RegionSummary {
  land: {
    type: "FeatureCollection";
    features: { type: "Feature"; properties: Record<string, unknown>; geometry: GeoJSONGeometry }[];
  };
  marineFeatures: {
    type: "FeatureCollection";
    features: { type: "Feature"; properties: Record<string, unknown>; geometry: GeoJSONGeometry }[];
  };
  marinePois: {
    type: "FeatureCollection";
    features: { type: "Feature"; properties: Record<string, unknown>; geometry: GeoJSONGeometry }[];
  };
  unresolvedRestrictionNotices: Array<Record<string, unknown>>;
}

export interface OperatingPoint {
  rpm: number;
  speedKn: number;
  fuelLph?: number;
  lpnm: number;
  isInefficient?: boolean;
}

export interface BandValidation {
  ok: boolean;
  land_leaks: number;
  boundary_land_touches: number;
  interior_outside_water: number;
  clearance_violations: number;
  origin_inside: boolean;
  n_boundary_samples: number;
  n_interior_samples: number;
  notes: string[];
}

export interface Band {
  percentage: number;
  geometry: GeoJSONGeometry | null;
  areaM2: number;
  maxFuelCostL: number;
  valid: boolean;
  validation: BandValidation;
}

export interface RangeResponse {
  requestId: string;
  quality: "preview" | "full";
  rangeMode: RangeMode;
  region: { id: string; name: string };
  vessel: { id: string; name: string; dataConfidence: number };
  operatingPoint: OperatingPoint;
  usableFuelL: number;
  maxRangeNm: number;
  depth: {
    policy: DepthPolicy;
    minimumSafeDepthM: number;
    source: string | null;
    verticalDatum: string | null;
  };
  bands: Record<string, Band>;
  reachableFuelStopIds: string[];
  allBandsValid: boolean;
  anomalies: string[];
  warnings: string[];
  confidence: "HIGH" | "MEDIUM" | "LOW";
  confidenceReasons: string[];
  metrics: Record<string, unknown>;
  versions: Record<string, string>;
  diagnostics?: DiagnosticSnapshot;
}

export interface RangeJob {
  jobId: string;
  status: "queued" | "running" | "complete" | "failed";
  progress: number;
  stage: string;
  result?: RangeResponse | null;
  error?: string | null;
  partialBands?: Record<string, Band>;
}

export interface DiagnosticSnapshot {
  schemaVersion: string;
  requestHash: string;
  requestState: Record<string, unknown>;
  polygons: Record<string, GeoJSONGeometry | null>;
  verifier: {
    accepted: boolean;
    failedBands: string[];
    anomalies: string[];
    randomSeed: number;
  };
  grid: Record<string, number | string>;
  timings: { computeMs: number | null };
  sourceLayers: Record<string, unknown>[];
  sourceFeatures: Record<string, unknown>[];
  overlays: {
    computeExtent: GeoJSONGeometry | null;
    classifiedSafeWater: GeoJSONGeometry | null;
    clearanceBuffer: GeoJSONGeometry | null;
    costField: {
      type: "FeatureCollection";
      features: Array<{ type: "Feature"; properties: { costL: number; refined: boolean }; geometry: GeoJSONGeometry }>;
    };
  };
}

export interface RouteResponse {
  region: { id: string; name: string };
  distanceNm: number;
  etaMinutes: number;
  fuelUsedL: number;
  usableFuelL: number;
  fuelRemainingL: number;
  reachableWithCurrentFuel: boolean;
  operatingPoint: OperatingPoint;
  routeGeojson: { type: "LineString"; coordinates: [number, number][] };
  confidence: "HIGH" | "MEDIUM" | "LOW";
  confidenceReasons: string[];
  warnings: string[];
  depth: RangeResponse["depth"];
}

export interface ApiErrorBody {
  detail: string | { message: string; candidates?: number[] };
}
