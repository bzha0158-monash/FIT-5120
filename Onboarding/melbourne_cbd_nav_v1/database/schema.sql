-- =============================================================================
-- CalmRoute — PostgreSQL / PostGIS Schema
-- Project: SilentWaze / FIT5120 S2 2026
-- Author: Arjun Mekala (Data Science)
-- Region: AWS ap-southeast-2 (Sydney)
-- =============================================================================

-- Enable PostGIS for spatial queries
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- for gen_random_uuid()

-- =============================================================================
-- TABLE 1: SENSOR_LOCATION
-- Source: pedestrian-counting-system-sensor-locations.csv
-- Purpose: Master reference for all pedestrian sensor devices in Melbourne CBD
-- =============================================================================
CREATE TABLE IF NOT EXISTS sensor_location (
    location_id         INTEGER         PRIMARY KEY,
    sensor_description  TEXT,
    sensor_name         VARCHAR(50),
    installation_date   DATE,
    note                TEXT,
    location_type       VARCHAR(20),        -- 'Outdoor' or 'Indoor'
    status              CHAR(1),            -- 'A' = Active
    direction_1         VARCHAR(20),        -- e.g. 'North', 'East'
    direction_2         VARCHAR(20),        -- e.g. 'South', 'West'
    latitude            DOUBLE PRECISION    NOT NULL,
    longitude           DOUBLE PRECISION    NOT NULL,
    geom                GEOMETRY(POINT, 4326),   -- PostGIS spatial point (WGS84)
    created_at          TIMESTAMPTZ         DEFAULT NOW()
);

-- Spatial index on sensor locations for fast proximity queries
CREATE INDEX IF NOT EXISTS idx_sensor_location_geom
    ON sensor_location USING GIST (geom);

-- Trigger to auto-populate geom from lat/lng on insert/update
CREATE OR REPLACE FUNCTION sync_sensor_geom()
RETURNS TRIGGER AS $$
BEGIN
    NEW.geom := ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sensor_geom
    BEFORE INSERT OR UPDATE ON sensor_location
    FOR EACH ROW EXECUTE FUNCTION sync_sensor_geom();


-- =============================================================================
-- TABLE 2: ROAD_NETWORK
-- Source: pedestrian-network.csv
-- Purpose: Walkable road segments and node connections across Melbourne CBD
--          Used by the Route Planning Engine for graph-based pathfinding
-- =============================================================================
CREATE TABLE IF NOT EXISTS road_network (
    object_id       INTEGER         PRIMARY KEY,
    network_id      INTEGER,
    latitude        DOUBLE PRECISION,
    longitude       DOUBLE PRECISION,
    geom            GEOMETRY(POINT, 4326),   -- node point geometry
    geo_shape       JSONB,                   -- full GeoJSON shape from source
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_road_network_geom
    ON road_network USING GIST (geom);

CREATE INDEX IF NOT EXISTS idx_road_network_network_id
    ON road_network (network_id);


-- =============================================================================
-- TABLE 3: PEDESTRIAN_MINUTE_COUNT
-- Source: pedestrian-counting-system-past-hour-counts-per-minute.csv
-- Purpose: Near real-time minute-level pedestrian counts per sensor
--          Refreshed daily via AWS Lambda pipeline
-- =============================================================================
CREATE TABLE IF NOT EXISTS pedestrian_minute_count (
    id                  SERIAL          PRIMARY KEY,
    location_id         INTEGER         NOT NULL REFERENCES sensor_location(location_id),
    sensing_datetime    TIMESTAMPTZ     NOT NULL,
    sensing_date        DATE            NOT NULL,
    sensing_time        TIME            NOT NULL,
    direction_1         INTEGER         DEFAULT 0,
    direction_2         INTEGER         DEFAULT 0,
    total_of_directions INTEGER         GENERATED ALWAYS AS (direction_1 + direction_2) STORED,
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

-- Composite index: queries filter by sensor + time window
CREATE INDEX IF NOT EXISTS idx_pmc_location_datetime
    ON pedestrian_minute_count (location_id, sensing_datetime DESC);

CREATE INDEX IF NOT EXISTS idx_pmc_sensing_date
    ON pedestrian_minute_count (sensing_date);

-- Unique constraint: one reading per sensor per minute
CREATE UNIQUE INDEX IF NOT EXISTS idx_pmc_unique
    ON pedestrian_minute_count (location_id, sensing_datetime);


-- =============================================================================
-- TABLE 4: PEDESTRIAN_HOUR_COUNT
-- Source: pedestrian-counting-system-monthly-counts-per-hour.csv
-- Purpose: Historical hourly aggregated pedestrian counts (2009 - present)
--          Core dataset for training baseline predictions
-- =============================================================================
CREATE TABLE IF NOT EXISTS pedestrian_hour_count (
    id                  SERIAL          PRIMARY KEY,
    location_id         INTEGER         NOT NULL REFERENCES sensor_location(location_id),
    sensing_date        DATE            NOT NULL,
    hour_of_day         SMALLINT        NOT NULL CHECK (hour_of_day BETWEEN 0 AND 23),
    day_type            VARCHAR(10)     NOT NULL CHECK (day_type IN ('weekday', 'weekend')),
    total_of_directions INTEGER         NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

-- Composite index: baseline queries group by sensor + hour + day_type
CREATE INDEX IF NOT EXISTS idx_phc_location_hour
    ON pedestrian_hour_count (location_id, hour_of_day, day_type);

CREATE INDEX IF NOT EXISTS idx_phc_sensing_date
    ON pedestrian_hour_count (sensing_date);

-- Unique constraint: one record per sensor per hour per date
CREATE UNIQUE INDEX IF NOT EXISTS idx_phc_unique
    ON pedestrian_hour_count (location_id, sensing_date, hour_of_day);


-- =============================================================================
-- TABLE 5: CROWD_DENSITY_PREDICTION
-- Source: Computed by Flask app / Lambda from tables 3 + 4
-- Purpose: Stores next-hour crowd density predictions per sensor
--          Drives the predictive alert layer (US 2.2)
-- =============================================================================
CREATE TABLE IF NOT EXISTS crowd_density_prediction (
    id                  SERIAL          PRIMARY KEY,
    location_id         INTEGER         NOT NULL REFERENCES sensor_location(location_id),
    prediction_for      TIMESTAMPTZ     NOT NULL,   -- the hour being predicted
    hour_of_day         SMALLINT        NOT NULL,
    day_type            VARCHAR(10)     NOT NULL,
    current_count       DOUBLE PRECISION,
    expected_count      DOUBLE PRECISION,
    ratio               DOUBLE PRECISION,           -- current / expected
    status              VARCHAR(10)     NOT NULL CHECK (status IN ('low', 'medium', 'high')),
    coverage_radius     INTEGER,                    -- metres: low=40, medium=60, high=85
    geom                GEOMETRY(POINT, 4326),      -- copied from sensor_location
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cdp_location_prediction
    ON crowd_density_prediction (location_id, prediction_for DESC);

CREATE INDEX IF NOT EXISTS idx_cdp_status
    ON crowd_density_prediction (status);

CREATE INDEX IF NOT EXISTS idx_cdp_geom
    ON crowd_density_prediction USING GIST (geom);

-- Keep only latest prediction per sensor (upsert target)
CREATE UNIQUE INDEX IF NOT EXISTS idx_cdp_unique_latest
    ON crowd_density_prediction (location_id, prediction_for);


-- =============================================================================
-- TABLE 6: ROUTE_CROWD_SCORE
-- Source: Computed by Route Planning Engine
-- Purpose: Stores scored routes with crowd-awareness for sensory-friendly routing
--          Powers "Find calm route" feature and alternative route comparison (US 1.1-1.3)
-- =============================================================================
CREATE TABLE IF NOT EXISTS route_crowd_score (
    id                  SERIAL          PRIMARY KEY,
    route_id            UUID            DEFAULT gen_random_uuid() UNIQUE,
    start_lat           DOUBLE PRECISION NOT NULL,
    start_lng           DOUBLE PRECISION NOT NULL,
    end_lat             DOUBLE PRECISION NOT NULL,
    end_lng             DOUBLE PRECISION NOT NULL,
    start_geom          GEOMETRY(POINT, 4326),
    end_geom            GEOMETRY(POINT, 4326),
    sensory_score       DOUBLE PRECISION CHECK (sensory_score BETWEEN 0 AND 1),
                        -- 0 = most calm, 1 = most overwhelming
    high_alert_count    INTEGER         DEFAULT 0,
    medium_alert_count  INTEGER         DEFAULT 0,
    distance_metres     INTEGER,
    duration_seconds    INTEGER,
    route_geojson       JSONB,          -- full route geometry from routing engine
    generated_at        TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rcs_start_geom
    ON route_crowd_score USING GIST (start_geom);

CREATE INDEX IF NOT EXISTS idx_rcs_end_geom
    ON route_crowd_score USING GIST (end_geom);

CREATE INDEX IF NOT EXISTS idx_rcs_sensory_score
    ON route_crowd_score (sensory_score ASC);

-- =============================================================================
-- TABLE 7: REFUGE_LOCATION  (supports US 2.1)
-- Source: landmarks-and-places-of-interest CSV
-- Purpose: Sensory refuge locations (parks, libraries, quiet public spaces)
--          Displayed on map after route is generated
-- =============================================================================
CREATE TABLE IF NOT EXISTS refuge_location (
    id              SERIAL          PRIMARY KEY,
    feature_name    TEXT            NOT NULL,
    theme           VARCHAR(50),
    sub_theme       VARCHAR(100),
    latitude        DOUBLE PRECISION NOT NULL,
    longitude       DOUBLE PRECISION NOT NULL,
    geom            GEOMETRY(POINT, 4326),
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refuge_geom
    ON refuge_location USING GIST (geom);

CREATE TRIGGER trg_refuge_geom
    BEFORE INSERT OR UPDATE ON refuge_location
    FOR EACH ROW EXECUTE FUNCTION sync_sensor_geom();  -- reuse same point sync fn


-- =============================================================================
-- BASELINE VIEW: pre-aggregated hourly averages per sensor
-- Used by /api/crowd to fetch expected counts without recalculating on every request
-- =============================================================================
CREATE OR REPLACE VIEW v_hourly_baseline AS
SELECT
    location_id,
    hour_of_day,
    day_type,
    ROUND(AVG(total_of_directions)::NUMERIC, 2) AS expected_count,
    COUNT(*)                                     AS observation_days
FROM pedestrian_hour_count
GROUP BY location_id, hour_of_day, day_type;
