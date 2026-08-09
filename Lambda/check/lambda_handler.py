"""
Lambda entry point for short-term crowd prediction, reading cleaned data
from S3 (populated by the team's ETL pipeline) instead of RDS or mock data.

Currently wires up sensor_locations, since that's the confirmed dataset so
far. If the latest run's manifest contains additional datasets (e.g.
pedestrian counts), their names are printed to CloudWatch Logs so we can
wire up the remaining forecast_logic.py inputs once the exact dataset name
is confirmed.
"""
from __future__ import annotations

from data_access import get_latest_run_prefix, load_manifest, load_sensor_locations


def lambda_handler(event, context):
    run_prefix = get_latest_run_prefix()
    manifest = load_manifest(run_prefix)

    dataset_names = list(manifest.get("datasets", {}).keys())
    print(f"Run: {run_prefix}")
    print(f"Datasets available in this run's manifest: {dataset_names}")

    locations = load_sensor_locations(manifest)
    print(f"Loaded {len(locations)} sensor locations inside the CBD demo box")

    sample = locations.head(5).to_dict("records")
    return {
        "statusCode": 200,
        "body": {
            "run": run_prefix,
            "datasets_in_manifest": dataset_names,
            "sensor_location_count": len(locations),
            "sample_locations": sample,
        },
    }


if __name__ == "__main__":
    result = lambda_handler({}, None)
    print(result)
