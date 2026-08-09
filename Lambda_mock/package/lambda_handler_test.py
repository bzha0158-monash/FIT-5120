"""
Standalone test version of the crowd prediction Lambda — no RDS required.
Use this to validate the forecasting logic and the AWS Lambda deployment
itself before the team's RDS instance and credentials are ready.

Once RDS is up: switch the Lambda's handler setting to
lambda_handler.lambda_handler (which reads real data via data_access.py).
forecast_logic.py needs no changes either way.
"""
from __future__ import annotations

import pandas as pd

from forecast_logic import build_forecast_frame
from mock_data import (
    mock_sensor_locations,
    mock_recent_totals,
    mock_historical_stats,
)


def lambda_handler(event, context):
    locations = mock_sensor_locations()
    recent_totals, latest_time = mock_recent_totals()

    next_time = latest_time + pd.Timedelta(hours=1)
    location_ids = locations["location_id"].tolist()

    current_stats = mock_historical_stats(location_ids, variant="current")
    next_stats = mock_historical_stats(location_ids, variant="next")

    forecast = build_forecast_frame(
        locations, recent_totals, current_stats, next_stats, latest_time, next_time
    )

    output = forecast[[
        "location_id", "current_count", "current_level",
        "expected_count", "forecast_level", "forecast_method",
    ]].to_dict("records")

    print(output)  # shows up in CloudWatch Logs when run as a real Lambda invoke
    return {"statusCode": 200, "body": output}


if __name__ == "__main__":
    # Run locally, no AWS needed: python lambda_handler_test.py
    result = lambda_handler({}, None)
    for row in result["body"]:
        print(row)
