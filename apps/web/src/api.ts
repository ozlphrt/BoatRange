import type { ApiErrorBody, DepthPolicy, FuelMode, LatLon, LoadState, RangeJob, RangeMode, RangeResponse, RegionGeometry, RegionSummary, RouteResponse, SeaState, VesselSummary } from "./types";
import { browserCall, browserPointDiagnostic, getBrowserRangeJob, startBrowserRangeJob } from "./browserEngine";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const USE_BROWSER_ENGINE = import.meta.env.PROD && !API_BASE_URL;

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

export class ApiError extends Error {
  status: number;
  body: ApiErrorBody | null;
  constructor(status: number, body: ApiErrorBody | null) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message ?? `HTTP ${status}`;
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  const res = await fetch(apiUrl(path), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = await res.json();
    } catch {
      // ignore
    }
    throw new ApiError(res.status, body);
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(apiUrl(path));
  if (!res.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = await res.json();
    } catch {
      // ignore
    }
    throw new ApiError(res.status, body);
  }
  return res.json() as Promise<T>;
}

export interface RangeRequestPayload {
  origin: LatLon;
  vesselProfileId?: string;
  fuel: { mode: FuelMode; value: number };
  reservePct: number;
  speedKn?: number;
  rpm?: number;
  seaState: SeaState;
  loadState: LoadState;
  clearanceM: number;
  rangeMode: RangeMode;
  depthPolicy?: DepthPolicy;
  depthSafetyMarginM?: number;
  developerMode?: boolean;
}

export function fetchVessels(): Promise<VesselSummary[]> {
  if (USE_BROWSER_ENGINE) return browserCall<VesselSummary[]>("vessels");
  return get<VesselSummary[]>("/api/v1/vessels");
}

export function fetchRegions(): Promise<RegionSummary[]> {
  if (USE_BROWSER_ENGINE) return browserCall<RegionSummary[]>("regions");
  return get<RegionSummary[]>("/api/v1/regions");
}

export function fetchRegionGeometry(regionId: string): Promise<RegionGeometry> {
  if (USE_BROWSER_ENGINE) return browserCall<RegionGeometry>("region-geometry", { regionId });
  return get<RegionGeometry>(`/api/v1/regions/${regionId}/geometry`);
}

export function fetchRangePreview(payload: RangeRequestPayload): Promise<RangeResponse> {
  if (USE_BROWSER_ENGINE) return browserCall<RangeResponse>("range", payload);
  return post<RangeResponse>("/api/v1/range/preview", payload);
}

export function fetchRangeFull(payload: RangeRequestPayload): Promise<RangeResponse> {
  if (USE_BROWSER_ENGINE) return browserCall<RangeResponse>("range", payload);
  return post<RangeResponse>("/api/v1/range/full", payload);
}

export function startRangeFullJob(payload: RangeRequestPayload): Promise<RangeJob> {
  if (USE_BROWSER_ENGINE) return startBrowserRangeJob(payload);
  return post<RangeJob>("/api/v1/range/full/jobs", payload);
}

export function fetchRangeFullJob(jobId: string): Promise<RangeJob> {
  if (USE_BROWSER_ENGINE) return getBrowserRangeJob(jobId);
  return get<RangeJob>(`/api/v1/range/full/jobs/${jobId}`);
}

export function fetchRoute(
  payload: RangeRequestPayload & { destination: LatLon }
): Promise<RouteResponse> {
  if (USE_BROWSER_ENGINE) return browserCall<RouteResponse>("route", payload);
  return post<RouteResponse>("/api/v1/route", payload);
}

export interface PointDiagnosticResponse {
  point: LatLon;
  navigable: boolean;
  minimumSafeDepthM: number;
  contributions: Array<Record<string, unknown>>;
  nearbyHardFeatures: Array<Record<string, unknown>>;
  requestHash: string;
}

export function fetchPointDiagnostic(
  payload: RangeRequestPayload & { point: LatLon }
): Promise<PointDiagnosticResponse> {
  if (USE_BROWSER_ENGINE) return browserPointDiagnostic(payload);
  return post<PointDiagnosticResponse>("/api/v1/diagnostics/classify-point", payload);
}
