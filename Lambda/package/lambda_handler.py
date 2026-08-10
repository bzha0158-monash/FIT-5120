"""
Lambda entry point for short-term crowd prediction, reading cleaned data
from S3 (the team's ETL pipeline output) and running the full forecast.

Pipeline:
  1. Find the latest ETL run and its manifest
  2. Load sensor_locations + pedestrian_counts from that run
  3. Compute current-hour totals and historical hour/weekday stats
  4. Run forecast_logic.build_forecast_frame() to classify + predict
  5. Return the forecast (RDS write-back can be added once RDS exists —
     see the TODO note below)
"""
from __future__ import annotations

import pandas as pd

from data_access import (
    get_latest_run_prefix,
    load_manifest,
    load_sensor_locations,
    load_pedestrian_counts,
    get_recent_hour_totals,
    get_historical_stats,
    write_predictions,
)
from forecast_logic import build_forecast_frame


def lambda_handler(event, context):
    run_prefix = get_latest_run_prefix()
    manifest = load_manifest(run_prefix)
    dataset_names = list(manifest.get("datasets", {}).keys())
    print(f"Run: {run_prefix}")
    print(f"Datasets available: {dataset_names}")

    locations = load_sensor_locations(manifest)
    print(f"Loaded {len(locations)} sensor locations inside the CBD demo box")

    if "pedestrian_counts" not in manifest.get("datasets", {}):
        return {
            "statusCode": 200,
            "body": {
                "run": run_prefix,
                "datasets_in_manifest": dataset_names,
                "sensor_location_count": len(locations),
                "message": "pedestrian_counts dataset not yet present in this run's manifest.",
            },
        }

    counts = load_pedestrian_counts(manifest)
    print(f"Loaded {len(counts)} pedestrian count rows")

    recent_totals, latest_time = get_recent_hour_totals(counts)
    if latest_time is None:
        return {"statusCode": 200, "body": "No pedestrian count data available; skipped."}

    next_time = latest_time + pd.Timedelta(hours=1)
    location_ids = locations["location_id"].tolist()

    current_stats = get_historical_stats(
        counts, hour=latest_time.hour, day_of_week=latest_time.dayofweek, location_ids=location_ids
    )
    next_stats = get_historical_stats(
        counts, hour=next_time.hour, day_of_week=next_time.dayofweek, location_ids=location_ids
    )

    forecast = build_forecast_frame(
        locations, recent_totals, current_stats, next_stats, latest_time, next_time
    )
    print(f"Computed forecast for {len(forecast)} sensors, next hour = {next_time.isoformat()}")

    write_predictions(forecast)
    print(f"Wrote {len(forecast)} predictions to CROWD_DENSITY_PREDICTION")

    output_columns = [
        "location_id", "sensor_description", "current_count", "current_level",
        "expected_count", "forecast_level", "forecast_method", "predicted_time",
    ]
    forecast_out = forecast[output_columns].copy()
    forecast_out["predicted_time"] = forecast_out["predicted_time"].apply(
        lambda t: t.isoformat() if pd.notna(t) else None
    )
    forecast_out = forecast_out.astype(object).where(pd.notna(forecast_out), None)

    return {
        "statusCode": 200,
        "body": {
            "run": run_prefix,
            "datasets_in_manifest": dataset_names,
            "reference_time": latest_time.isoformat(),
            "forecast_hour": next_time.isoformat(),
            "forecast": forecast_out.to_dict("records"),
        },
    }


if __name__ == "__main__":
    result = lambda_handler({}, None)
    print(result)
