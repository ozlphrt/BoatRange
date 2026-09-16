import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import { resolveBasemapStyleUrl } from "../lib/mapStyle";
import type { Band, DiagnosticSnapshot, GeoJSONGeometry, RegionGeometry } from "../types";

export type ClickMode = "set-origin" | "set-destination" | "inspect" | "idle";

interface MapViewProps {
  region: RegionGeometry;
  bands: Band[]; // sorted ascending by percentage, drawn in order (25 -> 100)
  origin: { lat: number; lon: number } | null;
  destination: { lat: number; lon: number } | null;
  routeLine: [number, number][] | null; // [lon, lat] pairs
  clickMode: ClickMode;
  onMapClick: (lat: number, lon: number) => void;
  onOriginDrop: (lat: number, lon: number) => Promise<boolean>;
  developerMode: boolean;
  diagnostics: DiagnosticSnapshot | null;
  reachableFuelStopIds: string[];
}

const BAND_COLORS: Record<number, string> = {
  25: "#1c7a58",
  50: "#3c965a",
  75: "#78af5a",
  100: "#b4c36e",
};

// The outer bands (75/100%) typically cover most of the compute box once a
// vessel's range is large relative to the region — at a uniform opacity
// that reads as a single solid green fill drowning out the basemap
// entirely, rather than a set of nested fuel thresholds. Tapering opacity
// down for the larger/outer bands keeps the map legible: 25% (the most
// fuel-conservative, "safest" band) stays the most visible, 100% becomes
// just a soft tint over the basemap.
const BAND_OPACITY: Record<number, number> = {
  25: 0.5,
  50: 0.38,
  75: 0.26,
  100: 0.14,
};

// "axopar-" prefix: a real licensed basemap style (MapTiler's "ocean", for
// one) already defines its own sources/layers named "land", "route", etc.
// addSource() with a colliding id either throws or silently hands back the
// STYLE's own source on a later getSource() call — which for e.g. a raster
// terrain-shading source has no .setData(), so every update to our overlay
// failed with "setData is not a function" and nothing ever rendered. Found
// via headless Playwright reproduction, not guesswork — see the ingest note
// in git history if this needs re-diagnosing.
const SRC_LAND = "axopar-land";
const SRC_ROUTE = "axopar-route";
const SRC_ORIGIN = "axopar-origin";
const SRC_DESTINATION = "axopar-destination";
const SRC_MARINE_FEATURES = "axopar-marine-features";
const SRC_MARINE_POIS = "axopar-marine-pois";
const SRC_SAFE_WATER = "axopar-debug-safe-water";
const SRC_CLEARANCE = "axopar-debug-clearance";
const SRC_COST_FIELD = "axopar-debug-cost-field";
const bandSourceId = (pct: number) => `axopar-band-${pct}`;

const EMPTY_FC = { type: "FeatureCollection" as const, features: [] as GeoJSON.Feature[] };

function emptyLineFC() {
  return { type: "FeatureCollection" as const, features: [] as GeoJSON.Feature[] };
}

function pointFeature(lon: number, lat: number): GeoJSON.Feature {
  return { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [lon, lat] } };
}

/** Run `fn` once our own sources/layers exist on the map. If a prop update
 * lands before that — entirely possible on first mount, since fetching
 * region geometry and setting up the map race each other — queue it instead
 * of silently dropping the update.
 *
 * Deliberately NOT gated on `map.isStyleLoaded()`: that reflects MapLibre's
 * OWN broader style/tile loading state, which can transiently flip back to
 * `false` well after our sources already exist (e.g. while a vector style
 * is still fetching tiles for some other layer during normal panning). A
 * caller that hit that transient `false` would fall back to
 * `map.once("load", ...)` — but a Map's "load" event fires exactly once per
 * instance, at the very first style load; registering for it again after
 * that is a listener that will never fire, silently dropping the update
 * forever. This was a real bug (found via headless Playwright
 * reproduction): destination-marker updates went missing intermittently
 * because of exactly this race, not because of anything about the click
 * handling itself. `sourcesReady`/`onSourcesReady` below track our own
 * one-time initialization instead, which is monotonic and race-free.
 *
 * `isLive` guards a second, independent bug found via the same
 * reproduction: React StrictMode double-invokes the mount effect in dev,
 * creating and then immediately tearing down a first Map instance before
 * the real one is created. Deferred callbacks must not act on that stale
 * instance if it somehow still fires. */
function whenStyleReady(
  sourcesReady: boolean,
  onSourcesReady: (fn: () => void) => void,
  isLive: () => boolean,
  fn: () => void
) {
  const run = () => {
    if (!isLive()) return;
    fn();
  };
  if (sourcesReady) {
    run();
  } else {
    onSourcesReady(run);
  }
}

/** A GeoJSONGeometry from the API is already plain WGS84 lon/lat — MapLibre
 * consumes it directly, no reprojection needed at the display boundary
 * (CLAUDE.md Section 7.5: computation stays in the engine's local projected
 * CRS; only display uses WGS84/Web Mercator, and the API already converts
 * before this component ever sees the data). */
function asFeature(geometry: GeoJSONGeometry | null, properties: Record<string, unknown> = {}): GeoJSON.Feature | null {
  if (!geometry) return null;
  return { type: "Feature", properties, geometry: geometry as unknown as GeoJSON.Geometry };
}

export function MapView({ region, bands, origin, destination, routeLine, clickMode, onMapClick, onOriginDrop, developerMode, diagnostics, reachableFuelStopIds }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const originMarkerRef = useRef<maplibregl.Marker | null>(null);
  const loadedRegionIdRef = useRef<string | null>(null);
  const originRef = useRef(origin);
  originRef.current = origin;
  // Flips to true exactly once, right after our sources/layers are added in
  // the "load" handler — see whenStyleReady's comment for why this can't
  // just be map.isStyleLoaded(). Any whenStyleReady call that lands before
  // that queues into sourcesReadyCallbacksRef instead of registering a
  // MapLibre "load" listener that might never fire again.
  const sourcesReadyRef = useRef(false);
  const sourcesReadyCallbacksRef = useRef<Array<() => void>>([]);
  const onMapClickRef = useRef(onMapClick);
  onMapClickRef.current = onMapClick;
  const onOriginDropRef = useRef(onOriginDrop);
  onOriginDropRef.current = onOriginDrop;
  const clickModeRef = useRef(clickMode);
  clickModeRef.current = clickMode;
  const [styleError, setStyleError] = useState<string | null>(null);

  // One persistent map instance for the component's lifetime — re-creating
  // it on every render would drop the user's current zoom/pan on every
  // control tweak, which is exactly what a slippy map is supposed to avoid.
  useEffect(() => {
    if (!containerRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: resolveBasemapStyleUrl(),
      center: [27.53894, 37.24587],
      zoom: 11,
      // Default is 3px — on a trackpad, the tiny motion that happens during
      // an otherwise-deliberate click routinely exceeds that, so MapLibre
      // classifies it as a drag/pan instead of a click and the "click"
      // event never fires at all. That's what made origin/destination
      // placement look completely dead (marker never moved, destination
      // could never be picked) rather than just occasionally flaky.
      clickTolerance: 16,
    });
    mapRef.current = map;
    sourcesReadyRef.current = false;
    sourcesReadyCallbacksRef.current = [];
    map.addControl(new maplibregl.NavigationControl(), "top-right");

    if (originRef.current) {
      const markerElement = document.createElement("button");
      markerElement.type = "button";
      markerElement.className = "origin-drag-marker";
      markerElement.setAttribute("aria-label", "Drag start position");
      markerElement.title = "Port Iasos Marina · drag to move the start position";
      const marker = new maplibregl.Marker({ element: markerElement, draggable: true, anchor: "bottom" })
        .setLngLat([originRef.current.lon, originRef.current.lat])
        .addTo(map);
      marker.on("dragend", async () => {
        const candidate = marker.getLngLat();
        markerElement.classList.add("is-validating");
        const accepted = await onOriginDropRef.current(candidate.lat, candidate.lng);
        markerElement.classList.remove("is-validating");
        if (!accepted && originRef.current) {
          marker.setLngLat([originRef.current.lon, originRef.current.lat]);
        }
      });
      originMarkerRef.current = marker;
    }

    // Tile/style fetch failures (network block, bad key, provider outage)
    // used to fail silently — the container just showed its background
    // color with no map, which read as "nothing loaded" with no clue why.
    map.on("error", (e) => {
      if (mapRef.current !== map) return; // stale instance — see whenStyleReady's comment
      setStyleError(e.error?.message ?? "The map style could not be loaded.");
    });

    map.on("load", () => {
      if (mapRef.current !== map) return;
      if (map.getSource(SRC_LAND)) return;

      map.addSource(SRC_LAND, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_LAND}-fill`,
        type: "fill",
        source: SRC_LAND,
        paint: { "fill-color": "#e8e4d9", "fill-opacity": 0.96 },
      });
      map.addLayer({
        id: `${SRC_LAND}-outline`,
        type: "line",
        source: SRC_LAND,
        paint: { "line-color": "#9f9a8e", "line-width": 0.9, "line-opacity": 0.85 },
      });

      map.addSource(SRC_MARINE_FEATURES, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_MARINE_FEATURES}-line`,
        type: "line",
        source: SRC_MARINE_FEATURES,
        filter: ["==", ["geometry-type"], "LineString"],
        paint: { "line-color": "#d93636", "line-width": 4, "line-opacity": 0.9 },
      });
      map.addLayer({
        id: `${SRC_MARINE_FEATURES}-polygon`,
        type: "fill",
        source: SRC_MARINE_FEATURES,
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: {
          "fill-color": [
            "match", ["get", "restrictionClass"],
            "HARD_NO_GO", "#d93636",
            "CAUTION_CONDITIONAL", "#e49b28",
            "#297f78",
          ],
          "fill-opacity": 0.16,
          "fill-outline-color": "#297f78",
        },
      });
      map.addLayer({
        id: `${SRC_MARINE_FEATURES}-point`,
        type: "circle",
        source: SRC_MARINE_FEATURES,
        filter: ["==", ["geometry-type"], "Point"],
        paint: {
          "circle-radius": 5,
          "circle-color": "#d93636",
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
        },
      });

      // Bands largest-percentage-first in the *source* order so 100% sits at
      // the bottom of the paint order and 25% (smallest, most fuel-critical)
      // ends up visually on top — matches the old SVG renderer's stacking.
      for (const pct of [100, 75, 50, 25]) {
        const id = bandSourceId(pct);
        map.addSource(id, { type: "geojson", data: EMPTY_FC });
        map.addLayer({
          id: `${id}-fill`,
          type: "fill",
          source: id,
          paint: { "fill-color": BAND_COLORS[pct], "fill-opacity": BAND_OPACITY[pct] },
        });
        // A low-opacity fill alone makes the outer bands' boundary hard to
        // place precisely — a thin, slightly more solid outline keeps each
        // threshold's actual extent legible even at 100%'s ~0.14 fill.
        map.addLayer({
          id: `${id}-outline`,
          type: "line",
          source: id,
          paint: { "line-color": BAND_COLORS[pct], "line-width": 1, "line-opacity": 0.7 },
        });
      }

      map.addSource(SRC_SAFE_WATER, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_SAFE_WATER}-line`, type: "line", source: SRC_SAFE_WATER,
        paint: { "line-color": "#2b88c9", "line-width": 2, "line-dasharray": [2, 2], "line-opacity": 0.9 },
      });
      map.addSource(SRC_CLEARANCE, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_CLEARANCE}-fill`, type: "fill", source: SRC_CLEARANCE,
        paint: { "fill-color": "#e84747", "fill-opacity": 0.2 },
      });
      map.addLayer({
        id: `${SRC_CLEARANCE}-line`, type: "line", source: SRC_CLEARANCE,
        paint: { "line-color": "#e84747", "line-width": 1, "line-opacity": 0.8 },
      });
      map.addSource(SRC_COST_FIELD, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_COST_FIELD}-points`, type: "circle", source: SRC_COST_FIELD,
        paint: {
          "circle-radius": 2.5,
          "circle-color": ["interpolate", ["linear"], ["get", "costL"], 0, "#1a9850", 50, "#fee08b", 200, "#d73027"],
          "circle-opacity": 0.65,
        },
      });
      map.addLayer({
        id: `${SRC_COST_FIELD}-refined`, type: "circle", source: SRC_COST_FIELD,
        filter: ["==", ["get", "refined"], true],
        paint: { "circle-radius": 4, "circle-color": "#a020f0", "circle-stroke-color": "#fff", "circle-stroke-width": 1 },
      });

      map.addSource(SRC_MARINE_POIS, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_MARINE_POIS}-reachable-fuel-halo`,
        type: "circle",
        source: SRC_MARINE_POIS,
        filter: ["all", ["==", ["get", "poiType"], "fuel_dock"], ["==", ["get", "reachable"], true]],
        paint: {
          "circle-radius": 12,
          "circle-color": "#f4b942",
          "circle-opacity": 0.28,
          "circle-stroke-color": "#fff1b8",
          "circle-stroke-width": 2,
        },
      });
      map.addLayer({
        id: `${SRC_MARINE_POIS}-points`,
        type: "circle",
        source: SRC_MARINE_POIS,
        paint: {
          "circle-radius": ["match", ["get", "poiType"], "fuel_dock", 7, "marina", 5, 4],
          "circle-color": ["match", ["get", "poiType"], "fuel_dock", "#f29f05", "marina", "#117c8a", "#385a8a"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.5,
        },
      });

      map.addSource(SRC_ROUTE, { type: "geojson", data: emptyLineFC() });
      map.addLayer({
        id: `${SRC_ROUTE}-line`,
        type: "line",
        source: SRC_ROUTE,
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#b0492c", "line-width": 3 },
      });

      map.addSource(SRC_ORIGIN, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_ORIGIN}-point`,
        type: "circle",
        source: SRC_ORIGIN,
        paint: {
          "circle-radius": 7,
          "circle-color": "#23303a",
          "circle-stroke-color": "#f8f4ea",
          "circle-stroke-width": 2,
        },
      });

      map.addSource(SRC_DESTINATION, { type: "geojson", data: EMPTY_FC });
      map.addLayer({
        id: `${SRC_DESTINATION}-point`,
        type: "circle",
        source: SRC_DESTINATION,
        paint: {
          // Distinct color from the origin marker (both were the same dark
          // circle before, indistinguishable at a glance) — the old SVG
          // renderer told them apart by shape (circle vs. diamond); a color
          // difference is simpler to get right with MapLibre's circle layer.
          "circle-radius": 8,
          "circle-color": "#b0492c",
          "circle-stroke-color": "#f8f4ea",
          "circle-stroke-width": 2,
        },
      });

      sourcesReadyRef.current = true;
      const pending = sourcesReadyCallbacksRef.current;
      sourcesReadyCallbacksRef.current = [];
      pending.forEach((cb) => cb());
    });

    map.on("click", (e: maplibregl.MapMouseEvent) => {
      if (clickModeRef.current === "idle") return;
      onMapClickRef.current(e.lngLat.lat, e.lngLat.lng);
    });

    // MapLibre measures its container when the map is created. Keep its canvas
    // synchronized with later layout changes. While the camera is still at
    // its automatic region view, refit it so maximizing a previously small
    // window doesn't leave a tiny map in the middle. Once the user pans or
    // zooms, resize only and preserve their chosen camera.
    let resizeFrame: number | null = null;
    const resizeObserver = new ResizeObserver(() => {
      if (resizeFrame != null) window.cancelAnimationFrame(resizeFrame);
      resizeFrame = window.requestAnimationFrame(() => {
        if (mapRef.current !== map) return;
        map.resize();
      });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      if (resizeFrame != null) window.cancelAnimationFrame(resizeFrame);
      originMarkerRef.current?.remove();
      originMarkerRef.current = null;
      map.remove();
      mapRef.current = null;
      loadedRegionIdRef.current = null;
      sourcesReadyRef.current = false;
      sourcesReadyCallbacksRef.current = [];
    };
    // Intentionally created once — see comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cursor affordance for click-to-place modes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    map.getCanvas().style.cursor = clickMode === "idle" ? "" : "crosshair";
  }, [clickMode]);

  // Land basemap overlay: only refetch/refit when the *region itself*
  // changes (a different id from the backend), not on every re-render —
  // this is the coastline the routing engine actually computed against, so
  // it must always match whichever region.id the current range/route result
  // resolved to (see App.tsx's region-sync effect).
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;

    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      const source = map.getSource(SRC_LAND) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      source.setData(region.land as unknown as GeoJSON.FeatureCollection);
      if (loadedRegionIdRef.current !== region.id) {
        loadedRegionIdRef.current = region.id;
        if (originRef.current) {
          map.jumpTo({ center: [originRef.current.lon, originRef.current.lat], zoom: 11 });
        }
      }
    });
  }, [region]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      const source = map.getSource(SRC_MARINE_FEATURES) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      source.setData(developerMode ? region.marineFeatures as unknown as GeoJSON.FeatureCollection : EMPTY_FC);
    });
  }, [developerMode, region]);

  // The bundled map has no external tiles: the routing engine's own coastline
  // is the visible land layer. Developer mode only changes emphasis.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      if (map.getLayer(`${SRC_LAND}-fill`)) {
        map.setPaintProperty(`${SRC_LAND}-fill`, "fill-opacity", developerMode ? 0.78 : 0.96);
      }
      if (map.getLayer(`${SRC_LAND}-outline`)) {
        map.setPaintProperty(`${SRC_LAND}-outline`, "line-opacity", developerMode ? 1 : 0.85);
      }
    });
  }, [developerMode]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      const source = map.getSource(SRC_MARINE_POIS) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      const reachable = new Set(reachableFuelStopIds);
      source.setData({
        type: "FeatureCollection",
        features: region.marinePois.features.map((feature) => ({
          ...feature,
          properties: {
            ...feature.properties,
            reachable: reachable.has(String(feature.properties.sourceId)),
          },
        })),
      } as unknown as GeoJSON.FeatureCollection);
    });
  }, [region, reachableFuelStopIds]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      const overlay = developerMode ? diagnostics?.overlays : null;
      const setGeometry = (sourceId: string, geometry: GeoJSONGeometry | null | undefined) => {
        const source = map.getSource(sourceId) as maplibregl.GeoJSONSource | undefined;
        const feature = geometry ? asFeature(geometry) : null;
        source?.setData(feature ? { type: "FeatureCollection", features: [feature] } : EMPTY_FC);
      };
      setGeometry(SRC_SAFE_WATER, overlay?.classifiedSafeWater);
      setGeometry(SRC_CLEARANCE, overlay?.clearanceBuffer);
      const costSource = map.getSource(SRC_COST_FIELD) as maplibregl.GeoJSONSource | undefined;
      costSource?.setData(overlay?.costField as unknown as GeoJSON.FeatureCollection ?? EMPTY_FC);
    });
  }, [developerMode, diagnostics]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      for (const pct of [25, 50, 75, 100]) {
        const source = map.getSource(bandSourceId(pct)) as maplibregl.GeoJSONSource | undefined;
        if (!source) continue;
        const band = bands.find((b) => b.percentage === pct);
        const feature = band ? asFeature(band.geometry) : null;
        source.setData(feature ? { type: "FeatureCollection", features: [feature] } : EMPTY_FC);
      }
    });
  }, [bands]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      const source = map.getSource(SRC_ORIGIN) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      // The draggable DOM marker is the normal origin affordance. Keep the
      // old GeoJSON source empty so two origin symbols are never stacked.
      source.setData(EMPTY_FC);
      if (origin && originMarkerRef.current) {
        originMarkerRef.current.setLngLat([origin.lon, origin.lat]);
      }
    });
  }, [origin]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      const source = map.getSource(SRC_DESTINATION) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      source.setData(
        destination ? { type: "FeatureCollection", features: [pointFeature(destination.lon, destination.lat)] } : EMPTY_FC
      );
    });
  }, [destination]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    whenStyleReady(sourcesReadyRef.current, (cb) => sourcesReadyCallbacksRef.current.push(cb), () => mapRef.current === map, () => {
      const source = map.getSource(SRC_ROUTE) as maplibregl.GeoJSONSource | undefined;
      if (!source) return;
      source.setData(
        routeLine
          ? {
              type: "FeatureCollection",
              features: [{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: routeLine } }],
            }
          : emptyLineFC()
      );
    });
  }, [routeLine]);

  return (
    <div
      ref={containerRef}
      className={`map-libre ${clickMode !== "idle" ? "map-libre--clickable" : ""}`}
      role="img"
      aria-label="Water-constrained range map"
    >
      {styleError && (
        <div className="map-style-warning map-style-warning--error">Map failed to load: {styleError}</div>
      )}
    </div>
  );
}
