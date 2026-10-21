# Operational stock evidence

Issue #53's approved 2026-10-21 decisions supersede migration and dual-prefix
proposals. The offline implementation starts evidence lineage `live-v1`.
Deployment and cutover have not been performed.

## Storage and identity

The existing private S3 bucket stores exactly the original response and one
canonical all-stock attempt document:

```text
evidence/live-v1/YYYY/MM/DD/HH/<uuid.hex>/raw.bin
evidence/live-v1/YYYY/MM/DD/HH/<uuid.hex>/attempt.json
```

The partition is the original retrieval hour in UTC, never processing or retry
time. Each acquisition gets one UUID. Raw bytes (including empty bytes) precede
the document; no response bytes means a failure document without a raw object.
Both PUTs use `If-None-Match: *`. A retry must retain the original result, UUID
and time; existing bytes, metadata and ContentType must match exactly. Lost
acknowledgements remain uncertain until retry/readback. There is no transaction,
cleanup, overwrite, third collection object or extra source request. A bodyless
retry performs one exact raw-key LIST to reject a conflicting raw-only attempt;
ordinary two-object persistence performs no namespace discovery.

## Primary document version 1

`format=stock-evidence`, `schema_version=1`, `lineage`, `collection_id`, `source`,
`source_url`, `retrieved_at`, `http_status`, `export_timestamp`, `raw_evidence`,
`acquisition`, `validation_status`, `countries`, and `diagnostics` form the primary
contract. Raw linkage contains original bucket/key, SHA-256 and byte length, or
null. The source identifier is provenance, not a country/item or storage layout.

Acquisition status is `success` or `collection_failed`. Acquisition context
retains detail, selected original Date/Retry-After headers and parsed restriction
information. A complete HTTP response can succeed in acquisition while failing
export validation. A partial transport response never supplies stock observations,
even if its retained bytes happen to parse with HTTP 200.

Validation status is `complete`, `partial`, `malformed` or `collection_failed`.
Countries retain source-reported timestamps and item arrays containing item ID,
name, quantity and cost. Missing/invalid fields remain null with path/code
diagnostics; ambiguous identities are omitted. Observed quantity/cost zero remain
zero. Country update zero means unknown time. Valid neighbours survive partial
validation. Complete validation never proves complete coverage or freshness.
Individual country/item eligibility is evaluated separately by the reader.

The existing YATA acquisition pipeline and deterministic payload parser remain
source adapters. The observation contract has no Japan fields, YATA-specific
format name, pilot envelope, provider registry or speculative provider framework.
Raw metadata retains lineage, attempt identity, original retrieval/source locator,
HTTP and acquisition status, and raw/context SHA-256 digests. Detailed acquisition
diagnostics and response headers live only in the canonical attempt document, keeping raw user
metadata below S3's 2 KiB limit for the existing acquisition pipeline. Raw-only
partial writes retain the original success/failure status: a failed acquisition
never supplies observations, even when its bytes parse. Missing detailed context
after a partial write must not be invented; an exact retry completes the document.
The fixed-size context digest binds retries to the original acquisition details
and headers without embedding them in raw user metadata.

## Availability, archive and recovery

S3 LastModified and verified read receipts establish storage availability;
embedded retrieval/source times and key partitions do not. Forecast inputs retain
both kinds of time. Time partitioning is discovery, not a completeness assertion
about collection between observations. See [shadow forecasting](shadow-forecast.md).

Old S3 objects, forecasts, Azure baseline and completion manifests remain sealed
and unchanged. New operational readers do not enumerate them, migrate links,
reserialize historical records or use historical labels for training. Archive
interpretation uses its preserved original code/configuration and trust material.
See [backup recovery](evidence-backup.md) and [cutover](deployment.md).
