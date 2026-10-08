# TornRedeye

A data engineering project exploring Torn's foreign market stock patterns,
restock timing, and probabilistic forecasting.

The Python collector and S3 persistence functions can be called directly:

```python
from uuid import uuid4

import boto3

from s3_persistence import persist
from yata_collector import collect

s3 = boto3.client("s3", region_name="eu-north-1")
collection_id = uuid4()
result = collect()
receipt = persist(result, s3=s3, bucket="YOUR_EXISTING_BUCKET", collection_id=collection_id)
```

The caller supplies boto3 and an existing bucket. The SDK must support S3
`put_object(IfNoneMatch="*")`. Client credentials use the SDK's normal credential
chain. Storage requires `s3:PutObject` and `s3:GetObject` for retry comparisons.
No SDK dependency is needed to import these modules or run the deterministic tests.

Generate a new UUID for each retrieval, even when YATA returns cached data.
Retry storage with the **same UUID and original result**, including its retrieval
time; fetching again is a separate collection run. Source timestamps never
identify a run. Conditional writes accept matching existing bytes and metadata
and reject conflicting evidence without overwriting it.

Raw bytes are under `raw/yata/jap/206/` and normalized JSON records under
`normalized/yata/jap/206/`. Raw objects also carry retrieval/provenance metadata;
normalized records link to the raw key, byte count, and SHA-256 digest. A collection
without an available response body has a normalized record with
`raw_evidence: null`; an empty or partial response body is still retained.
Collection status and errors are saved without inventing an observation. Timestamp ages support freshness assessment;
no freshness threshold or exact restock/sellout time is inferred.

Raw is written first. `PersistenceError` identifies the failed stage, object keys,
confirmed raw progress, and the original exception via `__cause__`. A failed
write may have succeeded remotely. Keep the UUID and original result for a
storage retry; it verifies existing objects and completes missing writes.
There is no transaction across the two objects: interrupted writes can leave
raw-only evidence. No existing objects are deleted or replaced.

The Lambda entry point is `lambda_function.lambda_handler`. It uses the SDK
included in the Lambda Python runtime; no additional runtime dependency is
introduced. These environment variables configure it:

| Variable | Behavior |
| --- | --- |
| `DESTINATION_BUCKET` | Required, existing destination bucket |
| `YATA_TIMEOUT_SECONDS` | Optional, finite positive request timeout; defaults to 15 seconds |
| `AWS_REGION` | Supplied by Lambda; defaults to `eu-north-1` outside Lambda |

Invalid configuration or SDK initialization fails before collection. A valid
invocation calls the collector once and passes its unchanged result to S3
persistence. Observed stock, including zero, returns a successful summary.
Missing records are persisted and return a `missing` status with a warning.
Malformed responses and collection failures are persisted first, then raise
`CollectionError` so Lambda sees an invocation failure. Persistence failures
propagate unchanged with their cause and partial-write progress. Logs include
the UUID, Lambda request ID, collection status, timestamps, diagnostics, and
storage outcome; raw response bodies are not logged.

Each invocation gets a new retrieval UUID, including redelivery of the same
event. The handler does not retry collection or persistence itself. SDK storage
retries use the same write content and keys; a new Lambda invocation performs
a new retrieval. A failed invocation cannot automatically resume the prior
in-memory result. Raw-only evidence may remain after a partial write, and an
S3 failure before the raw write completes can leave evidence unpersisted; the
failure is reported. Set the Lambda execution timeout to allow for collection
plus S3 writes and SDK retries when deploying.

The undeployed [SAM template](template.json) defines storage retention, Lambda,
IAM, logs, and a disabled schedule. See [deployment and operation](docs/deployment.md)
for packaging, offline checks, deployment commands, and data-retention limits.
