import io
import os
from urllib.request import Request, urlopen, quote
from datetime import datetime, timezone

import pandas as pd

def extract_csv(url: str)-> pd.DataFrame:
    """
     Download a CSV dataset and return it as a raw DataFrame.
    """
    request = Request(
        url,
        headers={
            "User-Agent": "Silento-ETL/1.0",
        },
    )

    with urlopen(request, timeout=30) as response:
        csv_content = response.read()

    return pd.read_csv(
        io.BytesIO(csv_content),
        encoding="utf-8-sig",
    )

def extract_sensor_locations() -> pd.DataFrame:
    """
     Download a CSV dataset and return it as a raw DataFrame.
    """
    source_url = os.environ["SENSOR_SOURCE_URL"]
    return extract_csv(source_url)

def extract_pedestrian_counts() -> pd.DataFrame:
    source_url = os.environ["PEDESTRIAN_SOURCE_URL"]

    today = datetime.now(timezone.utc).date()

    three_months_ago = (
        pd.Timestamp(today) - pd.DateOffset(months=1)
    ).date()

    where_clause = (
        f"sensing_date >= date'{three_months_ago.isoformat()}'"
    )

    filtered_url = (
        f"{source_url}&where={quote(where_clause)}"
    )

    return extract_csv(filtered_url)