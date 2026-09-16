
-- Auto-generated: ensure PostGIS is available
CREATE EXTENSION IF NOT EXISTS postgis;
-- Migration 003: Navigability tiles (pre-computed, vector-based)
-- In the vector-first approach, these are NOT raster cells.
-- They store pre-computed water polygon fragments that are known navigable
-- at a given resolution and parameter set.

BEGIN;

CREATE TABLE IF NOT EXISTS navigability_tiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region              TEXT NOT NULL,
    resolution_m        REAL NOT NULL CHECK (resolution_m > 0),
    tile_key            TEXT NOT NULL,  -- geographic tile identifier (e.g., "37.0_27.4_150m")
    marine_data_version TEXT NOT NULL,
    params_hash         TEXT NOT NULL,  -- hash of clearance, depth policy, restriction policy
    -- Water polygon fragment for this tile — vector, not raster
    water_polygon_geom  GEOMETRY(POLYGON, 3857),  -- Web Mercator for fast rendering
    blocked_geom        GEOMETRY(POLYGON, 3857),  -- Blocked areas within this tile (land, obstacles)
    graph_node_count    INT DEFAULT 0,
    graph_edge_count    INT DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (region, tile_key, resolution_m, marine_data_version, params_hash)
);

CREATE INDEX idx_nav_tiles_region ON navigability_tiles (region, marine_data_version);
CREATE INDEX idx_nav_tiles_geom ON navigability_tiles USING GIST (water_polygon_geom);

COMMENT ON TABLE navigability_tiles IS 'Pre-computed navigability tiles. Each stores vector water polygon fragments, not raster cell classifications.';

COMMIT;
