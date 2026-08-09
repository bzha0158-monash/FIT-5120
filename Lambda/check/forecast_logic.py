"""
Pure crowd-classification and forecasting logic — no Flask, no CSV I/O,
no AWS dependencies. Same rules as the original prototype, so behaviour
carries over unchanged when the data source is swapped from CSV to RDS.
"""
from __future__ import annotations

import pandas as pd


def classify_single_value(value, low_threshold, high_threshold) -> str:
    """Classify one hourly value using its sensor/time historical percentiles."""
    if pd.isna(value) or pd.isna(low_threshold) or pd.isna(high_threshold):
        return "no_data"

    value = float(value)
    low_threshold = float(low_threshold)
    high_threshold = float(high_threshold)

    if value >= high_threshold:
        return "high"
    if value >= low_threshold:
        return "medium"
    return "low"


def predict_next_hour(current, current_mean, next_mean) -> tuple[float | None, str]:
    """
    Trend-adjust the live count by the historical hour-to-hour ratio for this
    sensor. Falls back to the raw next-hour historical mean when the current
    count is zero/missing or the trend can't be computed, so a quiet moment
    doesn't mechanically force the forecast to zero.
    """
    if (
        pd.notna(current) and float(current) > 0
        and pd.notna(current_mean) and pd.notna(next_mean)
        and float(current_mean) > 0
    ):
        trend_factor = float(next_mean) / float(current_mean)
        return float(current) * trend_factor, "trend_adjusted"

    if pd.notna(next_mean):
        return float(next_mean), "historical_baseline"

    return None, "unavailable"


def build_forecast_frame(
    locations: pd.DataFrame,
    recent_totals: pd.DataFrame,
    current_stats: pd.DataFrame,
    next_stats: pd.DataFrame,
    latest_time: pd.Timestamp,
    next_time: pd.Timestamp,
) -> pd.DataFrame:
    """
    Join sensor locations with current counts and historical stats, then
    classify + forecast each sensor. Mirrors the prototype's /api/crowd
    logic, minus the Flask/JSON layer — this is what the Lambda handler calls.
    """
    result = locations.merge(recent_totals, on="location_id", how="left")
    result = result.merge(
        current_stats.rename(columns={
            "expected_count": "current_hist_mean",
            "low_threshold": "current_low_threshold",
            "high_threshold": "current_high_threshold",
        }),
        on="location_id", how="left",
    )
    result = result.merge(
        next_stats.rename(columns={
            "expected_count": "next_hist_mean",
            "low_threshold": "forecast_low_threshold",
            "high_threshold": "forecast_high_threshold",
        }),
        on="location_id", how="left",
    )

    result["current_count"] = pd.to_numeric(
        result["current_count"], errors="coerce"
    ).fillna(0.0)

    result["current_level"] = result.apply(
        lambda row: classify_single_value(
            row["current_count"], row["current_low_threshold"], row["current_high_threshold"]
        ),
        axis=1,
    )

    predictions = result.apply(
        lambda row: predict_next_hour(
            row["current_count"], row["current_hist_mean"], row["next_hist_mean"]
        ),
        axis=1,
    )
    result["expected_count"] = predictions.map(lambda v: v[0])
    result["forecast_method"] = predictions.map(lambda v: v[1])

    result["forecast_level"] = result.apply(
        lambda row: classify_single_value(
            row["expected_count"], row["forecast_low_threshold"], row["forecast_high_threshold"]
        ),
        axis=1,
    )

    result["predicted_time"] = next_time
    return result
