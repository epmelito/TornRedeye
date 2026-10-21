"""Offline prospective accounting; no durable issuance, provider calls or replay CLI."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math

from tornredeye.availability_forecast import (
    HORIZON, MAX_SOURCE_AGE, _probabilities, _rows, _select, _target, _timeline,
)
from tornredeye.stock_history import _time, _utc
from tornredeye.evidence_paths import is_normalized, counterpart


METHODS = {"empirical": "expanding-resolved-availability-v1",
           "persistence": "last-observed-availability-v1"}


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def forecast_identity(country, item_id, now):
    return {"country": country, "item_id": item_id,
            "utc_hour_slot": _utc(now.replace(minute=0, second=0, microsecond=0)),
            "methods": METHODS, "horizon_seconds": int(HORIZON.total_seconds())}


def outcome_label(selection, issued_at):
    selection = json.loads(_bytes(selection))
    if selection["status"] == "eligible":
        if selection["observation"]["source_timestamp"] <= _time(issued_at).timestamp():
            raise ValueError("target source state must be after issuance")
        selection["available"] = int(selection["observation"]["quantity"] > 0)
    return selection


def paired_brier(forecast, label):
    if forecast["forecast_status"] != "issued" or label["status"] != "eligible":
        return None
    return {method: (forecast[f"{method}_probability"] - label["available"]) ** 2
            for method in METHODS}


class OfflineShadowRun:
    """Exercise issuance/outcome ordering in memory, never attest live publication.

    The clock is the actual processing clock; inject one only for offline tests.
    All input snapshots and returned records are isolated from caller mutation.
    Restart safety, durable create-only writes and write-time attestations require
    a separately approved live adapter. No records can be imported as issued.
    """

    def __init__(self, *, country="haw", item_id=1485, clock=None):
        _target(country, item_id)
        self._country, self._item_id = country, item_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._last_time = None
        self._snapshots = []
        self._forecasts = {}
        self._outcomes = {}

    def _now(self):
        value = self._clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("processing clock must return an aware datetime")
        value = value.astimezone(timezone.utc)
        if self._last_time is not None and value < self._last_time:
            raise ValueError("processing clock regressed; backdated operations rejected")
        self._last_time = value
        return value

    def observe(self, history, *, attempts=()):
        """Capture a cumulative, previously validated history available now.

        Retrieval time alone does not attest availability to this consumer.
        Future retrievals and changed/removed previously captured evidence fail.
        Exact duplicate snapshots are harmless; nothing renews source freshness.
        """
        now = self._now()
        attempts = list(attempts)
        rows, inventory = _rows(history, self._country, self._item_id, attempts)
        if any(row["at"] > now for row in rows):
            raise ValueError("snapshot contains evidence retrieved after capture")
        encoded = [{**row, "at": _utc(row["at"])} for row in rows]
        by_key = {r["key"]: r for r in encoded}
        if self._snapshots:
            previous = self._snapshots[-1]["value"]
            for row in previous["rows"]:
                if by_key.get(row["key"]) != row:
                    # newly available raw evidence can supersede an unknown
                    # attempt in a new snapshot, never in an old decision
                    raw_key = counterpart(row["key"]) if is_normalized(row["key"]) else None
                    replacement = by_key.get(raw_key)
                    if not (is_normalized(row["key"])
                            and row["source"] is None and row["quantity"] is None
                            and row["key"] not in by_key and replacement
                            and replacement["at"] == row["at"]):
                        raise ValueError("captured evidence was removed or changed")
            if encoded == previous["rows"]:
                return previous["snapshot_id"]
        value = {
            "captured_at_utc": _utc(now), "rows": encoded,
            "input_sha256": _hash({"history": history, "attempts": attempts}),
            "inventory": inventory,
        }
        value["snapshot_id"] = _hash(value)
        # only canonical copies, not caller-owned row dictionaries, enter the run
        value = json.loads(_bytes(value))
        points, _, _ = _timeline(rows)
        self._snapshots.append({"value": value, "points": points,
                                "times": [p["at"] for p in points]})
        return value["snapshot_id"]

    def _selection(self, at):
        snapshot = next((s for s in reversed(self._snapshots)
                         if _time(s["value"]["captured_at_utc"]) <= at), None)
        if snapshot is None:
            return {"status": "no_observation"}, None
        return (_select(snapshot["points"], snapshot["times"], at),
                snapshot["value"]["snapshot_id"])

    def issue(self):
        """Record one offline decision per UTC-hour slot using the processing clock.

        The target is exactly 60 minutes after this decision, not a backdated
        nominal scheduler origin. This protocol is separate from Issue #49.
        """
        now = self._now()
        identity = forecast_identity(self._country, self._item_id, now)
        identifier = _hash(identity)
        if identifier in self._forecasts:
            raise ValueError("UTC-hour slot already recorded; retrieve, do not reissue")
        initial, snapshot_id = self._selection(now)
        training = []
        for key, encoded in self._outcomes.items():
            outcome = json.loads(encoded)
            forecast = json.loads(self._forecasts[key])
            if (forecast["origin"]["status"] == "eligible"
                    and outcome["label"]["status"] == "eligible"):
                training.append({"forecast_id": key, "outcome_sha256": _hash(outcome),
                                 "target_utc": outcome["target_utc"],
                                 "resolved_at_utc": outcome["resolved_at_utc"],
                                 "available": outcome["label"]["available"]})
        assert all(_time(t["target_utc"]) <= now
                   and _time(t["resolved_at_utc"]) <= now for t in training)
        probabilities = _probabilities(initial, len(training),
                                       sum(t["available"] for t in training))
        value = {
            "schema_version": 1, "record_kind": "offline_shadow_forecast",
            "forecast_id": identifier, **identity,
            "issued_at_utc": _utc(now), "target_utc": _utc(now + HORIZON),
            "maximum_source_age_seconds": MAX_SOURCE_AGE,
            "origin": initial, "snapshot_id": snapshot_id,
            "training": training, **probabilities,
            "live_issuance_verified": False,
        }
        self._forecasts[identifier] = _bytes(value)
        return json.loads(self._forecasts[identifier])

    def resolve(self):
        """Finalize matured outcomes once using only snapshots captured by targets.

        Late-arriving evidence cannot retrospectively resolve missing outcomes.
        Unmatured decisions stay pending. Later contradictions are report audits.
        """
        now = self._now()
        created = []
        forecasts = sorted((json.loads(v) for v in self._forecasts.values()),
                           key=lambda f: f["target_utc"])
        for forecast in forecasts:
            key = forecast["forecast_id"]
            target = _time(forecast["target_utc"])
            if key in self._outcomes or target > now:
                continue
            label, snapshot_id = self._selection(target)
            label = outcome_label(label, forecast["issued_at_utc"])
            value = {
                "schema_version": 1, "record_kind": "offline_shadow_outcome",
                "forecast_id": key, "forecast_sha256": _hash(forecast),
                "target_utc": forecast["target_utc"],
                "resolved_at_utc": _utc(now), "snapshot_id": snapshot_id,
                "label": label,
            }
            self._outcomes[key] = _bytes(value)
            created.append(json.loads(self._outcomes[key]))
        return created

    def _later_contradictions(self, selection, snapshot_id):
        if selection["status"] != "eligible":
            return []
        source = selection["observation"]["source_timestamp"]
        original = selection["observation"]["quantity"]
        selected_index = next(i for i, s in enumerate(self._snapshots)
                              if s["value"]["snapshot_id"] == snapshot_id)
        # snapshot order also distinguishes operations with equal clock readings
        for snapshot in self._snapshots[selected_index+1:]:
            value = snapshot["value"]
            matches = [r["key"] for r in value["rows"]
                       if not r["issues"] and r["source"] == source
                       and r["quantity"] != original]
            if matches:
                return [{"source_timestamp": source,
                         "known_at_utc": value["captured_at_utc"],
                         "snapshot_id": value["snapshot_id"],
                         "evidence_keys": matches}]
        return []

    def report(self):
        """Export isolated offline records, audit annotations and paired coverage."""
        now = self._now()
        records, scored = [], []
        for encoded in self._forecasts.values():
            forecast = json.loads(encoded)
            saved = self._outcomes.get(forecast["forecast_id"])
            outcome = json.loads(saved) if saved is not None else None
            assert outcome is None or outcome["forecast_sha256"] == _hash(forecast)
            brier = paired_brier(forecast, outcome["label"]) if outcome else None
            if brier is not None:
                scored.append(brier)
            audit = {"origin": self._later_contradictions(forecast["origin"], forecast["snapshot_id"])}
            if outcome is not None:
                audit["label"] = self._later_contradictions(outcome["label"], outcome["snapshot_id"])
            records.append({"forecast": forecast, "outcome": outcome,
                            "brier": brier, "later_contradictions": audit})
        eligible = [r for r in records if r["forecast"]["origin"]["status"] == "eligible"]
        slots = sorted(r["forecast"]["utc_hour_slot"] for r in records)
        span = int((_time(slots[-1]) - _time(slots[0])) / HORIZON) + 1 if slots else 0
        return {
            "execution": "offline_accounting_only", "live_issuance_verified": False,
            "reported_at_utc": _utc(now), "records": records,
            "snapshots": [json.loads(_bytes(s["value"])) for s in self._snapshots],
            "summary": {
                "recorded_slots": len(records), "eligible_origins": len(eligible),
                "hour_slots_in_recorded_span": span,
                "unrecorded_hour_slots_in_span": span - len(records),
                "forecast_status": dict(Counter(r["forecast"]["forecast_status"] for r in records)),
                "origin_status": dict(Counter(r["forecast"]["origin"]["status"] for r in records)),
                "outcome_status": dict(Counter(r["outcome"]["label"]["status"] if r["outcome"] else "pending" for r in records)),
                "eligible_origin_outcome_status": dict(Counter(r["outcome"]["label"]["status"] if r["outcome"] else "pending" for r in eligible)),
                "matured_unaccounted": sum(r["outcome"] is None and _time(r["forecast"]["target_utc"]) <= now for r in records),
                "paired_score_count": len(scored),
                "brier": {method: math.fsum(r[method] for r in scored) / len(scored)
                          if scored else None for method in METHODS},
                "later_contradicted_labels": sum(bool(r["later_contradictions"].get("label")) for r in records),
            },
        }
