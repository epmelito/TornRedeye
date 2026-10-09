"""Persist one collector result using an existing boto3-compatible S3 client.

Use a new uuid4() for each retrieval. Retry persist() with the same UUID and
original CollectionResult; collecting again is a new run. The raw/ prefix can
expire independently of normalized/. This module configures no AWS resources.
"""

from dataclasses import asdict, dataclass
from datetime import timezone
import hashlib
import json
from typing import Literal
from uuid import UUID

from yata_collector import CollectionResult


@dataclass(frozen=True)
class PersistenceReceipt:
    collection_id: UUID
    raw_key: str | None
    normalized_key: str
    raw_state: Literal["absent", "written", "existing"]
    normalized_state: Literal["written", "existing"]


class ObjectConflictError(Exception):
    """An existing object differs from the evidence supplied for this run."""


class PersistenceError(Exception):
    """Storage failed; inspect stage/raw_state and the chained original error.

    A failed write may have reached S3 despite a lost acknowledgement. Unknown
    does not mean absent. Keep the original result and UUID for a storage retry.
    """

    def __init__(self, collection_id, stage, raw_key, normalized_key, raw_state):
        self.collection_id = collection_id
        self.stage = stage
        self.raw_key = raw_key
        self.normalized_key = normalized_key
        self.raw_state = raw_state
        self.key = raw_key if stage == "raw" else normalized_key
        super().__init__(
            f"S3 persistence failed at {stage}: {self.key}; raw_state={raw_state}"
        )


def _put_once(s3, bucket, key, body, content_type, metadata):
    try:
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
            Metadata=metadata,
            IfNoneMatch="*",
        )
        return "written"
    except Exception as error:
        # only an existing object triggers readback; other failures stay visible
        response = getattr(error, "response", {})
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
            raise
    existing = s3.get_object(Bucket=bucket, Key=key)
    stream = existing["Body"]
    try:
        existing_body = stream.read()
    finally:
        stream.close()
    if existing_body != body or existing.get("Metadata", {}) != metadata:
        raise ObjectConflictError(f"existing S3 object differs: {key}")
    return "existing"


def persist(
    result: CollectionResult, *, s3, bucket: str, collection_id: UUID
) -> PersistenceReceipt:
    """Write raw evidence first, then a normalized JSON record for this UUID.

    Conditional puts never replace existing objects. Reruns compare exact bytes
    and metadata before accepting an existing object. A conflict or SDK failure
    raises PersistenceError with its original cause and confirmed raw progress.
    No cleanup is attempted after partial writes; a matching rerun completes
    the missing write. There is no transaction across the two S3 objects.

    A result without response bytes still gets a normalized failure record.
    Empty response bytes are evidence and are stored. The caller supplies the
    region-configured client, existing bucket, and stable UUID; no credentials
    or SDK client are created here.
    """
    if not isinstance(collection_id, UUID):
        raise ValueError("collection_id must be a UUID, newly generated per retrieval")
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("bucket must be a nonempty string")
    if result.retrieved_at.tzinfo is None or result.retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone aware")
    if result.raw_response is not None and not isinstance(result.raw_response, bytes):
        raise ValueError("raw_response must be bytes or None")
    retrieved_at = result.retrieved_at.astimezone(timezone.utc).isoformat(
        timespec="microseconds"
    ).replace("+00:00", "Z")
    suffix = f"yata/jap/206/{collection_id.hex}"
    raw_key = f"raw/{suffix}.bin" if result.raw_response is not None else None
    normalized_key = f"normalized/{suffix}.json"
    raw_sha256 = (
        hashlib.sha256(result.raw_response).hexdigest() if raw_key is not None else None
    )
    document = asdict(result)
    del document["raw_response"]
    document["retrieved_at"] = retrieved_at
    document.update(
        schema_version=1,
        collection_id=str(collection_id),
        raw_evidence=(
            {
                "bucket": bucket,
                "key": raw_key,
                "sha256": raw_sha256,
                "size_bytes": len(result.raw_response),
            }
            if raw_key is not None else None
        ),
    )
    normalized_body = json.dumps(
        document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    metadata = {
        "collection-id": str(collection_id),
        "source": result.source,
        "source-url": result.source_url,
        "source-path": result.source_path,
        "country": result.country,
        "item-id": str(result.item_id),
        "retrieved-at": retrieved_at,
        "status": result.status,
        "sha256": raw_sha256,
    }
    for key, value in (
        ("source-timestamp", result.source_timestamp),
        ("export-timestamp", result.export_timestamp),
        ("http-status", result.http_status),
    ):
        if value is not None:
            metadata[key] = str(value)
    raw_state = "absent"
    if raw_key is not None:
        try:
            raw_state = _put_once(
                s3, bucket, raw_key, result.raw_response,
                "application/octet-stream", metadata,
            )
        except Exception as error:
            raise PersistenceError(
                collection_id, "raw", raw_key, normalized_key, "unknown"
            ) from error
    try:
        normalized_state = _put_once(
            s3, bucket, normalized_key, normalized_body, "application/json", {}
        )
    except Exception as error:
        raise PersistenceError(
            collection_id, "normalized", raw_key, normalized_key, raw_state
        ) from error
    return PersistenceReceipt(
        collection_id, raw_key, normalized_key, raw_state, normalized_state
    )
