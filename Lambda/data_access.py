"""
S3-based data access layer for the crowd prediction Lambda.
Reads from the cleaned-data pipeline's manifest-driven S3 layout:
  s3://<bucket>/cleaned/runs/<run_id>/_SUCCESS.json   <- manifest
  s3://<bucket>/cleaned/runs/<run_id>/<dataset>.csv    <- cleaned data

The manifest lists each dataset's s3_key, so this module never hardcodes
paths beyond the base prefix — it discovers keys from _SUCCESS.json.
Column names are normalised to match forecast_logic.py's expectations
(location_id, sensing_datetime, etc.) via RENAME_MAP below.
"""
from __future__ import annotations

import io
import json
import os

import boto3
import pandas as pd

BUCKET = "silentwaze-036179730009-ap-southeast-2-an"
RUNS_PREFIX = "cleaned/runs/"

# Melbourne CBD demo boundary — same box as the prototype.
LAT_MIN, LAT_MAX = -37.8260, -37.7970
LON_MIN, LON_MAX = 144.9450, 144.9790

_s3 = boto3.client("s3")


def get_latest_run_prefix(bucket: str = BUCKET) -> str:
    """Find the most recent run folder under cleaned/runs/ by listing 'directories'."""
    resp = _s3.list_objects_v2(Bucket=bucket, Prefix=RUNS_PREFIX, Delimiter="/")
    run_prefixes = [p["Prefix"] for p in resp.get("CommonPrefixes", [])]
    if not run_prefixes:
        raise RuntimeError(f"No run folders found under s3://{bucket}/{RUNS_PREFIX}")
    # Run IDs are ISO-timestamp-named (e.g. 2026-08-09T11-40-36Z), so lexicographic
    # sort matches chronological order.
    return sorted(run_prefixes)[-1]


def load_manifest(run_prefix: str, bucket: str = BUCKET) -> dict:
    """Read _SUCCESS.json for a given run and return its parsed contents."""
    key = f"{run_prefix}_SUCCESS.json"
    obj = _s3.get_object(Bucket=bucket, Key=key)
    return json.loads(obj["Body"].read())


def _read_csv_from_s3(s3_key: str, bucket: str = BUCKET) -> pd.DataFrame:
    obj = _s3.get_object(Bucket=bucket, Key=s3_key)
    return pd.read_csv(io.BytesIO(obj["Body"].read()), encoding="utf-8-sig")


def load_sensor_locations(manifest: dict, bucket: str = BUCKET) -> pd.DataFrame:
    """
    Read the cleaned sensor_locations dataset referenced in the manifest,
    filter to the CBD demo box, and normalise column names to match
    forecast_logic.py's expectations.
    """
    s3_key = manifest["datasets"]["sensor_locations"]["s3_key"]
    df = _read_csv_from_s3(s3_key, bucket)

    df = df.rename(columns={
        "Location_ID": "location_id",
        "Sensor_Description": "sensor_description",
        "Sensor_Name": "sensor_name",
        "Location_Type": "location_type",
        "Latitude": "latitude",
        "Longitude": "longitude",
    })

    in_box = df["latitude"].between(LAT_MIN, LAT_MAX) & df["longitude"].between(LON_MIN, LON_MAX)
    return df[in_box].reset_index(drop=True)


def load_pedestrian_counts(manifest: dict, bucket: str = BUCKET) -> pd.DataFrame:
    """
    Read the cleaned pedestrian_counts dataset (single hourly file — no
    separate minute-level feed, unlike the original architecture diagram).
    Columns: Location_ID, Sensing_Date, Hour_of_Day, Total_of_Directions.
    """
    s3_key = manifest["datasets"]["pedestrian_counts"]["s3_key"]
    df = _read_csv_from_s3(s3_key, bucket)

    df = df.rename(columns={
        "Location_ID": "location_id",
        "Sensing_Date": "sensing_date",
        "Hour_of_Day": "hour_day",
        "Total_of_Directions": "total_of_directions",
    })

    # Dates come as YYYY-MM-DD (e.g. "2026-07-25").
    df["sensing_date"] = pd.to_datetime(df["sensing_date"], format="%Y-%m-%d", errors="coerce")
    df["hour_day"] = pd.to_numeric(df["hour_day"], errors="coerce")
    df["total_of_directions"] = pd.to_numeric(df["total_of_directions"], errors="coerce")
    df["day_of_week"] = df["sensing_date"].dt.dayofweek

    return df.dropna(subset=["location_id", "sensing_date", "hour_day", "total_of_directions"])


def get_recent_hour_totals(counts_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    """
    The most recent hour present in the data, and each sensor's count for
    that hour. Equivalent to the prototype's 60-minute window, but this
    feed is already hourly, so there's no minute-level aggregation step.
    """
    if counts_df.empty:
        return pd.DataFrame(columns=["location_id", "current_count"]), None

    max_date = counts_df["sensing_date"].max()
    same_day = counts_df[counts_df["sensing_date"] == max_date]
    latest_hour = int(same_day["hour_day"].max())

    recent = same_day[same_day["hour_day"] == latest_hour]
    totals = (
        recent.groupby("location_id", as_index=False)["total_of_directions"]
        .sum(min_count=1)
        .rename(columns={"total_of_directions": "current_count"})
    )
    latest_time = pd.Timestamp(max_date) + pd.Timedelta(hours=latest_hour)
    return totals, latest_time


def get_historical_stats(counts_df: pd.DataFrame, hour: int, day_of_week: int, location_ids: list) -> pd.DataFrame:
    """
    Mean and 33rd/66th percentile total for a specific hour-of-day and
    day-of-week, per sensor — same rule as the original prototype's
    load_historical_stats(), just computed from this single hourly feed.
    """
    subset = counts_df[
        (counts_df["hour_day"] == hour)
        & (counts_df["day_of_week"] == day_of_week)
        & (counts_df["location_id"].isin(location_ids))
    ]
    if subset.empty:
        return pd.DataFrame(columns=["location_id", "expected_count", "low_threshold", "high_threshold"])

    grouped = subset.groupby("location_id")["total_of_directions"]
    means = grouped.mean().rename("expected_count")
    low_q = grouped.quantile(1 / 3).rename("low_threshold")
    high_q = grouped.quantile(2 / 3).rename("high_threshold")
    return pd.concat([means, low_q, high_q], axis=1).reset_index()


def get_rds_connection():
    """
    Open a connection to RDS using env vars configured on the Lambda function:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
    """
    import psycopg2
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode="require",
        connect_timeout=5,
    )


def write_predictions(forecast_df: pd.DataFrame) -> None:
    """
    Insert forecast rows into CROWD_DENSITY_PREDICTION, matching the real
    table schema:
      location_id, prediction_for, hour_of_day, day_type, current_count,
      expected_count, ratio, status, coverage_radius, geom
    (id and created_at are auto-populated by the table itself.)

    There's no unique constraint on (location_id, prediction_for), so this
    is a plain insert, not an upsert — each run adds a new row rather than
    overwriting the previous forecast, which keeps a history over time.
    """
    if forecast_df.empty:
        return

    df = forecast_df.copy()

    df["hour_of_day"] = df["predicted_time"].dt.hour
    df["day_type"] = df["predicted_time"].dt.dayofweek.apply(
        lambda d: "weekday" if d < 5 else "weekend"
    )
    df["ratio"] = df.apply(
        lambda row: (row["expected_count"] / row["current_count"])
        if pd.notna(row["expected_count"]) and pd.notna(row["current_count"]) and row["current_count"] > 0
        else None,
        axis=1,
    )
    df["status"] = df["forecast_level"]

    # The DB only allows status IN ('low', 'medium', 'high') — a sensor with
    # no data genuinely has no valid crowd status to report, so those rows
    # are skipped rather than forced into an incorrect category.
    skipped = int((df["status"] == "no_data").sum())
    df = df[df["status"] != "no_data"].copy()
    if skipped:
        print(f"Skipped {skipped} sensors with no_data status (not writable under the status CHECK constraint)")

    if df.empty:
        return

    radius_map = {"low": 28, "medium": 42, "high": 58}
    df["coverage_radius"] = df["status"].map(radius_map).astype(int)

    df = df.where(pd.notna(df), None)

    rows = list(
        df[[
            "location_id", "predicted_time", "hour_of_day", "day_type",
            "current_count", "expected_count", "ratio", "status",
            "coverage_radius", "longitude", "latitude",
        ]].itertuples(index=False, name=None)
    )

    query = """
        INSERT INTO crowd_density_prediction
            (location_id, prediction_for, hour_of_day, day_type,
             current_count, expected_count, ratio, status, coverage_radius, geom)
        VALUES %s
        ON CONFLICT (location_id, prediction_for)
        DO UPDATE SET
            hour_of_day = EXCLUDED.hour_of_day,
            day_type = EXCLUDED.day_type,
            current_count = EXCLUDED.current_count,
            expected_count = EXCLUDED.expected_count,
            ratio = EXCLUDED.ratio,
            status = EXCLUDED.status,
            coverage_radius = EXCLUDED.coverage_radius,
            geom = EXCLUDED.geom
    """
    template = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))"

    conn = get_rds_connection()
    try:
        from psycopg2.extras import execute_values
        with conn.cursor() as cur:
            execute_values(cur, query, rows, template=template)
        conn.commit()
    finally:
        conn.close()
