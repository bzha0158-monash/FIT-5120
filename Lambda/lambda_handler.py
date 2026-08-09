"""
Lambda entry point for short-term crowd prediction.
Triggered on a schedule by EventBridge. Reads recent + historical data
from RDS, computes next-hour forecasts, writes them back into
CROWD_DENSITY_PREDICTION for the Backend API to serve.
"""
from __future__ import annotations

import pandas as pd

from data_access import (
    get_connection,
    load_sensor_locations,
    load_recent_hour_totals,
    load_historical_stats,
    write_predictions,
)
from forecast_logic import build_forecast_frame


def lambda_handler(event, context):
    conn = get_connection()
    try:
        locations = load_sensor_locations(conn)
        recent_totals, latest_time = load_recent_hour_totals(conn)

        if latest_time is None:
            return {"statusCode": 200, "body": "No recent minute-level data available; skipped."}

        next_time = latest_time + pd.Timedelta(hours=1)
        location_ids = locations["location_id"].tolist()

        current_stats = load_historical_stats(
            conn, hour=latest_time.hour, day_of_week=latest_time.dayofweek,
            location_ids=location_ids,
        )
        next_stats = load_historical_stats(
            conn, hour=next_time.hour, day_of_week=next_time.dayofweek,
            location_ids=location_ids,
        )

        forecast = build_forecast_frame(
            locations, recent_totals, current_stats, next_stats, latest_time, next_time
        )

        write_predictions(conn, forecast)

        return {
            "statusCode": 200,
            "body": f"Wrote {len(forecast)} predictions for {next_time.isoformat()}",
        }
    finally:
        conn.close()
