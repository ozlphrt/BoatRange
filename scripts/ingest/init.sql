-- Initialize PostGIS extension and run all migrations in order.

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Run migrations in order
\i /docker-entrypoint-initdb.d/001_vessel_and_fuel.sql
\i /docker-entrypoint-initdb.d/002_marine_features.sql
\i /docker-entrypoint-initdb.d/003_navigability_tiles.sql
\i /docker-entrypoint-initdb.d/004_user_overrides_and_scenarios.sql
