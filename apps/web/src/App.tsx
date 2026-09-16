import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, fetchPointDiagnostic, fetchRangeFullJob, fetchRegionGeometry, fetchRegions, fetchRoute, fetchVessels, startRangeFullJob, type PointDiagnosticResponse } from "./api";
import { ControlPanel } from "./components/ControlPanel";
import { MapView, type ClickMode } from "./components/MapView";
import { SidePanel } from "./components/SidePanel";
import type { Band, FuelMode, LoadState, RangeMode, RangeResponse, RegionGeometry, RouteResponse, SeaState, VesselSummary } from "./types";

// Port Iasos Marina entrance. The published marina position is on the shore;
// this nearby point is the closest location that passes the conservative
// coastline, depth (>= 1.35 m), and 50 m clearance checks.
const DEFAULT_ORIGIN = { lat: 37.24587, lon: 27.53894 };
const DEFAULT_VESSEL_ID = "axopar-28-2019-verado-300";

export default function App() {
  const [region, setRegion] = useState<RegionGeometry | null>(null);
  const [regionError, setRegionError] = useState<string | null>(null);

  const [vessels, setVessels] = useState<VesselSummary[]>([]);
  const [vesselId, setVesselId] = useState(DEFAULT_VESSEL_ID);
  const vessel = vessels.find((v) => v.id === vesselId) ?? null;

  const [origin, setOrigin] = useState(DEFAULT_ORIGIN);
  const [destination, setDestination] = useState<{ lat: number; lon: number } | null>(null);
  const [clickMode, setClickMode] = useState<ClickMode>("idle");
  const [setupOpen, setSetupOpen] = useState(false);
  const [routeOpen, setRouteOpen] = useState(false);
  const [resultsOpen, setResultsOpen] = useState(false);
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [mapKeyOpen, setMapKeyOpen] = useState(false);

  const [speedKn, setSpeedKn] = useState(20);
  const [fuelMode, setFuelMode] = useState<FuelMode>("percent");
  const [fuelValue, setFuelValue] = useState(100);
  const [reservePct, setReservePct] = useState(20);
  const [seaState, setSeaState] = useState<SeaState>("calm");
  const [loadState, setLoadState] = useState<LoadState>("normal");
  const [clearanceM, setClearanceM] = useState(50);
  const [rangeMode, setRangeMode] = useState<RangeMode>("one_way");
  const [developerMode, setDeveloperMode] = useState(false);
  const [pointDiagnostic, setPointDiagnostic] = useState<PointDiagnosticResponse | null>(null);

  const [rangeResult, setRangeResult] = useState<RangeResponse | null>(null);
  const [partialBands, setPartialBands] = useState<Record<string, Band>>({});
  const [routeResult, setRouteResult] = useState<RouteResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [calculationProgress, setCalculationProgress] = useState({ percent: 0, stage: "Preparing calculation" });
  const [originValidation, setOriginValidation] = useState<"valid" | "validating" | "invalid">("valid");
  const [originError, setOriginError] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [routeError, setRouteError] = useState<string | null>(null);

  // Load the vessel catalog once. The region basemap itself is loaded
  // below, keyed off whichever region the backend actually resolves the
  // current origin to (see the rangeResult effect) — a hardcoded region id
  // here would silently render the wrong basemap under a route/range that
  // was computed against a *different* region's coastline (looks like a
  // route crossing land that isn't actually there, or missing land that is).
  useEffect(() => {
    fetchVessels()
      .then(setVessels)
      .catch(() => {
        /* vessel selector just stays empty; range requests still work with the default id */
      });
  }, []);

  // Paint the coastline immediately instead of waiting for the first full
  // range calculation. The registry is ordered most-specific-first, matching
  // backend region resolution, so overlapping Bodrum/Kos coverage stays exact.
  useEffect(() => {
    let cancelled = false;
    fetchRegions()
      .then((regions) => regions.find((item) => {
        const [west, south, east, north] = item.bboxWgs84;
        return origin.lon >= west && origin.lon <= east && origin.lat >= south && origin.lat <= north;
      }))
      .then((match) => match && match.id !== region?.id ? fetchRegionGeometry(match.id) : null)
      .then((geometry) => {
        if (!cancelled && geometry) setRegion(geometry);
      })
      .catch((e) => {
        if (!cancelled) setRegionError(e instanceof Error ? e.message : String(e));
      });
    return () => { cancelled = true; };
  }, [origin, region]);

  // Keep the displayed basemap in sync with whichever region the backend
  // resolved the current origin to.
  useEffect(() => {
    const resolvedId = rangeResult?.region.id;
    if (!resolvedId || resolvedId === region?.id) return;
    fetchRegionGeometry(resolvedId)
      .then(setRegion)
      .catch((e) => setRegionError(e instanceof Error ? e.message : String(e)));
  }, [rangeResult?.region.id, region?.id]);

  // A vessel switch can change the valid speed range entirely (the Dusky
  // 233's curve only covers ~25-45 kn) — clamp instead of sending a request
  // guaranteed to 422.
  useEffect(() => {
    if (!vessel) return;
    setSpeedKn((current) => Math.min(Math.max(current, vessel.minSpeedKn), vessel.maxSpeedKn));
  }, [vessel]);

  const requestPayload = useMemo(
    () => ({
      origin,
      vesselProfileId: vesselId,
      fuel: { mode: fuelMode, value: fuelValue },
      reservePct,
      speedKn,
      seaState,
      loadState,
      clearanceM,
      rangeMode,
      depthPolicy: "conservative" as const,
      depthSafetyMarginM: 0.5,
      developerMode,
    }),
    [origin, vesselId, fuelMode, fuelValue, reservePct, speedKn, seaState, loadState, clearanceM, rangeMode, developerMode]
  );

  // Range work is explicit: opening the app or changing a control never starts
  // a large calculation. Parameter changes invalidate any previous result and
  // cancel its response by advancing the sequence number.
  const requestSeq = useRef(0);
  useEffect(() => {
    requestSeq.current += 1;
    setRangeResult(null);
    setPartialBands({});
    setErrorMessage(null);
    setLoading(false);
    setCalculationProgress({ percent: 0, stage: "Preparing calculation" });
  }, [requestPayload]);

  async function calculateRange() {
    if (originValidation !== "valid" || loading) return;
    const mySeq = ++requestSeq.current;
    setLoading(true);
    setCalculationProgress({ percent: 0, stage: "Starting detailed calculation" });
    setRangeResult(null);
    setPartialBands({});
    setErrorMessage(null);
    setResultsOpen(true);
    try {
      const started = await startRangeFullJob(requestPayload);
      let job = started;
      while (job.status === "queued" || job.status === "running") {
        if (requestSeq.current !== mySeq) return;
        setCalculationProgress({ percent: job.progress, stage: job.stage });
        if (job.partialBands) setPartialBands(job.partialBands);
        await new Promise((resolve) => window.setTimeout(resolve, 750));
        job = await fetchRangeFullJob(started.jobId);
      }
      if (job.status === "failed") {
        throw new Error(job.error || "Detailed range calculation failed.");
      }
      const full = job.result;
      if (!full) throw new Error("Detailed range calculation completed without a result.");
      if (requestSeq.current === mySeq) {
        setCalculationProgress({ percent: 100, stage: "Range calculation complete" });
        const renderableBands = Object.values(full.bands).filter((band) => band.geometry);
        if (renderableBands.length === 0) {
          setPartialBands({});
          setErrorMessage("No verified range bands could be generated for these settings.");
        } else {
          setRangeResult(full);
          setPartialBands({});
        }
      }
    } catch (e) {
      if (requestSeq.current !== mySeq) return;
      setRangeResult(null);
      setPartialBands({});
      setErrorMessage(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (requestSeq.current === mySeq) setLoading(false);
    }
  }

  async function validateAndSetOrigin(lat: number, lon: number): Promise<boolean> {
    const candidate = { lat, lon };
    setOriginValidation("validating");
    setOriginError(null);
    try {
      const diagnostic = await fetchPointDiagnostic({
        ...requestPayload,
        origin: candidate,
        point: candidate,
        developerMode: true,
      });
      if (!diagnostic.navigable) {
        // The candidate is rejected and the marker snaps back, so the active
        // origin remains valid and calculation should stay available.
        setOriginValidation("valid");
        setOriginError("That position is blocked, too shallow, or inside the safety clearance. The marker was returned to the last navigable position.");
        return false;
      }
      setOrigin(candidate);
      setDestination(null);
      setRouteResult(null);
      setClickMode("idle");
      setOriginValidation("valid");
      return true;
    } catch (e) {
      setOriginValidation("valid");
      setOriginError(e instanceof ApiError ? e.message : String(e));
      return false;
    }
  }

  function handleMapClick(lat: number, lon: number) {
    if (clickMode === "set-origin") {
      void validateAndSetOrigin(lat, lon);
    } else if (clickMode === "set-destination") {
      setDestination({ lat, lon });
      setClickMode("idle");
    } else if (clickMode === "inspect") {
      fetchPointDiagnostic({ ...requestPayload, developerMode: true, point: { lat, lon } })
        .then(setPointDiagnostic)
        .catch((e) => setErrorMessage(e instanceof ApiError ? e.message : String(e)));
    }
  }

  // Fire the route request whenever a destination is chosen (or the
  // controls affecting it change while one is set).
  const [routeLoading, setRouteLoading] = useState(false);
  const routeSeq = useRef(0);
  useEffect(() => {
    if (!destination) {
      setRouteResult(null);
      setRouteLoading(false);
      return;
    }
    const mySeq = ++routeSeq.current;
    setRouteError(null);
    setRouteLoading(true);
    fetchRoute({ ...requestPayload, destination })
      .then((res) => {
        if (routeSeq.current === mySeq) setRouteResult(res);
      })
      .catch((e) => {
        if (routeSeq.current !== mySeq) return;
        setRouteResult(null);
        setRouteError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => {
        if (routeSeq.current === mySeq) setRouteLoading(false);
      });
  }, [destination, requestPayload]);

  const bands: Band[] = rangeResult ? Object.values(rangeResult.bands) : Object.values(partialBands);
  const routeLine = routeResult?.routeGeojson.coordinates ?? null;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="eyebrow">Marine planning &middot; water-constrained range</span>
          <h1>Range Planner</h1>
          <p>Plan safe water access around your available fuel.</p>
        </div>

        <SidePanel id="setup" kicker="01 · Setup" title="Boat & origin" open={setupOpen} onOpenChange={setSetupOpen}>
          <div className="field-group">
          <span className="field-label">Boat</span>
          <select value={vesselId} onChange={(e) => setVesselId(e.target.value)}>
            {vessels.length === 0 && <option value={vesselId}>Loading…</option>}
            {vessels.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name} ({v.manufacturer})
              </option>
            ))}
          </select>
          {vessel && (
            <>
              <span className="field-row" style={{ fontSize: "0.72rem", color: "var(--ink-dim)" }}>
                {vessel.engineCount}× {vessel.propulsionType} &middot; {vessel.tankCapacityL.toFixed(0)} L tank &middot; {Math.round(vessel.dataConfidence * 100)}% data confidence
              </span>
              {vessel.dataConfidence < 0.5 && (
                <div className="banner warning">
                  This boat's fuel curve is based on sparse or low-confidence data:
                  <ul style={{ margin: "0.3rem 0 0", paddingLeft: "1.1rem" }}>
                    {vessel.notes.map((n, i) => (
                      <li key={i}>{n}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
          </div>

          <div className="field-group compact-divider">
          <span className="field-label">Origin</span>
          <strong className="origin-name">Port Iasos Marina</strong>
          <button
            type="button"
            className={`primary-btn ${clickMode === "set-origin" ? "active" : ""}`}
            onClick={() => setClickMode(clickMode === "set-origin" ? "idle" : "set-origin")}
          >
            {clickMode === "set-origin" ? "Select navigable water…" : "Place marker on map"}
          </button>
          <span className="field-row helper-text">Drag the marker directly. Invalid drops snap back.</span>
          <span className="field-row" style={{ fontSize: "0.72rem", color: "var(--ink-dim)" }}>
            {origin.lat.toFixed(4)}, {origin.lon.toFixed(4)}
          </span>
          {originValidation === "validating" && <div className="banner info">Checking navigability…</div>}
          {originError && <div className="banner warning">{originError}</div>}
          </div>
        </SidePanel>

        <ControlPanel
          speedKn={speedKn}
          onSpeedKnChange={setSpeedKn}
          rpm={rangeResult?.operatingPoint.rpm ?? null}
          fuelMode={fuelMode}
          fuelValue={fuelValue}
          onFuelChange={(mode, value) => {
            setFuelMode(mode);
            setFuelValue(value);
          }}
          reservePct={reservePct}
          onReservePctChange={setReservePct}
          seaState={seaState}
          onSeaStateChange={setSeaState}
          loadState={loadState}
          onLoadStateChange={setLoadState}
          clearanceM={clearanceM}
          onClearanceMChange={setClearanceM}
          rangeMode={rangeMode}
          onRangeModeChange={setRangeMode}
          tankCapacityL={vessel?.tankCapacityL ?? 100}
          speedMinKn={vessel?.minSpeedKn ?? 5}
          speedMaxKn={vessel?.maxSpeedKn ?? 35}
          developerMode={developerMode}
          onDeveloperModeChange={setDeveloperMode}
        />

        <div className="calculate-card">
          <button
            type="button"
            className="calculate-btn"
            onClick={() => void calculateRange()}
            disabled={loading || originValidation !== "valid"}
          >
            {loading ? "Calculating range…" : "Calculate range"}
          </button>
          <span>Uses the current marker and planning settings.</span>
        </div>

        {developerMode && (
          <SidePanel id="diagnostics" kicker="Advanced" title="Diagnostics" open={diagnosticsOpen} onOpenChange={setDiagnosticsOpen}>
            {rangeResult?.diagnostics && (
              <div className="field-group">
                <span className="field-label">Diagnostic package</span>
                <button
                  type="button"
                  className="primary-btn"
                  onClick={() => {
                    const blob = new Blob([JSON.stringify(rangeResult.diagnostics, null, 2)], { type: "application/json" });
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = `axopar-diagnostic-${rangeResult.diagnostics?.requestHash ?? "snapshot"}.json`;
                    link.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  Download diagnostic JSON
                </button>
                <span className="field-row helper-text" style={{ overflowWrap: "anywhere" }}>
                  Request hash: {rangeResult.diagnostics.requestHash}
                </span>
              </div>
            )}
            <div className="field-group compact-divider">
            <span className="field-label">Inspect cell / point</span>
            <button
              type="button"
              className={`primary-btn ${clickMode === "inspect" ? "active" : ""}`}
              onClick={() => setClickMode(clickMode === "inspect" ? "idle" : "inspect")}
            >
              {clickMode === "inspect" ? "Select a point on the map…" : "Inspect map point"}
            </button>
            {pointDiagnostic && (
              <div className={`banner ${pointDiagnostic.navigable ? "info" : "warning"}`}>
                <strong>{pointDiagnostic.navigable ? "NAVIGABLE" : "BLOCKED"}</strong>
                {pointDiagnostic.contributions.map((item, index) => (
                  <div key={index} style={{ marginTop: "0.35rem", fontSize: "0.7rem" }}>
                    {String(item.sourceName ?? item.sourceId)}: {String(item.decision)} · {String(item.rule ?? item.classificationRule)}
                  </div>
                ))}
              </div>
            )}
            </div>
          </SidePanel>
        )}

        <SidePanel id="route" kicker="04 · Route" title="Point route" open={routeOpen} onOpenChange={setRouteOpen}>
          <div className="field-group">
          <button
            type="button"
            className={`primary-btn ${clickMode === "set-destination" ? "active" : ""}`}
            onClick={() => setClickMode(clickMode === "set-destination" ? "idle" : "set-destination")}
          >
            {clickMode === "set-destination" ? "Select a destination on the map…" : "Choose destination"}
          </button>
          {destination && (
            <button type="button" className="primary-btn" onClick={() => setDestination(null)}>
              Clear route
            </button>
          )}
          </div>
        </SidePanel>

        <SidePanel id="results" kicker="05 · Output" title="Range summary" open={resultsOpen} onOpenChange={setResultsOpen}>
          {rangeResult && (
            <div className="stat-grid">
            <div className="stat-tile">
              <span className="k">Usable fuel</span>
              <span className="v">{rangeResult.usableFuelL.toFixed(0)} L</span>
            </div>
            <div className="stat-tile">
              <span className="k">Theoretical range</span>
              <span className="v">{rangeResult.maxRangeNm.toFixed(1)} nm</span>
            </div>
            <div className="stat-tile">
              <span className="k">L/nm</span>
              <span className="v">{rangeResult.operatingPoint.lpnm.toFixed(2)}</span>
            </div>
            <div className="stat-tile">
              <span className="k">Quality</span>
              <span className="v">{rangeResult.quality === "full" ? "Full" : "Preview"}</span>
            </div>
            <div className="stat-tile">
              <span className="k">Minimum safe depth</span>
              <span className="v">{rangeResult.depth.minimumSafeDepthM.toFixed(2)} m</span>
            </div>
            <div className="stat-tile">
              <span className="k">Depth policy</span>
              <span className="v">
                {rangeResult.depth.policy === "conservative" ? "Conservative" : "Developer"}
              </span>
            </div>
            <div className="stat-tile">
              <span className="k">Fuel stops in range</span>
              <span className="v">{rangeResult.reachableFuelStopIds.length}</span>
            </div>
            </div>
          )}

          {rangeResult?.depth.source && (
            <div className="banner info">
              Depth: {rangeResult.depth.source} · {rangeResult.depth.verticalDatum}
            </div>
          )}

          {loading && <div className="banner info">Calculating…</div>}
          {errorMessage && <div className="banner warning">{errorMessage}</div>}
          {rangeResult?.operatingPoint.isInefficient && (
            <div className="banner warning">
              The selected RPM is in the inefficient region (~2000-3200 RPM) and is not recommended for cruising.
            </div>
          )}
          {rangeResult && !rangeResult.allBandsValid && (
            <div className="banner warning">
              One or more bands failed independent verification; treat this result as unreliable.
            </div>
          )}
        </SidePanel>

        <SidePanel id="map-key" kicker="Map" title="Map key" open={mapKeyOpen} onOpenChange={setMapKeyOpen} bodyClassName="legend-list">
          {bands
            .slice()
            .sort((a, b) => a.percentage - b.percentage)
            .map((b) => (
              <div className="legend-row" key={b.percentage}>
                <span
                  className="legend-swatch"
                  style={{ background: `var(--band-${b.percentage})` }}
                />
                {b.percentage}% fuel
              </div>
            ))}
          {region && region.marinePoiCount > 0 && (
            <>
              <div className="legend-row">
                <span className="legend-swatch" style={{ background: "#117c8a" }} />
                Marina / harbour
              </div>
              <div className="legend-row">
                <span className="legend-swatch" style={{ background: "#f29f05" }} />
                Marine fuel point (halo: inside verified range)
              </div>
            </>
          )}
        </SidePanel>
      </aside>

      <main className="map-pane">
        {region ? (
          <MapView
            region={region}
            bands={bands}
            origin={origin}
            destination={destination}
            routeLine={routeLine}
            clickMode={clickMode}
            onMapClick={handleMapClick}
            onOriginDrop={validateAndSetOrigin}
            developerMode={developerMode}
            diagnostics={rangeResult?.diagnostics ?? null}
            reachableFuelStopIds={rangeResult?.reachableFuelStopIds ?? []}
          />
        ) : (
          <div style={{ padding: "2rem", color: "var(--ink-dim)" }}>
            {regionError ? `Region failed to load: ${regionError}` : "Loading map…"}
          </div>
        )}

        {clickMode !== "idle" && (
          <div className="map-hint">
            {clickMode === "set-origin"
              ? "Click the map to set a new origin."
              : clickMode === "set-destination"
                ? "Click the map to choose a destination."
                : "Click the map to inspect classification details."}
          </div>
        )}

        {(loading || routeLoading) && (
          <div className="compute-overlay">
            <div className="compute-overlay__box">
              <span className="spinner" aria-hidden="true" />
              <span>{routeLoading ? "Calculating route…" : `${calculationProgress.percent}% · ${calculationProgress.stage}`}</span>
              {!routeLoading && (
                <div className="progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={calculationProgress.percent}>
                  <span style={{ width: `${calculationProgress.percent}%` }} />
                </div>
              )}
              <span className="compute-overlay__hint">
                {routeLoading ? "Finding a collision-free water route." : "You can continue panning and zooming while this runs."}
              </span>
            </div>
          </div>
        )}

        {routeResult && (
          <div className="route-panel">
            <h3>Route details</h3>
            <div className="stat-grid">
              <div className="stat-tile">
                <span className="k">Distance</span>
                <span className="v">{routeResult.distanceNm.toFixed(1)} nm</span>
              </div>
              <div className="stat-tile">
                <span className="k">Time</span>
                <span className="v">{routeResult.etaMinutes.toFixed(0)} min</span>
              </div>
              <div className="stat-tile">
                <span className="k">Fuel used</span>
                <span className="v">{routeResult.fuelUsedL.toFixed(1)} L</span>
              </div>
              <div className="stat-tile">
                <span className="k">Fuel remaining</span>
                <span className="v">{routeResult.fuelRemainingL.toFixed(1)} L</span>
              </div>
            </div>
            {!routeResult.reachableWithCurrentFuel && (
              <div className="banner warning" style={{ marginTop: "0.6rem" }}>
                This route is not reachable with the current fuel and reserve settings.
              </div>
            )}
          </div>
        )}
        {routeError && (
          <div className="route-panel">
            <div className="banner warning">{routeError}</div>
          </div>
        )}
      </main>
    </div>
  );
}
