# Offline YATA all-stock normalization

`yata_normalizer.normalize_export` processes the complete YATA travel-export
response into one compact UTF-8 JSON document. It uses only the Python standard
library and performs no network, storage, or clock reads. It is separate from
the live Japan Xanax collector and is not included in the collector package.
Production integration and historical backfill require separate authorization.

## Calling the normalizer

```python
from datetime import datetime, timezone
from pathlib import Path
from yata_normalizer import normalize_export

raw = Path("export.bin").read_bytes()
result_bytes = normalize_export(
    raw,
    datetime(2026, 10, 18, 12, tzinfo=timezone.utc),
    raw_evidence_id="original raw evidence identity",
    http_status=200,
)
```

Supply the **original retrieval time**, identity, and HTTP status from retained
evidence, not processing time or an inferred successful status. HTTP status is
a required keyword argument; omitting it raises `TypeError` before normalization.
Supplied non-2xx responses produce a `collection_failed` result without parsing
observations. Missing retrieval
context must be resolved by the caller, not invented. Naive retrieval times,
non-byte input, empty identities, and invalid HTTP status arguments raise
`ValueError` rather than masquerading as source failures.

The caller-supplied identity can identify an original S3 object or local evidence
receipt. It is preserved exactly. SHA-256 and byte length are calculated from the
exact input, including whitespace; this binds the result to those bytes but does
not verify the caller's identity or retrieval metadata. Raw bytes are not copied
into the normalized document and must remain available separately. This module
does not read backups, migrate evidence, or persist results.

## Result contract

The independent `yata-all-stock` format has `schema_version: 1`. Its envelope
contains `source`, `source_url`, UTC `retrieved_at` (microseconds and `Z`),
`http_status`, `export_timestamp`, `raw_evidence` (`identity`, `sha256`,
`size_bytes`), `status`, `countries`, and `diagnostics`. The legacy persisted
Japan schema, S3 keys, monitoring and backup contracts are unchanged.

`countries` maps every returned valid nonempty country key to a
`source_timestamp` and `items` list. No country registry or item allowlist is
imposed. Each identifiable item contains `item_id`, `name`, `quantity`, and
`cost`. Country timestamps occur once per country rather than once per item.
Names retain exact source spelling and whitespace. Quantities and costs must
be nonnegative integers; IDs must be positive integers. Booleans, numeric strings
and floats are not coerced. Reported quantity/cost zero remains measured zero.

Countries are sorted by key and items by ID. JSON keys are sorted, separators
are compact, and no run ID or processing timestamp is generated. Identical bytes
and caller context yield identical result bytes. Reordered input can change the
raw hash and diagnostic source indices even when observations are equivalent.

Statuses describe validation, not complete collection coverage:

- `complete`: the expected export structure and returned records validated.
  Empty country maps or item lists remain empty, never synthetic zero stock.
- `partial`: the stock map was readable but some fields or records were missing,
  invalid, or ambiguous. This can include a result with no usable items.
- `malformed`: invalid JSON, a non-object root, or no usable top-level stock map.
  Any independently valid export timestamp and raw identity remain available.
- `collection_failed`: a supplied non-2xx status; no stock is inferred from its body.

Each diagnostic has a `path` array of original JSON keys/list indices and a
`code`: `missing`, `invalid`, `duplicate` (JSON key), `invalid_country`,
`duplicate_item_id`, `invalid_json`, or `http_failure`. Invalid JSON includes
nonstandard NaN/Infinity constants. A missing field is distinguished from an
explicit null or invalid value through its diagnostic.

Missing or invalid timestamps, names, quantities and costs become null while
independently valid fields and neighboring observations survive. Unidentifiable
items, non-object countries/items, and blank country keys are omitted with
diagnostics. Missing or invalid item lists produce an empty list with a
diagnostic, distinct from an explicitly returned empty list. All occurrences of
a duplicate item ID within one country are omitted as ambiguous, even if one
is otherwise valid; the same ID in different countries is valid. Duplicate JSON
keys are not silently resolved: only that field or record becomes unavailable.
Consumers must inspect diagnostics and field nullability before analysis.

## Time and evidence interpretation

Export and country update timestamps retain the source's nonnegative integer
Unix seconds. Country update `0` means **unknown update time**, not a Unix-epoch
stock observation. Missing or invalid update times are null with diagnostics.
Export timestamp zero remains zero, consistent with the existing collector.

Cached responses remain separate retrievals when supplied with different original
retrieval context. Repeated values and timestamps are not deduplicated into new
source updates. Old or future timestamps are preserved; no freshness cutoff,
stale/current classification, or guarantee is invented. For freshness analysis,
compare known timestamps to retrieval time, treating country update zero as
unknown and retaining future-time discrepancies. Neither retrieval time nor a
source update time establishes an exact restock, sellout, or stock-change event.

## Offline validation

```text
python -B -m unittest discover -s tests -p test_yata_normalizer.py
python -B -m unittest discover -s tests
git diff --check
```

Tests use synthetic bytes and cover all-country/item processing, raw linkage,
determinism, zeros, malformed neighbors, duplicate identity, missing fields,
timestamp and cached-response semantics, HTTP failure, and legacy Japan behavior.
