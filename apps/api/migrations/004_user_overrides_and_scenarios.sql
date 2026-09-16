
-- Auto-generated: ensure PostGIS is available
CREATE EXTENSION IF NOT EXISTS postgis;
-- Migration 004: User overrides and saved scenarios

BEGIN;

-- ============================================================================
-- user_overrides
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_overrides (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        TEXT,  -- nullable for anonymous/local-first
    type            TEXT NOT NULL CHECK (type IN ('blocked_polygon', 'passable_override', 'temporary_exclusion')),
    geom            GEOMETRY NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    source_note     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX idx_user_overrides_owner ON user_overrides (owner_id) WHERE owner_id IS NOT NULL;
CREATE INDEX idx_user_overrides_geom ON user_overrides USING GIST (geom);
CREATE INDEX idx_user_overrides_expires ON user_overrides (expires_at) WHERE expires_at IS NOT NULL;

COMMENT ON TABLE user_overrides IS 'User-defined no-go zones and passable overrides. Stored as separate overlay, never merged into source datasets.';

-- ============================================================================
-- scenarios
-- ============================================================================
CREATE TABLE IF NOT EXISTS scenarios (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vessel_profile_id       TEXT NOT NULL REFERENCES vessel_profiles(id),
    origin_lat              REAL NOT NULL,
    origin_lon              REAL NOT NULL,
    fuel_input              JSONB NOT NULL,  -- {mode, value}
    reserve_pct             REAL NOT NULL DEFAULT 20,
    selected_speed_kn       REAL,
    selected_rpm            INT,
    sea_state               TEXT NOT NULL CHECK (sea_state IN ('calm', 'moderate', 'rough')),
    load_state              TEXT NOT NULL CHECK (load_state IN ('light', 'normal', 'heavy')),
    clearance_m             REAL NOT NULL DEFAULT 50,
    range_mode              TEXT NOT NULL CHECK (range_mode IN ('one_way', 'round_trip')),
    -- Result snapshot (GeoJSON polygons)
    result_snapshot_ref     TEXT,  -- reference to stored result
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    app_version             TEXT,
    fuel_model_version      TEXT,
    marine_data_version     TEXT,
    routing_engine_version  TEXT,
    result_schema_version   TEXT NOT NULL DEFAULT 'v1'
);

CREATE INDEX idx_scenarios_vessel ON scenarios (vessel_profile_id);
CREATE INDEX idx_scenarios_created ON scenarios (created_at DESC);
CREATE INDEX idx_scenarios_origin ON scenarios USING gist (
    ST_SetSRID(ST_MakePoint(origin_lon, origin_lat), 4326)
);

COMMENT ON TABLE scenarios IS 'Saved calculation scenarios with full parameter and result snapshots.';

COMMIT;
