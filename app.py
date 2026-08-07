from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from flask import Flask, jsonify, render_template

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

HISTORICAL_FILE = DATA_DIR / "pedestrian-counting-system-monthly-counts-per-hour.csv"
REALTIME_FILE = DATA_DIR / "pedestrian-counting-system-past-hour-counts-per-minute.csv"
SENSOR_FILE = DATA_DIR / "pedestrian-counting-system-sensor-locations.csv"
PEDESTRIAN_NETWORK_FILE = DATA_DIR / "pedestrian-network.csv"
PLACES_FILE = DATA_DIR / "landmarks-and-places-of-interest-including-schools-theatres-health-services-spor.csv"

LAT_MIN, LAT_MAX = -37.8260, -37.7970
LON_MIN, LON_MAX = 144.9450, 144.9790

app = Flask(__name__)


def inside_cbd(df: pd.DataFrame, lat: str = "Latitude", lon: str = "Longitude") -> pd.Series:
    return df[lat].between(LAT_MIN, LAT_MAX) & df[lon].between(LON_MIN, LON_MAX)


@lru_cache(maxsize=1)
def load_sensor_locations() -> pd.DataFrame:
    df = pd.read_csv(SENSOR_FILE, encoding="utf-8-sig")
    df = df[inside_cbd(df)].copy()
    return df[[
        "Location_ID", "Sensor_Description", "Sensor_Name", "Location_Type",
        "Latitude", "Longitude"
    ]]


@lru_cache(maxsize=1)
def load_latest_counts() -> pd.DataFrame:
    df = pd.read_csv(
        REALTIME_FILE,
        encoding="utf-8-sig",
        usecols=["Location_ID", "Sensing_DateTime", "Total_of_Directions"],
    )
    df["Sensing_DateTime"] = pd.to_datetime(df["Sensing_DateTime"], errors="coerce")
    latest = (
        df.dropna(subset=["Sensing_DateTime"])
        .sort_values("Sensing_DateTime")
        .groupby("Location_ID", as_index=False)
        .tail(1)
    )
    return latest.rename(columns={"Total_of_Directions": "current_count"})


@lru_cache(maxsize=1)
def load_historical_baseline() -> pd.DataFrame:
    partials: list[pd.DataFrame] = []
    for chunk in pd.read_csv(
        HISTORICAL_FILE,
        encoding="utf-8-sig",
        usecols=["Location_ID", "Sensing_Date", "HourDay", "Total_of_Directions"],
        chunksize=250_000,
    ):
        dates = pd.to_datetime(chunk["Sensing_Date"], errors="coerce")
        chunk["day_type"] = np.where(dates.dt.dayofweek >= 5, "weekend", "weekday")
        grouped = (
            chunk.groupby(["Location_ID", "HourDay", "day_type"])["Total_of_Directions"]
            .agg(total="sum", observations="count")
            .reset_index()
        )
        partials.append(grouped)

    combined = pd.concat(partials, ignore_index=True)
    combined = (
        combined.groupby(["Location_ID", "HourDay", "day_type"], as_index=False)[
            ["total", "observations"]
        ].sum()
    )
    combined["expected_count"] = combined["total"] / combined["observations"].clip(lower=1)
    return combined[["Location_ID", "HourDay", "day_type", "expected_count"]]


@lru_cache(maxsize=1)
def load_safe_spaces() -> pd.DataFrame:
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


@app.get("/")
def index():
    return render_template("index.html")

# add health check
@app.get("/health")
def health():
    return jsonify({
        "status":"ok",
        "service":"Fit5120-API-Ok",
    }), 200

@app.get("/api/crowd")
def crowd():
    now = datetime.now()
    next_hour = (now.hour + 1) % 24
    day_type = "weekend" if now.weekday() >= 5 else "weekday"

    locations = load_sensor_locations()
    latest = load_latest_counts()
    baseline = load_historical_baseline()
    baseline = baseline[
        (baseline["HourDay"] == next_hour) & (baseline["day_type"] == day_type)
    ][["Location_ID", "expected_count"]]

    # The original CSV files remain unchanged. These joins exist only in memory
    # to prepare the API response required by the map.
    result = locations.merge(latest, on="Location_ID", how="left")
    result = result.merge(baseline, on="Location_ID", how="left")
    result["current_count"] = result["current_count"].fillna(0)
    result["expected_count"] = result["expected_count"].fillna(result["current_count"])
    result["ratio"] = result["current_count"] / result["expected_count"].replace(0, 1)

    def classify(row: pd.Series) -> str:
        crowd_value = max(float(row["current_count"]), float(row["expected_count"]))
        ratio = float(row["ratio"])
        if crowd_value >= 180 or ratio >= 1.5:
            return "high"
        if crowd_value >= 80 or ratio >= 1.15:
            return "medium"
        return "low"

    result["status"] = result.apply(classify, axis=1)
    result["coverage_radius"] = result["status"].map({"low": 40, "medium": 60, "high": 85})

    columns = [
        "Location_ID", "Sensor_Description", "Sensor_Name", "Latitude", "Longitude",
        "current_count", "expected_count", "ratio", "status", "coverage_radius"
    ]
    clean = result[columns].replace({np.nan: None})
    return jsonify(clean.to_dict("records"))


@app.get("/api/safe-spaces")
def safe_spaces():
    return jsonify(load_safe_spaces().replace({np.nan: None}).to_dict("records"))


@app.get("/api/datasets")
def datasets():
    """Show that the five source files are retained separately and unchanged."""
    files = [
        HISTORICAL_FILE, REALTIME_FILE, SENSOR_FILE,
        PEDESTRIAN_NETWORK_FILE, PLACES_FILE,
    ]
    return jsonify([{"name": file.name, "bytes": file.stat().st_size} for file in files])

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
