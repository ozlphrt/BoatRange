import type { StyleSpecification } from "maplibre-gl";

/** Provider seam for the display map. The default is bundled and requires no
 * tile server, API key, CORS permission, or network access beyond this site.
 * MapView paints the exact routing coastline over this water background.
 * Deployments may opt into a richer map with VITE_MAP_STYLE_URL. */

const SIMPLE_MARINE_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [
    {
      id: "water-background",
      type: "background",
      paint: { "background-color": "#cfe8ef" },
    },
  ],
};

export function resolveBasemapStyleUrl(): string | StyleSpecification {
  const explicit = import.meta.env.VITE_MAP_STYLE_URL as string | undefined;
  if (explicit) return explicit;

  return SIMPLE_MARINE_STYLE;
}

/** Kept as an offline-safe style for callers that want to opt out of network
 * tiles explicitly in a future map settings control. */
export const SIMPLE_OFFLINE_MARINE_STYLE = SIMPLE_MARINE_STYLE;

/** OpenSeaMap's seamark tile layer (buoys, lights, depth contours where
 * mapped) — CLAUDE.md Section 29's fuel-stop/marina awareness starts here.
 * This is an OVERLAY, not a basemap: OpenSeaMap only publishes seamark
 * symbols meant to sit on top of an existing map, not full ocean/land tiles.
 * Optional and off by default; wire a toggle in the UI when marina/fuel-dock
 * POIs actually get ingested (still outstanding — see README caveats). */
export const OPENSEAMAP_SEAMARK_TILE_URL =
  "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png";
