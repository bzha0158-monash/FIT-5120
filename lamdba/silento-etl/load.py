"""
Load stage of the Silento ETL pipeline.

This module loads transformed DataFrames into Amazon S3.
"""


import io
import json
import os
from typing import Any


import boto3
import pandas as pd


s3_client = boto3.client("s3")

def get_bucket_name() -> str:
    """
    Read the destination S3 bucket name from the
    Lambda environment variables.
    """
    return os.environ["ETL_BUCKET_NAME"]


def load_dataframe_to_s3(
    df: pd.DataFrame,
    object_key: str,
) -> dict:
    """
    Convert a cleaned DataFrame into CSV and upload it to S3.

    Parameters
    ----------
    df:
        The transformed DataFrame.

    object_key:
        The destination path inside the S3 bucket.

    Returns
    -------
    dict:
        Information about the uploaded object.
    """
    csv_buffer = io.StringIO()

    df.to_csv(
        csv_buffer,
        index=False,
    )

    bucket_name = get_bucket_name()

    response = s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=csv_buffer.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )

    return {
        "bucket": bucket_name,
        "key": object_key,
        "rows": len(df),
        "etag": response.get("ETag"),
    }


def load_json_to_s3(
    data: dict[str, Any],
    object_key: str,
) -> dict:
    """
    Upload a JSON document to S3.

    This is used to create the _SUCCESS.json marker after
    all cleaned datasets have been uploaded successfully.
    """ 
    bucket_name = get_bucket_name()

    json_content = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    response = s3_client.put_object(
        Bucket=bucket_name,
        Key=object_key,
        Body=json_content.encode("utf-8"),
        ContentType="application/json",
    )

    return {
        "bucket": bucket_name,
        "key": object_key,
        "etag": response.get("ETag"),
    }