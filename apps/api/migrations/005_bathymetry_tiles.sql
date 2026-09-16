-- Migration 005: versioned bathymetry raster references and provenance.
BEGIN;

-- Repair the spelling used by migration 002 in already-created databases.
ALTER TABLE marine_features
    DROP CONSTRAINT IF EXISTS marine_features_feature_type_check;
ALTER TABLE marine_features
    ADD CONSTRAINT marine_features_feature_type_check CHECK (
        feature_type IN (
            'coastline', 'obstacle', 'restriction', 'depth_sounding',
            'marina', 'fuel_dock', 'port', 'user_exclusion', 'user_override'
        )
    );

CREATE TABLE IF NOT EXISTS bathymetry_tiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    layer_id            UUID NOT NULL REFERENCES marine_layers(id) ON DELETE CASCADE,
    tile_key            TEXT NOT NULL,
    raster_ref          TEXT NOT NULL,
    geom                GEOMETRY(POLYGON, 4326) NOT NULL,
    horizontal_crs      TEXT NOT NULL DEFAULT 'EPSG:4326',
    vertical_datum      TEXT NOT NULL,
    resolution_m        REAL NOT NULL CHECK (resolution_m > 0),
    nodata_value        REAL,
    checksum_sha256     TEXT NOT NULL,
    properties          JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (layer_id, tile_key)
);

CREATE INDEX idx_bathymetry_tiles_geom ON bathymetry_tiles USING GIST (geom);
CREATE INDEX idx_bathymetry_tiles_layer ON bathymetry_tiles (layer_id);

COMMENT ON TABLE bathymetry_tiles IS
    'Versioned references to bathymetry rasters; missing pixels remain unknown and blocked in conservative mode.';

COMMIT;
