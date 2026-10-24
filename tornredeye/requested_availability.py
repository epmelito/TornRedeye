"""Offline requested-time availability replay; no live issuance or cloud access."""

import argparse
from collections import Counter
import json
import math
import sys

from tornredeye.availability_forecast import _probabilities, _read, _rows, _select, _timeline
from tornredeye.evidence_paths import counterpart
from tornredeye.stock_history import _time, _utc


def _summary(records):
    paired = [r for r in records if r["scored"]]
    resolved = [r for r in records if r["label"]["status"] == "eligible"]
    return {
        "requested_pairs": len(records), "paired_scored": len(paired),
        "excluded_from_paired": len(records) - len(paired),
        "origin_status": dict(Counter(r["origin"]["status"] for r in records)),
        "label_status": dict(Counter(r["label"]["status"] for r in records)),
        "forecast_status": dict(Counter(r["forecast_status"] for r in records)),
        "cold_start_abstentions": sum(r["forecast_status"] == "no_resolved_training_labels" for r in records),
        "resolved_positive": sum(r["label"]["available"] for r in resolved),
        "resolved_zero": sum(not r["label"]["available"] for r in resolved),
        "paired_positive": sum(r["label"]["available"] for r in paired),
        "paired_zero": sum(not r["label"]["available"] for r in paired),
        "distinct_paired_source_times": len({r["label"]["observation"]["source_timestamp"] for r in paired}),
        "brier": {method: math.fsum(r["brier"][method] for r in paired) / len(paired) if paired else None
                  for method in ("empirical", "persistence")},
    }


def evaluate(history, requests, *, country, item_id, maximum_source_age_seconds,
             attempts=(), available_at=None):
    """Replay explicit origin/target pairs using earlier resolved source states.

    Without a complete key-to-availability-time mapping, retrieval times are only
    a replay proxy. Supplied times are caller assertions, not live attestations.
    The empirical training unit is one eligible resolved target source timestamp,
    deduplicated across requests; this does not establish statistical independence.
    """
    if (type(maximum_source_age_seconds) not in (int, float)
            or not math.isfinite(maximum_source_age_seconds) or maximum_source_age_seconds < 0):
        raise ValueError("maximum source age must be finite and nonnegative")
    attempts = list(attempts)
    rows, inventory = _rows(history, country, item_id, attempts)
    retrieved = {row["key"]: row["at"] for row in rows}
    matched_attempts = set()
    # retain earlier unknown attempt events when raw evidence arrives downstream later
    if available_at is not None:
        for attempt in attempts:
            if counterpart(attempt["key"]) in retrieved:
                at = _time(attempt["retrieved_at"])
                rows.append({"at": at, "key": attempt["key"], "source": None, "quantity": None,
                             "issues": ["unavailable_target_evidence"]})
                retrieved[attempt["key"]] = at
                matched_attempts.add(attempt["key"])
    if available_at is not None and (not isinstance(available_at, dict) or set(available_at) != set(retrieved)):
        raise ValueError("availability mapping must cover exactly the retained input keys")
    availability = {key: _time(available_at[key]) if available_at is not None else at
                    for key, at in retrieved.items()}
    if any(availability[key] < at for key, at in retrieved.items()):
        raise ValueError("availability cannot precede retrieval")
    timed = [{**row, "at": availability[row["key"]]} for row in rows
             if row["key"] not in matched_attempts
             or availability[row["key"]] < availability[counterpart(row["key"])]]
    timed.sort(key=lambda row: (row["at"], retrieved[row["key"]], not bool(row["issues"])))
    points, contradictions, observation_counts = _timeline(timed)
    for point in points:
        observation = point["observation"]
        if observation is not None:
            key = observation["evidence_key"]
            observation["retrieved_at"] = _utc(retrieved[key])
            observation["available_at_utc"] = _utc(availability[key])
            observation["first_available_at_utc"] = observation.pop("first_seen_at")
    times = [point["at"] for point in points]
    cutoff = max(availability.values())

    def select(at):
        if at > cutoff:
            return {"status": "beyond_evidence_cutoff"}
        selection = _select(points, times, at)
        if "observation" in selection:
            observation = selection["observation"]
            age = observation["source_age_seconds"]
            selection["status"] = "eligible" if 0 <= age <= maximum_source_age_seconds else "stale"
        return selection

    if not isinstance(requests, list) or not requests:
        raise ValueError("requests must be a nonempty list of explicit origin/target pairs")
    pairs = []
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("invalid requested pair")
        origin, target = _time(request.get("origin_utc")), _time(request.get("target_utc"))
        if target <= origin:
            raise ValueError("requested target must follow origin")
        pairs.append((origin, target))
    if len(set(pairs)) != len(pairs):
        raise ValueError("duplicate origin/target pair")
    records = []
    for origin, target in sorted(pairs):
        label = select(target)
        if label["status"] == "eligible":
            if label["observation"]["source_timestamp"] <= origin.timestamp():
                label["status"] = "no_post_origin_source_update"
            else:
                label["available"] = int(label["observation"]["quantity"] > 0)
        records.append({"origin_utc": _utc(origin), "target_utc": _utc(target),
                        "offset_seconds": (target - origin).total_seconds(),
                        "origin": select(origin), "label": label})

    matured = sorted((r for r in records if r["origin"]["status"] == r["label"]["status"] == "eligible"),
                     key=lambda r: (r["target_utc"], r["origin_utc"]))
    seen_sources = set()
    training = []
    next_label = positives = 0
    for record in records:
        while next_label < len(matured) and matured[next_label]["target_utc"] < record["origin_utc"]:
            past = matured[next_label]
            source = past["label"]["observation"]["source_timestamp"]
            if source not in seen_sources:
                seen_sources.add(source)
                positives += past["label"]["available"]
                training.append({"origin_utc": past["origin_utc"], "target_utc": past["target_utc"],
                                 "source_timestamp": source,
                                 "evidence_key": past["label"]["observation"]["evidence_key"],
                                 "available": past["label"]["available"]})
            next_label += 1
        record.update(_probabilities(record["origin"], len(training), positives))
        if record["forecast_status"] == "issued":
            record["forecast_status"] = "replay_estimate"
        record["latest_training_target_utc"] = training[-1]["target_utc"] if training else None
        record["scored"] = record["forecast_status"] == "replay_estimate" and record["label"]["status"] == "eligible"
        record["brier"] = None
        if record["scored"]:
            record["brier"] = {method: (record[method + "_probability"] - record["label"]["available"]) ** 2
                               for method in ("empirical", "persistence")}
        record["later_contradictions"] = {
            name: [c for c in contradictions if selection["status"] == "eligible"
                   and c["source_timestamp"] == selection["observation"]["source_timestamp"]
                   and c["discovered_at"] > record[decision]]
            for name, decision, selection in (("origin", "origin_utc", record["origin"]),
                                               ("label", "target_utc", record["label"]))}
    return {
        "schema_version": 1, "country": country, "item_id": item_id,
        "evaluation_kind": ("retrospective_retrieval_time_proxy" if available_at is None
                            else "retrospective_supplied_availability_times"),
        "live_issuance_verified": False, "maximum_source_age_seconds": maximum_source_age_seconds,
        "evidence_first_retrieved_utc": _utc(min(retrieved.values())),
        "evidence_last_retrieved_utc": _utc(max(retrieved.values())),
        "evidence_first_available_utc": _utc(times[0]), "evidence_cutoff_utc": _utc(cutoff),
        "input_inventory": inventory, "observation_counts": observation_counts,
        "training_unit": "one earlier origin-eligible resolved requested outcome per source timestamp",
        "training_maturity": "target_utc strictly before origin_utc; actual accounting latency is unverified",
        "training_labels": training, "contradiction_discoveries": contradictions,
        "summary": _summary(records),
        "by_offset_seconds": {str(offset): _summary([r for r in records if r["offset_seconds"] == offset])
                              for offset in sorted({r["offset_seconds"] for r in records})},
        "forecasts": records,
        "limitations": ["Retrospective replay is not proof of live issuance or independent validation.",
                        "Input history and availability claims must be independently verified; no cloud access is performed.",
                        "Positive source-reported stock does not establish in-game availability or purchase success.",
                        "Requested outcomes and source states remain dependent; scores have no IID uncertainty interpretation.",
                        "Training labels are request-sampled, not an elapsed-time prevalence estimate.",
                        "Missing coverage remains unknown; bounded carry is flagged and never renews source age.",
                        "Strict maturity excludes equal-time targets but does not prove actual outcome accounting completed.",
                        "Later contradictions are audit annotations and never rewrite earlier training or scores."],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", help="previously verified stock-history JSON report")
    parser.add_argument("requests", help="JSON list of explicit origin_utc/target_utc pairs")
    parser.add_argument("--country", required=True)
    parser.add_argument("--item-id", required=True, type=int)
    parser.add_argument("--maximum-source-age-seconds", required=True, type=float)
    parser.add_argument("--attempts", help="existing normalized-attempt JSONL index")
    parser.add_argument("--available-at", help="JSON mapping of input keys to downstream availability UTC times")
    args = parser.parse_args(argv)
    stage = "history"
    try:
        inputs = {}

        def read(name, path):
            nonlocal stage
            stage = name
            raw, inputs[name] = _read(path)
            return raw

        history = json.loads(read("history", args.history))
        requests = json.loads(read("requests", args.requests))
        attempts = []
        if args.attempts:
            for number, line in enumerate(read("attempts", args.attempts).splitlines(), 1):
                if line.strip():
                    stage = f"attempts line {number}"
                    attempts.append(json.loads(line))
        available = json.loads(read("available_at", args.available_at)) if args.available_at else None
        stage = "evaluation"
        report = evaluate(history, requests, country=args.country, item_id=args.item_id,
                          maximum_source_age_seconds=args.maximum_source_age_seconds,
                          attempts=attempts, available_at=available)
        report["inputs"] = inputs
        output = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
    except (ValueError, OSError, TypeError, KeyError, AttributeError, OverflowError) as error:
        safe_reasons = {
            "maximum source age must be finite and nonnegative",
            "availability mapping must cover exactly the retained input keys",
            "availability cannot precede retrieval",
            "requests must be a nonempty list of explicit origin/target pairs",
            "invalid requested pair", "requested target must follow origin",
            "duplicate origin/target pair", "history country/item does not match selection",
            "resolve history warnings before forecasting", "history must contain observations",
            "missing or duplicate history evidence key", "history/attempt retrieval mismatch",
            "simultaneous retrievals have no established order", "naive retrieval time",
            "missing retrieval time", "invalid or duplicate normalized attempt key",
            "attempt retrieval partition mismatch", "history issues must be an explicit list of strings",
            "country must be a nonempty source country key", "item_id must be a positive integer",
        }
        if isinstance(error, json.JSONDecodeError):
            reason = f"invalid JSON at line {error.lineno}, column {error.colno}"
        elif isinstance(error, UnicodeDecodeError):
            reason = "invalid text encoding"
        elif isinstance(error, FileNotFoundError):
            reason = "input file not found"
        elif isinstance(error, PermissionError):
            reason = "input file access denied"
        elif isinstance(error, OSError):
            reason = "cannot read input file"
        else:
            reason = str(error) if type(error) is ValueError and str(error) in safe_reasons else "invalid input structure or timestamp"
        print(f"Cannot evaluate local inputs ({stage}): {reason}.", file=sys.stderr)
        return 2
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
