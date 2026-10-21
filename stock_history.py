"""Read-only stock history from local backup receipt/payload pairs."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import evidence_backup as backup
from yata_normalizer import normalize_export


def _time(value):
    if not isinstance(value, str):
        raise ValueError("missing retrieval time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("naive retrieval time")
    return parsed.astimezone(timezone.utc)


def _utc(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalized_target_fields(normalized, retrieved, country, item_id):
    """Shared target validation for retained raw exports, without I/O."""
    row = {"issues": [], "quantity": None, "name": None, "cost": None}
    row["normalization_status"] = normalized["status"]
    row["diagnostics"] = normalized["diagnostics"]
    row["export_timestamp"] = normalized["export_timestamp"]
    country_record = normalized["countries"].get(country, {})
    row["source_timestamp"] = country_record.get("source_timestamp")
    item = next((item for item in country_record.get("items", []) if item["item_id"] == item_id), None)
    if item is None:
        row["issues"].append("target_missing_or_invalid")
    else:
        row.update({k: item[k] for k in ("quantity", "name", "cost")})
        if any(item[k] is None for k in ("quantity", "name", "cost")):
            row["issues"].append("invalid_target_fields")
    for field in ("source", "export"):
        stamp = row[field + "_timestamp"]
        if stamp is None or (field == "source" and stamp == 0):
            row["issues"].append(field + "_time_unknown")
        else:
            age = retrieved.timestamp() - stamp
            row[field + "_age_seconds"] = age
            if age < 0:
                row["issues"].append(field + "_time_in_future")
    return row


def _row(root, member, country, item_id):
    row = {"receipt": member, "retrieved_at": None, "raw_evidence": None,
           "collection_id": None, "http_status": None, "collection_status": None,
           "retrieval_metadata": None,
           "quantity": None, "name": None, "cost": None,
           "source_timestamp": None, "export_timestamp": None,
           "source_age_seconds": None, "export_age_seconds": None,
           "normalization_status": None, "diagnostics": [], "issues": []}
    try:
        receipt = backup._read(root, member)
        key = receipt["key"]
        if key.startswith(backup.PREFIXES[1]):
            return None
        if (not key.startswith(backup.PREFIXES[0])
                or member != f"objects/{backup._key_id(key)}.json"
                or not isinstance(receipt["bucket"], str) or not receipt["bucket"].strip()
                or receipt["inventory"]["key"] != key
                or receipt["status"] != "verified"
                or type(receipt["size"]) is not int or receipt["size"] < 0):
            raise ValueError("invalid raw receipt")
        verified = backup._verified(root, receipt["inventory"], receipt)
        if verified != "verified":
            raise ValueError("raw evidence integrity failure")
        row["raw_evidence"] = {k: receipt[k] for k in ("bucket", "key", "sha256")}
        row["raw_evidence"]["size_bytes"] = receipt["size"]
        metadata = receipt["metadata"]
        row["retrieval_metadata"] = metadata
        retrieved = _time(metadata.get("retrieved-at"))
        row["retrieved_at"] = _utc(retrieved)
        row["collection_id"] = metadata.get("collection-id")
        row["collection_status"] = metadata.get("status")
        status = metadata.get("http-status")
        if not isinstance(status, str) or not re.fullmatch(r"[1-5][0-9]{2}", status):
            row["issues"].append("missing_or_invalid_http_status")
            return row
        row["http_status"] = int(status)
        if metadata.get("source") != "YATA" or metadata.get("status") not in (
            "observed", "missing", "malformed",
        ):
            row["issues"].append("failed_or_unknown_collection_context")
            return row
        raw = backup._path(root, member.replace(".json", ".bin")).read_bytes()
        normalized = json.loads(normalize_export(
            raw, retrieved, raw_evidence_id=f"s3://{receipt['bucket']}/{key}",
            http_status=row["http_status"],
        ))
        if (normalized["raw_evidence"]["sha256"] != receipt["sha256"]
                or len(raw) != receipt["size"]):
            raise ValueError("raw evidence changed during read")
        row.update(normalized_target_fields(normalized, retrieved, country, item_id))
    except (backup.BackupError, OSError, ValueError, KeyError, TypeError, AttributeError,
            RecursionError, OverflowError):
        row["issues"].append("invalid_receipt_payload_or_retrieval_context")
        row["retrieved_at"] = None
        row["quantity"] = None
    return row


def analyze(directory, *, country: str, item_id: int):
    """Analyze only the supplied local subset; never modify or fetch evidence."""
    if not isinstance(country, str) or not country.strip():
        raise ValueError("country must be a nonempty YATA country key")
    if type(item_id) is not int or item_id < 1:
        raise ValueError("item_id must be a positive integer")
    root = Path(directory).absolute()
    objects = backup._path(root, "objects")
    if not objects.is_dir():
        raise ValueError("input must contain an objects directory")
    rows = []
    members = sorted(p.name for p in objects.iterdir() if p.suffix in (".json", ".bin"))
    receipts = {name for name in members if name.endswith(".json")}
    for name in sorted(receipts):
        row = _row(root, "objects/" + name, country, item_id)
        if row is not None:
            rows.append(row)
    for name in members:
        if name.endswith(".bin") and name[:-4] + ".json" not in receipts:
            rows.append({"receipt": "objects/" + name, "retrieved_at": None,
                         "quantity": None, "issues": ["payload_without_receipt"]})
    if not rows:
        raise ValueError("no local raw evidence receipt/payload pairs found")
    rows.sort(key=lambda row: (row["retrieved_at"] is None, row["retrieved_at"] or "", row["receipt"]))
    unplaced = any(row["retrieved_at"] is None for row in rows)
    times = Counter(row["retrieved_at"] for row in rows if row["retrieved_at"] is not None)
    source_values = defaultdict(set)
    for row in rows:
        if row.get("source_timestamp") and row["quantity"] is not None:
            source_values[row["source_timestamp"]].add(row["quantity"])
    previous = None
    seen = set()
    latest_source = 0
    for row in rows:
        row["comparison"] = None
        row["elapsed_retrieval_seconds"] = None
        if row["retrieved_at"] is not None and times[row["retrieved_at"]] > 1:
            row["issues"].append("simultaneous_retrievals_order_unknown")
        source = row.get("source_timestamp")
        if source and len(source_values[source]) > 1:
            row["issues"].append("conflicting_quantities_at_same_source_time")
        if source and row["quantity"] is not None and row.get("source_age_seconds", -1) >= 0:
            if source < latest_source:
                row["issues"].append("source_time_regressed")
            latest_source = max(latest_source, source)
        signature = (source, row["quantity"])
        row["repeated_source_observation"] = bool(source and signature in seen)
        if source and row["quantity"] is not None:
            seen.add(signature)
        if previous and row["retrieved_at"] is not None:
            row["elapsed_retrieval_seconds"] = (
                _time(row["retrieved_at"]) - _time(previous["retrieved_at"])
            ).total_seconds()
            if not row["issues"] and not previous["issues"] and not unplaced:
                if source > previous["source_timestamp"]:
                    delta = row["quantity"] - previous["quantity"]
                    row["comparison"] = {
                        "kind": "increase" if delta > 0 else "decrease" if delta < 0 else "unchanged",
                        "quantity_change": delta,
                        "zero_to_positive": previous["quantity"] == 0 and row["quantity"] > 0,
                        "from_receipt": previous["receipt"],
                        "retrieval_interval_utc": [previous["retrieved_at"], row["retrieved_at"]],
                        "source_update_interval": [previous["source_timestamp"], source],
                        "elapsed_source_seconds": source - previous["source_timestamp"],
                    }
        previous = row if row["retrieved_at"] is not None else None
    return {
        "country": country, "item_id": item_id,
        "scope": "Caller-selected local subset; coverage between observations is unknown.",
        "interpretation": "Reported snapshot comparisons, not exact restock or sellout times. Cached retrieval intervals do not bound actual stock events.",
        "warnings": ["Unplaced evidence prevents all transition comparisons."] if unplaced else [],
        "observations": rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="local subset containing objects/<key-hash>.json and .bin")
    parser.add_argument("--country", required=True, help="YATA country key, e.g. haw")
    parser.add_argument("--item-id", required=True, type=int, help="positive item ID, e.g. 1485")
    args = parser.parse_args(argv)
    if not args.country.strip() or args.item_id < 1:
        parser.error("country must be nonempty and item ID must be positive")
    try:
        report = analyze(args.directory, country=args.country, item_id=args.item_id)
    except (ValueError, OSError, backup.BackupError):
        print("Cannot read local receipt/payload subset; check directory and file access.", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
