"""
Hand-built sample data standing in for the RDS tables, so the forecast
logic and Lambda handler can be tested end-to-end without a live database.
Column names match exactly what data_access.py expects from the real RDS
tables, so swapping this out for the real data source later needs no
changes to forecast_logic.py or the shape of the handler's logic.
"""
from __future__ import annotations

import pandas as pd


def mock_sensor_locations() -> pd.DataFrame:
    return pd.DataFrame([
        {"location_id": 1, "sensor_description": "Bourke St Mall (West)", "sensor_name": "Bou-Mall-W",
         "location_type": "Outdoor", "latitude": -37.8136, "longitude": 144.9646},
        {"location_id": 2, "sensor_description": "Southbank Promenade", "sensor_name": "South-Prom",
         "location_type": "Outdoor", "latitude": -37.8221, "longitude": 144.9648},
        {"location_id": 3, "sensor_description": "State Library", "sensor_name": "State-Lib",
         "location_type": "Outdoor", "latitude": -37.8098, "longitude": 144.9647},
    ])


def mock_recent_totals() -> tuple[pd.DataFrame, pd.Timestamp]:
    latest_time = pd.Timestamp.now().floor("min")
    totals = pd.DataFrame([
        {"location_id": 1, "current_count": 420},
        {"location_id": 2, "current_count": 0},    # exercises the zero-count fallback path
        {"location_id": 3, "current_count": 150},
    ])
    return totals, latest_time


def mock_historical_stats(location_ids: list, variant: str) -> pd.DataFrame:
    """variant='current' or 'next' shifts the mean slightly to simulate an upward trend."""
    bump = 1.2 if variant == "next" else 1.0
    stats = pd.DataFrame([
        {"location_id": 1, "expected_count": 500 * bump, "low_threshold": 300 * bump, "high_threshold": 700 * bump},
        {"location_id": 2, "expected_count": 200 * bump, "low_threshold": 100 * bump, "high_threshold": 350 * bump},
        {"location_id": 3, "expected_count": 180 * bump, "low_threshold": 90 * bump, "high_threshold": 300 * bump},
    ])
    return stats[stats["location_id"].isin(location_ids)]
