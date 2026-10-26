"""Operator-invoked immutable requested-time accounting; inject storage explicitly.

Fake storage proves software contracts, never actual prospective performance.
No provider client, scheduler, lease, mutable head or historical import is created.
"""

from collections import Counter
from datetime import datetime, timezone
import json
import math
import re

from aws_shadow_forecast import Consumer, SECOND
from tornredeye.availability_forecast import _probabilities, _select, _target
from tornredeye.evidence_paths import LINEAGE, validate_backup_key
from tornredeye.requested_forecast import METHODS
from tornredeye.shadow_forecast import _bytes, _hash, outcome_label, paired_brier
from tornredeye.stock_history import _time, _utc


MAX_OBJECT_BYTES = 1024 * 1024
MAX_LEDGER_BYTES = 32 * 1024 * 1024
MAX_LEDGER_OBJECTS = 3000


def planned_request_id(request_plan, index):
    """Commit the complete private plan through the existing immutable request ID.

    Entries contain country, item_id, target_utc and an arbitrary horizon_cohort
    label. Retain this exact plan privately; changing any entry changes every ID.
    """
    if not isinstance(request_plan, list) or not request_plan:
        raise ValueError("a nonempty request plan is required")
    for entry in request_plan:
        if not isinstance(entry, dict) or set(entry) != {"country", "item_id", "target_utc", "horizon_cohort"}:
            raise ValueError("invalid request plan entry")
        _target(entry["country"], entry["item_id"])
        if (not isinstance(entry["target_utc"], str)
                or _utc(_time(entry["target_utc"])) != entry["target_utc"]
                or not isinstance(entry["horizon_cohort"], str)
                or not entry["horizon_cohort"].strip()):
            raise ValueError("canonical UTC target and nonempty horizon cohort required")
    if type(index) is not int or not 0 <= index < len(request_plan):
        raise ValueError("invalid request plan index")
    return "planned:" + _hash(request_plan) + ":" + str(index)


class RequestedMeasurement(Consumer):
    """Reconstruct a complete, bounded immutable campaign before each operation.

    The injected client's primary store must be append-only and strongly
    consistent, with no overwrites/deletes or restored evidence in an active epoch.
    Objects in the cutoff's boundary second are excluded explicitly. This gives
    a stable reconstruction despite later appends, without mutable coordination.
    """

    def __init__(self, s3, bucket, *, epoch, country, item_id,
                 maximum_source_age_seconds, clock=None, remaining_millis=None,
                 maximum_evidence_bytes=16 * 1024 * 1024):
        _target(country, item_id)
        if not isinstance(epoch, str) or not re.fullmatch(r"requested-[a-z0-9][a-z0-9-]{0,52}", epoch):
            raise ValueError("an isolated requested measurement epoch is required")
        if (type(maximum_source_age_seconds) not in (int, float)
                or not math.isfinite(maximum_source_age_seconds)
                or not 0 <= maximum_source_age_seconds <= 300):
            raise ValueError("measurement source age must be between zero and 300 seconds")
        super().__init__(s3, bucket, epoch=epoch, clock=clock, remaining_millis=remaining_millis)
        self.country, self.item_id = country, item_id
        self.maximum_age = maximum_source_age_seconds
        if type(maximum_evidence_bytes) is not int or maximum_evidence_bytes <= 0:
            raise ValueError("a positive evidence read bound is required")
        self.maximum_evidence_bytes = maximum_evidence_bytes
        self.records_root = self.root + "requests/"

    def get(self, key, **kwargs):
        limit = MAX_OBJECT_BYTES if key.startswith(self.records_root) else self.maximum_evidence_bytes
        saved = super().get(key, max_bytes=limit, **kwargs)
        if saved is not None:
            saved[2]["read_at_utc"] = _utc(self.now())
        return saved

    def create(self, key, value):
        validate_backup_key(key)
        size = len(_bytes(value))
        if not key.startswith(self.records_root) or size > MAX_OBJECT_BYTES:
            raise ValueError("invalid or oversized measurement write")
        if self._write_bytes is None:
            self._inventory(self.started)
        new = key not in self._write_keys
        if (new and (self._write_count + 1 > MAX_LEDGER_OBJECTS
                     or self._write_bytes + size > MAX_LEDGER_BYTES)):
            raise ValueError("measurement write exceeds reconstruction bound")
        receipt = super().create(key, value)
        if new:
            self._write_keys.add(key)
            self._write_count += 1
            self._write_bytes += size
        return receipt

    def _begin(self):
        self.started = None
        cutoff = self.now()
        self.started = cutoff
        self._write_bytes = None
        return cutoff

    def _request(self, request_id):
        if not isinstance(request_id, str) or not request_id.strip():
            raise ValueError("request ID must be a nonempty string")
        return {"country": self.country, "item_id": self.item_id, "request_id": request_id}

    def key(self, request_id, kind):
        key = self.records_root + _hash(self._request(request_id)) + "/" + kind + ".json"
        validate_backup_key(key)
        return key

    def _inventory(self, cutoff):
        records, excluded, keys = {}, [], set()
        token, tokens, last, total, count = None, set(), "", 0, 0
        while True:
            self.now()
            page = self.s3.list_objects_v2(
                Bucket=self.bucket, Prefix=self.records_root, MaxKeys=1000,
                **({"ContinuationToken": token} if token else {}))
            members = page.get("Contents", [])
            if type(page.get("IsTruncated")) is not bool or not isinstance(members, list) or len(members) > 1000:
                raise ValueError("incomplete measurement inventory")
            for member in members:
                count += 1
                key, size, modified = member["Key"], member["Size"], member["LastModified"]
                validate_backup_key(key)
                if (not key.startswith(self.records_root) or key <= last
                        or type(size) is not int or size < 0 or size > MAX_OBJECT_BYTES):
                    raise ValueError("invalid measurement inventory member")
                last = key
                keys.add(key)
                total += size
                if count > MAX_LEDGER_OBJECTS or total > MAX_LEDGER_BYTES:
                    raise ValueError("measurement inventory exceeds reconstruction bound")
                if not isinstance(modified, datetime) or modified.utcoffset() is None:
                    raise ValueError("missing measurement persistence time")
                if modified + SECOND > cutoff:
                    excluded.append(key)
                    continue
                saved = self.get(key, etag=member["ETag"])
                if (saved[2]["size_bytes"] != size
                        or _time(saved[2]["last_modified_utc"]) != modified):
                    raise ValueError("measurement inventory/read mismatch")
                records[key] = (json.loads(saved[0]), saved[2])
            if not page["IsTruncated"]:
                break
            following = page.get("NextContinuationToken")
            if not members or not isinstance(following, str) or not following or following in tokens:
                raise ValueError("incomplete measurement pagination")
            tokens.add(following)
            token = following
        self._write_bytes, self._write_count, self._write_keys = total, count, keys
        return records, {"cutoff_utc": _utc(cutoff), "inventory_complete": True,
                         "listed_objects": count, "listed_bytes": total,
                         "listed_requests": len({key.rsplit("/", 1)[0] for key in keys}),
                         "boundary_exclusions": excluded}

    def _validate_forecast(self, key, forecast):
        _target(forecast["country"], forecast["item_id"])
        if (forecast["record_kind"] != "requested_forecast" or forecast["schema_version"] != 1
                or forecast["evidence_lineage"] != LINEAGE or forecast["forecast_epoch"] != self.epoch
                or forecast["methods"] != METHODS
                or type(forecast["item_id"]) is not int):
            raise ValueError("invalid requested forecast contract")
        if not isinstance(forecast["request_id"], str) or not forecast["request_id"].strip():
            raise ValueError("invalid persisted request ID")
        identity = {k: forecast[k] for k in ("country", "item_id", "request_id")}
        if key != self.records_root + _hash(identity) + "/forecast.json":
            raise ValueError("requested forecast identity mismatch")
        if (forecast["forecast_id"] != _hash({k: v for k, v in forecast.items() if k != "forecast_id"})
                or _time(forecast["target_utc"]) <= _time(forecast["issued_at_utc"])
                or forecast["horizon_seconds"] != (_time(forecast["target_utc"]) - _time(forecast["issued_at_utc"])).total_seconds()):
            raise ValueError("invalid requested forecast digest or chronology")
        if (forecast["country"] == self.country and forecast["item_id"] == self.item_id
                and forecast["maximum_source_age_seconds"] != self.maximum_age):
            raise ValueError("mixed freshness settings in a training cohort")

    def _verified(self, forecast, receipt, ack, ack_receipt):
        target = _time(forecast["target_utc"])
        if (ack["forecast_id"] != forecast["forecast_id"]
                or any(ack["forecast"][field] != receipt[field]
                       for field in ("bucket", "key", "sha256", "etag", "last_modified_utc"))
                or ack["record_kind"] != "requested_ack"):
            raise ValueError("requested acknowledgement mismatch")
        if not (_time(forecast["issued_at_utc"]) <= _time(ack["forecast"]["read_at_utc"])
                <= _time(ack["readback_at_utc"])
                <= _time(ack_receipt["persisted_before_utc"])):
            raise ValueError("requested acknowledgement chronology mismatch")
        return (ack["status"] == "verified" and _time(ack["readback_at_utc"]) < target
                and _time(receipt["persisted_before_utc"]) < target
                and _time(ack_receipt["persisted_before_utc"]) < target)

    def _entries(self, records):
        entries = []
        for key, (value, receipt) in records.items():
            if key.endswith("forecast.json"):
                self._validate_forecast(key, value)
                if _time(value["issued_at_utc"]) > _time(receipt["persisted_before_utc"]):
                    raise ValueError("forecast issuance follows persistence")
                base = key[:-len("forecast.json")]
                ack = records.get(base + "ack.json")
                outcome = records.get(base + "outcome.json")
                verified = bool(ack and self._verified(value, receipt, *ack))
                if outcome:
                    result = outcome[0]
                    if (result["record_kind"] != "requested_outcome"
                            or result["forecast_id"] != value["forecast_id"]
                            or result["forecast_sha256"] != receipt["sha256"]
                            or result["target_utc"] != value["target_utc"]
                            or _time(result["resolved_at_utc"]) < _time(value["target_utc"])
                            or _time(result["resolved_at_utc"]) > _time(outcome[1]["persisted_before_utc"])):
                        raise ValueError("requested outcome association or chronology mismatch")
                entries.append((value, receipt, verified, outcome))
            elif key.rsplit("/", 1)[0] + "/forecast.json" not in records:
                raise ValueError("orphan requested measurement record")
        return entries

    def _selected(self, cutoff):
        snapshot, points = self.evidence(cutoff, country=self.country, item_id=self.item_id)
        selection = _select(points, [p["at"] for p in points], cutoff)
        if "observation" in selection:
            age = selection["observation"]["source_age_seconds"]
            selection["status"] = "eligible" if 0 <= age <= self.maximum_age else "stale"
        return selection, snapshot

    def _acknowledge(self, forecast, receipt):
        key = self.key(forecast["request_id"], "ack")
        saved = self.get(key, optional=True)
        if saved:
            self._verified(forecast, receipt, json.loads(saved[0]), saved[2])
            return json.loads(saved[0])
        readback = self.now()
        verified = (_time(receipt["persisted_before_utc"]) < _time(forecast["target_utc"])
                    and readback < _time(forecast["target_utc"]))
        ack = {"record_kind": "requested_ack", "forecast_id": forecast["forecast_id"],
               "forecast": receipt, "readback_at_utc": _utc(readback),
               "status": "verified" if verified else "late_or_unverified"}
        self.create(key, ack)
        return ack

    def issue(self, target_utc, *, request_id):
        cutoff = self._begin()
        identity = self._request(request_id)
        target = _time(target_utc) if isinstance(target_utc, str) else target_utc
        if not isinstance(target, datetime) or target.utcoffset() is None:
            raise ValueError("requested target must be timezone aware")
        target = target.astimezone(timezone.utc)
        key = self.key(request_id, "forecast")
        saved = self.get(key, optional=True)
        if saved:
            forecast = json.loads(saved[0])
            self._validate_forecast(key, forecast)
            if _time(forecast["issued_at_utc"]) > _time(saved[2]["persisted_before_utc"]):
                raise ValueError("forecast issuance follows persistence")
            if forecast["target_utc"] != _utc(target) or forecast["maximum_source_age_seconds"] != self.maximum_age:
                raise ValueError("conflicting requested measurement identity")
        else:
            records, inventory = self._inventory(cutoff)
            if (inventory["listed_requests"] + 1) * 3 > MAX_LEDGER_OBJECTS:
                raise ValueError("measurement inventory has no room for a complete request")
            training, seen = [], set()
            entries = sorted(self._entries(records), key=lambda e: (e[3][0]["resolved_at_utc"] if e[3] else "", e[0]["forecast_id"]))
            for prior, receipt, verified, outcome in entries:
                if (not outcome or not verified or prior["country"] != self.country
                        or prior["item_id"] != self.item_id):
                    continue
                if prior["maximum_source_age_seconds"] != self.maximum_age:
                    raise ValueError("mixed freshness settings in a training cohort")
                label = outcome[0]["label"]
                if prior["origin"]["status"] != "eligible" or label["status"] != "eligible":
                    continue
                source = label["observation"]["source_timestamp"]
                if source in seen:
                    continue
                seen.add(source)
                training.append({"forecast_id": prior["forecast_id"], "outcome": outcome[1],
                                 "source_timestamp": source, "available": label["available"]})
            origin, snapshot = self._selected(self.now())
            issued = self.now()
            if target <= issued:
                raise ValueError("requested target must follow actual issuance")
            # age at decision, not at the beginning of the evidence scan
            if "observation" in origin:
                age = issued.timestamp() - origin["observation"]["source_timestamp"]
                origin["observation"]["source_age_seconds"] = age
                origin["status"] = "eligible" if 0 <= age <= self.maximum_age else "stale"
            forecast = {**identity, "record_kind": "requested_forecast", "schema_version": 1,
                        "evidence_lineage": LINEAGE, "forecast_epoch": self.epoch,
                        "issued_at_utc": _utc(issued), "target_utc": _utc(target),
                        "horizon_seconds": (target - issued).total_seconds(), "methods": dict(METHODS),
                        "maximum_source_age_seconds": self.maximum_age, "origin": origin,
                        "inputs": snapshot, "training_inventory": inventory, "training": training,
                        **_probabilities(origin, len(training), sum(t["available"] for t in training))}
            forecast["forecast_id"] = _hash(forecast)
            receipt = self.create(key, forecast)
            saved = (_bytes(forecast), None, receipt)
        self._acknowledge(forecast, saved[2])
        return forecast

    def resolve(self):
        cutoff = self._begin()
        records, _ = self._inventory(cutoff)
        created = []
        for forecast, receipt, verified, outcome in sorted(self._entries(records), key=lambda e: (e[0]["target_utc"], e[0]["forecast_id"])):
            if forecast["country"] != self.country or forecast["item_id"] != self.item_id:
                continue
            if outcome or _time(forecast["target_utc"]) > cutoff:
                continue
            key = self.key(forecast["request_id"], "outcome")
            existing = self.get(key, optional=True)
            if existing:
                continue
            label, snapshot = self._selected(_time(forecast["target_utc"]))
            if label["status"] == "eligible" and label["observation"]["source_timestamp"] <= _time(forecast["issued_at_utc"]).timestamp():
                label["status"] = "no_post_origin_source_update"
            value = {"record_kind": "requested_outcome", "schema_version": 1,
                     "forecast_id": forecast["forecast_id"],
                     "forecast_sha256": receipt["sha256"], "target_utc": forecast["target_utc"],
                     "resolved_at_utc": _utc(self.now()), "label": outcome_label(label, forecast["issued_at_utc"]),
                     "inputs": snapshot}
            self.create(key, value)
            created.append(value)
        return created

    def report(self, *, request_plan=None):
        plan = None if request_plan is None else {
            planned_request_id(request_plan, i): entry for i, entry in enumerate(request_plan)}
        if request_plan is not None and not plan:
            raise ValueError("a nonempty request plan is required")
        cutoff = self._begin()
        records, inventory = self._inventory(cutoff)
        grouped, details = {}, []
        for forecast, receipt, verified, outcome in self._entries(records):
            if forecast["country"] != self.country or forecast["item_id"] != self.item_id:
                continue
            horizon = str(forecast["horizon_seconds"])
            cohort = None
            if plan is not None:
                entry = plan.get(forecast["request_id"])
                if entry is not None:
                    if any(forecast[field] != entry[field] for field in ("country", "item_id", "target_utc")):
                        raise ValueError("forecast disagrees with committed request plan")
                    cohort = entry["horizon_cohort"]
                elif forecast["request_id"].startswith("planned:"):
                    raise ValueError("forecast request plan commitment mismatch")
                horizon = "unplanned" if cohort is None else "cohort:" + cohort
            group = grouped.setdefault(horizon, {"recorded": 0, "issuance_ineligible": 0,
                "origin_status": Counter(), "forecast_status": Counter(), "outcome_status": Counter(),
                "pending": 0, "matured_unaccounted": 0, "paired_count": 0, "positive": 0, "zero": 0,
                "resolved_positive": 0, "resolved_zero": 0, "unresolved": 0,
                "cold_start_abstentions": 0,
                "brier_sums": {"empirical": 0., "persistence": 0., "always_empty": 0.}})
            group["recorded"] += 1
            group["issuance_ineligible"] += not verified
            group["origin_status"][forecast["origin"]["status"]] += 1
            group["forecast_status"][forecast["forecast_status"]] += 1
            group["cold_start_abstentions"] += forecast["forecast_status"] == "no_resolved_training_labels"
            details.append({"forecast": forecast, "forecast_receipt": receipt,
                            "issuance_verified": verified, "outcome": outcome[0] if outcome else None,
                            **({"horizon_cohort": cohort} if plan is not None else {})})
            if not outcome:
                group["pending"] += 1
                group["matured_unaccounted"] += _time(forecast["target_utc"]) <= cutoff
                continue
            label = outcome[0]["label"]
            group["outcome_status"][label["status"]] += 1
            if label["status"] == "eligible":
                group["resolved_positive" if label["available"] else "resolved_zero"] += 1
            else:
                group["unresolved"] += 1
            brier = paired_brier(forecast, label) if verified else None
            if brier is not None:
                group["paired_count"] += 1
                group["positive" if label["available"] else "zero"] += 1
                for method, score in {**brier, "always_empty": float(label["available"])}.items():
                    group["brier_sums"][method] += score
        for group in grouped.values():
            group["brier"] = {m: total / group["paired_count"] if group["paired_count"] else None
                              for m, total in group.pop("brier_sums").items()}
        return {"country": self.country, "item_id": self.item_id, "forecast_epoch": self.epoch,
                "inventory": inventory,
                **({"horizons": grouped} if plan is None else {
                    "cohorts": grouped, "request_plan": request_plan,
                    "request_plan_sha256": _hash(request_plan)}),
                "records": details,
                "interpretation": "S3-available source stock; not target-time consumer reads or purchase success."}
