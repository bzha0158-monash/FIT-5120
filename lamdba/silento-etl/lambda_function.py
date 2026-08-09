"""
Main controller for the Silento ETL Lambda.

ETL workflow:
1. Extract raw data
2. Transform and validate the data
3. Load the cleaned data into Amazon S3
"""

import json
import logging
from datetime import datetime, timezone

from extra import (
    extract_sensor_locations,
    extract_pedestrian_counts,
)

from transforms import (
    transform_sensor_locations,
    transform_pedestrian_counts,
)

from load import load_dataframe_to_s3, load_json_to_s3


logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    AWS Lambda entry point.
    """
    run_time = datetime.now(timezone.utc)

    run_id = run_time.strftime(
        "%Y-%m-%dT%H-%M-%SZ"
    )

    run_prefix = f"cleaned/runs/{run_id}"

    logger.info(
        "Starting Silento ETL run: %s",
        run_id,
    )

    try:
        # ================================================
        # 1. EXTRACT
        # ================================================
        raw_sensor_df = extract_sensor_locations()

        logger.info(
            "Extracted %s sensor-location rows",
            len(raw_sensor_df),
        )

        # ================================================
        # 2. TRANSFORM
        # ================================================
        clean_sensor_df = transform_sensor_locations(
            raw_sensor_df
        )

        logger.info(
            "Transformed sensor rows: raw=%s, clean=%s",
            len(raw_sensor_df),
            len(clean_sensor_df),
        )

        # ================================================
        # 3. LOAD
        # ================================================
        sensor_result = load_dataframe_to_s3(
            clean_sensor_df,
            (
                f"{run_prefix}/"
                "sensor-locations.csv"
            ),
        )

        logger.info(
            "Sensor data uploaded to s3://%s/%s",
            sensor_result["bucket"],
            sensor_result["key"],
        )

        # ================================================
        # 4. EXTRACT, TRANSFORM, AND LOAD PEDESTRIAN COUNTS
        # ================================================
        raw_pedestrian_df = extract_pedestrian_counts()

        logger.info(
            "Extracted %s pedestrian-count rows",
            len(raw_pedestrian_df),
        )

        clean_pedestrian_df = transform_pedestrian_counts(
            raw_pedestrian_df
        )

        logger.info(
            "Transformed pedestrian rows: raw=%s, clean=%s",
            len(raw_pedestrian_df),
            len(clean_pedestrian_df),
        )

        pedestrian_result = load_dataframe_to_s3(
            clean_pedestrian_df,
            f"{run_prefix}/pedestrian-counts.csv",
        )

        logger.info(
            "Pedestrian data uploaded to s3://%s/%s",
            pedestrian_result["bucket"],
            pedestrian_result["key"],
        )

        # Create this marker only after every ETL step
        # has completed successfully.
        success_data = {
            "status": "completed",
            "run_id": run_id,
            "completed_at": run_time.isoformat(),
            "datasets": {
                "sensor_locations": {
                    "raw_rows": len(raw_sensor_df),
                    "clean_rows": len(clean_sensor_df),
                    "s3_key": sensor_result["key"],
                },
                "pedestrian_counts": {
                    "raw_rows": len(raw_pedestrian_df),
                    "clean_rows": len(clean_pedestrian_df),
                    "s3_key": pedestrian_result["key"],
                },
            },
        }

        success_result = load_json_to_s3(
            success_data,
            f"{run_prefix}/_SUCCESS.json",
        )

        logger.info(
            "ETL run completed successfully: %s",
            run_id,
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "ETL completed successfully",
                    "run_id": run_id,
                    "sensor_result": sensor_result,
                    "pedestrian_result": pedestrian_result,
                    "success_marker": success_result,
                },
                default=str,
            ),
        }

    except Exception:
        logger.exception(
            "ETL run failed: %s",
            run_id,
        )

        raise
