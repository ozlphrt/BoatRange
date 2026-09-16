
-- Auto-generated: ensure PostGIS is available
CREATE EXTENSION IF NOT EXISTS postgis;
-- Migration 002: Marine features (vector-based, not raster)
-- This is the core of the vector-first approach. All marine data is stored
-- as PostGIS vector geometries and classified by type and restriction level.

BEGIN;

-- ============================================================================
-- marine_layers
-- ============================================================================
CREATE TABLE IF NOT EXISTS marine_layers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layer_type      TEXT NOT NULL,
    source_name     TEXT NOT NULL,
    source_version  TEXT NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_from      TIMESTAMPTZ,
    stale_after     TIMESTAMPTZ,
    region_id       TEXT NOT NULL,
    metadata_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    checksum        TEXT,  -- SHA-256 of source data for integrity
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX idx_marine_layers_region ON marine_layers (region_id);
CREATE INDEX idx_marine_layers_stale ON marine_layers (stale_after) WHERE is_active;

COMMENT ON TABLE marine_layers IS 'Source datasets for marine navigation. Each layer has versioning and freshness tracking.';

-- ============================================================================
-- marine_features
-- ============================================================================
CREATE TABLE IF NOT EXISTS marine_features (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layer_id            UUID NOT NULL REFERENCES marine_layers(id) ON DELETE CASCADE,
    feature_type        TEXT NOT NULL CHECK (
        feature_type IN (
            'coastline', 'obstacle', 'restriction', 'depth_sounding',
            'marina', 'fuel_dock', 'port', 'user_exclusion', 'user_override'
        )
    ),
    restriction_class   TEXT CHECK (
        restriction_class IN ('HARD_NO_GO', 'CAUTION_CONDITIONAL', 'INFORMATIONAL')
    ),
    -- PostGIS geometry — the vector data
    geom                GEOMETRY NOT NULL,
    properties          JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Provenance: which classification rule made this navigable/blocked
    classification_rule TEXT,
    -- Confidence in the source data
    data_confidence     REAL DEFAULT 1.0 CHECK (data_confidence >= 0 AND data_confidence <= 1)
);

-- Spatial index on geometry — critical for intersection queries
CREATE INDEX idx_marine_features_geom ON marine_features USING GIST (geom);
CREATE INDEX idx_marine_features_type ON marine_features (feature_type);
CREATE INDEX idx_marine_features_restriction ON marine_features (restriction_class) WHERE restriction_class IS NOT NULL;

COMMENT ON TABLE marine_features IS 'Vector geometries of marine navigation data. The building blocks of the water polygon graph.';
COMMENT ON COLUMN marine_features.geom IS 'PostGIS geometry — Point, LineString, Polygon, or MultiPolygon.';
COMMENT ON COLUMN marine_features.classification_rule IS 'Which rule determined this feature is navigable/blocked. For provenance and developer mode.';

-- ============================================================================
-- Pre-computed water polygons table
-- ============================================================================
-- This stores the union of all "passable water" geometry for a region.
-- Computed during data ingestion, not at query time.
-- Used as the base graph for routing.

CREATE TABLE IF NOT EXISTS water_polygons (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    region_id           TEXT NOT NULL,
    marine_data_version TEXT NOT NULL,
    -- The navigable water geometry (union of all passable water areas)
    geom                GEOMETRY(MULTIPOLYGON, 4326) NOT NULL,
    -- Metadata about what features were included/excluded
    inclusion_summary   JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (region_id, marine_data_version)
);

CREATE INDEX idx_water_polygons_region ON water_polygons (region_id);
CREATE INDEX idx_water_polygons_geom ON water_polygons USING GIST (geom);

COMMENT ON TABLE water_polygons IS 'Pre-computed navigable water polygons for a region. This is the vector graph base — NOT raster cells.';

COMMIT;
