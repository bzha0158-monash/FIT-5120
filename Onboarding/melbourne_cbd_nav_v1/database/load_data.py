"""
SilentWaze — Initial Data Loader
==================================
Loads all 5 source CSVs into the PostgreSQL/PostGIS database on AWS RDS.

Run ONCE after the schema has been applied:
    python database/load_data.py

Requires:
    pip install psycopg2-binary pandas python-dotenv tqdm
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

SENSOR_FILE      = DATA_DIR / "pedestrian-counting-system-sensor-locations.csv"
MINUTE_FILE      = DATA_DIR / "pedestrian-counting-system-past-hour-counts-per-minute.csv"
HOURLY_FILE      = DATA_DIR / "pedestrian-counting-system-monthly-counts-per-hour.csv"
NETWORK_FILE     = DATA_DIR / "pedestrian-network.csv"
LANDMARKS_FILE   = DATA_DIR / "landmarks-and-places-of-interest-including-schools-theatres-health-services-spor.csv"

# ── Config ───────────────────────────────────────────────────────────────────
load_dotenv()  # reads .env file

DATABASE_URL = os.getenv("DATABASE_URL")

CBD_LAT_MIN, CBD_LAT_MAX = -37.8260, -37.7970
CBD_LON_MIN, CBD_LON_MAX =  144.9450, 144.9790

SAFE_SPACE_PATTERN = re.compile(
    r"Park|Garden|Reserve|Library|Public Buildings|Community|Visitor Centre|Indoor Recreation",
    re.IGNORECASE
)


def get_conn():
    if not DATABASE_URL:
        sys.exit(
            "❌  DATABASE_URL not set.\n"
            "    Create a .env file with:\n"
            "    DATABASE_URL=postgresql://user:pass@your-rds-endpoint:5432/silentwaze"
        )
    return psycopg2.connect(DATABASE_URL)


def inside_cbd(lat: float, lon: float) -> bool:
    return CBD_LAT_MIN <= lat <= CBD_LAT_MAX and CBD_LON_MIN <= lon <= CBD_LON_MAX


def normalise_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase, strip, and normalise column names — replaces spaces/hyphens with underscores."""
    df.columns = df.columns.str.strip().str.lower().str.replace(r'[\s\-]+', '_', regex=True)
    return df


# ── Loaders ──────────────────────────────────────────────────────────────────

def load_sensor_locations(cur):
    """Load sensor_location table from sensor-locations CSV."""
    print("\n[1/5] Loading sensor locations...")
    df = normalise_cols(pd.read_csv(SENSOR_FILE, encoding="utf-8-sig"))

    rows = []
    for _, r in df.iterrows():
        try:
            lat = float(r["latitude"])
            lon = float(r["longitude"])
        except (ValueError, KeyError):
            continue
        def clean(v):
            """Return None for NaN/empty, else the value."""
            import math
            if v is None:
                return None
            try:
                if math.isnan(float(v)):
                    return None
            except (TypeError, ValueError):
                pass
            return v or None

        rows.append((
            int(r["location_id"]),
            clean(r.get("sensor_description")),
            clean(r.get("sensor_name")),
            clean(r.get("installation_date")),
            clean(r.get("note")),
            clean(r.get("location_type")),
            clean(r.get("status")),
            clean(r.get("direction_1")),
            clean(r.get("direction_2")),
            lat,
            lon,
        ))

    execute_values(cur, """
        INSERT INTO sensor_location
            (location_id, sensor_description, sensor_name, installation_date,
             note, location_type, status, direction_1, direction_2, latitude, longitude)
        VALUES %s
        ON CONFLICT (location_id) DO UPDATE SET
            sensor_description = EXCLUDED.sensor_description,
            status             = EXCLUDED.status,
            latitude           = EXCLUDED.latitude,
            longitude          = EXCLUDED.longitude
    """, rows)
    print(f"    ✅  {len(rows)} sensors inserted/updated.")


def load_road_network(cur):
    """Load road_network table from pedestrian-network CSV."""
    print("\n[2/5] Loading road network...")
    df = normalise_cols(pd.read_csv(NETWORK_FILE, encoding="utf-8-sig"))

    rows = []
    for _, r in df.iterrows():
        try:
            geo_str = str(r.get("geo_point", "")).strip()
            lat_str, lon_str = geo_str.split(",")
            lat, lon = float(lat_str.strip()), float(lon_str.strip())
        except (ValueError, AttributeError):
            continue

        import math
        try:
            network_id = int(r.get("neworkid", 0)) if not math.isnan(float(r.get("neworkid", 0))) else 0
        except (TypeError, ValueError):
            network_id = 0

        rows.append((
            int(r["objectid"]),
            network_id,
            lat,
            lon,
            str(r.get("geo_shape", "{}")).strip(),
        ))

    execute_values(cur, """
        INSERT INTO road_network (object_id, network_id, latitude, longitude, geo_shape)
        VALUES %s
        ON CONFLICT (object_id) DO NOTHING
    """, rows)
    print(f"    ✅  {len(rows)} network nodes inserted.")


def load_minute_counts(cur):
    """Load pedestrian_minute_count from past-hour CSV."""
    print("\n[3/5] Loading real-time minute counts...")
    df = normalise_cols(pd.read_csv(MINUTE_FILE, encoding="utf-8-sig"))
    df["sensing_datetime"] = pd.to_datetime(df["sensing_datetime"], errors="coerce", utc=True)
    df = df.dropna(subset=["sensing_datetime", "location_id"])

    # Deduplicate: keep the last record for any (location_id, sensing_datetime) pair
    before = len(df)
    df = df.sort_values("sensing_datetime").drop_duplicates(
        subset=["location_id", "sensing_datetime"], keep="last"
    )
    dupes_removed = before - len(df)
    if dupes_removed:
        print(f"    ℹ️  Removed {dupes_removed} duplicate (location_id, sensing_datetime) rows")

    rows = [
        (
            int(r["location_id"]),
            r["sensing_datetime"].isoformat(),
            str(r["sensing_date"]),
            str(r["sensing_time"]),
            int(r.get("direction_1", 0) or 0),
            int(r.get("direction_2", 0) or 0),
        )
        for _, r in df.iterrows()
    ]

    execute_values(cur, """
        INSERT INTO pedestrian_minute_count
            (location_id, sensing_datetime, sensing_date, sensing_time, direction_1, direction_2)
        VALUES %s
        ON CONFLICT (location_id, sensing_datetime) DO NOTHING
    """, rows)
    print(f"    ✅  {len(rows)} minute-count rows inserted.")


def load_hourly_counts(cur):
    """
    Load pedestrian_hour_count from the large monthly historical CSV.
    Streams in 250k-row chunks to avoid memory exhaustion.

    CoM CSV columns (all lowercase):
        id, location_id, sensing_date, hourday, direction_1, direction_2,
        pedestriancount, sensor_name, location
    """
    print("\n[4/5] Loading historical hourly counts (large file — this may take a few minutes)...")

    # ── Detect the actual hour column name (after lowercasing) ───────────────
    sample = normalise_cols(pd.read_csv(HOURLY_FILE, encoding="utf-8-sig", nrows=2))
    cols = list(sample.columns)

    # Candidates in order of preference (all lowercase after normalise_cols)
    hour_candidates = ["hourday", "hour", "time", "sensing_time"]
    hour_col = next((c for c in hour_candidates if c in cols), None)

    if hour_col is None:
        sys.exit(
            f"❌  Cannot find hour column in monthly CSV.\n"
            f"    Columns found: {cols}\n"
            f"    Update hour_candidates in load_hourly_counts()."
        )
    print(f"    Using hour column: '{hour_col}'")

    # ── Detect the pedestrian count column ───────────────────────────────────
    count_candidates = ["pedestriancount", "total_of_directions", "count"]
    count_col = next((c for c in count_candidates if c in cols), None)

    if count_col is None:
        sys.exit(
            f"❌  Cannot find count column in monthly CSV.\n"
            f"    Columns found: {cols}\n"
            f"    Update count_candidates in load_hourly_counts()."
        )
    print(f"    Using count column: '{count_col}'")

    # Fetch valid location_ids from DB to filter orphan rows
    cur.execute("SELECT location_id FROM sensor_location")
    valid_sensor_ids = {r[0] for r in cur.fetchall()}
    if not valid_sensor_ids:
        print("    ⚠️  sensor_location table is empty — load sensors first (step 1)")
    else:
        print(f"    Filtering against {len(valid_sensor_ids)} known sensor IDs")

    total_rows = 0
    orphan_rows = 0
    for chunk in pd.read_csv(
        HOURLY_FILE,
        encoding="utf-8-sig",
        chunksize=250_000,
    ):
        chunk = normalise_cols(chunk)
        chunk = chunk[["location_id", "sensing_date", hour_col, count_col]].copy()

        # Drop rows whose location_id doesn't exist in sensor_location
        if valid_sensor_ids:
            before = len(chunk)
            chunk = chunk[chunk["location_id"].isin(valid_sensor_ids)]
            orphan_rows += before - len(chunk)

        dates = pd.to_datetime(chunk["sensing_date"], errors="coerce")
        chunk["day_type"] = dates.dt.dayofweek.ge(5).map({True: "weekend", False: "weekday"})
        chunk["hour_int"] = pd.to_numeric(chunk[hour_col], errors="coerce").fillna(0).astype(int)
        chunk = chunk.dropna(subset=["location_id"])

        rows = [
            (
                int(r["location_id"]),
                str(r["sensing_date"]),
                int(r["hour_int"]) % 24,
                r["day_type"],
                int(r.get(count_col, 0) or 0),
            )
            for _, r in chunk.iterrows()
        ]

        execute_values(cur, """
            INSERT INTO pedestrian_hour_count
                (location_id, sensing_date, hour_of_day, day_type, total_of_directions)
            VALUES %s
            ON CONFLICT (location_id, sensing_date, hour_of_day) DO NOTHING
        """, rows)
        total_rows += len(rows)
        print(f"    ... {total_rows:,} rows inserted so far")

    if orphan_rows:
        print(f"    ℹ️  Skipped {orphan_rows:,} rows with unknown location_id (decommissioned sensors)")
    print(f"    ✅  {total_rows:,} hourly-count rows inserted total.")


def load_refuge_locations(cur):
    """Load refuge_location from landmarks CSV, filtered to safe spaces in CBD."""
    print("\n[5/5] Loading refuge locations (landmarks)...")
    df = normalise_cols(pd.read_csv(LANDMARKS_FILE, encoding="utf-8-sig"))
    # CoM CSV columns: theme, sub_theme, feature_name, co_ordinates

    rows = []
    for _, r in df.iterrows():
        sub_theme = str(r.get("sub_theme", ""))
        if not SAFE_SPACE_PATTERN.search(sub_theme):
            continue
        try:
            coord_str = str(r.get("co_ordinates", "")).strip()
            lat_s, lon_s = coord_str.split(",", 1)
            lat, lon = float(lat_s.strip()), float(lon_s.strip())
        except (ValueError, AttributeError):
            continue
        if not inside_cbd(lat, lon):
            continue
        rows.append((
            str(r.get("feature_name", "")),
            str(r.get("theme", "")),
            sub_theme,
            lat,
            lon,
        ))

    execute_values(cur, """
        INSERT INTO refuge_location (feature_name, theme, sub_theme, latitude, longitude)
        VALUES %s
        ON CONFLICT DO NOTHING
    """, rows)
    print(f"    ✅  {len(rows)} refuge locations inserted.")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("SilentWaze — Database Initial Load")
    print("=" * 60)

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        load_sensor_locations(cur)
        conn.commit()

        load_road_network(cur)
        conn.commit()

        load_minute_counts(cur)
        conn.commit()

        load_hourly_counts(cur)
        conn.commit()

        load_refuge_locations(cur)
        conn.commit()

        print("\n" + "=" * 60)
        print("✅  All data loaded successfully.")
        print("=" * 60)

    except Exception as e:
        conn.rollback()
        print(f"\n❌  Error during load: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
