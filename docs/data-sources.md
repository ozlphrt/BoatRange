# Marine data sources

## Bodrum/Kos bathymetry

The Bodrum/Kos depth layer is a geographic subset of the EMODnet Bathymetry
Digital Terrain Model 2024. It was requested from EMODnet's official Web
Coverage Service as the `emodnet:mean` coverage in EPSG:4326 using nearest
sampling at 1/16 arc minute resolution, approximately 115 metres.

- Coverage: `26.85,36.60,27.85,37.25`
- Elevation convention: depths are negative elevations in metres
- Vertical reference: Lowest Astronomical Tide (LAT)
- Local artifact: `data/fixtures/bodrum_kos_real/depth-emodnet-2024.tif`
- Manifest: `data/manifests/bodrum_kos_bathymetry.json`
- Reproducible fetch: `scripts/ingest/fetch_bodrum_kos_emodnet.py`

The classifier accepts a pixel only when it has a finite value and its
elevation is at or below the negative vessel-specific minimum safe depth.
Known shallow pixels and unknown pixels are blocked. The result is intersected
with the independently ingested coastline water geometry before graph
construction.

The DTM resolution is similar to the routing base grid and cannot establish
the safety of rocks, harbor entrances, dredged channels, breakwaters, or
rapid near-shore changes. Results remain LOW confidence and are for planning
only. Official navigation charts and normal seamanship remain required.

Source documentation:

- https://emodnet.ec.europa.eu/en/bathymetry
- https://emodnet.ec.europa.eu/en/emodnet-web-service-documentation

## Bodrum/Kos physical obstacles and restrictions

OpenStreetMap and OpenSeaMap seamark tags were queried through Overpass for
breakwaters, groynes, rocks, wrecks, obstructions, and restricted areas. The
normalized snapshot contains 168 features: 56 breakwaters, one groyne, 109
rocks, one wreck, and one obstruction. No restricted-area polygon was
returned; this records missing legal coverage and does not assert that the
region has no restrictions.

Physical obstacles are `HARD_NO_GO`. A restricted area is hard only when its
tags explicitly prohibit entry or navigation. Other restricted areas remain
`CAUTION_CONDITIONAL`; unknown seamarks remain `INFORMATIONAL`. Every feature
retains its OSM element ID, raw tags, source version, and classification rule.

- Manifest: `data/manifests/bodrum_kos_marine_features.json`
- Normalized data: `data/fixtures/bodrum_kos_real/marine-features.geojson`
- Reproducible ingestion: `scripts/ingest/fetch_bodrum_kos_osm_features.py`

## Bodrum/Kos official managed areas and notices

`scripts/ingest/fetch_bodrum_kos_official_areas.py` queries the European
Commission DG MARE EMODnet Human Activities WFS for military-area and Natura
2000 polygons, clips them to the region, validates geometry, records raw and
normalized checksums, and generates
`data/manifests/bodrum_kos_official_areas.json`.

The 2026-09-14 snapshot contains eight Natura 2000 polygons and no military
polygons in the Bodrum/Kos bbox. Natura designation alone does not prohibit
navigation, so these polygons are `INFORMATIONAL`. The upstream Natura release
date is 2020-06-30 and is surfaced as a freshness warning.

HNHS Pilot D amendments identify a seasonal vessel-transit prohibition near
Cape Louros and a 100 m sailing/anchoring/fishing prohibition around abandoned
mining structures on Nisyros. The publication does not provide a usable
machine-readable boundary for either rule. They are stored separately in
`official-unresolved-notices.json` as `CAUTION_CONDITIONAL`; the application
does not invent boundaries or silently claim they are enforced by routing.

## Bodrum/Kos marinas and marine fuel points

`scripts/ingest/fetch_bodrum_kos_marine_pois.py` queries QLever's OSM planet
snapshot for `leisure=marina`, `seamark:type=harbour`, `waterway=fuel`, and
`seamark:small_craft_facility:category=fuel_station`. It converts area and
line facilities to display centroids while retaining the original OSM element
ID, tags, source version, confidence, and classification rule.

The 2026-09-16 snapshot contains 33 POIs: 29 marinas, two harbours, and two
explicitly tagged marine fuel docks. The app displays all of them and marks a
fuel dock reachable only when its point is covered by the independently
validated outer isochrone. It does not infer fuel availability from a marina
name or nearby road-fuel station.

- Manifest: `data/manifests/bodrum_kos_marine_pois.json`
- Normalized data: `data/fixtures/bodrum_kos_real/marine-pois.geojson`
- Reproducible ingestion: `scripts/ingest/fetch_bodrum_kos_marine_pois.py`

These POIs are community-maintained planning references. A mapped fuel point
does not establish current stock, opening hours, berth access, safe approach,
or gasoline compatibility.
