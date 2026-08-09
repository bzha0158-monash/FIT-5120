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


def transform_sensor_locations(dataframe: pd.DataFrame) -> pd.DataFrame:
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
        subset=["Location_ID", "Latitude", "Longitude"]
    )
    cleaned = cleaned[inside_cbd(cleaned)]
    cleaned = cleaned.drop_duplicates(
        subset=["Location_ID"],
        keep="last",
    )
    cleaned["Location_ID"] = cleaned["Location_ID"].astype(int)

    output_columns = [
        "Location_ID",
        "Sensor_Description",
        "Sensor_Name",
        "Location_Type",
        "Latitude",
        "Longitude",
    ]
    return cleaned[output_columns].reset_index(drop=True)
