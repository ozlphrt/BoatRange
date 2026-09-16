# Developer diagnostics

Developer mode exposes the evidence used to accept or reject a result without
changing normal planner behavior. Enable **Geliştirici tanıları** in the PWA.
The map then shows the classified safe-depth boundary, clearance buffers,
sampled fuel-cost grid, locally refined nodes, ingested hard obstacles, and
final fuel contours. It supports point inspection
and offers the completed range calculation as a downloadable JSON snapshot.

The snapshot includes the normalized request, stable SHA-256 request hash,
vessel and region identities, fuel and routing settings, data/model versions,
source layer checksums, source feature IDs and classification rules, graph and
local-refinement counts, final WGS84 band polygons, verifier sample results,
anomalies, warnings, random seed, and compute timing. Request IDs and timing do
not participate in the stable hash.

Developer-only API endpoints:

- `POST /api/v1/verify` independently checks supplied Polygon/MultiPolygon
  GeoJSON against classified water, raw obstacles, clearance, topology, origin,
  and deterministic boundary/interior samples.
- `POST /api/v1/diagnostics/classify-point` reports coastline, bathymetry, and
  nearby hard-feature contributions for a tapped point.
- `POST /api/v1/diagnostics/classify-segment` reports exact source IDs, rules,
  and clearance reasons for a rejected candidate edge.

All three require `developerMode: true`. Verification is fail closed: malformed,
empty, non-polygonal, off-water, or obstacle-intersecting geometry is rejected.
