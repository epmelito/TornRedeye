"""Private requested-time availability accounting; in memory, never live attestation."""

from datetime import datetime, timezone
import json
import math

from tornredeye.availability_forecast import _probabilities
from tornredeye.requested_availability import _summary
from tornredeye.shadow_forecast import OfflineShadowRun, _bytes, _hash, outcome_label, paired_brier
from tornredeye.stock_history import _time, _utc


METHODS = {"empirical": "expanding-distinct-resolved-source-availability-v1",
           "persistence": "last-observed-availability-v1"}


class RequestedForecastRun(OfflineShadowRun):
    """Capture, issue and account for one selected country/item without hourly slots.

    Reuse the offline core's clock, cumulative evidence capture and audit behavior.
    Request IDs deduplicate only within this in-memory run. No durable identity,
    import/restart protocol, provider calls or persistence adapter is supplied.
    """

    def __init__(self, *, country, item_id, maximum_source_age_seconds, clock=None):
        if (type(maximum_source_age_seconds) not in (int, float)
                or not math.isfinite(maximum_source_age_seconds) or maximum_source_age_seconds < 0):
            raise ValueError("maximum source age must be finite and nonnegative")
        super().__init__(country=country, item_id=item_id, clock=clock)
        self._maximum_age = maximum_source_age_seconds
        self._requests = {}
        self._sequence = 0

    def _selection(self, at):
        selection, snapshot_id = super()._selection(at)
        if snapshot_id is not None:
            snapshot = next(s["value"] for s in self._snapshots if s["value"]["snapshot_id"] == snapshot_id)
            selection["captured_at_utc"] = snapshot["captured_at_utc"]
        if "observation" in selection:
            selection["observation"]["first_retrieved_at_utc"] = selection["observation"].pop("first_seen_at")
            age = selection["observation"]["source_age_seconds"]
            selection["status"] = "eligible" if 0 <= age <= self._maximum_age else "stale"
        return selection, snapshot_id

    def issue(self, target_utc, *, request_id):
        """Issue at the processing clock; targets and IDs cannot backdate a decision."""
        now = self._now()
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request ID must be a nonempty string")
        if request_id in self._requests:
            raise ValueError("request ID already recorded; retrieve the existing forecast")
        target = _time(target_utc) if isinstance(target_utc, str) else target_utc
        if not isinstance(target, datetime) or target.utcoffset() is None:
            raise ValueError("target must be a timezone-aware datetime or ISO timestamp")
        target = target.astimezone(timezone.utc)
        if target <= now:
            raise ValueError("requested target must be strictly after actual issuance")
        initial, snapshot_id = self._selection(now)
        sequence = self._sequence + 1
        training, seen_sources = [], set()
        for identifier, encoded in self._outcomes.items():
            outcome = json.loads(encoded)
            forecast = json.loads(self._forecasts[identifier])
            if forecast["origin"]["status"] != "eligible" or outcome["label"]["status"] != "eligible":
                continue
            source = outcome["label"]["observation"]["source_timestamp"]
            if source in seen_sources:
                continue
            assert (_time(outcome["target_utc"]) <= now and _time(outcome["resolved_at_utc"]) <= now
                    and outcome["operation_sequence"] < sequence)
            seen_sources.add(source)
            training.append({"forecast_id": identifier, "outcome_sha256": _hash(outcome),
                             "target_utc": outcome["target_utc"], "resolved_at_utc": outcome["resolved_at_utc"],
                             "operation_sequence": outcome["operation_sequence"], "source_timestamp": source,
                             "available": outcome["label"]["available"]})
        probabilities = _probabilities(initial, len(training), sum(t["available"] for t in training))
        identity = {"record_kind": "offline_requested_availability_forecast", "schema_version": 1,
                    "country": self._country, "item_id": self._item_id, "request_id": request_id,
                    "issued_at_utc": _utc(now), "target_utc": _utc(target), "methods": dict(METHODS),
                    "maximum_source_age_seconds": self._maximum_age}
        identifier = _hash(identity)
        value = {**identity, "forecast_id": identifier, "operation_sequence": sequence,
                 "horizon_seconds": (target - now).total_seconds(),
                 "origin": initial, "snapshot_id": snapshot_id, "training": training,
                 **probabilities, "execution": "offline_accounting_only", "live_issuance_verified": False}
        encoded = _bytes(value)
        self._forecasts[identifier] = encoded
        self._requests[request_id] = identifier
        self._sequence = sequence
        return json.loads(encoded)

    def resolve(self):
        """Account once in target order using evidence captured by each target."""
        now = self._now()
        created = []
        forecasts = sorted((json.loads(value) for value in self._forecasts.values()),
                           key=lambda f: (f["target_utc"], f["operation_sequence"]))
        for forecast in forecasts:
            identifier = forecast["forecast_id"]
            target = _time(forecast["target_utc"])
            if identifier in self._outcomes or target > now:
                continue
            label, snapshot_id = self._selection(target)
            if (label["status"] == "eligible"
                    and label["observation"]["source_timestamp"] <= _time(forecast["issued_at_utc"]).timestamp()):
                label["status"] = "no_post_origin_source_update"
            label = outcome_label(label, forecast["issued_at_utc"])
            sequence = self._sequence + 1
            outcome = {"record_kind": "offline_requested_availability_outcome", "schema_version": 1,
                       "forecast_id": identifier, "forecast_sha256": _hash(forecast),
                       "target_utc": forecast["target_utc"], "resolved_at_utc": _utc(now),
                       "operation_sequence": sequence, "snapshot_id": snapshot_id, "label": label,
                       "execution": "offline_accounting_only", "live_issuance_verified": False}
            encoded = _bytes(outcome)
            self._outcomes[identifier] = encoded
            self._sequence = sequence
            created.append(json.loads(encoded))
        return created

    def report(self):
        """Export private copies, unresolved/pending outcomes and paired diagnostics."""
        now = self._now()
        records, scored_rows = [], []
        for encoded in self._forecasts.values():
            forecast = json.loads(encoded)
            saved = self._outcomes.get(forecast["forecast_id"])
            outcome = json.loads(saved) if saved is not None else None
            assert outcome is None or outcome["forecast_sha256"] == _hash(forecast)
            brier = paired_brier(forecast, outcome["label"]) if outcome is not None else None
            audit = {"origin": self._later_contradictions(forecast["origin"], forecast["snapshot_id"])}
            if outcome is not None:
                audit["label"] = self._later_contradictions(outcome["label"], outcome["snapshot_id"])
            records.append({"forecast": forecast, "outcome": outcome, "brier": brier,
                            "later_contradictions": audit})
            scored_rows.append({**forecast, "label": outcome["label"] if outcome else {"status": "pending"},
                                "scored": brier is not None, "brier": brier})
        summary = _summary(scored_rows)
        summary["matured_unaccounted"] = sum(r["outcome"] is None and _time(r["forecast"]["target_utc"]) <= now
                                            for r in records)
        summary["later_contradicted_labels"] = sum(bool(r["later_contradictions"].get("label")) for r in records)
        return {"execution": "offline_accounting_only", "live_issuance_verified": False,
                "country": self._country, "item_id": self._item_id, "reported_at_utc": _utc(now),
                "records": records, "summary": summary,
                "snapshots": [json.loads(_bytes(s["value"])) for s in self._snapshots]}
