
-- Auto-generated: ensure PostGIS is available
CREATE EXTENSION IF NOT EXISTS postgis;
-- Migration 001: Vessel profiles and fuel curve data
-- Creates the vessel schema and populates Axopar 28 v1 data.

BEGIN;

-- ============================================================================
-- vessel_profiles
-- ============================================================================
CREATE TABLE IF NOT EXISTS vessel_profiles (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    manufacturer    TEXT NOT NULL,
    model           TEXT NOT NULL,
    year            INT NOT NULL,
    propulsion_type TEXT NOT NULL CHECK (propulsion_type IN ('outboard', 'inboard', 'stern_drive', 'waterjet', 'electric')),
    engine_count    INT NOT NULL DEFAULT 1 CHECK (engine_count > 0),
    fuel_type       TEXT NOT NULL DEFAULT 'gasoline' CHECK (fuel_type IN ('gasoline', 'diesel', 'electric')),
    tank_capacity_l REAL NOT NULL CHECK (tank_capacity_l > 0),
    hull_draft_m    REAL NOT NULL CHECK (hull_draft_m > 0),
    propulsion_depth_m REAL NOT NULL CHECK (propulsion_depth_m >= hull_draft_m),
    default_reserve_pct REAL NOT NULL DEFAULT 20 CHECK (default_reserve_pct >= 0 AND default_reserve_pct < 100),
    fuel_model_version TEXT NOT NULL DEFAULT 'v1',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_vessel_profiles_manufacturer ON vessel_profiles (manufacturer, model);

COMMENT ON TABLE vessel_profiles IS 'Boat profile definitions. Generic enough for future vessels.';

-- ============================================================================
-- fuel_curve_points
-- ============================================================================
CREATE TABLE IF NOT EXISTS fuel_curve_points (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vessel_profile_id   TEXT NOT NULL REFERENCES vessel_profiles(id) ON DELETE CASCADE,
    rpm                 INT NOT NULL CHECK (rpm > 0),
    speed_kn            REAL NOT NULL CHECK (speed_kn > 0),
    fuel_lph            REAL NOT NULL CHECK (fuel_lph > 0),
    raw_lpnm_optional   REAL,
    derived_lpnm        REAL NOT NULL CHECK (derived_lpnm > 0),
    source              TEXT NOT NULL,
    confidence          REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    is_raw              BOOLEAN NOT NULL DEFAULT TRUE,
    model_version       TEXT NOT NULL DEFAULT 'v1',
    UNIQUE (vessel_profile_id, rpm)
);

CREATE INDEX idx_fuel_curve_vessel ON fuel_curve_points (vessel_profile_id, rpm);

COMMENT ON TABLE fuel_curve_points IS 'Fuel consumption curve data. Source of truth: RPM, speed_kn, fuel_lph.';
COMMENT ON COLUMN fuel_curve_points.derived_lpnm IS 'Computed as fuel_lph / speed_kn. Never trusted blindly from source.';

-- ============================================================================
-- Insert Axopar 28 2019 Verado 300 (cleaned data from CLAUDE.md section 4.2)
-- ============================================================================

INSERT INTO vessel_profiles (
    id, name, manufacturer, model, year, propulsion_type, engine_count,
    fuel_type, tank_capacity_l, hull_draft_m, propulsion_depth_m,
    default_reserve_pct, fuel_model_version
) VALUES (
    'axopar-28-2019-verado-300',
    'Axopar 28',
    'Axopar',
    '28',
    2019,
    'outboard',
    1,
    'gasoline',
    750,
    1.35,
    1.50,
    20,
    'v1'
);

-- Fuel curve points (cleaned and reconciled)
INSERT INTO fuel_curve_points (vessel_profile_id, rpm, speed_kn, fuel_lph, derived_lpnm, source, confidence, is_raw, model_version) VALUES
('axopar-28-2019-verado-300', 1000, 5.0,  6.0,  1.20, 'axopar_performance_graphic_reconciled', 0.95, TRUE, 'v1'),
('axopar-28-2019-verado-300', 1500, 6.0,  8.0,  1.33, 'axopar_performance_graphic_reconciled', 0.95, TRUE, 'v1'),
('axopar-28-2019-verado-300', 2000, 8.0,  13.0, 1.63, 'axopar_performance_graphic_reconciled', 0.90, TRUE, 'v1'),
('axopar-28-2019-verado-300', 3200, 13.0, 27.0, 2.08, 'axopar_performance_graphic_reconciled', 0.90, TRUE, 'v1'),
('axopar-28-2019-verado-300', 3500, 16.9, 38.0, 2.25, 'axopar_performance_graphic_reconciled_3500_rpm', 0.92, TRUE, 'v1'),
('axopar-28-2019-verado-300', 4000, 20.0, 43.8, 2.19, 'axopar_performance_graphic_reconciled_4000_rpm', 0.92, TRUE, 'v1'),
('axopar-28-2019-verado-300', 4500, 26.0, 60.5, 2.33, 'axopar_performance_graphic_reconciled', 0.90, TRUE, 'v1'),
('axopar-28-2019-verado-300', 5000, 31.0, 75.5, 2.44, 'axopar_performance_graphic_reconciled', 0.90, TRUE, 'v1'),
('axopar-28-2019-verado-300', 5400, 35.0, 87.2, 2.49, 'axopar_performance_graphic_reconciled', 0.88, TRUE, 'v1');

COMMIT;
