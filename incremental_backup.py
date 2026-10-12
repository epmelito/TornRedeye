"""Offline partitioned coverage; no SDK, cloud transfer, extraction or scheduling.

Checksums detect corruption, not forgery. Index origin and verified immutable
batch receipts must be trusted. Full provenance stays in those receipts.
"""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

import evidence_backup as backup

BASELINE_BLOB = "baseline/yata-cutoff-20261011.7z"
BASELINE_SHA256 = "e57758215de330b7f9db1fd8a75754ba2161764f32824cfb2485bcf639cef5f8"
SCHEMA = 3
SHARDS = tuple(f"{number:02x}" for number in range(256))
MAX_RECORDS = 65_536
MAX_SHARD_BYTES = 16 * 1024 ** 2
MAX_ROOT_BYTES = 4 * 1024 ** 2
MAX_BATCHES = 8192
KINDS = {"r": (backup.PREFIXES[0], ".bin"), "n": (backup.PREFIXES[1], ".json")}


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
    """Validate lowercase UUID.hex filenames, without imposing a UUID version.

    persist accepts any UUID object; the live handler generates uuid4 objects.
    """
    _require(isinstance(key, str), "invalid source key")
    for kind, (prefix, extension) in KINDS.items():
        if key.startswith(prefix):
            suffix = key[len(prefix):]
            _require(re.fullmatch(r"[0-9a-f]{32}" + re.escape(extension), suffix),
                     "unknown collector key format")
            return kind, suffix[:32]
    raise CatalogError("unknown collector key prefix")


def source_key(kind, identifier):
    _require(isinstance(kind, str) and kind in KINDS and isinstance(identifier, str)
             and re.fullmatch(r"[0-9a-f]{32}", identifier), "invalid collection identity")
    prefix, extension = KINDS[kind]
    return prefix + identifier + extension


def revision_fingerprint(item):
    """Hash the listing descriptor, never treat an ETag as a content hash."""
    _require(isinstance(item, dict), "invalid inventory object")
    key_parts(item["key"])
    _require(type(item["size"]) is int and item["size"] >= 0, "invalid source size")
    _require(isinstance(item["etag"], str) and 0 < len(item["etag"]) <= 1024, "invalid source ETag")
    return backup._digest({"size": item["size"], "etag": item["etag"],
                           "last_modified": _utc(item["last_modified"])})


def _blob(value):
    _require(isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 1024 and "\\" not in value
             and all(p not in ("", ".", "..") for p in value.split("/")), "invalid immutable reference")


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


def _baseline_records(directory, verified_archive_sha256, max_objects, max_pages, max_bytes):
    """Reuse historical page, receipt, payload and relationship verification."""
    _require(isinstance(verified_archive_sha256, str)
             and verified_archive_sha256.lower() == BASELINE_SHA256, "untrusted baseline archive")
    root = Path(directory).absolute()
    manifest = backup._manifest(root)
    _require(bool(manifest["bucket"]), "missing source bucket")
    _require(all(s["complete"] and not s["error"] for s in manifest["inventory"]),
             "incomplete baseline inventory")
    cutoff = _utc(manifest["paused_cutoff_utc"])
    count = sum(s["objects"] for s in manifest["inventory"])
    size = sum(s["bytes"] for s in manifest["inventory"])
    _require(count <= max_objects and size <= max_bytes
             and sum(s["pages"] for s in manifest["inventory"]) <= max_pages, "baseline limit exceeded")
    yield manifest["bucket"], {"cutoff_utc": cutoff, "objects": count, "bytes": size}
    expected = {backup._page_name(i, n) for i, s in enumerate(manifest["inventory"])
                for n in range(s["pages"])}
    verified_count, verified_bytes = 0, 0
    for page in backup._pages(root, manifest):
        for item in page["objects"]:
            kind, identifier = key_parts(item["key"])
            fingerprint = revision_fingerprint(item)
            verified_count += 1
            verified_bytes += item["size"]
            _require(verified_count <= max_objects and verified_bytes <= max_bytes,
                     "baseline verification limit exceeded")
            key_id = backup._key_id(item["key"])
            payload, receipt_member = f"objects/{key_id}.bin", f"objects/{key_id}.json"
            expected.update((payload, receipt_member))
            _require(backup._path(root, payload).stat().st_size == item["size"],
                     "baseline payload length inconsistent")
            receipt = backup._receipt(root, manifest, item)
            _require(receipt is not None and receipt.get("status") == "verified"
                     and not receipt.get("error"), "baseline receipt not verified")
            _require(backup._verified(root, item, receipt) == "verified",
                     "baseline payload or metadata inconsistent")
            relationship = backup._relationship(root, manifest, item)
            allowed = ({"normalized_receipt_present", "no_normalized_local_receipt"} if kind == "r"
                       else {"raw_link_verified", "no_raw_link"})
            _require(relationship in allowed, "baseline relationship unresolved")
            _require(receipt.get("version_id") is None or isinstance(receipt["version_id"], str),
                     "invalid source version")
            yield [kind, identifier, fingerprint, "baseline"], item["size"]
    for folder in ("inventory", "objects"):
        for path in backup._path(root, folder).rglob("*"):
            relative = path.relative_to(root).as_posix()
            backup._path(root, relative)
            _require(not path.is_file() or relative in expected, "unindexed baseline evidence")


def _append(root, shard, record):
    data = backup._bytes(record) + b"\n"
    with (root / (shard + ".jsonl")).open("ab") as stream:
        stream.write(data)
    return len(data)


def _spooled(root, shard, max_records, max_bytes):
    path = root / (shard + ".jsonl")
    if not path.exists():
        return []
    _require(path.stat().st_size <= max_bytes, "partition spool limit exceeded")
    records = []
    with path.open("rb") as stream:
        for line in stream:
            _require(len(records) < max_records, "partition record limit exceeded")
            records.append(json.loads(line))
    return records


def build_baseline_index(directory, *, index_directory, verified_archive_sha256,
                         max_objects=100_000, max_pages=10_000, max_bytes=100 * 1024 ** 3,
                         max_records_per_shard=MAX_RECORDS, max_shard_bytes=MAX_SHARD_BYTES):
    """Publish the root only after every baseline object has verified.

    Authenticate the archive before safe extraction and ensure no concurrent
    writer. The SHA argument attests to that check, not to extraction origin.
    Full metadata remains in the unchanged archive's receipts. One-off baseline
    bounds do not limit the growing production index.
    """
    _limits(max_objects=max_objects, max_pages=max_pages, max_bytes=max_bytes,
            max_records_per_shard=max_records_per_shard, max_shard_bytes=max_shard_bytes)
    try:
        with backup._lock(index_directory) as root, tempfile.TemporaryDirectory() as temporary:
            _empty(root)
            spool = Path(temporary)
            records = _baseline_records(directory, verified_archive_sha256,
                                        max_objects, max_pages, max_bytes)
            bucket, baseline = next(records)
            counts, sizes = dict.fromkeys(SHARDS, 0), dict.fromkeys(SHARDS, 0)
            for record, size in records:
                shard = record[1][:2]
                counts[shard] += 1
                sizes[shard] += size
                _require(counts[shard] <= max_records_per_shard, "baseline shard limit exceeded")
                _append(spool, shard, record)
            index = {"schema_version": SCHEMA, "bucket": bucket, "prefixes": list(backup.PREFIXES),
                     "baseline": baseline, "batches": {"baseline": {
                         "kind": "baseline", "blob": BASELINE_BLOB, "sha256": BASELINE_SHA256}}, "shards": {}}
            for shard in SHARDS:
                entries = _spooled(spool, shard, max_records_per_shard, max_shard_bytes)
                value = {"schema_version": SCHEMA, "bucket": bucket, "shard": shard, "entries": entries}
                index["shards"][shard] = _stage_shard(
                    root, value, index, sizes[shard], max_records_per_shard, max_shard_bytes)
            return _publish_index(root, index, max_records_per_shard, max_shard_bytes)
    except CatalogError:
        raise
    except (backup.BackupError, OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise CatalogError("invalid baseline evidence") from error


def _shard_location(shard, descriptor):
    _hash(descriptor["sha256"])
    expected = f"shards/{shard}/{descriptor['sha256']}.json"
    _require(descriptor["location"] == expected, "invalid immutable shard location")
    return expected


def _validate_index(value):
    _require(type(value["schema_version"]) is int and value["schema_version"] == SCHEMA
             and value["prefixes"] == list(backup.PREFIXES)
             and isinstance(value["bucket"], str) and bool(value["bucket"]), "unsupported index")
    _utc(value["baseline"]["cutoff_utc"])
    _require(set(value["shards"]) == set(SHARDS), "missing index shard descriptors")
    _require(isinstance(value["batches"], dict) and 1 <= len(value["batches"]) <= MAX_BATCHES,
             "batch reference limit exceeded")
    _require(value["batches"]["baseline"] == {
        "kind": "baseline", "blob": BASELINE_BLOB, "sha256": BASELINE_SHA256}, "untrusted baseline reference")
    blobs = set()
    for name, batch in value["batches"].items():
        _require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name), "invalid batch identity")
        _blob(batch["blob"])
        _hash(batch["sha256"])
        _require(batch["blob"] not in blobs, "duplicate batch destination")
        blobs.add(batch["blob"])
        if name != "baseline":
            _require(batch["kind"] == "increment" and batch["blob"].startswith("incremental/"),
                     "invalid incremental batch")
            _blob(batch["receipts_member"])
    for shard, descriptor in value["shards"].items():
        _shard_location(shard, descriptor)
        _require(all(type(descriptor[f]) is int and descriptor[f] >= 0
                     for f in ("records", "baseline_objects", "baseline_bytes"))
                 and descriptor["baseline_objects"] <= descriptor["records"], "invalid shard totals")
    for field, descriptor_field in (("objects", "baseline_objects"), ("bytes", "baseline_bytes")):
        _require(type(value["baseline"][field]) is int and value["baseline"][field] >= 0
                 and sum(d[descriptor_field] for d in value["shards"].values()) == value["baseline"][field],
                 "incomplete baseline coverage")


def load_index(directory):
    """Load a root snapshot; referenced immutable shards verify independently."""
    try:
        value = backup._read(Path(directory).absolute(), "index.json", MAX_ROOT_BYTES)
        _validate_index(value)
        return value
    except CatalogError:
        raise
    except (backup.BackupError, OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise CatalogError("invalid coverage index") from error


def _validate_entries(value, index, shard, max_records):
    _require(type(value["schema_version"]) is int and value["schema_version"] == SCHEMA
             and value["bucket"] == index["bucket"] and value["shard"] == shard
             and isinstance(value["entries"], list) and len(value["entries"]) <= max_records,
             "invalid or over-limit shard")
    revisions, baseline_keys = set(), set()
    for entry in value["entries"]:
        _require(isinstance(entry, list) and len(entry) == 4, "invalid compact entry")
        kind, identifier, revision, batch = entry
        source_key(kind, identifier)
        _require(identifier[:2] == shard, "entry in wrong shard")
        _hash(revision)
        _require(isinstance(batch, str) and batch in index["batches"], "missing verified batch")
        identity = (kind, identifier, revision)
        _require(identity not in revisions, "duplicate or conflicting coverage revision")
        revisions.add(identity)
        if batch == "baseline":
            _require((kind, identifier) not in baseline_keys, "conflicting baseline coverage")
            baseline_keys.add((kind, identifier))
    return len(baseline_keys)


def read_shard(directory, shard, *, index=None, max_records=MAX_RECORDS, max_bytes=MAX_SHARD_BYTES):
    """Read independent values; reject missing, corrupt or incomplete coverage."""
    _limits(max_records=max_records, max_bytes=max_bytes)
    _require(shard in SHARDS, "invalid shard")
    try:
        index = load_index(directory) if index is None else index
        descriptor = index["shards"][shard]
        _require(descriptor["records"] <= max_records, "shard record limit exceeded")
        value = backup._read(Path(directory).absolute(), _shard_location(shard, descriptor), max_bytes)
        _require(backup._digest(value) == descriptor["sha256"], "shard descriptor checksum mismatch")
        count = _validate_entries(value, index, shard, max_records)
        _require(len(value["entries"]) == descriptor["records"]
                 and count == descriptor["baseline_objects"], "incomplete shard coverage")
        return value
    except CatalogError:
        raise
    except (backup.BackupError, OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise CatalogError("invalid coverage shard") from error


def _stage_shard(root, value, index, baseline_bytes, max_records, max_bytes):
    shard = value["shard"]
    _require(shard in SHARDS, "invalid shard")
    _require(type(baseline_bytes) is int and baseline_bytes >= 0, "invalid baseline byte total")
    count = _validate_entries(value, index, shard, max_records)
    digest = backup._digest(value)
    descriptor = {"location": f"shards/{shard}/{digest}.json", "sha256": digest,
                  "records": len(value["entries"]), "baseline_objects": count,
                  "baseline_bytes": baseline_bytes}
    target = backup._path(root, descriptor["location"])
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = f"staging/{uuid4().hex}.json"
    temporary = backup._path(root, temporary_name)
    try:
        _write_bounded(root, temporary_name, value, max_bytes)
        try:
            os.link(temporary, target)
        except FileExistsError:
            _require(backup._read(root, descriptor["location"], max_bytes) == value,
                     "immutable shard conflict")
    finally:
        temporary.unlink(missing_ok=True)
    return descriptor


def stage_shard_revision(directory, value, *, index, baseline_bytes,
                         max_records=MAX_RECORDS, max_bytes=MAX_SHARD_BYTES):
    """Create/reuse exact immutable shard bytes; never modify the committed root.

    Call under a single-writer publication protocol. Unreferenced revisions left
    by interruption are metadata for later bounded cleanup, not backup evidence.
    """
    _limits(max_records=max_records, max_bytes=max_bytes)
    with backup._lock(directory) as root:
        return _stage_shard(root, value, index, baseline_bytes, max_records, max_bytes)


def _publish_index(root, index, max_records, max_bytes, previous=None):
    _validate_index(index)
    if previous is not None:
        _require(all(index[field] == previous[field]
                     for field in ("schema_version", "bucket", "prefixes", "baseline")),
                 "committed baseline identity or totals changed")
        _require(all(index["batches"].get(name) == batch
                     for name, batch in previous["batches"].items()),
                 "committed batch descriptor changed or removed")
    for shard in SHARDS:
        if previous is None:
            read_shard(root, shard, index=index, max_records=max_records, max_bytes=max_bytes)
            continue
        old_entries = {tuple(entry) for entry in read_shard(
            root, shard, index=previous, max_records=max_records, max_bytes=max_bytes)["entries"]}
        new_entries = {tuple(entry) for entry in read_shard(
            root, shard, index=index, max_records=max_records, max_bytes=max_bytes)["entries"]}
        _require(old_entries <= new_entries, "committed coverage changed or removed")
        _require({entry for entry in new_entries if entry[3] == "baseline"}
                 == {entry for entry in old_entries if entry[3] == "baseline"},
                 "new coverage falsely attributed to baseline")
        _require(index["shards"][shard]["baseline_bytes"]
                 == previous["shards"][shard]["baseline_bytes"], "baseline shard byte total changed")
        del old_entries, new_entries
    _write_bounded(root, "index.json", index, MAX_ROOT_BYTES)
    return load_index(root)


def publish_index(directory, index, *, max_records=MAX_RECORDS, max_bytes=MAX_SHARD_BYTES):
    """Append to existing committed coverage, then atomically replace the root.

    Validate both snapshots one shard at a time. Baseline identities/totals and
    existing batch descriptors/entries cannot change; only incremental coverage
    may be appended. Initial baseline construction uses the separate bootstrap.
    Azure conditional publication is provided by the injected transfer layer.
    """
    _limits(max_records=max_records, max_bytes=max_bytes)
    with backup._lock(directory) as root:
        previous = load_index(root)
        return _publish_index(root, index, max_records, max_bytes, previous=previous)


def _reference(entry, index):
    kind, identifier, revision, batch_id = entry
    key = source_key(kind, identifier)
    batch = index["batches"][batch_id]
    return {"key": key, "revision": revision, "batch": batch_id, "blob": batch["blob"],
            "batch_sha256": batch["sha256"],
            "receipt_member": (f"objects/{backup._key_id(key)}.json" if batch_id == "baseline"
                               else batch["receipts_member"]),
            "receipt_entry": None if batch_id == "baseline" else f"{kind}/{identifier}/{revision}"}


def _plan_shard(value, observations, index):
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


def plan_reconciliation(index_directory, inventories, *, output_directory,
                        max_objects_per_shard=MAX_RECORDS, max_records_per_shard=MAX_RECORDS,
                        max_shard_bytes=MAX_SHARD_BYTES, max_pages=262_144,
                        max_spool_bytes=4 * 1024 ** 3, max_output_bytes=4 * 1024 ** 3, max_seconds=3600):
    """Scan full prefixes, then reconcile one UUID partition at a time.

    inventories maps canonical prefixes to saved-format page iterables. Every
    key is validated before routing; narrow shard listings hide unknown formats.
    Return a small summary; classifications live in bounded shard files. Failed
    sweeps never claim global completion. Restart with fresh scratch/output and
    verified coverage. Keep index/input files quiescent; live source scans remain
    provisional. No catalog structure is copied or mutated.
    """
    _limits(max_objects_per_shard=max_objects_per_shard, max_records_per_shard=max_records_per_shard,
            max_shard_bytes=max_shard_bytes, max_pages=max_pages,
            max_spool_bytes=max_spool_bytes, max_output_bytes=max_output_bytes)
    _require(type(max_seconds) in (int, float) and math.isfinite(max_seconds) and max_seconds > 0,
             "invalid runtime limit")
    started = time.monotonic()
    summary = {"schema_version": SCHEMA, "complete": False, "consistent_cutoff": False,
               "prefixes_complete": [], "pages": 0, "observed_objects": 0, "spool_bytes": 0,
               "output_bytes": 0, "shards": {}, "error": None}
    def runtime():
        _require(time.monotonic() - started < max_seconds, "sweep runtime limit exceeded")
    with backup._lock(output_directory) as output, tempfile.TemporaryDirectory() as temporary:
        _empty(output)
        spool = Path(temporary)
        try:
            index = load_index(index_directory)
            summary["index_sha256"] = backup._digest(index)
            summary["bucket"] = index["bucket"]
            _require(set(inventories) == set(backup.PREFIXES), "both complete prefixes required")
            counts = dict.fromkeys(SHARDS, 0)
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
                        last, shard = item["key"], identifier[:2]
                        counts[shard] += 1
                        _require(counts[shard] <= max_objects_per_shard, "source shard object limit exceeded")
                        observation = {"key": item["key"], "size": item["size"],
                                       "etag": item["etag"], "last_modified": _utc(item["last_modified"])}
                        projected = summary["spool_bytes"] + len(backup._bytes(observation)) + 1
                        _require(projected <= max_spool_bytes, "sweep spool limit exceeded")
                        _append(spool, shard, observation)
                        summary["spool_bytes"] = projected
                        summary["observed_objects"] += 1
                    summary["pages"] += 1
                    token, complete = next_token, next_token is None
                    if token is not None:
                        tokens.add(token)
                _require(complete, "interrupted prefix inventory")
                summary["prefixes_complete"].append(prefix)
            for shard in SHARDS:
                runtime()
                value = read_shard(index_directory, shard, index=index,
                                   max_records=max_records_per_shard, max_bytes=max_shard_bytes)
                observations = _spooled(spool, shard, max_objects_per_shard, max_shard_bytes)
                result = _plan_shard(value, observations, index)
                runtime()
                result.update(schema_version=SCHEMA, shard=shard, bucket=index["bucket"])
                size = _write_bounded(output, f"shards/{shard}.json", result,
                                      min(4 * max_shard_bytes, max_output_bytes - summary["output_bytes"]))
                summary["output_bytes"] += size
                summary["shards"][shard] = {"sha256": backup._digest(result),
                    "observed": len(observations), "records": len(value["entries"]),
                    "already_covered": result["already_covered"],
                    **{f: len(result[f]) for f in ("new", "changed", "previous_revision_not_visible")}}
                del value, observations, result
            runtime()
            summary["complete"] = True
        except Exception as error:
            summary["error"] = str(error) if isinstance(error, CatalogError) else type(error).__name__
        _write_bounded(output, "sweep.json", summary, MAX_ROOT_BYTES)
    return summary


def read_plan_shard(directory, shard, *, max_bytes=4 * MAX_SHARD_BYTES):
    """Expose classifications only after a complete checksummed whole sweep."""
    _limits(max_bytes=max_bytes)
    _require(shard in SHARDS, "invalid shard")
    try:
        root = Path(directory).absolute()
        summary = backup._read(root, "sweep.json", MAX_ROOT_BYTES)
        _require(summary["complete"] is True and set(summary["shards"]) == set(SHARDS)
                 and summary["prefixes_complete"] == list(backup.PREFIXES), "incomplete sweep")
        value = backup._read(root, f"shards/{shard}.json", max_bytes)
        _require(value["shard"] == shard and backup._digest(value) == summary["shards"][shard]["sha256"],
                 "invalid plan shard")
        return value
    except CatalogError:
        raise
    except (backup.BackupError, OSError, KeyError, TypeError, ValueError, AttributeError) as error:
        raise CatalogError("invalid plan output") from error
