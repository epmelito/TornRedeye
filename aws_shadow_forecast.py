"""Independent private hourly S3 consumer. No provider requests or AWS setup."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import re

from availability_forecast import HORIZON, MAX_SOURCE_AGE, _probabilities, _select, _timeline
from evidence_backup import PREFIXES
from shadow_forecast import _bytes, _hash, forecast_identity, outcome_label, paired_brier
from stock_history import _time, _utc, normalized_target_fields
from yata_normalizer import normalize_export


ROOT = "shadow/haw/1485/v1/"
POINTER = ROOT + "head.json"
LEASE = ROOT + "lease.json"
WINDOW = timedelta(minutes=10)
SECOND = timedelta(seconds=1)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _code(error):
    return getattr(error, "response", {}).get("Error", {}).get("Code")


class Consumer:
    """Single Lambda writer, enforced by a five-minute conditional S3 lease.

    Immutable hourly checkpoints precede forecasts. A conditional head pointer
    commits resolved counts and reserves the next slot before its forecast write.
    A missing reserved forecast is an explicit failed slot, never a training row.
    """

    def __init__(self, s3, bucket, *, clock=None, remaining_millis=None):
        if not bucket:
            raise ValueError("evidence bucket is required")
        self.s3, self.bucket = s3, bucket
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.remaining_millis = remaining_millis
        self.last_time = None
        self.started = None

    def now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("processing clock must return an aware datetime")
        value = value.astimezone(timezone.utc)
        if self.last_time is not None and value < self.last_time:
            raise ValueError("processing clock regressed")
        self.last_time = value
        if self.started is not None and value >= self.started + timedelta(seconds=240):
            raise RuntimeError("execution deadline reached; lease cannot be extended")
        if self.remaining_millis and self.remaining_millis() < 10000:
            raise RuntimeError("insufficient execution budget; no partial scan accepted")
        return value

    def get(self, key, *, etag=None, optional=False):
        self.now()
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key,
                                          **({"IfMatch": etag} if etag else {}))
        except Exception as error:
            if optional and _code(error) == "NoSuchKey":
                return None
            raise
        stream = response["Body"]
        try:
            body = stream.read()
        finally:
            stream.close()
        modified = response["LastModified"]
        if not isinstance(modified, datetime) or modified.utcoffset() is None:
            raise ValueError("missing trusted S3 object time")
        metadata = response["ResponseMetadata"]
        if metadata["HTTPStatusCode"] != 200 or not metadata.get("RequestId") or not response["ETag"]:
            raise ValueError("missing S3 read acknowledgement")
        receipt = {"bucket": self.bucket, "key": key,
                   "sha256": hashlib.sha256(body).hexdigest(),
                   "size_bytes": len(body), "etag": response["ETag"],
                   "last_modified_utc": _utc(modified.astimezone(timezone.utc)),
                   "persisted_before_utc": _utc(modified.astimezone(timezone.utc) + SECOND),
                   "read_request_id": metadata["RequestId"]}
        if key.startswith(ROOT) and response.get("Metadata", {}).get("sha256") != receipt["sha256"]:
            raise ValueError("derived object digest mismatch")
        return body, response, receipt

    def create(self, key, value):
        body = _bytes(value)
        try:
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=body,
                               ContentType="application/json", IfNoneMatch="*",
                               Metadata={"sha256": hashlib.sha256(body).hexdigest()})
        except Exception as error:
            if _code(error) != "PreconditionFailed":
                raise
        saved = self.get(key)
        if saved[0] != body:
            raise ValueError("create-only object conflict: " + key)
        return saved[2]

    def acquire(self, started):
        existing = self.get(LEASE, optional=True)
        if existing:
            lease = json.loads(existing[0])
            if _time(lease["acquired_at_utc"]) > started:
                raise ValueError("processing clock precedes lease acquisition")
            if _time(lease["expires_at_utc"]) > started:
                return False
        body = _bytes({"acquired_at_utc": _utc(started),
                       "expires_at_utc": _utc(started + timedelta(minutes=5))})
        try:
            self.s3.put_object(Bucket=self.bucket, Key=LEASE, Body=body,
                               ContentType="application/json",
                               Metadata={"sha256": hashlib.sha256(body).hexdigest()},
                               **({"IfMatch": existing[1]["ETag"]} if existing else {"IfNoneMatch": "*"}))
        except Exception as error:
            if _code(error) == "PreconditionFailed":
                return False
            raise
        return True

    def evidence(self, cutoff):
        """Stream complete inventories, retain only a bounded recent input window.

        UUID keys cannot support chronological range scans. Canonical writes are
        already immutable; LIST and conditional GET establish the original S3
        persistence boundary, independently of when this consumer processes it.
        """
        rows, provenance, boundary = {}, [], []
        for prefix in PREFIXES:
            token = None
            while True:
                self.now()
                page = self.s3.list_objects_v2(
                    Bucket=self.bucket, Prefix=prefix, MaxKeys=1000,
                    **({"ContinuationToken": token} if token else {}))
                for member in page.get("Contents", []):
                    modified = member["LastModified"]
                    if modified < cutoff - WINDOW or modified > cutoff:
                        continue
                    key = member["Key"]
                    suffix = key[len(prefix):]
                    if not re.fullmatch(r"[0-9a-f]{32}\.(bin|json)", suffix):
                        raise ValueError("unexpected canonical evidence key")
                    if modified + SECOND > cutoff:
                        boundary.append(key)
                        continue
                    body, response, receipt = self.get(key, etag=member["ETag"])
                    if receipt["last_modified_utc"] != _utc(modified):
                        raise ValueError("canonical evidence changed during inventory")
                    provenance.append({**receipt, "metadata": response.get("Metadata", {})})
                    if len(provenance) > 2000:
                        raise ValueError("evidence window exceeds collector contract")
                    if prefix == PREFIXES[0]:
                        metadata = response.get("Metadata", {})
                        retrieved = _time(metadata.get("retrieved-at"))
                        if (metadata.get("source") != "YATA"
                                or metadata.get("sha256") != receipt["sha256"]
                                or metadata.get("status") not in ("observed", "missing", "malformed", "collection_failed")
                                or not re.fullmatch(r"[1-5][0-9]{2}", metadata.get("http-status", ""))):
                            raise ValueError("invalid canonical raw context")
                        normalized = json.loads(normalize_export(
                            body, retrieved, raw_evidence_id=f"s3://{self.bucket}/{key}",
                            http_status=int(metadata["http-status"])))
                        fields = normalized_target_fields(normalized, retrieved, "haw", 1485)
                        row = {"at": retrieved, "key": key,
                               "source": fields["source_timestamp"],
                               "quantity": fields["quantity"], "issues": fields["issues"]}
                        rows[suffix[:-4]] = row
                    else:
                        document = json.loads(body)
                        retrieved = _time(document["retrieved_at"])
                        if document["collection_id"].replace("-", "") != suffix[:-5]:
                            raise ValueError("normalized identity mismatch")
                        if suffix[:-5] in rows and rows[suffix[:-5]]["at"] != retrieved:
                            raise ValueError("raw/normalized retrieval mismatch")
                        rows.setdefault(suffix[:-5], {
                            "at": retrieved, "key": key, "source": None, "quantity": None,
                            "issues": ["collection_failed" if document["status"] == "collection_failed"
                                       else "unavailable_target_evidence"]})
                    if retrieved > modified + SECOND or retrieved > cutoff:
                        raise ValueError("retrieval is after evidence persistence/cutoff")
                if not page.get("IsTruncated"):
                    break
                next_token = page["NextContinuationToken"]
                if next_token == token:
                    raise ValueError("inventory did not advance")
                token = next_token
        ordered = sorted(rows.values(), key=lambda r: r["at"])
        if len({r["at"] for r in ordered}) != len(ordered):
            raise ValueError("simultaneous retrievals have no established order")
        points, _, _ = _timeline(ordered)
        return {"cutoff_utc": _utc(cutoff), "window_seconds": int(WINDOW.total_seconds()),
                "rows": [{**r, "at": _utc(r["at"])} for r in ordered],
                "objects": sorted(provenance, key=lambda r: r["key"]),
                "persistence_boundary_exclusions": sorted(boundary)}, points

    def attest(self, forecast, receipt):
        verified = _time(receipt["persisted_before_utc"]) < _time(forecast["target_utc"])
        value = {"forecast_id": forecast["forecast_id"], "forecast": receipt,
                 "live_issuance_verified": verified,
                 "status": "verified" if verified else "late_or_ambiguous_write"}
        # read-request IDs vary on restart; preserve the first successful receipt
        key = self.slot_key(forecast["utc_hour_slot"], "ack")
        existing = self.get(key, optional=True)
        if existing:
            value = json.loads(existing[0])
            if (value["forecast"]["sha256"] != receipt["sha256"]
                    or value["forecast"]["etag"] != receipt["etag"]
                    or value["live_issuance_verified"] != verified):
                raise ValueError("forecast acknowledgement mismatch")
        else:
            self.create(key, value)
        return value

    @staticmethod
    def slot_key(slot, kind):
        return ROOT + slot[:13].replace(":", "") + "/" + kind + ".json"

    def run(self):
        started = self.now()
        self.started = started
        if not self.acquire(started):
            logger.info("shadow skipped reason=lease_busy")
            return {"status": "skipped", "reason": "lease_busy"}
        slot = forecast_identity("haw", 1485, started)["utc_hour_slot"]
        head = self.get(POINTER, optional=True)
        if head:
            reference = json.loads(head[0])
            saved = self.get(reference["key"])
            if saved[2]["sha256"] != reference["sha256"]:
                raise ValueError("checkpoint digest mismatch")
            state = json.loads(saved[0])
            if state["slot"] > slot:
                raise ValueError("processing clock precedes committed slot")
            if _time(state["committed_at_utc"]) > started:
                raise ValueError("processing clock precedes committed checkpoint")
        else:
            reference = None
            state = {"slot": None, "pending": [], "training_count": 0,
                     "training_positive_count": 0, "recorded_slots": 0,
                     "failed_slots": 0, "unrecorded_slots": 0, "paired_count": 0,
                     "brier_sum": {"empirical": 0., "persistence": 0.},
                     "origin_status": {}, "forecast_status": {}, "outcome_status": {},
                     "issuance_status": {}}

        checkpoint_key = self.slot_key(slot, "checkpoint")
        if state["slot"] != slot:
            # an immutable but uncommitted checkpoint can be adopted after a crash
            existing = self.get(checkpoint_key, optional=True)
            if existing:
                checkpoint = json.loads(existing[0])
                if checkpoint["previous"] != reference:
                    raise ValueError("checkpoint predecessor conflict")
                if _time(checkpoint["committed_at_utc"]) > self.now():
                    raise ValueError("processing clock precedes orphan checkpoint")
            else:
                checkpoint = json.loads(_bytes(state))
                pending, resolutions = [], []
                for prior_slot in state["pending"]:
                    key = self.slot_key(prior_slot, "forecast")
                    saved = self.get(key, optional=True)
                    if saved is None:
                        checkpoint["failed_slots"] += 1
                        continue
                    forecast = json.loads(saved[0])
                    ack = self.attest(forecast, saved[2])
                    if _time(forecast["target_utc"]) > started:
                        pending.append(prior_slot)
                        continue
                    outcome_key = self.slot_key(prior_slot, "outcome")
                    outcome_saved = self.get(outcome_key, optional=True)
                    if outcome_saved:
                        outcome = json.loads(outcome_saved[0])
                    else:
                        snapshot, points = self.evidence(_time(forecast["target_utc"]))
                        label = outcome_label(_select(points, [p["at"] for p in points],
                                                      _time(forecast["target_utc"])),
                                              forecast["issued_at_utc"])
                        outcome = {"schema_version": 1, "record_kind": "shadow_outcome",
                                   "forecast_id": forecast["forecast_id"],
                                   "forecast_sha256": saved[2]["sha256"],
                                   "target_utc": forecast["target_utc"],
                                   "resolved_at_utc": _utc(self.now()), "label": label,
                                   "inputs": snapshot, "brier": paired_brier(forecast, label)
                                   if ack["live_issuance_verified"] else None}
                        self.create(outcome_key, outcome)
                        outcome_saved = self.get(outcome_key)
                    if outcome["forecast_sha256"] != saved[2]["sha256"]:
                        raise ValueError("outcome forecast mismatch")
                    if _time(outcome["resolved_at_utc"]) > self.now():
                        raise ValueError("processing clock precedes outcome resolution")
                    # successful readback precedes the checkpoint and issuance
                    resolutions.append({"key": outcome_key, "sha256": _hash(outcome),
                                        "acknowledged_at_utc": _utc(self.now())})
                    checkpoint["recorded_slots"] += 1
                    for field, status in (("origin_status", forecast["origin"]["status"]),
                                          ("forecast_status", forecast["forecast_status"]),
                                          ("outcome_status", outcome["label"]["status"]),
                                          ("issuance_status", ack["status"])):
                        counts = Counter(checkpoint[field])
                        counts[status] += 1
                        checkpoint[field] = dict(counts)
                    if (ack["live_issuance_verified"] and forecast["origin"]["status"] == "eligible"
                            and outcome["label"]["status"] == "eligible"):
                        checkpoint["training_count"] += 1
                        checkpoint["training_positive_count"] += outcome["label"]["available"]
                    if outcome["brier"] is not None:
                        checkpoint["paired_count"] += 1
                        for method, score in outcome["brier"].items():
                            checkpoint["brier_sum"][method] += score
                if state["slot"]:
                    checkpoint["unrecorded_slots"] += int((_time(slot) - _time(state["slot"])) / HORIZON) - 1
                checkpoint.update(slot=slot, pending=pending + [slot], previous=reference,
                                  resolutions=resolutions, committed_at_utc=_utc(self.now()))
                self.create(checkpoint_key, checkpoint)
            reference = {"key": checkpoint_key, "sha256": _hash(checkpoint)}
            body = _bytes(reference)
            self.s3.put_object(Bucket=self.bucket, Key=POINTER, Body=body,
                               ContentType="application/json",
                               Metadata={"sha256": hashlib.sha256(body).hexdigest()},
                               **({"IfMatch": head[1]["ETag"]} if head else {"IfNoneMatch": "*"}))
            state = checkpoint

        forecast_key = self.slot_key(slot, "forecast")
        saved = self.get(forecast_key, optional=True)
        if not saved:
            snapshot, points = self.evidence(self.now())
            issued = self.now()
            if forecast_identity("haw", 1485, issued)["utc_hour_slot"] != slot:
                raise RuntimeError("scan crossed UTC slot; reservation remains a failed slot")
            origin = _select(points, [p["at"] for p in points], issued)
            identity = forecast_identity("haw", 1485, issued)
            forecast = {"schema_version": 1, "record_kind": "shadow_forecast",
                        "forecast_id": _hash(identity), **identity,
                        "issued_at_utc": _utc(issued), "target_utc": _utc(issued + HORIZON),
                        "maximum_source_age_seconds": MAX_SOURCE_AGE,
                        "origin": origin, "inputs": snapshot, "training_checkpoint": reference,
                        **_probabilities(origin, state["training_count"], state["training_positive_count"])}
            self.create(forecast_key, forecast)
            saved = self.get(forecast_key)
        forecast = json.loads(saved[0])
        ack = self.attest(forecast, saved[2])
        summary = {k: state[k] for k in ("recorded_slots", "failed_slots", "unrecorded_slots",
                                        "training_count", "paired_count", "origin_status",
                                        "forecast_status", "outcome_status", "issuance_status")}
        summary["pending_slots"] = len(state["pending"])
        summary["brier"] = {m: v / state["paired_count"] if state["paired_count"] else None
                            for m, v in state["brier_sum"].items()}
        logger.info("shadow slot=%s verified=%s summary=%s", slot, ack["live_issuance_verified"], summary)
        return {"forecast_key": forecast_key, "acknowledgement": ack, "summary": summary}


def lambda_handler(event, context):
    """Ignore event payloads; configuration alone selects the private pilot."""
    import boto3

    bucket = os.environ.get("EVIDENCE_BUCKET", "").strip()
    return Consumer(boto3.client("s3", region_name="eu-north-1"), bucket,
                    remaining_millis=getattr(context, "get_remaining_time_in_millis", None)).run()
