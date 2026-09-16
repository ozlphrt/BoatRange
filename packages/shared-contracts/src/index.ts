/**
 * @axopar/shared-contracts
 * Shared TypeScript types for the Axopar 28 Range Planner API.
 * 
 * Vector-first design: all geometry types reference GeoJSON-compatible
 * structures, not raster grids. The routing engine operates on water
 * polygon graphs, not cell grids.
 */

// ============================================================================
// Enums
// ============================================================================

export type PropulsionType = "outboard" | "inboard" | "stern_drive" | "waterjet" | "electric";

export type SeaState = "calm" | "moderate" | "rough";

export type LoadState = "light" | "normal" | "heavy";

export type RangeMode = "one_way" | "round_trip";
export type DepthPolicy = "conservative" | "permissive";

export type ConfidenceLevel = "HIGH" | "MEDIUM" | "LOW";

export type RestrictionClass = "HARD_NO_GO" | "CAUTION_CONDITIONAL" | "INFORMATIONAL";

export type MarineFeatureType =
  | "coastline"
  | "obstacle"
  | "restriction"
  | "depth_sounding"
  | "marina"
  | "fuel_dock"
  | "port"
  | "user_exclusion"
  | "user_override";

// ============================================================================
// Vessel Profile
// ============================================================================

export interface VesselProfile {
  id: string;
  name: string;
  manufacturer: string;
  model: string;
  year: number;
  propulsion_type: PropulsionType;
  engine_count: number;
  tank_capacity_l: number;
  hull_draft_m: number;
  propulsion_depth_m: number;
  default_reserve_pct: number;
  fuel_model_version: string;
  created_at: string; // ISO 8601
  updated_at: string; // ISO 8601
}

// ============================================================================
// Fuel Model
// ============================================================================

export interface FuelCurvePoint {
  id: string;
  vessel_profile_id: string;
  rpm: number;
  speed_kn: number;
  fuel_lph: number;
  /** Derived L/nm = L/h / knots. Not stored in DB, computed at query time. */
  derived_lpnm?: number;
  source: string;
  confidence: number; // 0-1
  is_raw: boolean;
  model_version: string;
}

export interface OperatingPoint {
  rpm: number;
  speed_kn: number;
  fuel_lph: number;
  lpnm: number; // liters per nautical mile
  /** Whether this operating point is in an inefficient zone. */
  is_inefficient: boolean;
}

export interface FuelInput {
  mode: "full_tank" | "liters" | "percent";
  value: number;
}

export interface ReserveConfig {
  pct: number; // 0-100
  /** Protected fuel that must remain on board for return trip. */
  protected_l: number;
}

// ============================================================================
// Range Request / Response
// ============================================================================

export interface GeoPoint {
  lat: number;
  lon: number;
}

export interface RangeRequest {
  origin: GeoPoint;
  vesselProfileId: string;
  fuel: FuelInput;
  reservePct: number;
  speedKn?: number;
  rpm?: number;
  seaState: SeaState;
  loadState: LoadState;
  clearanceM: number; // safety clearance in meters
  rangeMode: RangeMode;
  depthPolicy: DepthPolicy;
  depthSafetyMarginM: number;
  developerMode?: boolean;
  /** Optional debug mode — includes diagnostic data in response. */
  debug?: boolean;
}

export interface BandResult {
  /** Percentage of usable fuel (25, 50, 75, 100). */
  percentage: number;
  /** Polygon representing the reachable area at this fuel level. GeoJSON MultiPolygon. */
  polygon: GeoJSON.MultiPolygon;
  /** Max distance from origin along valid navigable paths. */
  max_distance_nm: number;
}

export interface RangeResponse {
  requestId: string;
  quality: "preview" | "full";
  bands: Record<string, BandResult>;
  operatingPoint: OperatingPoint;
  usableFuelL: number;
  reserveProtectedL: number;
  confidence: ConfidenceLevel;
  confidenceReasons: string[];
  warnings: string[];
  versions: {
    app: string;
    fuelModel: string;
    marineData: string;
    routingEngine: string;
    validationEngine: string;
  };
  /** Optional diagnostic snapshot (debug mode only). */
  diagnostics?: DiagnosticSnapshot;
}

// ============================================================================
// Route Request / Response
// ============================================================================

export interface RouteRequest {
  origin: GeoPoint;
  destination: GeoPoint;
  vesselProfileId: string;
  fuel: FuelInput;
  reservePct: number;
  seaState: SeaState;
  loadState: LoadState;
  clearanceM: number;
  depthPolicy: DepthPolicy;
  depthSafetyMarginM: number;
  developerMode?: boolean;
}

export interface RouteResult {
  /** Valid navigable route as GeoJSON LineString. */
  geometry: GeoJSON.LineString;
  distanceNm: number;
  estimatedTimeMinutes: number;
  fuelConsumedL: number;
  fuelRemainingL: number;
  reserveRemainingL: number;
  confidence: ConfidenceLevel;
  warnings: string[];
}

// ============================================================================
// Marine Data (Vector-First)
// ============================================================================

/**
 * A marine feature stored in PostGIS as a vector geometry.
 * This is the building block of the water polygon graph.
 */
export interface MarineFeature {
  id: string;
  layerId: string;
  featureType: MarineFeatureType;
  restrictionClass?: RestrictionClass;
  /** GeoJSON geometry (Point, LineString, Polygon, MultiPolygon). */
  geometry: GeoJSON.Geometry;
  properties: Record<string, unknown>;
  sourceName: string;
  sourceVersion: string;
  fetchedAt: string;
}

/**
 * Navigability tile — a pre-computed region of navigable water.
 * In the vector-first approach, this stores a collection of water
 * polygon fragments that are known to be navigable at a given resolution.
 */
export interface NavigabilityTile {
  id: string;
  region: string; // e.g., "bodrum-kos"
  resolutionM: number;
  tileKey: string; // geographic tile identifier
  marineDataVersion: string;
  paramsHash: string;
  /** GeoJSON FeatureCollection of navigable water polygons. */
  waterPolygons: GeoJSON.FeatureCollection;
  createdAt: string;
}

// ============================================================================
// Scenario
// ============================================================================

export interface Scenario {
  id?: string;
  vesselProfileId: string;
  origin: GeoPoint;
  fuelInput: FuelInput;
  reservePct: number;
  selectedSpeedKn?: number;
  selectedRpm?: number;
  seaState: SeaState;
  loadState: LoadState;
  clearanceM: number;
  rangeMode: RangeMode;
  resultSnapshot?: RangeResponse;
  createdAt?: string;
  appVersion?: string;
  fuelModelVersion?: string;
  marineDataVersion?: string;
  routingEngineVersion?: string;
}

// ============================================================================
// Diagnostic Snapshot (Developer Mode)
// ============================================================================

export interface DiagnosticSnapshot {
  origin: GeoPoint;
  mapExtent: {
    minLat: number;
    minLon: number;
    maxLat: number;
    maxLon: number;
  };
  vessel: VesselProfile;
  fuelSettings: {
    input: FuelInput;
    reservePct: number;
    usableFuelL: number;
    operatingPoint: OperatingPoint;
  };
  modelVersions: {
    fuelModel: string;
    marineData: string;
    routingEngine: string;
    validationEngine: string;
  };
  marineDataVersions: Record<string, string>;
  rawSourceIds: string[];
  /** Graph resolution — edge count and node count. */
  graphMetrics: {
    nodeCount: number;
    edgeCount: number;
    waterPolygonCount: number;
  };
  blockedCellCounts?: {
    totalCells: number;
    landCells: number;
    shallowCells: number;
    obstacleCells: number;
    restrictionCells: number;
  };
  routingParams: Record<string, unknown>;
  /** Polygon WKT/GeoJSON for debugging. */
  polygonGeoJSON: GeoJSON.FeatureCollection;
  failedValidationChecks: ValidationFailure[];
  verifierResults: VerifierResult[];
  timingMetrics: Record<string, number>; // ms per stage
  requestHash: string;
}

export interface ValidationFailure {
  check: string;
  severity: "HIGH" | "MEDIUM" | "LOW";
  description: string;
  geometry?: GeoJSON.Geometry;
}

export interface VerifierResult {
  test: string;
  passed: boolean;
  samplesChecked: number;
  failures: number;
  details?: string;
}

// ============================================================================
// Type Guard Utilities
// ============================================================================

/** Validate that a value is a valid SeaState. */
export function isSeaState(value: unknown): value is SeaState {
  return ["calm", "moderate", "rough"].includes(value as string);
}

/** Validate that a value is a valid LoadState. */
export function isLoadState(value: unknown): value is LoadState {
  return ["light", "normal", "heavy"].includes(value as string);
}

/** Validate that a value is a valid RangeMode. */
export function isRangeMode(value: unknown): value is RangeMode {
  return ["one_way", "round_trip"].includes(value as string);
}

/** Validate that a value is a valid ConfidenceLevel. */
export function isConfidenceLevel(value: unknown): value is ConfidenceLevel {
  return ["HIGH", "MEDIUM", "LOW"].includes(value as string);
}
