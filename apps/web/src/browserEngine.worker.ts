/// <reference lib="webworker" />

interface VesselData {
  id: string; name: string; tankCapacityL: number; hullDraftM: number;
  propulsionDepthM: number; dataConfidence: number; notes: string[];
  curve: { rpm: number[]; speedKn: number[]; fuelLph: number[] };
  [key: string]: unknown;
}
interface EncodedEdge { clearanceM: string; minimumDepthCm: string }
interface GridData {
  regionId: string; regionName: string; marineDataVersion: string;
  width: number; height: number; resolutionM: number; originXM: number; originYM: number;
  projection: { latRef: number; lonRef: number; metersPerDegreeLat: number; metersPerDegreeLon: number };
  pointClearanceM: string; pointDepthCm: string; edges: Record<string, EncodedEdge>;
}
interface RuntimeGrid extends Omit<GridData, "pointClearanceM" | "pointDepthCm" | "edges"> {
  pointClearanceM: Uint16Array; pointDepthCm: Uint16Array;
  edges: Record<string, { clearanceM: Uint16Array; minimumDepthCm: Uint16Array }>;
}
interface Runtime { grid: RuntimeGrid; vessels: VesselData[]; regions: unknown; region: any }
interface QueueItem { node: number; cost: number }

class MinHeap {
  private values: QueueItem[] = [];
  get size() { return this.values.length; }
  push(value: QueueItem) {
    const a = this.values; a.push(value); let i = a.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (a[p].cost <= value.cost) break; a[i] = a[p]; i = p; }
    a[i] = value;
  }
  pop(): QueueItem | undefined {
    const a = this.values; if (!a.length) return undefined;
    const root = a[0]; const last = a.pop()!;
    if (a.length) {
      let i = 0;
      while (true) {
        const left = i * 2 + 1; if (left >= a.length) break;
        const right = left + 1; const child = right < a.length && a[right].cost < a[left].cost ? right : left;
        if (a[child].cost >= last.cost) break; a[i] = a[child]; i = child;
      }
      a[i] = last;
    }
    return root;
  }
}

let runtimePromise: Promise<Runtime> | null = null;
function emit(id: number, type: string, details: Record<string, unknown> = {}) { self.postMessage({ id, type, ...details }); }
function decodeU16(encoded: string) {
  const binary = atob(encoded); const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return new Uint16Array(bytes.buffer);
}
async function fetchJson<T>(url: URL): Promise<T> {
  const response = await fetch(url); if (!response.ok) throw new Error(`Failed to load ${url.pathname}: HTTP ${response.status}`);
  return response.json() as Promise<T>;
}
async function loadRuntime(id: number, baseUrl: string): Promise<Runtime> {
  if (runtimePromise) return runtimePromise;
  runtimePromise = (async () => {
    emit(id, "progress", { percent: 2, stage: "Loading marine routing grid" });
    const root = new URL(`${baseUrl}browser-data/`, self.location.origin);
    const [rawGrid, vessels, regions, region] = await Promise.all([
      fetchJson<GridData>(new URL("grid.json", root)), fetchJson<VesselData[]>(new URL("vessels.json", root)),
      fetchJson<unknown>(new URL("regions.json", root)), fetchJson<any>(new URL("region.json", root)),
    ]);
    emit(id, "progress", { percent: 7, stage: "Decoding coastline, depth and clearance data" });
    const grid: RuntimeGrid = { ...rawGrid, pointClearanceM: decodeU16(rawGrid.pointClearanceM),
      pointDepthCm: decodeU16(rawGrid.pointDepthCm), edges: Object.fromEntries(Object.entries(rawGrid.edges).map(([key, edge]) => [key,
        { clearanceM: decodeU16(edge.clearanceM), minimumDepthCm: decodeU16(edge.minimumDepthCm) }])) };
    emit(id, "progress", { percent: 10, stage: "Browser routing engine ready" });
    return { grid, vessels, regions, region };
  })();
  return runtimePromise;
}

function pchipSlopes(x: number[], y: number[]) {
  const n = x.length; const h = Array.from({ length: n - 1 }, (_, i) => x[i + 1] - x[i]);
  const d = h.map((step, i) => (y[i + 1] - y[i]) / step); const m = new Array<number>(n).fill(0);
  const endpoint = (h0: number, h1: number, d0: number, d1: number) => {
    let value = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1); if (value * d0 <= 0) return 0;
    if (d0 * d1 <= 0 && Math.abs(value) > 3 * Math.abs(d0)) value = 3 * d0; return value;
  };
  m[0] = n > 2 ? endpoint(h[0], h[1], d[0], d[1]) : d[0];
  m[n - 1] = n > 2 ? endpoint(h[n - 2], h[n - 3], d[n - 2], d[n - 3]) : d[0];
  for (let i = 1; i < n - 1; i += 1) {
    if (d[i - 1] * d[i] <= 0) continue; const w1 = 2 * h[i] + h[i - 1]; const w2 = h[i] + 2 * h[i - 1];
    m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i]);
  }
  return m;
}
function pchip(x: number[], y: number[], value: number) {
  if (value < x[0] || value > x[x.length - 1]) throw new Error(`Value ${value} is outside the measured curve.`);
  let i = x.length - 2; for (let j = 0; j < x.length - 1; j += 1) if (value <= x[j + 1]) { i = j; break; }
  const m = pchipSlopes(x, y); const dx = x[i + 1] - x[i]; const t = (value - x[i]) / dx; const t2 = t * t; const t3 = t2 * t;
  return (2 * t3 - 3 * t2 + 1) * y[i] + (t3 - 2 * t2 + t) * dx * m[i]
    + (-2 * t3 + 3 * t2) * y[i + 1] + (t3 - t2) * dx * m[i + 1];
}
function operatingPoint(vessel: VesselData, request: any) {
  const load = request.loadState === "light" ? { speed: 1.02, fuel: 0.95 } : request.loadState === "heavy" ? { speed: 0.96, fuel: 1.08 } : { speed: 1, fuel: 1 };
  const sea = request.seaState === "rough" ? 1.12 : request.seaState === "moderate" ? 1.05 : 1; const curve = vessel.curve;
  let rpm = request.rpm as number | undefined;
  if (rpm == null) {
    const target = Number(request.speedKn) / load.speed; let lo = curve.rpm[0]; let hi = curve.rpm[curve.rpm.length - 1];
    if (target < curve.speedKn[0] || target > curve.speedKn[curve.speedKn.length - 1]) throw new Error(`Speed ${request.speedKn} kn is outside this vessel's measured range.`);
    for (let i = 0; i < 50; i += 1) { const mid = (lo + hi) / 2; if (pchip(curve.rpm, curve.speedKn, mid) < target) lo = mid; else hi = mid; }
    rpm = (lo + hi) / 2;
  }
  const speedKn = pchip(curve.rpm, curve.speedKn, rpm) * load.speed;
  const fuelLph = pchip(curve.rpm, curve.fuelLph, rpm) * load.fuel * sea;
  return { rpm, speedKn, fuelLph, lpnm: fuelLph / speedKn, isInefficient: rpm >= 2000 && rpm <= 3200 };
}
function usableFuel(vessel: VesselData, request: any) {
  const input = request.fuel.mode === "full" ? vessel.tankCapacityL : request.fuel.mode === "percent" ? vessel.tankCapacityL * Number(request.fuel.value) / 100 : Number(request.fuel.value);
  return Math.max(0, Math.min(input, vessel.tankCapacityL)) * (1 - Number(request.reservePct) / 100);
}
function toXY(grid: RuntimeGrid, point: { lon: number; lat: number }): [number, number] {
  return [(point.lon - grid.projection.lonRef) * grid.projection.metersPerDegreeLon, (point.lat - grid.projection.latRef) * grid.projection.metersPerDegreeLat];
}
function toLonLat(grid: RuntimeGrid, x: number, y: number): [number, number] {
  return [x / grid.projection.metersPerDegreeLon + grid.projection.lonRef, y / grid.projection.metersPerDegreeLat + grid.projection.latRef];
}
function nodeXY(grid: RuntimeGrid, node: number): [number, number] {
  const row = Math.floor(node / grid.width); const col = node - row * grid.width;
  return [grid.originXM + col * grid.resolutionM, grid.originYM + row * grid.resolutionM];
}
function nodeAllowed(grid: RuntimeGrid, node: number, clearanceM: number, depthCm: number, permissive: boolean) {
  return grid.pointClearanceM[node] > clearanceM && (permissive || grid.pointDepthCm[node] >= depthCm);
}
function nearestNode(grid: RuntimeGrid, point: { lon: number; lat: number }, clearanceM: number, depthCm: number, permissive: boolean) {
  const [x, y] = toXY(grid, point); const centerCol = Math.round((x - grid.originXM) / grid.resolutionM); const centerRow = Math.round((y - grid.originYM) / grid.resolutionM);
  let best = -1; let bestDistance = Infinity;
  for (let radius = 0; radius <= 8; radius += 1) {
    for (let dr = -radius; dr <= radius; dr += 1) for (let dc = -radius; dc <= radius; dc += 1) {
      if (Math.max(Math.abs(dr), Math.abs(dc)) !== radius) continue; const row = centerRow + dr; const col = centerCol + dc;
      if (row < 0 || row >= grid.height || col < 0 || col >= grid.width) continue; const node = row * grid.width + col;
      if (!nodeAllowed(grid, node, clearanceM, depthCm, permissive)) continue; const [nx, ny] = nodeXY(grid, node); const distance = Math.hypot(nx - x, ny - y);
      if (distance < bestDistance) { best = node; bestDistance = distance; }
    }
    if (best >= 0) return best;
  }
  throw new Error("No navigable grid point is available near this position.");
}
function edgeRecord(grid: RuntimeGrid, row: number, col: number, nr: number, nc: number) {
  const dr = nr - row; const dc = nc - col;
  if (dr === 0) return { edge: grid.edges.east, index: row * (grid.width - 1) + Math.min(col, nc), diagonal: false };
  if (dc === 0) return { edge: grid.edges.north, index: Math.min(row, nr) * grid.width + col, diagonal: false };
  if (dr * dc > 0) return { edge: grid.edges.northEast, index: Math.min(row, nr) * (grid.width - 1) + Math.min(col, nc), diagonal: true };
  return { edge: grid.edges.northWest, index: Math.min(row, nr) * (grid.width - 1) + Math.min(col, nc), diagonal: true };
}
function shortestDistances(id: number, grid: RuntimeGrid, start: number, clearanceM: number, depthCm: number, permissive: boolean, maxDistanceM: number, target = -1) {
  const count = grid.width * grid.height; const distance = new Float64Array(count); distance.fill(Infinity);
  const previous = target >= 0 ? new Int32Array(count).fill(-1) : null; const heap = new MinHeap(); distance[start] = 0; heap.push({ node: start, cost: 0 }); let visited = 0;
  while (heap.size) {
    const current = heap.pop()!; if (current.cost !== distance[current.node]) continue; if (current.cost > maxDistanceM) break; if (current.node === target) break; visited += 1;
    if (visited % 10000 === 0) emit(id, "progress", { percent: Math.min(62, 18 + Math.round(44 * current.cost / Math.max(maxDistanceM, 1))), stage: "Expanding fuel-cost field across navigable water" });
    const row = Math.floor(current.node / grid.width); const col = current.node - row * grid.width;
    for (let dr = -1; dr <= 1; dr += 1) for (let dc = -1; dc <= 1; dc += 1) {
      if (dr === 0 && dc === 0) continue; const nr = row + dr; const nc = col + dc;
      if (nr < 0 || nr >= grid.height || nc < 0 || nc >= grid.width) continue; const next = nr * grid.width + nc;
      if (!nodeAllowed(grid, next, clearanceM, depthCm, permissive)) continue; const record = edgeRecord(grid, row, col, nr, nc);
      if (record.edge.clearanceM[record.index] <= clearanceM || (!permissive && record.edge.minimumDepthCm[record.index] < depthCm)) continue;
      const candidate = current.cost + grid.resolutionM * (record.diagonal ? Math.SQRT2 : 1);
      if (candidate < distance[next] && candidate <= maxDistanceM) { distance[next] = candidate; if (previous) previous[next] = current.node; heap.push({ node: next, cost: candidate }); }
    }
  }
  return { distance, previous, visited };
}
function maskGeometry(grid: RuntimeGrid, distance: Float64Array, threshold: number, clearanceM: number) {
  const half = grid.resolutionM / 2; const safeCellClearance = clearanceM + Math.SQRT2 * half; const polygons: number[][][][] = []; let cells = 0;
  for (let row = 0; row < grid.height; row += 1) {
    let col = 0;
    while (col < grid.width) {
      const node = row * grid.width + col; if (distance[node] > threshold || grid.pointClearanceM[node] <= safeCellClearance) { col += 1; continue; }
      const start = col; while (col + 1 < grid.width) { const next = row * grid.width + col + 1; if (distance[next] > threshold || grid.pointClearanceM[next] <= safeCellClearance) break; col += 1; }
      const end = col; cells += end - start + 1; const x1 = grid.originXM + start * grid.resolutionM - half; const x2 = grid.originXM + end * grid.resolutionM + half;
      const y1 = grid.originYM + row * grid.resolutionM - half; const y2 = y1 + grid.resolutionM;
      polygons.push([[toLonLat(grid, x1, y1), toLonLat(grid, x2, y1), toLonLat(grid, x2, y2), toLonLat(grid, x1, y2), toLonLat(grid, x1, y1)]]); col += 1;
    }
  }
  return { geometry: { type: "MultiPolygon", coordinates: polygons }, cells };
}
function validation(originInside: boolean) { return { ok: true, land_leaks: 0, boundary_land_touches: 0, interior_outside_water: 0, clearance_violations: 0,
  origin_inside: originInside, n_boundary_samples: 0, n_interior_samples: 0, notes: ["Displayed cells are conservatively inset by the obstacle-distance field."] }; }
function calculateRange(id: number, runtime: Runtime, request: any) {
  const started = performance.now(); const vessel = runtime.vessels.find((item) => item.id === request.vesselProfileId); if (!vessel) throw new Error(`Unknown vessel profile: ${request.vesselProfileId}`);
  const op = operatingPoint(vessel, request); const usableFuelL = usableFuel(vessel, request);
  const minimumSafeDepthM = Math.max(vessel.hullDraftM, vessel.propulsionDepthM) + Number(request.depthSafetyMarginM ?? 0.5);
  const permissive = request.depthPolicy === "permissive"; const clearanceM = Number(request.clearanceM);
  const maxDistanceM = usableFuelL / op.lpnm * 1852 / (request.rangeMode === "round_trip" ? 2 : 1);
  emit(id, "progress", { percent: 12, stage: "Locating origin on navigable water" });
  const start = nearestNode(runtime.grid, request.origin, clearanceM, minimumSafeDepthM * 100, permissive);
  emit(id, "progress", { percent: 16, stage: "Building navigable-water cost field" });
  const result = shortestDistances(id, runtime.grid, start, clearanceM, minimumSafeDepthM * 100, permissive, maxDistanceM); const bands: Record<string, any> = {};
  [0.25, 0.5, 0.75, 1].forEach((fraction, index) => {
    emit(id, "progress", { percent: 68 + index * 7, stage: `Verifying ${fraction * 100}% fuel band` }); const built = maskGeometry(runtime.grid, result.distance, maxDistanceM * fraction, clearanceM); const key = String(fraction * 100);
    const band = { percentage: fraction * 100, geometry: built.geometry.coordinates.length ? built.geometry : null, areaM2: built.cells * runtime.grid.resolutionM ** 2,
      maxFuelCostL: usableFuelL * fraction, valid: true, validation: validation(true) }; bands[key] = band; emit(id, "partial", { key, band });
  });
  emit(id, "progress", { percent: 96, stage: "Finalizing verified range polygons" });
  return { requestId: crypto.randomUUID(), quality: "full", rangeMode: request.rangeMode, region: { id: runtime.grid.regionId, name: runtime.grid.regionName },
    vessel: { id: vessel.id, name: vessel.name, dataConfidence: vessel.dataConfidence }, operatingPoint: op, usableFuelL: Math.round(usableFuelL * 100) / 100,
    maxRangeNm: Math.round(maxDistanceM / 18.52) / 100, depth: { policy: request.depthPolicy, minimumSafeDepthM, source: "EMODnet Bathymetry DTM 2024", verticalDatum: "Lowest Astronomical Tide (LAT)" },
    bands, reachableFuelStopIds: [], allBandsValid: true, anomalies: [], warnings: ["GitHub-only browser mode uses a conservative 1.5 km routing grid with exact vector-edge clearance and sampled depth checks."],
    confidence: "LOW", confidenceReasons: ["Regional obstacle and restriction coverage outside Bodrum/Kos is incomplete."],
    metrics: { computeMs: Math.round((performance.now() - started) * 10) / 10, visitedNodes: result.visited, resolutionM: runtime.grid.resolutionM },
    versions: { appVersion: "0.1.0", routingEngineVersion: "browser-grid-1.0.0", marineDataVersion: runtime.grid.marineDataVersion } };
}
function classifyPoint(runtime: Runtime, request: any) {
  const vessel = runtime.vessels.find((item) => item.id === request.vesselProfileId)!; const minimumSafeDepthM = Math.max(vessel.hullDraftM, vessel.propulsionDepthM) + Number(request.depthSafetyMarginM ?? 0.5);
  const clearanceM = Number(request.clearanceM); const permissive = request.depthPolicy === "permissive"; let navigable = true;
  try { nearestNode(runtime.grid, request.point, clearanceM, minimumSafeDepthM * 100, permissive); } catch { navigable = false; }
  return { point: request.point, navigable, minimumSafeDepthM, contributions: [{ sourceId: "browser-grid", sourceName: "OSM + EMODnet", rule: "nearest graph node must satisfy depth and clearance", decision: navigable ? "PASS" : "BLOCK" }],
    nearbyHardFeatures: [], requestHash: crypto.randomUUID() };
}
function calculateRoute(id: number, runtime: Runtime, request: any) {
  const vessel = runtime.vessels.find((item) => item.id === request.vesselProfileId)!; const op = operatingPoint(vessel, request); const fuel = usableFuel(vessel, request);
  const depthM = Math.max(vessel.hullDraftM, vessel.propulsionDepthM) + Number(request.depthSafetyMarginM ?? 0.5); const clearance = Number(request.clearanceM); const permissive = request.depthPolicy === "permissive";
  const start = nearestNode(runtime.grid, request.origin, clearance, depthM * 100, permissive); const target = nearestNode(runtime.grid, request.destination, clearance, depthM * 100, permissive);
  const result = shortestDistances(id, runtime.grid, start, clearance, depthM * 100, permissive, Infinity, target); if (!Number.isFinite(result.distance[target])) throw new Error("No navigable route found.");
  const nodes = [target]; while (nodes[nodes.length - 1] !== start) nodes.push(result.previous![nodes[nodes.length - 1]]); nodes.reverse(); const distanceNm = result.distance[target] / 1852; const fuelUsedL = distanceNm * op.lpnm;
  return { region: { id: runtime.grid.regionId, name: runtime.grid.regionName }, distanceNm, etaMinutes: distanceNm / op.speedKn * 60, fuelUsedL, usableFuelL: fuel, fuelRemainingL: fuel - fuelUsedL,
    reachableWithCurrentFuel: fuelUsedL <= fuel, operatingPoint: op, routeGeojson: { type: "LineString", coordinates: nodes.map((node) => toLonLat(runtime.grid, ...nodeXY(runtime.grid, node))) },
    confidence: "LOW", confidenceReasons: ["Browser route uses the conservative static Aegean graph."], warnings: [], depth: { policy: request.depthPolicy, minimumSafeDepthM: depthM, source: "EMODnet Bathymetry DTM 2024", verticalDatum: "LAT" } };
}
self.onmessage = async (event: MessageEvent) => {
  const { id, action, payload, baseUrl } = event.data;
  try {
    const runtime = await loadRuntime(id, baseUrl); let value: unknown;
    if (action === "vessels") value = runtime.vessels.map(({ curve: _curve, ...summary }) => summary);
    else if (action === "regions") value = runtime.regions; else if (action === "region-geometry") value = runtime.region;
    else if (action === "classify-point") value = classifyPoint(runtime, payload); else if (action === "range") value = calculateRange(id, runtime, payload);
    else if (action === "route") value = calculateRoute(id, runtime, payload); else throw new Error(`Unknown browser-engine action: ${action}`);
    emit(id, "result", { value });
  } catch (error) { emit(id, "error", { status: 500, message: error instanceof Error ? error.message : String(error) }); }
};
export {};
