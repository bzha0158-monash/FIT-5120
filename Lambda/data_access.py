"""
Data access layer for the crowd prediction Lambda.
Replaces the CSV-based loaders in the original prototype with RDS/PostGIS
queries against the core tables from the system design: SENSOR_LOCATION,
PEDESTRIAN_MINUTE_COUNT, PEDESTRIAN_HOUR_COUNT, CROWD_DENSITY_PREDICTION.

Assumes lowercase snake_case table/column names (Postgres default when
tables are created unquoted). Adjust names below if your actual DDL differs.
"""
from __future__ import annotations

import os
from datetime import timedelta

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# Melbourne CBD demo boundary — same box as the prototype.
LAT_MIN, LAT_MAX = -37.8260, -37.7970
LON_MIN, LON_MAX = 144.9450, 144.9790


def get_connection():
    """Open a connection to RDS using env vars configured on the Lambda function."""
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=5,
    )


def load_sensor_locations(conn) -> pd.DataFrame:
    query = """
        SELECT location_id, sensor_description, sensor_name, location_type,
               latitude, longitude
        FROM sensor_location
        WHERE latitude BETWEEN %s AND %s
          AND longitude BETWEEN %s AND %s
    """
    return pd.read_sql(query, conn, params=(LAT_MIN, LAT_MAX, LON_MIN, LON_MAX))


def load_recent_hour_totals(conn) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """Sum the latest available 60 minutes of counts per sensor."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(sensing_datetime) FROM pedestrian_minute_count")
        (latest_time,) = cur.fetchone()

    if latest_time is None:
        return pd.DataFrame(columns=["location_id", "current_count"]), None

    latest_time = pd.Timestamp(latest_time)
    window_start = latest_time - timedelta(minutes=59)

    query = """
        SELECT location_id, SUM(total_of_directions) AS current_count
        FROM pedestrian_minute_count
        WHERE sensing_datetime BETWEEN %s AND %s
        GROUP BY location_id
    """
    totals = pd.read_sql(query, conn, params=(window_start, latest_time))
    return totals, latest_time


def load_historical_stats(conn, hour: int, day_of_week: int, location_ids: list) -> pd.DataFrame:
    """
    Historical mean and 33rd/66th percentiles for a specific hour-of-day and
    day-of-week, per sensor — computed in SQL via percentile_cont, mirroring
    the prototype's in-memory pandas quantile logic.
    """
    if not location_ids:
        return pd.DataFrame(
            columns=["location_id", "expected_count", "low_threshold", "high_threshold"]
        )

    query = """
        SELECT
            location_id,
            AVG(total_of_directions) AS expected_count,
            PERCENTILE_CONT(0.3333) WITHIN GROUP (ORDER BY total_of_directions) AS low_threshold,
            PERCENTILE_CONT(0.6667) WITHIN GROUP (ORDER BY total_of_directions) AS high_threshold
        FROM pedestrian_hour_count
        WHERE hour_day = %s
          AND EXTRACT(DOW FROM sensing_date) = %s
          AND location_id = ANY(%s)
        GROUP BY location_id
    """
    return pd.read_sql(query, conn, params=(hour, day_of_week, location_ids))


def write_predictions(conn, predictions: pd.DataFrame) -> None:
    """
    Upsert forecast rows into CROWD_DENSITY_PREDICTION.
    Requires a unique constraint on (location_id, predicted_time) in that
    table for ON CONFLICT to work — add one via migration if it's missing:
        ALTER TABLE crowd_density_prediction
        ADD CONSTRAINT uq_location_time UNIQUE (location_id, predicted_time);
    """
    if predictions.empty:
        return

    rows = list(
        predictions[
            ["location_id", "predicted_time", "expected_count", "forecast_level", "forecast_method"]
        ].itertuples(index=False, name=None)
    )

    query = """
        INSERT INTO crowd_density_prediction
            (location_id, predicted_time, expected_count, forecast_level, forecast_method)
        VALUES %s
        ON CONFLICT (location_id, predicted_time)
        DO UPDATE SET
            expected_count = EXCLUDED.expected_count,
            forecast_level = EXCLUDED.forecast_level,
            forecast_method = EXCLUDED.forecast_method
    """
    with conn.cursor() as cur:
        execute_values(cur, query, rows)
    conn.commit()
