# Axopar 28 Water-Constrained Range Planner

A mobile-first PWA that calculates and displays the **fuel-limited reachable water area** for a 2019 Axopar 28 with a single Mercury Verado 300.

## Live deployment

The React client is deployed at
[https://ozlphrt.github.io/BoatRange/](https://ozlphrt.github.io/BoatRange/)
through GitHub Pages. Its workflow reads the
repository variable `API_BASE_URL` and uses it as the public FastAPI origin.
The included `render.yaml` can deploy that API as a Render Blueprint; after
the service is created, set `API_BASE_URL` to its HTTPS URL and rerun the
Pages workflow.

[Deploy the API on Render](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Fozlphrt%2FBoatRange)

## Defining Requirement

> The reachable-area model must never pass through land, islands, breakwaters, blocked shallow water, restricted zones, or other non-navigable terrain.

This project uses a **vector-first graph approach** over raster grids to guarantee navigable paths never cross blocked terrain.

## Architecture

```
/
├─ apps/
│  ├─ web/          — React + Vite + TypeScript PWA frontend
│  └─ api/          — Python FastAPI backend with PostGIS
├─ packages/
│  ├─ shared-contracts/ — TypeScript types for API contracts
│  └─ fuel-model/      — Fuel model core logic (Python)
├─ infra/
│  └─ docker/           — Docker Compose (PostGIS dev environment)
├─ data/
│  └─ fixtures/          — Regression test geographic fixtures
├─ scripts/
│  └─ ingest/            — Marine data ingestion pipeline
└─ docs/
```

## Quick Start

V1 runs entirely in-memory against the Bodrum/Kos demo fixture — **PostGIS is
not wired in yet**, so you can skip the Docker step for now and just run the
two dev servers.

### Setup checklist for an AI agent

If you are an agent setting this project up (not a human reading this),
follow these steps in order. Each one names exactly what to check and what
to do if the check fails — don't skip the "if missing" branches.

1. **Run the start script** — `./start.sh` (macOS/Linux) or `start.bat`
   (Windows), from the repo root. First run creates Python virtualenvs and
   runs `npm install`; later runs just launch both servers. This alone is
   enough to get the app running end to end with the simple provider-free
   marine map.
2. **Optional custom basemap**: set `VITE_MAP_STYLE_URL` in
   `apps/web/.env.local` only when a deployment needs a richer licensed
   MapLibre style. Restart Vite after changing it.
3. **No credentials are required** for local dev — the backend runs
   entirely in-memory against the checked-in real coastline data (see
   "Region coverage" below), no database, no other API keys.

### One-command start

```bash
./start.sh          # macOS/Linux
start.bat           # Windows
```

First run creates the Python virtualenvs and runs `npm install` (mirrors the
manual steps below); every run after that just launches both servers. Open
**http://localhost:2343** once it's up. Ctrl+C (or close the two opened
windows on Windows) stops both servers.

### First-time setup (Python) — manual equivalent of the script above

Each Python package/service has its own virtualenv. From the repo root:

```bash
for d in packages/fuel-model apps/api services/routing services/marine-data services/isochrone services/diagnostics; do
  python3 -m venv "$d/.venv"
  "$d/.venv/bin/pip" install -e "$d" -e "$d[dev]" -q
done

# apps/api also needs the four packages above installed into ITS venv
# (it imports them directly — see apps/api/app/main.py):
apps/api/.venv/bin/pip install -e packages/fuel-model -e services/routing \
  -e services/marine-data -e services/isochrone -e services/diagnostics -q
```

### Run it

```bash
npm install          # frontend deps (root workspace)
npm run dev:api       # FastAPI on http://localhost:8000
npm run dev:web       # Vite dev server on http://localhost:2343 (proxies /api)
```

Open **http://localhost:2343** — origin defaults to the Bodrum/Kos channel
(known navigable water in the demo fixture). Drag the sliders, click "Haritadan
seç" to move the origin, or "Haritaya dokunup rota çiz" to tap a destination.

### Basemap provider

The map (`apps/web/src/components/MapView.tsx`) is MapLibre GL JS, not the
earlier custom SVG renderer — real zoom/pan/pinch, not a fixed viewBox. The
basemap style is a separate concern from the water/land
geometry the routing engine actually computes against (that always comes
from the API, reprojected to WGS84).

The default is a deliberately simple, provider-free marine map: flat water
plus the exact coastline geometry used by the routing engine. This keeps the
planning result legible and removes third-party tile loading from the initial
experience. A deployment can opt into a richer licensed MapLibre style with
`VITE_MAP_STYLE_URL`; the routing geometry remains the authoritative overlay.

OpenSeaMap's seamark tiles (buoys, lights, depth contours) are wired as an
*optional overlay* constant (`OPENSEAMAP_SEAMARK_TILE_URL`), not a basemap —
OpenSeaMap only publishes marks meant to sit on top of an existing map. No UI
toggle for the tile overlay exists yet. Marinas, harbours, and explicitly
tagged marine fuel docks are ingested separately as versioned vector POIs and
rendered directly by the app.

### Run the tests

```bash
for d in packages/fuel-model apps/api services/routing services/marine-data services/isochrone services/diagnostics; do
  (cd "$d" && .venv/bin/python -m pytest -q)
done
```

### Known caveats (read before demoing)

- **Region coverage**: the app works anywhere along Italy, Greece, Cyprus,
  and Turkey's coasts, not just inside a small Bodrum/Kos box. Three regions
  are registered (`apps/api/app/regions.py`), checked most-specific-first:
  1. `bodrum-kos-real` — real OSM coastline specifically for Bodrum/Kos
     (Bodrum's administrative boundary relation, Kos's island relation, and
     ~470 surrounding islets down to 500 m², 125 pieces after filtering
     slivers; provenance in `data/manifests/bodrum_kos_real.json`, ingested
     via `scripts/ingest/fetch_bodrum_kos_osm.py`). This region also uses
     EMODnet DTM 2024 bathymetry at roughly 115 m resolution. Conservative
     mode blocks shallow pixels and everything outside known depth coverage.
     A separately versioned OSM/OpenSeaMap layer adds 57 breakwater/groyne
     geometries and 111 rock/wreck/obstruction hazards as hard blockers.
  2. `east-med-real` — real, full-detail OSM coastline (the same
     `land-polygons` dataset OSM itself publishes, ~925 MB downloaded once
     and clipped locally) covering Italy, Greece, Cyprus, and Turkey (and
     incidentally their neighbors, since a rectangular bounding box can't
     avoid it) — 2,315 land pieces down to 1-hectare islets after filtering;
     provenance in `data/manifests/east_med_real.json`, ingested via
     `scripts/ingest/fetch_east_med_osm.py`. This region carries a real,
     documented accuracy caveat: it spans ~12° of latitude, but the API's
     `LocalProjection` uses one fixed reference point for the whole box, so
     distance/fuel calculations lose accuracy (up to roughly 10-15% in the
     east-west direction) for an origin far from that reference point. A
     correct fix is a per-request projection centered on the actual origin,
     a separate architecture change not yet made.
     This wider region has no bathymetry yet, so normal conservative requests
     fail closed. Developer mode may explicitly request the permissive policy
     for comparison and receives a LOW-confidence warning.
  3. `bodrum-kos-demo` — the original hand-built *synthetic* fixture, kept
     only because `services/marine-data`, `services/routing`, and
     `services/isochrone`'s test suites import it directly as a
     fully-controlled world; its bbox is a strict subset of `bodrum-kos-real`'s,
     so it's effectively unreachable through the live API now.

  An origin outside all three returns a clear `422`, by design (CLAUDE.md
  Section 27) — it never silently extrapolates. Confidence stays `"LOW"`
  everywhere because the DTM is too coarse for navigation-grade near-shore
  decisions, incomplete official restriction geometry, and potentially incomplete/stale community POIs remain material gaps.
- **Performance**: a full `/range/full` compute against `east-med-real` (a
  region with hundreds of real islands, versus the ~125 pieces the earlier
  Bodrum-only optimizations were tuned against) started at **~37s** once
  that region became the live default — profiled and fixed down to **~8s**,
  close to CLAUDE.md's 5s target (Section 25). Five real bottlenecks, found
  via `cProfile`, not guesswork:
  1. `_refine_missed_corridors` buffering/opening the full, unsimplified
     safe-water region before looking for narrow corridors — fixed by
     simplifying first (`routing/water_graph.py`).
  2. The independent verifier's clearance check doing a linear
     `min(g.distance(pt) for g in land_geometries)` per boundary sample —
     51 million `Polygon.distance()` calls in one profiled run. Fixed with
     an `STRtree` nearest-neighbor index plus a cheap bbox pre-query
     (`isochrone/navigability.py`, `isochrone/polygon_utils.py`).
  3. `land_raw_union` (buffer+union over every land polygon) recomputed
     from scratch on every one of the 4 per-request verifier calls instead
     of once — now a `cached_property` on `NavigabilityRegion`.
  4. Several `obstacle_union`/`land_raw_union` `.intersects()`/`.contains()`
     checks running unprepared against a multi-hundred-piece geometry —
     fixed with `shapely.prepared.prep()` (the same fix already applied to
     `routing.water_graph`'s own obstacle checks).
  5. `NavigabilityRegion` (used by the verifier and `is_navigable_point`)
     was built from the *entire* region's land list regardless of how far
     from the origin most of it was — `IsochroneEngine.build_graph()`
     already filtered land to the compute extent for the routing graph
     itself, but the verifier-facing region object didn't share that
     filter. This was the single biggest win.
  A visible symptom this collection of fixes also solved: a vessel's
  reachable-area fill used to stop dead in a straight line partway through
  open water whenever its fuel range exceeded a small region's bounding
  box — not a rendering bug, but literally running out of region data.
  Promoting `east-med-real` to the default region (see above) fixed that,
  at the cost of exposing these performance bottlenecks, since a region
  with hundreds of islands stresses code paths a small curated region never
  did.
- **Nine vessels are selectable** (`packages/fuel-model/fuel_model/catalog.py`),
  all single-outboard so they share one performance model (RPM -> speed/fuel
  curve). Every vessel's `notes` and `dataConfidence` are surfaced in the API
  response and the web UI rather than hidden:
  - **Axopar 28** — the original v1 vessel. Fuel curve: 9 reconciled anchor
    points (`dataConfidence` 0.85). `tank_capacity_l`/`hull_draft_m` were
    originally an unverified 750 L / 1.35 m (implausibly large for a 28 ft
    boat) and have since been corrected to 257 L / 0.80 m using an
    independent boattest.com test of the Axopar 28 Cabin — see the vessel's
    `notes` for the twin/single-engine caveat that comes with that source.
  - **Dusky 233** (Evinrude 300 HP) — only 3 published data points, all
    above 3500 RPM (`dataConfidence` 0.35); can't answer a query below ~25 kn,
    and one fuel-burn figure is flagged as a likely source artifact.
  - **7 more** — Axopar 22 T-Top, Axopar 25 Cross Top, Boston Whaler 190
    Montauk, Boston Whaler 210 Dauntless, Robalo R160, Robalo R180, and
    Grady-White Fisherman 236 — each from a FULL published boattest.com
    "Test Results" table (7-13 RPM steps, idle to WOT), fetched with the
    `scrapling` MCP server. `dataConfidence` 0.70-0.80 depending on how
    dense/clean that table was; see each entry's `notes` for specifics
    (e.g. the Boston Whaler 190 Montauk's last two points show fuel burn
    dropping as speed rises — a source quirk, kept and flagged rather than
    smoothed away).

  Two full sailboat databases (`sailing_database.xlsx`,
  `sailing_database_2.xlsx` — boat-specs.com and sailboatdata.com,
  10,374 rows checked row-by-row) contain zero motorized vessels; adding
  sailboats would need a wind-polar performance model, not this RPM/fuel
  curve, and is out of scope for now.
- **No PostGIS/DB yet**: scenarios, user overrides, and persistent caching
  (Sections 18, 24, 30) aren't implemented; every request recomputes from
  scratch against the in-memory fixture.

## Implementation Phases

See [CLAUDE.md](./CLAUDE.md) for full product specification.

- Phase 0: Repository and contracts ✓
- Phase 1: Fuel model ✓ (PCHIP interpolation, verified against scipy to 1e-14; bidirectional speed/RPM with ambiguity detection)
- Phase 2: Static marine data — real OSM coastline across Italy/Greece/Cyprus/Turkey; EMODnet DTM 2024 bathymetry, 168 OSM/OpenSeaMap physical hazards, eight official EMODnet managed-area polygons, two unresolved HNHS legal notices, and 33 marina/harbour/fuel POIs ingested for Bodrum/Kos; authoritative boundaries for the unresolved notices and wider bathymetry remain outstanding
- Phase 3: Navigability classification (vector-first) ✓
- Phase 4: Routing ✓ (8-connected graph, line-of-sight simplification, fail-closed endpoints)
- Phase 5: Isochrone engine ✓ (one-way + round-trip, independent verifier, clearance-aware clipping)
- Phase 6: Local refinement — not yet implemented
- Phase 9: Round-trip model ✓
- Phase 10: Developer mode — not yet implemented
- Phase 11: PWA UI — functional demo UI shipped (lightweight SVG map, not the final MapLibre/PWA stack)
- Phase 12+: Scenarios, sharing, caching — not yet implemented

## Core Success Metric

> **No impossible land-crossing route or reachable-area leak in the validated test set.**
