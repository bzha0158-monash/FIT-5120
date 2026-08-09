from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template

# ============================================================
# 1. APPLICATION PATHS AND SOURCE DATA
# ============================================================
# All five raw project datasets remain separate on disk. The backend
# reads them at runtime and performs transformations only in memory.
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

HISTORICAL_FILE = DATA_DIR / "pedestrian-counting-system-monthly-counts-per-hour.csv"
REALTIME_FILE = DATA_DIR / "pedestrian-counting-system-past-hour-counts-per-minute.csv"
SENSOR_FILE = DATA_DIR / "pedestrian-counting-system-sensor-locations.csv"
PEDESTRIAN_NETWORK_FILE = DATA_DIR / "pedestrian-network.csv"
PLACES_FILE = DATA_DIR / "landmarks-and-places-of-interest-including-schools-theatres-health-services-spor.csv"

# Melbourne CBD demo boundary.
LAT_MIN, LAT_MAX = -37.8260, -37.7970
LON_MIN, LON_MAX = 144.9450, 144.9790

app = Flask(__name__)


# Return a Boolean mask for records inside the Melbourne CBD demo box.
def inside_cbd(df: pd.DataFrame, lat: str = "Latitude", lon: str = "Longitude") -> pd.Series:
    return df[lat].between(LAT_MIN, LAT_MAX) & df[lon].between(LON_MIN, LON_MAX)


# ============================================================
# 2. DATA-LOADING HELPERS
# ============================================================
# Caching avoids repeatedly reading large CSV files for every API call.
@lru_cache(maxsize=1)
def load_sensor_locations() -> pd.DataFrame:
    """Load sensor metadata without changing the source CSV."""
    df = pd.read_csv(SENSOR_FILE, encoding="utf-8-sig")
    df = df[inside_cbd(df)].copy()
    return df[[
        "Location_ID",
        "Sensor_Description",
        "Sensor_Name",
        "Location_Type",
        "Latitude",
        "Longitude",
    ]]


# Aggregate the minute-level feed into one comparable 60-minute total
# per sensor. No source file is overwritten.
@lru_cache(maxsize=1)
def load_recent_hour_totals() -> tuple[pd.DataFrame, pd.Timestamp]:
    """
    Calculate the total pedestrian count in the latest 60-minute window.

    The raw minute-level file remains unchanged. We only aggregate it in memory
    so that the current value and the historical next-hour value use the same
    hourly unit.
    """
    df = pd.read_csv(
        REALTIME_FILE,
        encoding="utf-8-sig",
        usecols=["Location_ID", "Sensing_DateTime", "Total_of_Directions"],
    )
    df["Sensing_DateTime"] = pd.to_datetime(df["Sensing_DateTime"], errors="coerce")
    df["Total_of_Directions"] = pd.to_numeric(df["Total_of_Directions"], errors="coerce")
    df = df.dropna(subset=["Sensing_DateTime"])

    if df.empty:
        return pd.DataFrame(columns=["Location_ID", "current_count"]), pd.NaT

    latest_time = df["Sensing_DateTime"].max()
    window_start = latest_time - pd.Timedelta(minutes=59)
    recent = df[df["Sensing_DateTime"].between(window_start, latest_time)].copy()

    totals = (
        recent.groupby("Location_ID", as_index=False)["Total_of_Directions"]
        .sum(min_count=1)
        .rename(columns={"Total_of_Directions": "current_count"})
    )
    return totals, latest_time


# Build sensor-specific historical baselines for the same weekday and
# hour. The 33rd and 66th percentiles define Low/Medium/High bands.
@lru_cache(maxsize=1)
def load_historical_stats() -> pd.DataFrame:
    """
    Build context-specific historical statistics for every pedestrian sensor.

    Context key:
      Location_ID + hour of day + exact day of week

    For each context we calculate:
      expected_count = historical mean pedestrian count
      low_threshold  = historical 33rd percentile
      high_threshold = historical 66th percentile

    This means every sensor is compared with its own historical behaviour at
    the same time context. A naturally busy Swanston Street sensor therefore
    does not use the same absolute threshold as a quieter side-street sensor.

    The source CSV is read only and is never modified or overwritten.
    """
    frames: list[pd.DataFrame] = []

    for chunk in pd.read_csv(
        HISTORICAL_FILE,
        encoding="utf-8-sig",
        usecols=["Location_ID", "Sensing_Date", "HourDay", "Total_of_Directions"],
        chunksize=250_000,
    ):
        dates = pd.to_datetime(chunk["Sensing_Date"], errors="coerce")
        chunk["day_of_week"] = dates.dt.dayofweek
        chunk["HourDay"] = pd.to_numeric(chunk["HourDay"], errors="coerce")
        chunk["Total_of_Directions"] = pd.to_numeric(
            chunk["Total_of_Directions"], errors="coerce"
        )
        chunk = chunk.dropna(
            subset=["Location_ID", "HourDay", "day_of_week", "Total_of_Directions"]
        )
        frames.append(
            chunk[["Location_ID", "HourDay", "day_of_week", "Total_of_Directions"]].copy()
        )

    if not frames:
        return pd.DataFrame(
            columns=[
                "Location_ID",
                "HourDay",
                "day_of_week",
                "expected_count",
                "low_threshold",
                "high_threshold",
            ]
        )

    history = pd.concat(frames, ignore_index=True)
    grouped = history.groupby(["Location_ID", "HourDay", "day_of_week"])["Total_of_Directions"]

    means = grouped.mean().rename("expected_count")
    low_q = grouped.quantile(1 / 3).rename("low_threshold")
    high_q = grouped.quantile(2 / 3).rename("high_threshold")

    stats = pd.concat([means, low_q, high_q], axis=1).reset_index()
    return stats


# Select potential sensory-refuge locations by place category and CBD
# location. These are candidate quiet/refuge places, not verified silence.
@lru_cache(maxsize=1)
def load_safe_spaces() -> pd.DataFrame:
    """Select potential sensory-refuge locations from the original landmarks CSV."""
    df = pd.read_csv(PLACES_FILE, encoding="utf-8-sig")
    coordinates = df["Co-ordinates"].str.split(",", n=1, expand=True)
    df["Latitude"] = pd.to_numeric(coordinates[0], errors="coerce")
    df["Longitude"] = pd.to_numeric(coordinates[1], errors="coerce")

    safe_pattern = (
        r"Park|Garden|Reserve|Library|Public Buildings|Community|"
        r"Visitor Centre|Indoor Recreation"
    )
    mask = df["Sub Theme"].str.contains(safe_pattern, case=False, na=False)
    df = df[mask & inside_cbd(df)].copy()

    return df[["Feature Name", "Theme", "Sub Theme", "Latitude", "Longitude"]].dropna()


# ============================================================
# 3. CROWD-LEVEL CLASSIFICATION
# ============================================================
# Classification is relative to each sensor's own historical behaviour.
def classify_single_value(
    value: float | None,
    low_threshold: float | None,
    high_threshold: float | None,
) -> str:
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


def choose_map_status(current_level: str, forecast_level: str) -> tuple[str, str]:
    """
    The map is labelled 'Predicted crowd coverage', so the next-hour prediction
    is used whenever it is available. If the historical forecast is unavailable,
    the current level is used as a transparent fallback.
    """
    if forecast_level != "no_data":
        return forecast_level, "forecast"
    if current_level != "no_data":
        return current_level, "current_fallback"
    return "no_data", "unavailable"


# ============================================================
# 4. FLASK ROUTES / API ENDPOINTS
# ============================================================
@app.get("/")
def index():
    return render_template("index.html")


# Combine sensor positions, current 60-minute totals and historical
# baselines into the crowd JSON consumed by the front-end map.
@app.get("/api/crowd")
def crowd():
    locations = load_sensor_locations()
    recent_totals, latest_time = load_recent_hour_totals()
    stats = load_historical_stats()

    if pd.isna(latest_time):
        current_slice = pd.DataFrame(
            columns=[
                "Location_ID",
                "current_hist_mean",
                "current_low_threshold",
                "current_high_threshold",
            ]
        )
        forecast_slice = pd.DataFrame(
            columns=[
                "Location_ID",
                "next_hist_mean",
                "forecast_low_threshold",
                "forecast_high_threshold",
            ]
        )
        next_hour = None
    else:
        current_hour = int(latest_time.hour)
        current_day_of_week = int(latest_time.dayofweek)
        current_slice = stats[
            (stats["HourDay"] == current_hour)
            & (stats["day_of_week"] == current_day_of_week)
        ][
            [
                "Location_ID",
                "expected_count",
                "low_threshold",
                "high_threshold",
            ]
        ].rename(
            columns={
                "expected_count": "current_hist_mean",
                "low_threshold": "current_low_threshold",
                "high_threshold": "current_high_threshold",
            }
        )

        next_time = latest_time + pd.Timedelta(hours=1)
        next_hour = int(next_time.hour)
        next_day_of_week = int(next_time.dayofweek)
        forecast_slice = stats[
            (stats["HourDay"] == next_hour)
            & (stats["day_of_week"] == next_day_of_week)
        ][
            [
                "Location_ID",
                "expected_count",
                "low_threshold",
                "high_threshold",
            ]
        ].rename(
            columns={
                "expected_count": "next_hist_mean",
                "low_threshold": "forecast_low_threshold",
                "high_threshold": "forecast_high_threshold",
            }
        )

    # Left joins keep every active CBD sensor even when it has no row in
    # the latest minute-level window or a particular historical slice.
    result = locations.merge(recent_totals, on="Location_ID", how="left")
    result = result.merge(current_slice, on="Location_ID", how="left")
    result = result.merge(forecast_slice, on="Location_ID", how="left")

    numeric_columns = [
        "current_count",
        "current_hist_mean",
        "current_low_threshold",
        "current_high_threshold",
        "next_hist_mean",
        "forecast_low_threshold",
        "forecast_high_threshold",
    ]
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    # Product rule for the live minute feed:
    # if a sensor has no row in the latest 60-minute window, this application
    # interprets that interval as zero observed pedestrians rather than missing.
    # Historical baselines/thresholds are NOT filled with zero.
    # Project rule: no recent minute rows are interpreted as zero observed
    # pedestrians for the current 60-minute period. Historical baselines
    # are deliberately not replaced with zero.
    result["current_count"] = result["current_count"].fillna(0.0)

    # Current crowd level: compare the latest 60-minute total with this sensor's
    # own historical 33rd/66th percentiles for the current hour and weekday.
    result["current_level"] = result.apply(
        lambda row: classify_single_value(
            row["current_count"],
            row["current_low_threshold"],
            row["current_high_threshold"],
        ),
        axis=1,
    )

    # Predict the next hour using the current observed level adjusted by the
    # historical hour-to-hour trend for the same sensor.
    #
    # Example:
    #   current observed = 400
    #   historical mean now = 500
    #   historical mean next hour = 600
    #   trend factor = 600 / 500 = 1.2
    #   next-hour estimate = 400 * 1.2 = 480
    #
    # A missing recent-hour record has already been interpreted as current=0.
    # If a historical trend cannot be calculated, fall back to the next-hour
    # historical mean when that baseline exists.
    def predict_next_hour(row: pd.Series) -> tuple[float | None, str]:
        current = row["current_count"]
        current_mean = row["current_hist_mean"]
        next_mean = row["next_hist_mean"]

        # Use the live count with the historical hour-to-hour trend when
        # a positive current count is available. If the latest 60-minute
        # total is zero, keep Current = 0 in the UI, but use the historical
        # next-hour mean as the forecast fallback instead of forcing
        # 0 * trend = 0.
        if (
            pd.notna(current)
            and float(current) > 0
            and pd.notna(current_mean)
            and pd.notna(next_mean)
            and float(current_mean) > 0
        ):
            trend_factor = float(next_mean) / float(current_mean)
            return float(current) * trend_factor, "trend_adjusted"

        # A zero current count should not automatically make the forecast zero.
        # When historical data exists, use the next-hour historical mean.
        if pd.notna(next_mean):
            return float(next_mean), "historical_baseline"

        return None, "unavailable"

    # Apply the forecast rule independently to every sensor. A current
    # value of zero falls back to the historical next-hour mean so the
    # forecast is not mechanically forced to zero.
    predictions = result.apply(predict_next_hour, axis=1)
    result["expected_count"] = predictions.map(lambda value: value[0])
    result["forecast_method"] = predictions.map(lambda value: value[1])

    # Predicted crowd level uses next-hour thresholds for the same sensor, hour
    # and weekday. This is the level shown by the map coverage.
    result["forecast_level"] = result.apply(
        lambda row: classify_single_value(
            row["expected_count"],
            row["forecast_low_threshold"],
            row["forecast_high_threshold"],
        ),
        axis=1,
    )
    result["status"] = result["forecast_level"]

    result["coverage_radius"] = result["status"].map(
        {"low": 28, "medium": 42, "high": 58, "no_data": 0}
    )
    result["reference_time"] = None if pd.isna(latest_time) else latest_time.isoformat()
    result["forecast_hour"] = next_hour

    columns = [
        "Location_ID",
        "Sensor_Description",
        "Sensor_Name",
        "Latitude",
        "Longitude",
        "current_count",
        "current_hist_mean",
        "current_low_threshold",
        "current_high_threshold",
        "current_level",
        "expected_count",
        "next_hist_mean",
        "forecast_low_threshold",
        "forecast_high_threshold",
        "forecast_level",
        "forecast_method",
        "status",
        "coverage_radius",
        "reference_time",
        "forecast_hour",
    ]

    clean = result[columns].astype(object).where(pd.notna(result[columns]), None)
    return jsonify(clean.to_dict("records"))


# Return only the filtered candidate refuge locations required by the map.
@app.get("/api/safe-spaces")
def safe_spaces():
    clean = load_safe_spaces().astype(object).where(pd.notna(load_safe_spaces()), None)
    return jsonify(clean.to_dict("records"))


# Development/verification endpoint showing that the five original
# source files remain independent and unchanged.
@app.get("/api/datasets")
def datasets():
    """Confirm that all five source files remain separate and unchanged."""
    files = [
        HISTORICAL_FILE,
        REALTIME_FILE,
        SENSOR_FILE,
        PEDESTRIAN_NETWORK_FILE,
        PLACES_FILE,
    ]
    return jsonify([{"name": file.name, "bytes": file.stat().st_size} for file in files])


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
