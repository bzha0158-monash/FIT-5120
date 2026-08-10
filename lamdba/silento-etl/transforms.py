"""Transform stage for pedestrian sensor-location data."""

import pandas as pd


# Melbourne CBD boundary used by the application prototype.
LAT_MIN, LAT_MAX = -37.8260, -37.7970
LON_MIN, LON_MAX = 144.9450, 144.9790


def inside_cbd(
    dataframe: pd.DataFrame,
    latitude_column: str = "Latitude",
    longitude_column: str = "Longitude",
) -> pd.Series:
    """Return a Boolean mask selecting coordinates inside the CBD boundary."""
    return (
        dataframe[latitude_column].between(LAT_MIN, LAT_MAX)
        & dataframe[longitude_column].between(LON_MIN, LON_MAX)
    )


def transform_sensor_locations(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """Clean and normalise raw pedestrian sensor-location records.

    The transform applies the following deterministic rules:
    1. Work on a copy so the extracted raw frame is not mutated.
    2. Coerce identifiers and coordinates to numeric values.
    3. Remove rows that cannot identify or position a sensor.
    4. Keep only sensors located inside the Melbourne CBD boundary.
    5. Keep the final occurrence of each sensor ID when duplicates exist.
    6. Return only the columns required by downstream consumers.
    """

    cleaned = dataframe.copy()

    # Convert API lowercase column names into the names
    # already used by the existing transform code.
    cleaned = cleaned.rename(
        columns={
            "location_id": "Location_ID",
            "sensor_description": "Sensor_Description",
            "sensor_name": "Sensor_Name",
            "location_type": "Location_Type",
            "latitude": "Latitude",
            "longitude": "Longitude",
        }
    )

    cleaned["Location_ID"] = pd.to_numeric(
        cleaned["Location_ID"],
        errors="coerce",
    )

    cleaned["Latitude"] = pd.to_numeric(
        cleaned["Latitude"],
        errors="coerce",
    )

    cleaned["Longitude"] = pd.to_numeric(
        cleaned["Longitude"],
        errors="coerce",
    )

    cleaned = cleaned.dropna(
        subset=[
            "Location_ID",
            "Latitude",
            "Longitude",
        ]
    )

    cleaned = cleaned[inside_cbd(cleaned)]

    cleaned = cleaned.drop_duplicates(
        subset=["Location_ID"],
        keep="last",
    )

    cleaned["Location_ID"] = (
        cleaned["Location_ID"].astype(int)
    )

    output_columns = [
        "Location_ID",
        "Sensor_Description",
        "Sensor_Name",
        "Location_Type",
        "Latitude",
        "Longitude",
    ]

    return cleaned[
        output_columns
    ].reset_index(drop=True)


def transform_pedestrian_counts(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Clean hourly pedestrian-count records.
    """
    df = df.copy()

    # Convert possible source column names into one
    # consistent output naming convention.
    rename_map = {
        # Lowercase API field names
        "location_id": "Location_ID",
        "sensing_date": "Sensing_Date",
        "hourday": "Hour_of_Day",
        "hour_day": "Hour_of_Day",
        "pedestriancount": "Total_of_Directions",
        "pedestrian_count": "Total_of_Directions",
        "total_of_directions": "Total_of_Directions",

        # Alternative labelled CSV field names
        "HourDay": "Hour_of_Day",
        "Hour_Day": "Hour_of_Day",
        "Pedestrian_Count": "Total_of_Directions",
        "PedestrianCount": "Total_of_Directions",
    }

    df = df.rename(
        columns={
            old_name: new_name
            for old_name, new_name in rename_map.items()
            if old_name in df.columns
        }
    )

    required_columns = [
        "Location_ID",
        "Sensing_Date",
        "Hour_of_Day",
        "Total_of_Directions",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Pedestrian dataset is missing columns: "
            f"{missing_columns}. "
            f"Available columns: {list(df.columns)}"
        )

    df["Location_ID"] = pd.to_numeric(
        df["Location_ID"],
        errors="coerce",
    )

    df["Sensing_Date"] = pd.to_datetime(
        df["Sensing_Date"],
        errors="coerce",
    )

    df["Hour_of_Day"] = pd.to_numeric(
        df["Hour_of_Day"],
        errors="coerce",
    )

    df["Total_of_Directions"] = pd.to_numeric(
        df["Total_of_Directions"],
        errors="coerce",
    )

    df = df.dropna(
        subset=[
            "Location_ID",
            "Sensing_Date",
            "Hour_of_Day",
            "Total_of_Directions",
        ]
    )

    # Accept only valid hourly records.
    df = df[
        df["Hour_of_Day"].between(0, 23)
    ]

    df = df[
        df["Total_of_Directions"] >= 0
    ]

    df["Location_ID"] = (
        df["Location_ID"].astype(int)
    )

    df["Hour_of_Day"] = (
        df["Hour_of_Day"].astype(int)
    )

    df["Total_of_Directions"] = (
        df["Total_of_Directions"].astype(int)
    )

    # Store a simple ISO date in the output CSV.
    df["Sensing_Date"] = (
        df["Sensing_Date"].dt.strftime("%Y-%m-%d")
    )

    # One record per sensor, date and hour.
    df = df.drop_duplicates(
        subset=[
            "Location_ID",
            "Sensing_Date",
            "Hour_of_Day",
        ],
        keep="last",
    )

    return df[
        [
            "Location_ID",
            "Sensing_Date",
            "Hour_of_Day",
            "Total_of_Directions",
        ]
    ].reset_index(drop=True)