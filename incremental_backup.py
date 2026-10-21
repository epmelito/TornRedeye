"""Verified batch coverage and disposable bounded reconciliation; no cloud SDK.

Checksums detect corruption, not forgery. Authenticate the empty-lineage anchor
and completion-manifest origin. Full provenance stays in unchanged archives.
"""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import tempfile
import time

import evidence_backup as backup

from tornredeye.evidence_paths import LINEAGE, PREFIXES, validate_backup_key

AZURE_ROOT = f"operational/{LINEAGE}/"
ANCHOR_BLOB = AZURE_ROOT + "anchor.json"
BATCH_PREFIX = AZURE_ROOT + "batches/"
SCHEMA = 3
PARTITIONS = tuple(f"{number:02x}" for number in range(256))
MAX_RECORDS = 65_536
MAX_PARTITION_BYTES = 16 * 1024 ** 2
MAX_SUMMARY_BYTES = 4 * 1024 ** 2
MAX_BATCHES = 8192
MAX_COMPLETION_BYTES = 16 * 1024 ** 2
MAX_BATCH_OBJECTS = 4096
MAX_ARCHIVE_BYTES = 128 * 1024 ** 2


class CatalogError(backup.BackupError):
    """Coverage or sweep completeness could not be established."""


def _require(condition, message):
    if not condition:
        raise CatalogError(message)


def _limits(**limits):
    _require(all(type(v) is int and v > 0 for v in limits.values()), "limits must be positive integers")


def _hash(value):
    _require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "invalid SHA-256")


def _utc(value):
    _require(isinstance(value, str), "invalid source timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.utcoffset() == timezone.utc.utcoffset(parsed), "timestamp must be explicit UTC")
        return parsed.isoformat()
    except ValueError as error:
        raise CatalogError("invalid source timestamp") from error


def key_parts(key):
    try:
        validate_backup_key(key)
    except ValueError as error:
        raise CatalogError("unknown operational object key") from error
    return "object", key


def source_key(kind, identifier):
    _require(kind == "object", "invalid object identity")
    key_parts(identifier)
    return identifier


def reference_id(key, revision):
    return backup._digest({"key": key, "revision": revision})


def partition_for(key):
    return backup._key_id(key)[:2]


def empty_anchor(bucket):
    _require(isinstance(bucket, str) and bool(bucket), "missing source bucket")
    return {"schema_version": SCHEMA, "format": "empty-lineage-anchor", "lineage": LINEAGE,
            "bucket": bucket, "prefixes": list(PREFIXES)}


def validate_anchor(value, trusted_anchor_sha256):
    _hash(trusted_anchor_sha256)
    _require(value == empty_anchor(value["bucket"])
             and backup._digest(value) == trusted_anchor_sha256, "untrusted lineage anchor")
    return value


def revision_fingerprint(item):
    """Hash the listing descriptor, never treat an ETag as a content hash."""
    _require(isinstance(item, dict), "invalid inventory object")
    key_parts(item["key"])
    _require(type(item["size"]) is int and item["size"] >= 0, "invalid source size")
    _require(isinstance(item["etag"], str) and 0 < len(item["etag"]) <= 1024, "invalid source ETag")
    return backup._digest({"size": item["size"], "etag": item["etag"],
                           "last_modified": _utc(item["last_modified"])})


def _empty(root):
    _require(not any(p.name != "writer.lock" for p in root.iterdir()),
             "output directory must be empty; restart in a fresh directory")


def _metadata_size(value, limit):
    # count the canonical wrapper without allocating an oversized serialized copy
    encoder = json.JSONEncoder(sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True, allow_nan=False)
    size = 0
    for chunk in encoder.iterencode({"value": value, "sha256": "0" * 64}):
        size += len(chunk.encode("utf-8"))
        _require(size <= limit, "serialized metadata limit exceeded")
    return size


def _write_bounded(root, name, value, limit):
    size = _metadata_size(value, limit)
    backup._write(root, name, value)
    return size


def _append(root, partition, record):
    data = backup._bytes(record) + b"\n"
    with (root / (partition + ".jsonl")).open("ab") as stream:
        stream.write(data)
    return len(data)


def _spooled(root, partition, max_records, max_bytes):
    path = root / (partition + ".jsonl")
    if not path.exists():
        return []
    _require(path.stat().st_size <= max_bytes, "partition spool limit exceeded")
    records = []
    with path.open("rb") as stream:
        for line in stream:
            _require(len(records) < max_records, "partition record limit exceeded")
            records.append(json.loads(line))
    return records


def _record(row):
    _require(isinstance(row, list) and len(row) == 5, "invalid compact coverage")
    source_key(row[0], row[1])
    _hash(row[2])
    _hash(row[3])
    _require(type(row[4]) is int and row[4] >= 0, "invalid covered byte length")


def batch_blob(batch):
    _hash(batch)
    return BATCH_PREFIX + batch + ".zip"


def validate_completion(value, bucket):
    """Completion rows come only from independently verified persisted receipts."""
    _require(value["schema_version"] == SCHEMA and value["status"] == "destination_verified"
             and value["bucket"] == bucket, "invalid batch completion")
    _hash(value["batch"])
    _require(value["blob"] == batch_blob(value["batch"]), "batch destination conflict")
    _hash(value["sha256"])
    _hash(value["manifest_sha256"])
    _require(type(value["size"]) is int and 0 <= value["size"] <= MAX_ARCHIVE_BYTES,
             "invalid archive length")
    rows = value["coverage"]
    _require(isinstance(rows, list) and 0 < len(rows) <= MAX_BATCH_OBJECTS, "invalid completion count")
    refs, payload_bytes = set(), 0
    for row in rows:
        _record(row)
        ref = reference_id(source_key(row[0], row[1]), row[2])
        _require(ref not in refs, "duplicate completion revision")
        refs.add(ref)
        payload_bytes += row[4]
    _require(payload_bytes <= value["size"], "completion byte total inconsistent")
    _require(value["batch"] == backup._digest({"bucket": bucket, "objects": sorted(refs)}),
             "completion identity conflict")
    return value


def reconstruct_coverage(anchor, completions, *, trusted_anchor_sha256, output_directory,
                         max_records_per_partition=MAX_RECORDS, max_partition_bytes=MAX_PARTITION_BYTES,
                         max_spool_bytes=4 * 1024 ** 3, check_runtime=lambda: None):
    """Rebuild disposable local coverage; never publish or replace remote catalogs.

    Origin of immutable completion records must be authenticated by the caller.
    Download each compact record once; never download historical payloads here.
    """
    _limits(max_records_per_partition=max_records_per_partition, max_partition_bytes=max_partition_bytes,
            max_spool_bytes=max_spool_bytes)
    validate_anchor(anchor, trusted_anchor_sha256)
    with backup._lock(output_directory) as root:
        _empty(root)
        records = root / "records"
        records.mkdir()
        header = {"schema_version": SCHEMA, "complete": False, "bucket": anchor["bucket"],
                  "anchor_sha256": trusted_anchor_sha256, "batches": {}, "partitions": {}}
        total = 0
        counts, sizes = dict.fromkeys(PARTITIONS, 0), dict.fromkeys(PARTITIONS, 0)
        def append(row, batch):
            nonlocal total
            check_runtime()
            data = row + [batch]
            partition = partition_for(row[1])
            length = len(backup._bytes(data)) + 1
            counts[partition] += 1
            sizes[partition] += length
            _require(counts[partition] <= max_records_per_partition
                     and sizes[partition] <= max_partition_bytes, "coverage partition limit exceeded")
            total += length
            _require(total <= max_spool_bytes, "coverage spool limit exceeded")
            _append(records, partition, data)
        for value in completions:
            validate_completion(value, header["bucket"])
            batch = value["batch"]
            _require(batch not in header["batches"], "duplicate batch completion")
            _require(len(header["batches"]) < MAX_BATCHES, "completion count limit exceeded")
            header["batches"][batch] = {k: value[k] for k in ("blob", "size", "sha256", "manifest_sha256")}
            for row in value["coverage"]:
                append(row, batch)
        for partition in PARTITIONS:
            check_runtime()
            rows = _spooled(records, partition, max_records_per_partition, max_partition_bytes)
            seen = set()
            for row in rows:
                _record(row[:5])
                _require(tuple(row[:3]) not in seen, "duplicate or conflicting covered revision")
                seen.add(tuple(row[:3]))
            path = records / (partition + ".jsonl")
            if not path.exists():
                path.touch()
            size, checksum = backup._file_hash(path)
            header["partitions"][partition] = {"records": len(rows), "bytes": size, "sha256": checksum}
        header["complete"] = True
        _write_bounded(root, "coverage.json", header, MAX_SUMMARY_BYTES)
        return header


def load_coverage(directory):
    value = backup._read(Path(directory).absolute(), "coverage.json", MAX_SUMMARY_BYTES)
    _require(value["schema_version"] == SCHEMA and value["complete"] is True
             and set(value["partitions"]) == set(PARTITIONS), "incomplete local coverage")
    return value


def read_coverage_partition(directory, partition, *, coverage=None,
                            max_records=MAX_RECORDS, max_bytes=MAX_PARTITION_BYTES):
    _limits(max_records=max_records, max_bytes=max_bytes)
    _require(partition in PARTITIONS, "invalid partition")
    header = coverage if coverage is not None else load_coverage(directory)
    path = backup._path(Path(directory).absolute(), "records/" + partition + ".jsonl")
    descriptor = header["partitions"][partition]
    _require(descriptor["records"] <= max_records and descriptor["bytes"] <= max_bytes,
             "coverage partition limit exceeded")
    _require(backup._file_hash(path) == (descriptor["bytes"], descriptor["sha256"]),
             "local coverage checksum mismatch")
    rows = _spooled(path.parent, partition, max_records, max_bytes)
    _require(len(rows) == descriptor["records"], "incomplete coverage partition")
    return {"entries": rows}


def _reference(entry, coverage):
    kind, identifier, revision, checksum, size, batch = entry
    key = source_key(kind, identifier)
    descriptor = coverage["batches"][batch]
    return {"key": key, "revision": revision, "sha256": checksum, "size": size,
            "batch": batch, "blob": descriptor["blob"], "batch_sha256": descriptor["sha256"],
            "receipt_member": "receipts.json",
            "receipt_entry": reference_id(key, revision)}


def _plan_partition(value, observations, index):
    """Bounded core, also used for deterministic scalability measurements."""
    coverage, seen = {}, {}
    for entry in value["entries"]:
        coverage.setdefault((entry[0], entry[1]), set()).add(entry[2])
    result = {"already_covered": 0, "new": [], "changed": [], "previous_revision_not_visible": []}
    for item in observations:
        kind, identifier = key_parts(item["key"])
        identity = (kind, identifier)
        fingerprint = revision_fingerprint(item)
        _require(identity not in seen, "duplicate or conflicting source observation")
        seen[identity] = fingerprint
        revisions = coverage.get(identity)
        if revisions is not None and fingerprint in revisions:
            result["already_covered"] += 1
        else:
            field = "new" if revisions is None else "changed"
            result[field].append({"key": item["key"], "size": item["size"], "etag": item["etag"],
                                  "last_modified": _utc(item["last_modified"]), "revision": fingerprint})
    for entry in value["entries"]:
        if seen.get((entry[0], entry[1])) != entry[2]:
            result["previous_revision_not_visible"].append(_reference(entry, index))
    return result


def plan_reconciliation(coverage_directory, inventories, *, output_directory,
                        max_objects_per_partition=MAX_RECORDS, max_records_per_partition=MAX_RECORDS,
                        max_partition_bytes=MAX_PARTITION_BYTES, max_pages=262_144,
                        max_spool_bytes=4 * 1024 ** 3, max_output_bytes=4 * 1024 ** 3, max_seconds=3600):
    """Scan full prefixes, then reconcile one key-hash partition at a time.

    inventories maps canonical prefixes to saved-format page iterables. Every
    key is validated before routing; narrow partition listings hide unknown formats.
    Return a small summary; classifications live in bounded partition files. Failed
    sweeps never claim global completion. Restart with fresh scratch/output and
    verified coverage. Keep coverage/input files quiescent; live source scans remain
    provisional. No catalog structure is copied or mutated.
    """
    _limits(max_objects_per_partition=max_objects_per_partition, max_records_per_partition=max_records_per_partition,
            max_partition_bytes=max_partition_bytes, max_pages=max_pages,
            max_spool_bytes=max_spool_bytes, max_output_bytes=max_output_bytes)
    _require(type(max_seconds) in (int, float) and math.isfinite(max_seconds) and max_seconds > 0,
             "invalid runtime limit")
    started = time.monotonic()
    summary = {"schema_version": SCHEMA, "complete": False, "consistent_cutoff": False,
               "prefixes_complete": [], "pages": 0, "observed_objects": 0, "spool_bytes": 0,
               "output_bytes": 0, "partitions": {}, "error": None}
    def runtime():
        _require(time.monotonic() - started < max_seconds, "sweep runtime limit exceeded")
    with backup._lock(output_directory) as output, tempfile.TemporaryDirectory() as temporary:
        _empty(output)
        spool = Path(temporary)
        try:
            index = load_coverage(coverage_directory)
            summary["coverage_sha256"] = backup._digest(index)
            summary["bucket"] = index["bucket"]
            _require(set(inventories) == set(backup.PREFIXES), "all complete prefixes required")
            counts = dict.fromkeys(PARTITIONS, 0)
            for prefix in backup.PREFIXES:
                token, last, complete, tokens = None, "", False, set()
                for page in inventories[prefix]:
                    runtime()
                    _require(not complete, "page after terminal page")
                    _require(summary["pages"] < max_pages, "sweep page limit exceeded")
                    _require(isinstance(page, dict) and page["request_token"] == token, "invalid page token chain")
                    items, next_token = page["objects"], page["next_token"]
                    _require(isinstance(items, list) and len(items) <= 1000, "invalid inventory page size")
                    _require(next_token is None or isinstance(next_token, str) and bool(next_token),
                             "invalid continuation token")
                    _require(next_token is None or next_token not in tokens and bool(items),
                             "repeated token or empty truncated page")
                    for item in items:
                        runtime()
                        kind, identifier = key_parts(item["key"])
                        revision_fingerprint(item)
                        _require(item["key"].startswith(prefix) and item["key"] > last,
                                 "duplicate, conflicting, or unordered inventory key")
                        last, partition = item["key"], partition_for(identifier)
                        counts[partition] += 1
                        _require(counts[partition] <= max_objects_per_partition, "source partition object limit exceeded")
                        observation = {"key": item["key"], "size": item["size"],
                                       "etag": item["etag"], "last_modified": _utc(item["last_modified"])}
                        projected = summary["spool_bytes"] + len(backup._bytes(observation)) + 1
                        _require(projected <= max_spool_bytes, "sweep spool limit exceeded")
                        _append(spool, partition, observation)
                        summary["spool_bytes"] = projected
                        summary["observed_objects"] += 1
                    summary["pages"] += 1
                    token, complete = next_token, next_token is None
                    if token is not None:
                        tokens.add(token)
                _require(complete, "interrupted prefix inventory")
                summary["prefixes_complete"].append(prefix)
            for partition in PARTITIONS:
                runtime()
                value = read_coverage_partition(coverage_directory, partition, coverage=index,
                                   max_records=max_records_per_partition, max_bytes=max_partition_bytes)
                observations = _spooled(spool, partition, max_objects_per_partition, max_partition_bytes)
                result = _plan_partition(value, observations, index)
                runtime()
                result.update(schema_version=SCHEMA, partition=partition, bucket=index["bucket"])
                size = _write_bounded(output, f"partitions/{partition}.json", result,
                                      min(4 * max_partition_bytes, max_output_bytes - summary["output_bytes"]))
                summary["output_bytes"] += size
                summary["partitions"][partition] = {"sha256": backup._digest(result),
                    "observed": len(observations), "records": len(value["entries"]),
                    "already_covered": result["already_covered"],
                    **{f: len(result[f]) for f in ("new", "changed", "previous_revision_not_visible")}}
                del value, observations, result
            runtime()
            summary["complete"] = True
        except Exception as error:
            summary["error"] = str(error) if isinstance(error, CatalogError) else type(error).__name__
        _write_bounded(output, "sweep.json", summary, MAX_SUMMARY_BYTES)
    return summary


def read_plan_partition(directory, partition, *, max_bytes=4 * MAX_PARTITION_BYTES):
    """Expose classifications only after a complete checksummed whole sweep."""
    _limits(max_bytes=max_bytes)
    _require(partition in PARTITIONS, "invalid partition")
    try:
        root = Path(directory).absolute()
        summary = backup._read(root, "sweep.json", MAX_SUMMARY_BYTES)
        _require(summary["complete"] is True and set(summary["partitions"]) == set(PARTITIONS)
                 and summary["prefixes_complete"] == list(backup.PREFIXES), "incomplete sweep")
        value = backup._read(root, f"partitions/{partition}.json", max_bytes)
        _require(value["partition"] == partition and backup._digest(value) == summary["partitions"][partition]["sha256"],
                 "invalid plan partition")
        return value
    except CatalogError:
        raise
    except (backup.BackupError, OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise CatalogError("invalid plan output") from error
