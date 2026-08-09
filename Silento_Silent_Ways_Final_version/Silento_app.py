from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd
import psycopg
from flask import Flask, jsonify, render_template
from psycopg.rows import dict_row


# ============================================================
# 1. APPLICATION AND DATABASE CONFIGURATION
# ============================================================
# The map intentionally focuses on a small Melbourne CBD bounding box.
# Applying the same boundary in SQL reduces the amount of data transferred
# from RDS and prevents out-of-scope records from reaching the browser.
LAT_MIN, LAT_MAX = -37.8260, -37.7970
LON_MIN, LON_MAX = 144.9450, 144.9790

app = Flask(__name__)


def get_db_connection():
    """Create a short-lived PostgreSQL connection from environment variables.

    Credentials remain in the server environment (local PowerShell variables
    or Elastic Beanstalk environment properties). They are never embedded in
    JavaScript or returned to the browser.

    A new connection is opened for each query and closed by the caller's
    context manager. This simple lifecycle is appropriate for the current
    low-traffic prototype. A production system with sustained traffic should
    use a connection pool or RDS Proxy to avoid repeatedly opening connections.
    """
    required_variables = [
        "DB_HOST",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ]

    missing = [
        name
        for name in required_variables
        if not os.environ.get(name)
    ]

    if missing:
        raise RuntimeError(
            f"Missing database environment variables: {', '.join(missing)}"
        )

    return psycopg.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
        connect_timeout=10,
        row_factory=dict_row,
    )


def query_dataframe(sql: str, parameters=None) -> pd.DataFrame:
    """Execute a parameterised read query and return its rows as a DataFrame.

    SQL values are supplied separately through ``parameters`` rather than
    interpolated into the SQL text. Psycopg therefore performs the quoting and
    type conversion, which avoids SQL-injection and formatting mistakes.

    ``dict_row`` makes every database row a mapping from column name to value.
    Constructing a DataFrame from those mappings preserves the SQL aliases
    expected by the existing front-end API contract.
    """
    with get_db_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, parameters or ())
            rows = cursor.fetchall()

    return pd.DataFrame(rows)


def dataframe_records(dataframe: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame into JSON-safe record dictionaries.

    Pandas represents missing values as NaN/NaT, while standard JSON represents
    missing values as ``null``. Converting to object dtype allows Python
    ``None`` values, which Flask serialises as JSON ``null``.
    """
    clean = dataframe.astype(object).where(pd.notna(dataframe), None)
    return clean.to_dict("records")


# ============================================================
# 2. REFERENCE-DATA QUERIES
# ============================================================
@lru_cache(maxsize=1)
def load_sensor_locations() -> pd.DataFrame:
    """Load CBD sensor metadata from RDS.

    Sensor locations change infrequently, so the result is cached in each web
    process. This avoids repeating an identical RDS query for every request.
    Restarting the application clears the cache after reference data changes.

    The quoted aliases deliberately preserve the field names already consumed
    by ``static/app.js``. This lets the storage layer use PostgreSQL-style
    lowercase names without forcing a simultaneous front-end rewrite.
    """
    return query_dataframe(
        """
        SELECT
            location_id AS "Location_ID",
            sensor_description AS "Sensor_Description",
            sensor_name AS "Sensor_Name",
            location_type AS "Location_Type",
            latitude AS "Latitude",
            longitude AS "Longitude"
        FROM sensor_location
        WHERE latitude BETWEEN %s AND %s
          AND longitude BETWEEN %s AND %s
        ORDER BY location_id
        """,
        (
            LAT_MIN,
            LAT_MAX,
            LON_MIN,
            LON_MAX,
        ),
    )


@lru_cache(maxsize=1)
def load_safe_spaces() -> pd.DataFrame:
    """Load candidate sensory-refuge locations from RDS.

    As with sensor metadata, these locations are treated as reference data and
    cached per web process. Duplicate source rows are intentionally left to the
    database/data-governance workflow; the application reads the table without
    modifying team-owned data.
    """
    return query_dataframe(
        """
        SELECT
            feature_name AS "Feature Name",
            theme AS "Theme",
            sub_theme AS "Sub Theme",
            latitude AS "Latitude",
            longitude AS "Longitude"
        FROM refuge_location
        WHERE latitude BETWEEN %s AND %s
          AND longitude BETWEEN %s AND %s
        ORDER BY feature_name
        """,
        (
            LAT_MIN,
            LAT_MAX,
            LON_MIN,
            LON_MAX,
        ),
    )


# ============================================================
# 3. FLASK ROUTES / API ENDPOINTS
# ============================================================
@app.get("/health")
def health():
    """Return a lightweight process-health response for Elastic Beanstalk.

    This endpoint deliberately does not query RDS. A temporary database outage
    should not cause Elastic Beanstalk to classify the Python web process itself
    as unhealthy and repeatedly replace the instance.
    """
    return jsonify({
        "status": "ok",
        "service": "silent-waze-web",
    }), 200


@app.get("/")
def index():
    """Render the single-page map application."""
    return render_template("index.html")


@app.get("/api/crowd")
def crowd():
    """Return every CBD sensor with its most recent crowd prediction.

    Query algorithm
    ---------------
    1. ``latest_prediction`` uses PostgreSQL ``DISTINCT ON (location_id)``.
       Rows are ordered by ``prediction_for DESC``, so the first row retained
       for each sensor is its newest available prediction.
    2. The latest prediction set is LEFT JOINed to ``sensor_location``. The
       sensor table is intentionally on the left so every known CBD sensor is
       returned even when the prediction pipeline has not produced a row for
       it yet.
    3. ``COALESCE`` converts missing prediction status to ``no_data`` and a
       missing coverage radius to zero. The front end can therefore skip the
       coverage circle without treating a missing prediction as an API error.
    4. Sensor field aliases preserve the existing browser contract, while the
       prediction fields use lower-case JSON names already expected by the map.

    Complexity is dominated by selecting the newest prediction per location.
    A database index on ``(location_id, prediction_for DESC)`` is beneficial,
    but index ownership remains with the database team.
    """
    try:
        result = query_dataframe(
            """
            WITH latest_prediction AS (
                SELECT DISTINCT ON (location_id)
                    location_id,
                    prediction_for,
                    hour_of_day,
                    day_type,
                    current_count,
                    expected_count,
                    ratio,
                    status,
                    coverage_radius
                FROM crowd_density_prediction
                ORDER BY location_id, prediction_for DESC
            )
            SELECT
                sl.location_id AS "Location_ID",
                sl.sensor_description AS "Sensor_Description",
                sl.sensor_name AS "Sensor_Name",
                sl.location_type AS "Location_Type",
                sl.latitude AS "Latitude",
                sl.longitude AS "Longitude",
                prediction.current_count AS "current_count",
                prediction.expected_count AS "expected_count",
                prediction.ratio AS "ratio",
                COALESCE(
                    prediction.status,
                    'no_data'
                ) AS "current_level",
                COALESCE(
                    prediction.status,
                    'no_data'
                ) AS "forecast_level",
                COALESCE(
                    prediction.status,
                    'no_data'
                ) AS "status",
                COALESCE(
                    prediction.coverage_radius,
                    0
                ) AS "coverage_radius",
                prediction.prediction_for::text AS "reference_time",
                prediction.hour_of_day AS "forecast_hour",
                CASE
                    WHEN prediction.location_id IS NOT NULL
                        THEN 'database_prediction'
                    ELSE 'unavailable'
                END AS "forecast_method"
            FROM sensor_location AS sl
            LEFT JOIN latest_prediction AS prediction
                ON prediction.location_id = sl.location_id
            WHERE sl.latitude BETWEEN %s AND %s
              AND sl.longitude BETWEEN %s AND %s
            ORDER BY sl.location_id
            """,
            (
                LAT_MIN,
                LAT_MAX,
                LON_MIN,
                LON_MAX,
            ),
        )

        return jsonify(dataframe_records(result))

    except Exception:
        # Keep connection details out of the HTTP response. The full exception
        # is still written to the application log for operational diagnosis.
        app.logger.exception("Failed to query crowd predictions")
        return jsonify({
            "status": "error",
            "message": "Failed to query crowd predictions",
        }), 500


@app.get("/api/safe-spaces")
def safe_spaces():
    """Return candidate refuge locations required by the selected route map."""
    try:
        return jsonify(dataframe_records(load_safe_spaces()))

    except Exception:
        app.logger.exception("Failed to query refuge locations")
        return jsonify({
            "status": "error",
            "message": "Failed to query refuge locations",
        }), 500


if __name__ == "__main__":
    # This server is for local development only. Elastic Beanstalk imports
    # ``app`` through application.py and serves it with a production WSGI server.
    app.run(debug=True, host="127.0.0.1", port=5000)
