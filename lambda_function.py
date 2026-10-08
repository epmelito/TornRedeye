"""Lambda entry point for one YATA retrieval and its S3 evidence writes."""

import logging
import math
import os
from uuid import uuid4

from s3_persistence import persist
from yata_collector import collect


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class CollectionError(RuntimeError):
    """The collection failed or was malformed; its evidence was persisted."""


def _configuration():
    bucket = os.environ.get("DESTINATION_BUCKET", "").strip()
    if not bucket:
        raise ValueError("DESTINATION_BUCKET must be configured")
    try:
        timeout = float(os.environ.get("YATA_TIMEOUT_SECONDS", "15"))
    except ValueError as error:
        raise ValueError("YATA_TIMEOUT_SECONDS must be a finite positive number") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("YATA_TIMEOUT_SECONDS must be a finite positive number")
    region = os.environ.get("AWS_REGION", "eu-north-1").strip()
    if not region:
        raise ValueError("AWS_REGION must be nonempty when supplied")
    return bucket, timeout, region


def _s3_client(region):
    # the Lambda Python runtime supplies boto3; local tests need no SDK
    import boto3

    return boto3.client("s3", region_name=region)


def lambda_handler(event, context):
    """Collect once, persist all outcomes, then signal source/storage failures.

    Event contents do not configure storage or identity. Each invocation makes
    a new retrieval, including redelivery of the same event. Storage retries
    for the original evidence still require its original UUID and result.
    """
    collection_id = uuid4()
    request_id = getattr(context, "aws_request_id", None)
    stage = "configuration"
    try:
        bucket, timeout, region = _configuration()
        stage = "client_initialization"
        s3 = _s3_client(region)
        stage = "collection"
        result = collect(timeout=timeout)
        logger.info(
            "collection_id=%s request_id=%s status=%s retrieved_at=%s "
            "source_timestamp=%s export_timestamp=%s http_status=%s detail=%s",
            collection_id, request_id, result.status, result.retrieved_at.isoformat(),
            result.source_timestamp, result.export_timestamp, result.http_status, result.detail,
        )
        stage = "persistence"
        receipt = persist(result, s3=s3, bucket=bucket, collection_id=collection_id)
        logger.info(
            "persisted collection_id=%s raw_key=%s normalized_key=%s "
            "raw_state=%s normalized_state=%s",
            collection_id, receipt.raw_key, receipt.normalized_key,
            receipt.raw_state, receipt.normalized_state,
        )
        if result.status in ("collection_failed", "malformed"):
            stage = "collection_outcome"
            raise CollectionError(
                f"collection_id={collection_id} status={result.status} "
                f"normalized_key={receipt.normalized_key}: {result.detail}"
            )
        if result.status == "missing":
            logger.warning("missing collection_id=%s: %s", collection_id, result.detail)
        return {
            "collection_id": str(collection_id),
            "status": result.status,
            "raw_key": receipt.raw_key,
            "normalized_key": receipt.normalized_key,
        }
    except Exception:
        logger.exception(
            "invocation failed stage=%s collection_id=%s request_id=%s",
            stage, collection_id, request_id,
        )
        raise
