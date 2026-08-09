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


def load_dataset(manifest: dict, dataset_name: str, bucket: str = BUCKET) -> pd.DataFrame:
    """
    Generic loader for any dataset listed in the manifest, by its key name
    (e.g. 'pedestrian_minute_counts'). Use this once we know the exact name
    of the second dataset, to build the current-count / historical-stats
    loaders the same way load_sensor_locations() reads sensor_locations.
    """
    s3_key = manifest["datasets"][dataset_name]["s3_key"]
    return _read_csv_from_s3(s3_key, bucket)
