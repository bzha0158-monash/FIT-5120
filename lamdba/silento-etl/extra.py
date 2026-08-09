import io
import os
from urllib.request import Request, urlopen

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

def extract_senor_location() -> pd.DataFrame:
    """
     Download a CSV dataset and return it as a raw DataFrame.
    """
    source_url = os.environ["SENSOR_SOURCE_URL"]
    return extract_csv(source_url)
