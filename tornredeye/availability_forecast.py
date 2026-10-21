"""Offline hourly availability baselines from previously validated stock history."""

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import sys

from tornredeye.evidence_paths import is_normalized, counterpart, evidence_identity
from tornredeye.stock_history import _time, _utc


HORIZON = timedelta(hours=1)
MAX_SOURCE_AGE = 300
# history comparisons flag these retrospectively; reconstruct them as of retrieval
TEMPORAL_FLAGS = {"conflicting_quantities_at_same_source_time",
                  "conflicting_source_quantity", "source_time_regressed"}


def _target(country, item_id):
    if not isinstance(country, str) or not country.strip():
        raise ValueError("country must be a nonempty source country key")
    if type(item_id) is not int or item_id < 1:
        raise ValueError("item_id must be a positive integer")


def _rows(history, country, item_id, attempts):
    _target(country, item_id)
    if (not isinstance(history, dict) or history.get("country") != country
            or type(history.get("item_id")) is not int or history["item_id"] != item_id):
        raise ValueError("history country/item does not match selection")
    if history.get("warnings"):
        raise ValueError("resolve history warnings before forecasting")
    observations = history.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("history must contain observations")
    rows, keys = [], {}
    for row in observations:
        at = _time(row.get("retrieved_at", row.get("retrieved_at_utc")))
        key = row.get("evidence_key") or (row.get("raw_evidence") or {}).get("key")
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError("missing or duplicate history evidence key")
        issues = row.get("issues")
        if not isinstance(issues, list) or any(not isinstance(i, str) for i in issues):
            raise ValueError("history issues must be an explicit list of strings")
        issues = sorted(set(issues) - TEMPORAL_FLAGS)
        source, quantity = row.get("source_timestamp"), row.get("quantity")
        if type(source) is not int or source <= 0:
            issues.append("unknown_source_time")
        elif source > at.timestamp():
            issues.append("future_source_time")
        if type(quantity) is not int or quantity < 0:
            issues.append("invalid_quantity")
        keys[key] = at
        rows.append({"at": at, "key": key, "source": source,
                     "quantity": quantity, "issues": sorted(set(issues))})
    attempt_keys = set()
    extra_attempts = 0
    for row in attempts:
        key, at = row.get("key"), _time(row.get("retrieved_at"))
        if (not isinstance(key, str) or not is_normalized(key)
                or not key.endswith(".json") or key in attempt_keys):
            raise ValueError("invalid or duplicate normalized attempt key")
        partition, _, _ = evidence_identity(key)
        if at.replace(minute=0, second=0, microsecond=0) != partition:
            raise ValueError("attempt retrieval partition mismatch")
        attempt_keys.add(key)
        raw_key = counterpart(key)
        if raw_key in keys:
            if keys[raw_key] != at:
                raise ValueError("history/attempt retrieval mismatch")
            continue
        reason = ("collection_failed" if row.get("document_status") == "collection_failed"
                  else "unavailable_target_evidence")
        rows.append({"at": at, "key": key, "source": None,
                     "quantity": None, "issues": [reason]})
        extra_attempts += 1
    rows.sort(key=lambda row: row["at"])
    if len({row["at"] for row in rows}) != len(rows):
        raise ValueError("simultaneous retrievals have no established order")
    return rows, {"history_rows": len(observations), "attempt_rows": len(attempt_keys),
                  "additional_unknown_attempts": extra_attempts}


def _timeline(rows):
    seen, first_seen = defaultdict(set), {}
    current, maximum_source = None, 0
    points, contradictions, counts = [], [], Counter()
    for row in rows:
        source, quantity = row["source"], row["quantity"]
        carried = bool(row["issues"])
        if row["issues"]:
            counts["unavailable_attempts"] += 1
        else:
            values = seen[source]
            first_seen.setdefault(source, row["at"])
            if values and quantity not in values:
                contradictions.append({"source_timestamp": source,
                                       "discovered_at": _utc(row["at"]),
                                       "evidence_key": row["key"]})
            repeated = quantity in values
            values.add(quantity)
            if source < maximum_source:
                counts["regressing_source_attempts"] += 1
                carried = True
            else:
                maximum_source = source
                counts["cached_attempts" if repeated else "new_source_values"] += 1
                current = {"evidence_key": row["key"], "retrieved_at": _utc(row["at"]),
                           "source_timestamp": source, "quantity": quantity,
                           "first_seen_at": _utc(first_seen[source])}
        # copy each as-of state so later contradictions cannot change past selections
        points.append({"at": row["at"], "observation": dict(current) if current else None,
                       "conflicting": bool(current and len(seen[current["source_timestamp"]]) > 1),
                       "carried_over_unavailable_attempt": carried})
    return points, contradictions, dict(counts)


def _select(points, times, at):
    index = bisect_right(times, at) - 1
    if index < 0 or points[index]["observation"] is None:
        return {"status": "no_observation"}
    point = points[index]
    if point["conflicting"]:
        return {"status": "conflicting_source_time"}
    observation = dict(point["observation"])
    age = at.timestamp() - observation["source_timestamp"]
    observation.update({"source_age_seconds": age,
                        "carried_over_unavailable_attempt": point["carried_over_unavailable_attempt"]})
    return {"status": "eligible" if 0 <= age <= MAX_SOURCE_AGE else "stale",
            "observation": observation}


def _probabilities(initial, trained, positives):
    """The frozen unsmoothed empirical baseline and state comparator."""
    eligible = initial["status"] == "eligible"
    return {
        "training_count": trained, "training_positive_count": positives,
        "empirical_probability": positives / trained if eligible and trained else None,
        "persistence_probability": (float(initial["observation"]["quantity"] > 0)
                                    if eligible else None),
        "forecast_status": ("origin_ineligible" if not eligible else
                            "no_resolved_training_labels" if not trained else "issued"),
    }


def evaluate(history, *, country, item_id, attempts=()):
    """Compare expanding resolved hourly labels with persistence on common origins.

    Inputs are existing validated history outputs, not raw archives. Training uses
    earlier origin-eligible rows with labels resolved at targets <= the origin,
    including bootstrap rows that did not yet have an empirical forecast.
    """
    rows, inventory = _rows(history, country, item_id, attempts)
    points, contradictions, observation_counts = _timeline(rows)
    times = [point["at"] for point in points]
    origin = times[0].replace(minute=0, second=0, microsecond=0)
    if origin < times[0]:
        origin += HORIZON
    records = []
    while origin <= times[-1]:
        target = origin + HORIZON
        initial = _select(points, times, origin)
        outcome = ({"status": "beyond_evidence_cutoff"} if target > times[-1]
                   else _select(points, times, target))
        if outcome["status"] == "eligible":
            if outcome["observation"]["source_timestamp"] <= origin.timestamp():
                raise ValueError("target observation does not describe a post-origin source state")
            outcome["available"] = int(outcome["observation"]["quantity"] > 0)
        records.append({"origin_utc": _utc(origin), "target_utc": _utc(target),
                        "origin": initial, "label": outcome})
        origin = target

    trained, positives, next_maturity = 0, 0, 0
    latest_training_target = None
    for index, record in enumerate(records):
        while next_maturity < index and records[next_maturity]["target_utc"] <= record["origin_utc"]:
            past = records[next_maturity]
            if past["origin"]["status"] == past["label"]["status"] == "eligible":
                trained += 1
                positives += past["label"]["available"]
                latest_training_target = past["target_utc"]
            next_maturity += 1
        record.update(_probabilities(record["origin"], trained, positives))
        record["latest_training_target_utc"] = latest_training_target
        record["scored"] = record["forecast_status"] == "issued" and record["label"]["status"] == "eligible"
        record["brier"] = None
        if record["scored"]:
            y = record["label"]["available"]
            record["brier"] = {"empirical": (record["empirical_probability"] - y) ** 2,
                               "persistence": (record["persistence_probability"] - y) ** 2}

    # audit only: never filter labels, training or scores with later information
    for record in records:
        record["later_contradictions"] = {}
        for name, decision in (("origin", record["origin_utc"]), ("label", record["target_utc"])):
            selection = record[name]
            if selection["status"] == "eligible":
                matches = [c for c in contradictions
                           if c["source_timestamp"] == selection["observation"]["source_timestamp"]
                           and c["discovered_at"] > decision]
                if matches:
                    record["later_contradictions"][name] = matches

    def scores(selected):
        return {"count": len(selected), "brier": {
            method: math.fsum(r["brier"][method] for r in selected) / len(selected) if selected else None
            for method in ("empirical", "persistence")}}

    eligible = [r for r in records if r["origin"]["status"] == "eligible"]
    scored = [r for r in records if r["scored"]]
    summary = {
        "hourly_origins": len(records), "eligible_origins": len(eligible),
        "forecast_status": dict(Counter(r["forecast_status"] for r in records)),
        "origin_status": dict(Counter(r["origin"]["status"] for r in records)),
        "label_status_among_eligible_origins": dict(Counter(r["label"]["status"] for r in eligible)),
        "labels_resolved_among_eligible_origins": sum(r["label"]["status"] == "eligible" for r in eligible),
        "later_contradicted_label_count": sum("label" in r["later_contradictions"] for r in eligible),
        "scored_later_contradicted_label_count": sum("label" in r["later_contradictions"] for r in scored),
        "distinct_scored_source_times": len({r["label"]["observation"]["source_timestamp"] for r in scored}),
        "comparison": scores(scored),
        "by_utc_origin_date": {day: scores([r for r in scored if r["origin_utc"][:10] == day])
                               for day in sorted({r["origin_utc"][:10] for r in records})},
    }
    return {"schema_version": 1, "country": country, "item_id": item_id,
            "horizon_seconds": 3600, "maximum_source_age_seconds": MAX_SOURCE_AGE,
            "origin_schedule": "hourly UTC from ceiling(first retrieval) through last retrieval",
            "evidence_first_retrieved_utc": _utc(times[0]), "evidence_last_retrieved_utc": _utc(times[-1]),
            "input_inventory": inventory, "observation_counts": observation_counts,
            "contradiction_discoveries": contradictions, "summary": summary, "forecasts": records,
            "limitations": ["Previously explored, short, dependent single-source evidence; no independent validation.",
                            "Latest fresh source report is a proxy, not exact future in-game stock.",
                            "Unresolved outcomes are excluded from scores, not treated as zero.",
                            "Later contradictions are audit annotations; as-of labels remain in training and scores.",
                            "The empirical mean gives equal weight to resolved eligible hourly-origin labels."]}


def _read(path):
    data = Path(path).read_bytes()
    return data, {"path": str(Path(path).absolute()), "size_bytes": len(data),
                  "sha256": hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history", help="stock_history JSON report or existing country/item history JSONL")
    parser.add_argument("--attempts", help="optional normalized attempt index JSONL; unavailable target rows stay unknown")
    parser.add_argument("--country", required=True)
    parser.add_argument("--item-id", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        _target(args.country, args.item_id)
        data, identity = _read(args.history)
        inputs = [identity]
        if Path(args.history).suffix == ".jsonl":
            rows = [json.loads(line) for line in data.splitlines() if line.strip()]
            if any(not isinstance(r.get("country"), str) or type(r.get("item_id")) is not int for r in rows):
                raise ValueError("JSONL rows must identify country and item")
            history = {"country": args.country, "item_id": args.item_id,
                       "observations": [r for r in rows if r["country"] == args.country and r["item_id"] == args.item_id]}
        else:
            history = json.loads(data)
        attempts = []
        if args.attempts:
            data, identity = _read(args.attempts)
            inputs.append(identity)
            attempts = [json.loads(line) for line in data.splitlines() if line.strip()]
        report = evaluate(history, country=args.country, item_id=args.item_id, attempts=attempts)
        report["inputs"] = inputs
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
        print(f"Cannot evaluate local history: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
