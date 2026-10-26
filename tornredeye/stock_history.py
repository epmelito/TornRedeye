"""Read-only stock history from local backup receipt/payload pairs."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import evidence_backup as backup
from tornredeye.evidence_paths import is_raw, evidence_identity
from tornredeye.normalization import normalize_export, normalized_target_fields


def _time(value):
    if not isinstance(value, str):
        raise ValueError("missing retrieval time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("naive retrieval time")
    return parsed.astimezone(timezone.utc)


def _utc(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


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
        if not is_raw(key):
            return None
        if (member != f"objects/{backup._key_id(key)}.json"
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
        partition, identifier, _ = evidence_identity(key)
        if (retrieved.replace(minute=0, second=0, microsecond=0) != partition
                or metadata.get("collection-id", "").replace("-", "") != identifier.hex):
            raise ValueError("raw identity or retrieval partition mismatch")
        row["collection_id"] = metadata.get("collection-id")
        row["collection_status"] = metadata.get("acquisition-status")
        status = metadata.get("http-status")
        if not isinstance(status, str) or not re.fullmatch(r"[1-5][0-9]{2}", status):
            row["issues"].append("missing_or_invalid_http_status")
            return row
        row["http_status"] = int(status)
        if metadata.get("source") != "YATA" or metadata.get("acquisition-status") not in (
            "success",
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


def _stock_cycles(rows, *, unplaced):
    """Summarize existing comparisons without identifying latent game events."""
    candidates, intervals, runs = [], [], []
    counts = Counter(increase=0, decrease=0, unchanged=0)
    issues = Counter(issue for row in rows for issue in row["issues"])
    usable = [row for row in rows if not row["issues"]]
    ages = [row["source_age_seconds"] for row in usable]
    spacing = [row["elapsed_retrieval_seconds"] for row in rows
               if row["elapsed_retrieval_seconds"] is not None]
    last_appearance = None
    run = None
    left_boundary = "subset_start"
    for row in rows:
        if unplaced or row["issues"]:
            if run is not None:
                run["right_boundary"] = "quality_barrier"
                runs.append(run)
                run = None
            last_appearance = None
            left_boundary = "quality_barrier"
            continue
        comparison = row["comparison"]
        candidate = None
        if comparison is not None:
            counts[comparison["kind"]] += 1
            previous_quantity = row["quantity"] - comparison["quantity_change"]
            if comparison["zero_to_positive"] or previous_quantity > 0 and row["quantity"] == 0:
                candidate = {
                    "kind": "zero_to_positive" if comparison["zero_to_positive"] else "positive_to_zero",
                    "from_receipt": comparison["from_receipt"], "to_receipt": row["receipt"],
                    "quantity_change": comparison["quantity_change"],
                    "source_update_interval": comparison["source_update_interval"],
                    "retrieval_interval_utc": comparison["retrieval_interval_utc"],
                }
                candidates.append(candidate)
                if candidate["kind"] == "zero_to_positive":
                    if last_appearance is not None:
                        a, b = last_appearance["source_update_interval"]
                        c, d = candidate["source_update_interval"]
                        intervals.append({
                            "from_candidate_receipt": last_appearance["to_receipt"],
                            "to_candidate_receipt": candidate["to_receipt"],
                            "source_change_separation_bounds_seconds": [max(0, c - b), d - a],
                            "appearance_report_separation_seconds": d - b,
                        })
                    last_appearance = candidate
        if row["quantity"] > 0:
            if run is None:
                run = {
                    "first_receipt": row["receipt"], "last_receipt": row["receipt"],
                    "source_update_interval": [row["source_timestamp"], row["source_timestamp"]],
                    "retrieval_interval_utc": [row["retrieved_at"], row["retrieved_at"]],
                    "source_span_seconds": 0, "distinct_source_reports": 1,
                    "cached_receipts": 0, "first_quantity": row["quantity"],
                    "last_quantity": row["quantity"], "net_increase_units": 0,
                    "net_decrease_units": 0, "unchanged_comparisons": 0,
                    "opening_candidate_receipt": row["receipt"] if candidate else None,
                    "closing_candidate_receipt": None,
                    "left_boundary": "zero_to_positive_candidate" if candidate else left_boundary,
                    "right_boundary": "subset_end",
                }
            else:
                run["distinct_source_reports"] += int(comparison is not None)
                if comparison is not None:
                    delta = comparison["quantity_change"]
                    run["net_increase_units"] += max(delta, 0)
                    run["net_decrease_units"] += max(-delta, 0)
                    run["unchanged_comparisons"] += int(delta == 0)
            run["cached_receipts"] += int(row["repeated_source_observation"])
            run["last_receipt"] = row["receipt"]
            run["last_quantity"] = row["quantity"]
            run["source_update_interval"][1] = row["source_timestamp"]
            run["retrieval_interval_utc"][1] = row["retrieved_at"]
            run["source_span_seconds"] = row["source_timestamp"] - run["source_update_interval"][0]
        elif run is not None:
            run["closing_candidate_receipt"] = row["receipt"]
            run["right_boundary"] = "positive_to_zero_candidate"
            runs.append(run)
            run = None
        left_boundary = "quality_barrier"
    if run is not None:
        runs.append(run)
    return {
        "status": "snapshot_comparisons_only" if sum(counts.values()) else "insufficient_evidence_for_transitions",
        "interpretation": "Source-report candidates, not confirmed restocks, sellouts or continuous stock lifetimes. Coverage between snapshots is unknown; sellbacks and missed events remain possible.",
        "observations": len(rows), "usable_observations": len(usable),
        "cached_receipts": sum(bool(row.get("repeated_source_observation")) for row in rows),
        "issue_counts": dict(sorted(issues.items())),
        "source_age_seconds": {"min": min(ages) if ages else None, "max": max(ages) if ages else None},
        "max_retrieval_spacing_seconds": max(spacing) if spacing else None,
        "comparison_counts": dict(counts),
        "candidate_counts": {kind: sum(candidate["kind"] == kind for candidate in candidates)
                             for kind in ("zero_to_positive", "positive_to_zero")},
        "candidates": candidates, "appearance_intervals": intervals,
        "positive_report_runs": runs,
    }


def analyze(directory, *, country: str, item_id: int):
    """Analyze only the supplied local subset; never modify or fetch evidence."""
    if not isinstance(country, str) or not country.strip():
        raise ValueError("country must be a nonempty country key")
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
        "stock_cycles": _stock_cycles(rows, unplaced=unplaced),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", help="local subset containing objects/<key-hash>.json and .bin")
    parser.add_argument("--country", required=True, help="source country key")
    parser.add_argument("--item-id", required=True, type=int, help="positive item ID")
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
