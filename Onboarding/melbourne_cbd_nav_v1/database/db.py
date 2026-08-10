"""
SilentWaze — Database Connection Module
========================================
Replaces CSV-based data loading in app.py with live PostgreSQL queries.
All queries read from AWS RDS (PostgreSQL + PostGIS).

Usage in app.py:
    from database.db import get_crowd_data, get_safe_spaces
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Generator

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
# Set in .env: DATABASE_URL=postgresql://user:pass@rds-endpoint:5432/silentwaze


@contextmanager
def get_db() -> Generator[psycopg2.extensions.cursor, None, None]:
    """Context manager: yields a dict cursor, auto-commits, closes on exit."""
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def get_crowd_data() -> list[dict]:
    """
    Returns per-sensor crowd status for the next hour.
    Replaces the CSV-based /api/crowd logic in app.py.

    Classification thresholds (matching original app.py):
        high   : count >= 180 OR ratio >= 1.5
        medium : count >= 80  OR ratio >= 1.15
        low    : everything else
    """
    now       = datetime.now()
    next_hour = (now.hour + 1) % 24
    day_type  = "weekend" if now.weekday() >= 5 else "weekday"

    with get_db() as cur:
        cur.execute("""
            SELECT
                sl.location_id,
                sl.sensor_description,
                sl.sensor_name,
                sl.latitude,
                sl.longitude,

                -- latest real-time count
                COALESCE(mc.total_of_directions, 0)         AS current_count,

                -- historical baseline for next hour
                COALESCE(b.expected_count, 0)               AS expected_count,

                -- ratio: current vs expected
                CASE
                    WHEN COALESCE(b.expected_count, 0) = 0 THEN 1.0
                    ELSE COALESCE(mc.total_of_directions, 0)::FLOAT
                         / b.expected_count
                END                                         AS ratio,

                -- crowd status classification
                CASE
                    WHEN COALESCE(mc.total_of_directions, 0) >= 180
                         OR (CASE WHEN COALESCE(b.expected_count,0)=0 THEN 1.0
                             ELSE COALESCE(mc.total_of_directions,0)::FLOAT/b.expected_count
                             END) >= 1.5                    THEN 'high'
                    WHEN COALESCE(mc.total_of_directions, 0) >= 80
                         OR (CASE WHEN COALESCE(b.expected_count,0)=0 THEN 1.0
                             ELSE COALESCE(mc.total_of_directions,0)::FLOAT/b.expected_count
                             END) >= 1.15                   THEN 'medium'
                    ELSE 'low'
                END                                         AS status

            FROM sensor_location sl

            -- latest minute-count per sensor
            LEFT JOIN LATERAL (
                SELECT total_of_directions
                FROM pedestrian_minute_count pmc
                WHERE pmc.location_id = sl.location_id
                ORDER BY sensing_datetime DESC
                LIMIT 1
            ) mc ON TRUE

            -- baseline for the next hour
            LEFT JOIN v_hourly_baseline b
                ON  b.location_id = sl.location_id
                AND b.hour_of_day = %(next_hour)s
                AND b.day_type    = %(day_type)s

            ORDER BY sl.location_id
        """, {"next_hour": next_hour, "day_type": day_type})

        rows = cur.fetchall()

    # Add coverage radius (matches original app.py logic)
    radius_map = {"low": 40, "medium": 60, "high": 85}
    result = []
    for row in rows:
        r = dict(row)
        r["coverage_radius"] = radius_map.get(r["status"], 40)
        result.append(r)

    return result


def get_safe_spaces() -> list[dict]:
    """
    Returns sensory refuge locations (parks, libraries, quiet spaces) in Melbourne CBD.
    Replaces the CSV-based /api/safe-spaces logic in app.py.
    """
    with get_db() as cur:
        cur.execute("""
            SELECT
                feature_name    AS "Feature Name",
                theme           AS "Theme",
                sub_theme       AS "Sub Theme",
                latitude        AS "Latitude",
                longitude       AS "Longitude"
            FROM refuge_location
            ORDER BY feature_name
        """)
        return [dict(r) for r in cur.fetchall()]


def get_latest_predictions() -> list[dict]:
    """
    Returns the most recent crowd density prediction per sensor.
    Used for pre-computed alerts stored by Lambda pipeline.
    """
    with get_db() as cur:
        cur.execute("""
            SELECT DISTINCT ON (location_id)
                location_id,
                status,
                expected_count,
                current_count,
                ratio,
                coverage_radius,
                prediction_for
            FROM crowd_density_prediction
            ORDER BY location_id, prediction_for DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def upsert_prediction(location_id: int, prediction_for: datetime,
                       hour_of_day: int, day_type: str,
                       current_count: float, expected_count: float,
                       ratio: float, status: str, coverage_radius: int) -> None:
    """
    Insert or update a crowd density prediction row.
    Called by the Lambda function after each daily data refresh.
    """
    with get_db() as cur:
        cur.execute("""
            INSERT INTO crowd_density_prediction
                (location_id, prediction_for, hour_of_day, day_type,
                 current_count, expected_count, ratio, status, coverage_radius,
                 geom)
            SELECT
                %(location_id)s, %(prediction_for)s, %(hour_of_day)s, %(day_type)s,
                %(current_count)s, %(expected_count)s, %(ratio)s, %(status)s,
                %(coverage_radius)s,
                sl.geom
            FROM sensor_location sl
            WHERE sl.location_id = %(location_id)s
            ON CONFLICT (location_id, prediction_for) DO UPDATE SET
                current_count   = EXCLUDED.current_count,
                expected_count  = EXCLUDED.expected_count,
                ratio           = EXCLUDED.ratio,
                status          = EXCLUDED.status,
                coverage_radius = EXCLUDED.coverage_radius
        """, {
            "location_id": location_id,
            "prediction_for": prediction_for,
            "hour_of_day": hour_of_day,
            "day_type": day_type,
            "current_count": current_count,
            "expected_count": expected_count,
            "ratio": ratio,
            "status": status,
            "coverage_radius": coverage_radius,
        })
