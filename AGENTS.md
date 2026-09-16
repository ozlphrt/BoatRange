# AGENTS.md

## Project: Axopar 28 Water-Constrained Range Planner

### Purpose

Build a mobile-first PWA that calculates and displays the **fuel-limited reachable water area** for a 2019 Axopar 28 with a single Mercury Verado 300.

The defining requirement is:

> **The reachable-area model must never pass through land, islands, breakwaters, blocked shallow water, restricted zones, or other non-navigable terrain.**

Previous attempts failed because they treated range as a geometric radius or produced polygons that leaked through land. This project must solve that problem at the routing-engine level, not hide it visually.

The application is a **planning tool, not a navigation-grade chartplotter**.

---

# 1. V1 Product Scope

## 1.1 V1 vessel

Primary and only user-facing vessel in v1:

- Axopar 28
- Model year: 2019
- Engine: single Mercury Verado 300
- Fuel: gasoline
- Performance model: continuous interpolation from cleaned reference data
- Fuel reserve: configurable, default 20%
- Draft: use published Axopar 28 draft in vessel profile
- Propulsion depth: stored separately from hull draft
- Boat profile schema must remain generic enough for future vessels

Do not expose a generic multi-boat product in v1.

Internally, design the engine so future boat profiles can use:
- single/twin outboards
- inboards
- diesel
- electric
- arbitrary consumption curves

---

# 2. Geographic Scope

V1 target region:

- Aegean coast of Türkiye
- nearby Greek islands
- especially Bodrum / Güllük Bay / Kos / surrounding waters

The architecture must allow additional regions to be added later by data ingestion only, without changing routing-engine code.

---

# 3. Core User Experience

## 3.1 Start position

Support all of the following:

1. Current GPS position
2. Drop a pin anywhere on the map
3. Search for a marina/place

## 3.2 Main result

The primary output is a **water-only reachable-area isochrone**.

It must represent points reachable using valid marine paths only.

Do not display a simple radius or straight-line range circle.

## 3.3 Range modes

Support:

- One-way range
- Round-trip safe range

Round-trip safe range must compute:

`outbound fuel cost + return fuel cost <= usable non-reserve fuel`

Do not implement round-trip range as a simple half-distance rule.

## 3.4 Range visualization

Default map presentation:

- clean marine-first map
- subdued land
- prominent water
- marinas/harbors visible
- minimal road clutter

Show:

- 25% fuel band
- 50% fuel band
- 75% fuel band
- 100% usable-fuel boundary
- comfortable / caution / limit safety bands
- fuel percentage on map
- actual liters in detail panels
- final navigable range only by default

Optional debug overlay:
- theoretical fuel-only geometric range
- raw grid
- blocked cells
- refined cells
- obstacle vectors
- final contour

## 3.5 Main controls

Primary UI:

- speed slider
- RPM displayed alongside
- fuel level
- reserve %
- sea-state adjustment
- safety clearance
- one-way / round-trip toggle

Advanced controls belong in a bottom sheet/panel.

Do not clutter the main map.

## 3.6 Fuel input

Support:

- full tank
- liters remaining
- percentage remaining

Reserve:
- default 20%
- user-adjustable
- reserve is protected during the full round-trip calculation

## 3.7 Tap a point

When a user taps inside the reachable area, calculate the **shortest valid navigable-water route** to that point.

Display:

- route distance
- estimated travel time
- estimated fuel consumed
- fuel remaining
- reserve remaining
- data-confidence level
- relevant warnings

Never use straight-line distance if that line crosses land or blocked water.

---

# 4. Fuel Model

## 4.1 Source-data policy

The supplied Axopar performance graphic contains internal contradictions.

Therefore:

- store raw source values separately
- never trust published `L/nm` blindly
- source-of-truth fields are:
  - RPM
  - speed in knots
  - fuel flow in L/h
- derive:
  - `L/nm = L/h / knots`
- automatically flag inconsistent source values

## 4.2 Cleaned v1 anchor data

Use the following cleaned working dataset:

| RPM | Speed kn | Fuel L/h | Derived L/nm |
|---:|---:|---:|---:|
| 1000 | 5.0 | 6.0 | 1.20 |
| 1500 | 6.0 | 8.0 | 1.33 |
| 2000 | 8.0 | 13.0 | 1.63 |
| 3200 | 13.0 | 27.0 | 2.08 |
| 3500 | 16.9 | 38.0 | 2.25 |
| 4000 | 20.0 | 43.8 | 2.19 |
| 4500 | 26.0 | 60.5 | 2.33 |
| 5000 | 31.0 | 75.5 | 2.44 |
| 5400 | 35.0 | 87.2 | 2.49 |

Important:

- The original graphic shows conflicting values around 3500 RPM.
- 3500 RPM is reconciled from 38 L/h and 2.25 L/nm:
  - `38 / 2.25 = 16.89 kn`
- The graphic's 4000 RPM printed efficiency is inconsistent:
  - `43.8 / 20 = 2.19 L/nm`, not 2.33.

## 4.3 Continuous interpolation

Use **shape-preserving monotonic interpolation**.

Preferred implementation:
- PCHIP / monotonic cubic interpolation

Interpolate independently:

- RPM -> speed
- RPM -> L/h

Then derive:
- L/nm
- endurance
- range

Do not interpolate published L/nm directly.

Do not use unrestricted cubic splines because they can overshoot between sparse control points.

Do not extrapolate beyond the measured RPM range.

## 4.4 Bidirectional input

Support:

- speed -> RPM
- RPM -> speed

If inversion becomes ambiguous due to a non-monotonic region, do not silently choose a branch.

Return:
- ambiguity warning
- valid candidate operating points

## 4.5 Operating zones

Support the full curve but automatically identify and visually mark inefficient regions.

The hump / inefficient region around roughly 2000-3200 RPM:
- may be selected by advanced users
- should not be presented as a preferred operating mode

## 4.6 Fuel model calibration

Keep:
- raw reference curve
- smoothed operational curve
- calibrated real-world curve

Allow:
- global correction factor
- Light / Normal / Heavy load presets
- manual efficiency adjustment

Load should affect:
- speed at RPM
- fuel flow

Future trip calibration:
- GPS + speed recording in v1 architecture
- NMEA 2000 support later
- engine telemetry model must exist in schemas now
- manual trip-end fuel reconciliation supported later

---

# 5. Sea-State Model

V1:

- manual adjustment only
- Calm
- Moderate
- Rough

Initially apply a global fuel-efficiency penalty.

Architecture must support future directional effects from:
- wind
- waves
- current

The routing cost engine must accept directional penalties later without redesign.

---

# 6. Marine Navigability Model

This is the most important subsystem.

## 6.1 Navigability definition

A location or segment is navigable only if it satisfies all required rules:

- not land
- not inside blocked obstacle geometry
- not inside hard restricted zone
- passes depth policy
- passes safety-clearance policy
- not excluded by user-defined no-go zone
- not blocked by valid temporary exclusion zone

## 6.2 Marine data sources

Provider-agnostic architecture.

V1 ingestion baseline:

- OpenStreetMap coastline and hydrographic features
- higher-resolution official/open coastline/hydrographic data where available
- open bathymetry/depth data
- mapped restrictions
- marinas
- fuel docks
- ports
- known obstacles
- user-defined exclusions

Commercial chart sources may be added later.

Never hardwire the engine to a single map/chart provider.

## 6.3 Missing-depth policy

Default policy: conservative.

- known safe depth -> navigable
- known shallow -> blocked
- unknown depth -> blocked in normal mode
- developer mode may compare against a permissive policy

Unknown-depth permissive mode must never silently replace conservative mode.

## 6.4 Depth

Minimum safe depth:

`max(hull_draft, propulsion_depth) + safety_margin`

Store:
- hull draft
- propulsion depth
- safety depth margin

Tide is not implemented in v1 but schemas must allow a future tide correction.

## 6.5 Safety clearance

Default:
- 50 m

User adjustable:
- 25 m
- 50 m
- 100 m
- 250 m
- optionally continuous within this range

Clearance applies to:
- land
- obstacles
- hard restriction boundaries where appropriate

Later support adaptive clearance:
- smaller in legitimate narrow channels
- larger near exposed hazards

## 6.6 Restrictions

Classify marine restrictions as:

1. HARD_NO_GO
2. CAUTION_CONDITIONAL
3. INFORMATIONAL

Normal calculations:
- HARD_NO_GO = blocked
- CAUTION_CONDITIONAL = warning or higher future routing cost
- INFORMATIONAL = map overlay only

Developer mode may override restrictions for diagnostics only.

## 6.7 User-defined exclusions

Users can create:
- persistent blocked polygons
- persistent passable overrides
- temporary exclusion zones with expiry

These must be stored as a separate overlay.

Never silently merge user edits into source datasets.

---

# 7. Core Routing / Isochrone Architecture

Use a **hybrid raster + vector engine**.

## 7.1 Base grid

Typical v1 cell size:
- 75-150 m

Use:
- medium resolution globally
- local refinement in ambiguous narrow passages

Do not use a uniformly fine 20-50 m grid across the whole compute extent.

## 7.2 Adaptive local refinement

Trigger refinement for:
- narrow channels
- coast-adjacent corridors
- passages near breakwaters
- suspicious single-cell gaps
- cells intersected by narrow vector obstacles
- areas where a coarse-grid classification changes across neighboring cells
- corridors whose width is near safety-clearance threshold

Refined resolution can be:
- 20-50 m

Refinement must be local only.

## 7.3 Vector obstacle collision checks

Small features can be lost inside a coarse raster.

Therefore every candidate path edge must also be checked against vector obstacles.

Examples:
- breakwaters
- harbor walls
- small islands
- rocks
- narrow exclusion polygons

A segment is invalid if it intersects or violates clearance against a hard obstacle.

This rule is non-negotiable.

## 7.4 Search algorithm

Primary approach:

- grid graph over navigable water
- weighted shortest-path expansion
- Dijkstra or equivalent cost-field propagation
- A* for individual point routes if useful
- compute fuel cost rather than only geometric distance

Each graph edge stores:
- geodesic/projected distance
- base fuel cost
- optional sea-state multiplier
- optional load correction
- future directional penalty slot

## 7.5 Computation projection

Display:
- WGS84 / Web Mercator as appropriate

Computation:
- local projected CRS suitable for the region

Do not perform cost-field geometry directly in Web Mercator.

## 7.6 Edge cost

For a symmetric v1 model:

`fuel_cost_liters = segment_distance_nm * effective_liters_per_nm`

where:

`effective_liters_per_nm =
base_curve_lpnm
* load_factor
* sea_state_factor
* calibration_factor`

Keep future interface:

`cost(edge, direction, environment)`

so wind/wave/current penalties can later depend on heading.

---

# 8. One-Way Isochrone

Given:

- origin
- usable fuel
- operating point
- navigability mask

Run shortest-cost expansion from origin.

A cell is reachable if:

`minimum_fuel_cost(origin -> cell) <= usable_fuel`

Generate thresholds at:
- 25%
- 50%
- 75%
- 100%

Do not use Euclidean or great-circle radius as the final reachable result.

---

# 9. Round-Trip Isochrone

For every candidate point:

`outbound_min_cost + return_min_cost <= usable_fuel`

Use true navigable costs.

Do not implement:

`round_trip_range = one_way_range / 2`

Even with symmetric costs, keep the two-cost architecture because future directional conditions will make outbound and return costs different.

---

# 10. Reachable Polygon Generation

Pipeline:

1. Compute reachable cost field
2. Extract threshold contours
3. Convert contour to polygon/multipolygon
4. Validate geometry
5. Remove raster artifacts
6. Simplify cautiously
7. Preserve disconnected reachable regions
8. Re-check simplified boundary against obstacles
9. Run anomaly detector
10. Run independent verifier
11. Only then return to client

## 10.1 Cleanup

Remove:
- tiny spikes
- single-cell protrusions
- microscopic holes
- numerical slivers

Do not remove legitimate islands of reachable water.

## 10.2 Smoothing

Use navigation-aware line-of-sight simplification.

Rules:
- simplify only if replacement segment remains valid
- never use decorative spline smoothing
- preserve turns around hazards
- preserve narrow valid channels

---

# 11. Narrow Corridor Validation

Any suspicious narrow reachable corridor must be locally refined.

After refinement:

- corridor must remain navigable
- obstacle clearance must pass on both sides
- route segment collision checks must pass

If not:
- remove corridor from reachable set

This directly prevents thin polygon tendrils leaking through land or breakwaters.

---

# 12. Independent Verifier

The primary engine must not be trusted blindly.

Build a separate verifier that checks:

1. final polygon boundary samples
2. random interior samples
3. representative routes from origin
4. route-segment collisions
5. land intersections
6. obstacle intersections
7. blocked-depth crossings
8. restriction crossings

The verifier should not reuse the exact same logic path as the main engine where avoidable.

If verification fails:
- fail closed
- do not show the polygon
- return diagnostics

---

# 13. Anomaly Detection

Before displaying a result, test for:

- invalid polygon geometry
- polygon intersecting land
- polygon intersecting hard obstacles
- suspicious thin tendrils
- isolated unreachable pockets
- impossible shortcuts
- extreme perimeter/area ratio
- corridor width below threshold
- disconnected cells with no valid path
- range beyond theoretical fuel maximum
- polygon extending outside compute extent unexpectedly
- holes or rings with invalid topology

If anomaly severity is HIGH:
- suppress result
- return error
- generate diagnostic snapshot

---

# 14. Confidence Model

Each returned area or tapped route should expose:

- HIGH
- MEDIUM
- LOW

Confidence reasons may include:

- missing depth
- stale restriction data
- coarse bathymetry
- conflicting sources
- user override
- local refinement required
- incomplete obstacle data

When tapped, explain why confidence is reduced.

Do not imply certainty where data is incomplete.

---

# 15. Data Fusion / Provenance

Every navigability decision should preserve provenance.

For a cell or segment, retain information such as:

- coastline source
- depth source
- restriction source
- obstacle source
- user override
- data version
- classification rule
- final navigability decision

Priority should be rule-based, not "first source wins."

Example conceptual precedence:

1. hard legal exclusion
2. user hard exclusion
3. hard obstacle
4. land
5. unsafe depth
6. passable water
7. caution metadata
8. informational metadata

Expose source reasoning in developer mode.

---

# 16. Marine Data Ingestion

Build a repeatable scripted pipeline.

Stages:

1. fetch source
2. checksum/version
3. normalize CRS
4. validate geometry
5. repair invalid geometry
6. clip by region
7. classify features
8. simplify only where safe
9. load vectors into PostGIS
10. rasterize navigability tiles
11. build indexes
12. run topology tests
13. generate data manifest
14. invalidate affected caches

Architecture must support scheduled refresh later.

---

# 17. Geometry Validation During Ingestion

Detect and repair:

- self-intersections
- invalid polygon rings
- broken multipolygons
- gaps
- slivers
- invalid winding
- duplicate vertices
- near-zero-area polygons

Run topology tests designed to detect "water leaks" through bad coastline data.

---

# 18. Database

Use PostgreSQL + PostGIS.

Suggested tables:

## vessel_profiles

- id
- name
- manufacturer
- model
- year
- propulsion_type
- engine_count
- tank_capacity_l
- hull_draft_m
- propulsion_depth_m
- default_reserve_pct
- fuel_model_version
- created_at
- updated_at

## fuel_curve_points

- id
- vessel_profile_id
- rpm
- speed_kn
- fuel_lph
- raw_lpnm_optional
- derived_lpnm
- source
- confidence
- is_raw
- model_version

## marine_layers

- id
- layer_type
- source_name
- source_version
- fetched_at
- valid_from
- stale_after
- region_id
- metadata_json

## marine_features

- id
- layer_id
- feature_type
- restriction_class
- geom geometry
- properties jsonb

Spatial index required.

## navigability_tiles

- id
- region
- resolution_m
- tile_key
- marine_data_version
- params_hash
- raster/object-storage reference
- created_at

## user_overrides

- id
- owner_id nullable
- type
- geometry
- created_at
- expires_at
- source_note

## scenarios

- id
- vessel_profile_id
- origin
- fuel_input
- reserve_pct
- selected_speed
- selected_rpm
- sea_state
- clearance_m
- range_mode
- created_at
- app_version
- fuel_model_version
- marine_data_version
- routing_engine_version
- result_snapshot_ref

## trip_calibration_samples

Schema now, feature later:

- timestamp
- location
- rpm
- speed_kn
- fuel_lph
- sea_state
- load_state
- source
- quality

---

# 19. Backend

Use:

- Python
- FastAPI
- PostGIS
- geospatial libraries such as:
  - Shapely
  - GeoPandas where appropriate
  - Rasterio
  - PyProj
  - NumPy
  - SciPy
- Redis/cache can be added if needed

Backend responsibilities:

- fuel-model evaluation
- navigability mask construction
- cost-field calculation
- isochrone generation
- local refinement
- route queries
- validation
- anomaly detection
- confidence calculation
- diagnostics
- marine-data ingestion utilities

---

# 20. Frontend

Use:

- React
- Vite
- TypeScript
- PWA
- MapLibre-compatible map stack
- provider-agnostic tile abstraction

Frontend responsibilities:

- map
- controls
- GPS
- search
- start-point placement
- scenario persistence
- low-resolution previews where practical
- request cancellation
- rendering
- debug overlays
- local settings

---

# 21. Monorepo Structure

Use strict domain boundaries.

```text
/
├─ AGENTS.md
├─ README.md
├─ apps/
│  ├─ web/
│  │  ├─ src/
│  │  ├─ public/
│  │  └─ tests/
│  └─ api/
│     ├─ app/
│     ├─ tests/
│     └─ migrations/
├─ packages/
│  ├─ shared-contracts/
│  ├─ fuel-model/
│  ├─ map-ui/
│  └─ test-fixtures/
├─ services/
│  ├─ routing/
│  ├─ isochrone/
│  ├─ verifier/
│  ├─ marine-data/
│  └─ diagnostics/
├─ data/
│  ├─ fixtures/
│  ├─ manifests/
│  └─ sample-scenarios/
├─ scripts/
│  ├─ ingest/
│  ├─ validate-data/
│  ├─ benchmarks/
│  └─ dev/
├─ infra/
│  ├─ docker/
│  └─ deployment/
└─ docs/
   ├─ architecture.md
   ├─ api.md
   ├─ data-sources.md
   ├─ routing-engine.md
   ├─ validation.md
   └─ acceptance-tests.md
```

Do not mix UI code with routing logic.

Do not put marine-data ingestion inside frontend code.

---

# 22. API Contracts

Use versioned endpoints.

## POST /api/v1/range/preview

Purpose:
- fast low-resolution preview

Input:

```json
{
  "origin": {"lat": 37.0, "lon": 27.4},
  "vesselProfileId": "axopar-28-2019-verado-300",
  "fuel": {"mode": "percent", "value": 100},
  "reservePct": 20,
  "speedKn": 20,
  "seaState": "calm",
  "loadState": "normal",
  "clearanceM": 50,
  "rangeMode": "one_way"
}
```

Return:

```json
{
  "requestId": "...",
  "quality": "preview",
  "bands": {
    "25": {},
    "50": {},
    "75": {},
    "100": {}
  },
  "warnings": [],
  "versions": {}
}
```

## POST /api/v1/range/full

Same input plus optional advanced settings.

Return:
- final validated polygons
- confidence
- warnings
- compute metrics
- version metadata
- diagnostic reference if requested

## POST /api/v1/route

Input:
- origin
- destination
- same vessel/settings

Return:
- shortest valid route
- distance
- time
- fuel
- confidence
- warnings

## POST /api/v1/verify

Developer-only.

Validate a supplied result or stored scenario.

## GET /api/v1/vessels/{id}

Return vessel and fuel model.

## POST /api/v1/scenarios

Save scenario.

## GET /api/v1/scenarios/{id}

Return original snapshot plus versions.

## POST /api/v1/scenarios/{id}/recalculate

Recompute using latest data/model.

---

# 23. Request Concurrency

The UI must remain interactive during calculations.

Rules:

- panning/zooming remains available
- changing origin/settings cancels stale work
- every request has a unique request ID
- every request has a calculation-state hash
- stale responses are ignored
- do not render a response whose hash no longer matches current UI state

Rapid control changes:

1. immediate local/low-res preview
2. debounce full compute ~300-500 ms
3. cancel previous full compute
4. render only latest valid result

---

# 24. Caching

Use version-aware cache keys.

Cache dimensions must include at minimum:

- region
- origin tile
- resolution
- marine-data version
- routing-engine version
- depth policy
- clearance
- restriction policy
- user override version
- vessel fuel-model version
- sea-state model version

Cache:
- navigability tiles
- refined tiles
- reusable local topology
- cost-field chunks where safe

Never reuse stale navigability results after relevant data/settings change.

---

# 25. Performance Targets

Use balanced targets with adaptive tolerance.

Target on a normal phone/network:

- preview: < 500 ms
- full isochrone typical: < 5 s
- tap-to-route: < 1 s
- local refinement: < 2 s

For unusually large/extreme requests:
- allow slower compute
- show progress
- never freeze UI

Track performance benchmarks in CI.

Performance regression should fail CI if materially outside established tolerance.

---

# 26. Hard Compute Limits

Every backend request must have budgets:

- max compute extent
- max cells
- max refinement count
- max CPU time
- max memory
- max tile fetches
- max user-defined polygons
- max returned polygon complexity

If exceeded:
- fail clearly
- suggest a narrower computation
- never degrade silently into unsafe shortcuts

---

# 27. Data Availability Policy

Marine layers are classified:

## Hard-required

Examples:
- coastline/land
- primary obstacle mask
- core navigability geometry

If unavailable:
- fail closed

## Optional

Examples:
- informational shipping lane
- marina POIs
- non-critical overlays

If unavailable:
- continue
- show explicit warning

---

# 28. Data Freshness

Every layer must have:

- source version
- fetched timestamp
- stale threshold
- region

If stale:
- expose warning
- downgrade confidence if relevant

Do not silently pretend old data is current.

---

# 29. Fuel-Stop Awareness

V1 map should show:

- marinas
- fuel docks

Highlight fuel stops inside the reachable area.

Do not implement multi-leg refueling route planning in v1.

Architecture should allow these POIs to become graph nodes later.

---

# 30. Saved Scenarios

Allow user to save scenarios containing:

- origin
- fuel
- reserve
- speed/RPM
- sea state
- load state
- clearance
- range mode
- overrides
- result snapshot
- model/data versions

Support side-by-side comparison.

Examples:

- Calm @ 20 kn
- Moderate @ 26 kn
- Heavy load @ 20 kn

---

# 31. Sharing

Support shareable links.

A shared scenario stores:

- original parameters
- original result snapshot
- model/data versions

Recipient can:

1. view original snapshot
2. choose "Recalculate with latest data"

Do not silently replace the original result.

---

# 32. Versioning

Every calculation result must record:

- app version
- fuel-model version
- marine-data version
- routing-engine version
- validation-engine version
- scenario schema version

Support future replay/migration.

---

# 33. Developer / Debug Mode

This is mandatory.

Show:

- raw navigability grid
- blocked cells
- depth exclusions
- land buffer
- obstacle buffer
- hard restrictions
- user exclusions
- refinement zones
- candidate corridors
- cost field
- contour before cleanup
- contour after cleanup
- verifier sample points
- rejected segments
- exact reason a cell/segment was classified blocked

Allow tapping a cell to display:
- all contributing datasets
- versions
- classification rules
- final decision

---

# 34. Diagnostic Snapshot Export

Developer mode must export a complete JSON diagnostic snapshot.

Include:

- origin
- map extent
- vessel
- fuel settings
- model versions
- marine data versions
- raw source IDs
- grid resolution
- refined regions
- blocked-cell counts
- routing params
- polygon WKT/GeoJSON
- failed validation checks
- verifier results
- random seed if sampling used
- timing metrics
- request hash

Goal:

A broken result should be reproducible by Codex from one diagnostic file.

---

# 35. Regression Geography

Before UI polish, create hard geographic fixtures for known difficult locations in the Aegean.

Include cases with:

- Bodrum peninsula
- Kos
- Güllük Bay
- narrow marina entrances
- breakwaters
- small islands
- channels
- coves
- coastal gaps
- peninsulas
- overlapping islands at coarse grid resolution

Each fixture should include explicit assertions such as:

- route must not cross this land polygon
- this channel must be passable
- this breakwater must block direct crossing
- this island must create a shadow in the reachable area
- this cove must only be reachable through its entrance
- no polygon segment may intersect blocked geometry

---

# 36. Test Strategy

## 36.1 Unit tests

Fuel:
- interpolation
- inversion
- reserve
- L/nm derivation
- load factor
- sea-state factor

Geometry:
- point classification
- buffers
- segment collision
- depth logic

## 36.2 Integration tests

- grid creation
- refinement
- route generation
- cost field
- contour extraction
- polygon cleanup
- verifier
- cache invalidation

## 36.3 Geographic regression tests

Fixed known Aegean scenarios.

## 36.4 Visual snapshot tests

Generate deterministic renderable GeoJSON snapshots.

Compare:
- shape
- topology
- land intersections
- corridor changes

Do not use image similarity alone as correctness proof.

---

# 37. Acceptance Criteria

V1 engine is not accepted until:

1. all unit tests pass
2. all integration tests pass
3. all known Aegean regression fixtures pass
4. anomaly detector passes
5. independent verifier passes
6. no accepted route intersects blocked land/obstacle geometry
7. no final accepted range polygon leaks through known land barriers
8. narrow corridors are locally refined before acceptance
9. stale async results are never rendered
10. model/data versions are recorded
11. diagnostic snapshot reproduces failures
12. performance targets are met in typical scenarios
13. selected calculations are compared with real Axopar trips

The core success metric is not "looks plausible."

It is:

> **No impossible land-crossing route or reachable-area leak in the validated test set.**

---

# 38. Real-World Axopar Validation

Before calling v1 reliable, compare against real trips.

Record manually where telemetry is unavailable:

- start fuel
- end fuel
- approximate average RPM
- GPS route
- speed
- sea state
- load state

Compare predicted versus actual:
- trip distance
- fuel used
- effective L/nm

Do not overfit the curve to one trip.

---

# 39. Implementation Order

Do not start with polished UI.

## Phase 0 — Repository and contracts

Create:
- monorepo
- shared schemas
- vessel model
- scenario model
- version model
- Docker dev environment
- PostGIS setup

Exit criteria:
- frontend and backend boot
- health check
- migrations work

## Phase 1 — Fuel model

Implement:
- cleaned Axopar curve
- PCHIP interpolation
- derived L/nm
- inverse speed/RPM mapping
- reserve logic
- load factor
- sea-state factor
- unit tests

Exit criteria:
- deterministic numeric tests pass

## Phase 2 — Static marine data

Implement:
- ingestion scripts
- coastline
- obstacles
- restrictions
- depth
- PostGIS indexes
- version manifest
- geometry repair

Exit criteria:
- Bodrum/Kos region can be queried reliably

## Phase 3 — Navigability classification

Implement:
- grid
- water/land
- depth
- safety buffer
- restrictions
- user overrides
- provenance

Exit criteria:
- cell inspection matches expected geography

## Phase 4 — Routing

Implement:
- edge generation
- collision checks
- shortest path
- cost calculation

Exit criteria:
- point routes never cross known obstacles

## Phase 5 — Isochrone engine

Implement:
- cost-field expansion
- fuel bands
- one-way polygon
- contour extraction

Exit criteria:
- correct water-constrained shape around islands/peninsulas

## Phase 6 — Local refinement

Implement:
- corridor detection
- high-res patch generation
- refined re-routing

Exit criteria:
- known narrow passages handled correctly

## Phase 7 — Round-trip model

Implement:
- outbound cost
- return cost
- combined threshold

Exit criteria:
- no half-range approximation

## Phase 8 — Polygon safety

Implement:
- cleanup
- topology validation
- navigation-aware simplification
- anomaly detection

Exit criteria:
- no accepted invalid geometry

## Phase 9 — Independent verifier

Implement:
- boundary samples
- interior samples
- independent route validation
- collision checks

Exit criteria:
- injected corrupt polygons are rejected

## Phase 10 — Developer mode

Implement:
- overlays
- cell diagnostics
- provenance
- snapshot export

Exit criteria:
- broken scenario can be reproduced from JSON

## Phase 11 — PWA UI

Implement:
- marine map
- GPS
- pin
- search
- speed slider
- fuel
- reserve
- sea state
- clearance
- one-way/round-trip
- bands
- tap details
- cancellation/debounce

Exit criteria:
- mobile-first workflow is usable

## Phase 12 — Scenarios / sharing

Implement:
- local-first persistence
- saved scenarios
- comparisons
- share link
- snapshot + recalculate

## Phase 13 — Performance / cache

Implement:
- persistent navigability cache
- refined tile cache
- request budgets
- profiling
- CI benchmarks

## Phase 14 — Real-world validation

Compare against actual Axopar trips.

---

# 40. Core Isochrone Pseudocode

```python
def calculate_range(request):
    vessel = load_vessel(request.vessel_id)

    operating_point = fuel_model.solve(
        vessel=vessel,
        speed_kn=request.speed_kn,
        rpm=request.rpm,
    )

    usable_fuel = compute_usable_fuel(
        tank_capacity=vessel.tank_capacity_l,
        fuel_input=request.fuel,
        reserve_pct=request.reserve_pct,
    )

    effective_lpnm = fuel_model.adjust(
        base_lpnm=operating_point.lpnm,
        load_state=request.load_state,
        sea_state=request.sea_state,
        calibration=request.calibration,
    )

    compute_crs = choose_local_projection(request.origin)

    nav = build_navigability_region(
        origin=request.origin,
        max_theoretical_range_nm=usable_fuel / effective_lpnm,
        clearance_m=request.clearance_m,
        depth_policy=request.depth_policy,
        marine_data_version=request.marine_data_version,
        compute_crs=compute_crs,
    )

    nav = detect_and_refine_ambiguous_corridors(nav)

    graph = build_water_graph(nav)

    outbound = dijkstra_cost_field(
        graph=graph,
        origin=request.origin,
        edge_cost=lambda edge: edge.distance_nm * effective_lpnm
    )

    if request.range_mode == "one_way":
        reachable = outbound <= usable_fuel

    elif request.range_mode == "round_trip":
        return_cost = compute_return_cost_field(
            graph=graph,
            origin=request.origin,
            future_directional_model_ready=True
        )

        reachable = (outbound + return_cost) <= usable_fuel

    contours = extract_threshold_contours(
        cost_field=outbound,
        thresholds=[
            usable_fuel * 0.25,
            usable_fuel * 0.50,
            usable_fuel * 0.75,
            usable_fuel
        ],
        reachable_mask=reachable
    )

    polygons = navigation_aware_cleanup(
        contours=contours,
        nav=nav
    )

    validation = validate_polygons(
        polygons=polygons,
        nav=nav
    )

    if not validation.ok:
        return fail_closed_with_diagnostics(validation)

    verification = independent_verifier(
        origin=request.origin,
        polygons=polygons,
        nav=nav
    )

    if not verification.ok:
        return fail_closed_with_diagnostics(verification)

    confidence = compute_confidence(nav, validation, verification)

    return RangeResult(
        polygons=polygons,
        operating_point=operating_point,
        usable_fuel_l=usable_fuel,
        confidence=confidence,
        versions=current_versions(),
    )
```

---

# 41. Route-Segment Validation Pseudocode

```python
def is_segment_navigable(a, b, context):
    segment = LineString([a, b])

    if segment.intersects(context.land_buffer):
        return False

    if segment.intersects(context.hard_obstacles_buffer):
        return False

    if segment.intersects(context.hard_restrictions):
        return False

    if segment.intersects(context.user_exclusions):
        return False

    if crosses_unsafe_depth(segment, context.depth_surface):
        return False

    return True
```

No shortcutting around this check.

---

# 42. Corridor Refinement Pseudocode

```python
def refine_if_ambiguous(cell_or_corridor, context):
    if not is_ambiguous(cell_or_corridor):
        return original_resolution_result

    high_res_grid = build_local_grid(
        bounds=expanded_bounds(cell_or_corridor),
        resolution_m=25
    )

    classify(high_res_grid, context)

    valid = confirm_connected_passage(
        high_res_grid,
        required_clearance=context.clearance_m
    )

    return valid
```

---

# 43. Failure Behavior

Fail closed when:

- required land data unavailable
- geometry invalid after repair
- polygon intersects blocked terrain
- verifier fails
- local corridor cannot be resolved
- source versions are incompatible
- compute budget exceeded in a way that would require unsafe simplification

Return a clear diagnostic message.

Never replace a failed safe calculation with:
- straight-line radius
- convex hull
- generic buffer
- visual approximation
- coastline clipping after the fact

---

# 44. Explicit "Do Not Do" Rules for Codex

Do not:

1. calculate range as a circle
2. calculate range using only great-circle distance
3. clip a range circle against land and call it navigable
4. create a polygon first and route second
5. use convex hull to hide fragmented reachable cells
6. smooth polygons with splines that can cross land
7. trust a raster cell if a vector obstacle crosses it
8. treat unknown depth as safe in normal mode
9. silently ignore missing hard-required data
10. calculate round-trip range as half of one-way range
11. render stale async calculations
12. trust malformed coastline topology
13. trust infographic L/nm values without checking `L/h / kn`
14. extrapolate the Axopar fuel curve outside measured RPM
15. optimize before correctness tests exist
16. make UI polish the first implementation milestone
17. create fake marine data just to make demos look good
18. weaken collision checks to improve performance
19. hide validator failures
20. claim navigation-grade accuracy

---

# 45. Product Safety Language

The app should state:

> Range estimates are for planning purposes only. They depend on fuel-model assumptions, environmental conditions, vessel load, chart-data quality, and user inputs. Always use official navigation charts and normal seamanship when operating the vessel.

Do not make the disclaimer intrusive on the main map.

---

# 46. Offline Strategy

V1:
- online-first

Architecture:
- service worker
- cached UI shell
- cache previously viewed map data
- design navigability tiles so selected regions can later be downloaded

Future:
- offline maps
- offline navigability mask
- local range calculation

Do not block v1 on full offline support.

---

# 47. Deployment

V1:

- containerized FastAPI backend
- managed PostgreSQL/PostGIS
- frontend deployed separately
- object storage for larger raster/cache artifacts if needed

Design so later:
- compute workers can scale separately
- Redis can be added
- ingestion can run as scheduled jobs

Avoid unnecessary cloud-native complexity in v1.

---

# 48. Definition of Done

The first meaningful milestone is not a pretty map.

It is:

> Given a fixed origin near Bodrum and a fixed Axopar fuel setting, the backend returns a water-constrained reachable area that correctly wraps around islands and peninsulas, passes through real navigable channels, never crosses blocked terrain, and passes an independent verifier.

Only after this works should the main PWA UI be polished.

---

# 49. Codex Working Rules

When implementing:

1. Read this file before changing architecture.
2. Do not reinterpret explicit product decisions without documenting why.
3. Prefer small testable commits.
4. Add tests before fixing any terrain-crossing bug.
5. Every routing bug must become a permanent regression fixture.
6. Do not remove safety checks to make a test pass.
7. Preserve model/data versioning.
8. Keep the fuel model independent from marine routing.
9. Keep marine data ingestion independent from UI.
10. Make diagnostic outputs reproducible.
11. If data quality is insufficient, say so in code/UI rather than inventing confidence.
12. Correctness beats visual smoothness.
13. Correctness beats raw speed.
14. After correctness is proven, optimize against the stated performance budgets.
15. Keep the implementation simple unless complexity is required by a known failure case.

---

# 50. Immediate First Tasks

Codex should begin with:

1. scaffold monorepo
2. create Axopar vessel schema
3. implement cleaned fuel curve
4. implement monotonic interpolation
5. create numeric fuel-model tests
6. configure PostGIS
7. define marine feature schemas
8. ingest one small Bodrum/Kos test region
9. create known land/sea regression fixtures
10. build cell-classification diagnostics
11. implement shortest navigable route
12. prove that a route cannot cross a peninsula
13. implement one-way isochrone
14. prove that isochrone cannot leak through land
15. only then continue to round-trip, refinement, and UI

Do not skip steps 8-14.

