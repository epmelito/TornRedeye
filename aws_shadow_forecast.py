"""Independent private hourly S3 consumer. No provider requests or AWS setup."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import re

from tornredeye.availability_forecast import HORIZON, MAX_SOURCE_AGE, _probabilities, _select, _timeline
from tornredeye.evidence_paths import (LINEAGE, FORECAST_PREFIX, STATE_PREFIX, counterpart,
                                      evidence_identity, evidence_keys, window_prefixes)
from tornredeye.shadow_forecast import _bytes, _hash, forecast_identity, outcome_label, paired_brier
from tornredeye.stock_history import _time, _utc
from tornredeye.normalization import normalize_export, normalized_target_fields


DEFAULT_EPOCH = "epoch-1"
ROOT = FORECAST_PREFIX + DEFAULT_EPOCH + "/"
POINTER = STATE_PREFIX + DEFAULT_EPOCH + "/head.json"
LEASE = STATE_PREFIX + DEFAULT_EPOCH + "/lease.json"
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

    def __init__(self, s3, bucket, *, clock=None, remaining_millis=None, epoch=DEFAULT_EPOCH):
        if not bucket:
            raise ValueError("evidence bucket is required")
        if not isinstance(epoch, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", epoch):
            raise ValueError("invalid forecast epoch")
        self.epoch = epoch
        self.root = FORECAST_PREFIX + epoch + "/"
        self.pointer = STATE_PREFIX + epoch + "/head.json"
        self.lease = STATE_PREFIX + epoch + "/lease.json"
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
        if optional:
            page = self.s3.list_objects_v2(Bucket=self.bucket, Prefix=key, MaxKeys=1)
            members = page.get("Contents", [])
            if (type(page.get("IsTruncated")) is not bool or len(members) > 1
                    or any(not m["Key"].startswith(key) for m in members)):
                raise ValueError("invalid exact-key inventory")
            if not members or members[0]["Key"] != key:
                return None
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
        if key.startswith((self.root, STATE_PREFIX + self.epoch + "/")) and response.get("Metadata", {}).get("sha256") != receipt["sha256"]:
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
        existing = self.get(self.lease, optional=True)
        if existing:
            lease = json.loads(existing[0])
            if _time(lease["acquired_at_utc"]) > started:
                raise ValueError("processing clock precedes lease acquisition")
            if _time(lease["expires_at_utc"]) > started:
                return False
        body = _bytes({"acquired_at_utc": _utc(started),
                       "expires_at_utc": _utc(started + timedelta(minutes=5))})
        try:
            self.s3.put_object(Bucket=self.bucket, Key=self.lease, Body=body,
                               ContentType="application/json",
                               Metadata={"sha256": hashlib.sha256(body).hexdigest()},
                               **({"IfMatch": existing[1]["ETag"]} if existing else {"IfNoneMatch": "*"}))
        except Exception as error:
            if _code(error) == "PreconditionFailed":
                return False
            raise
        return True

    def evidence(self, cutoff):
        """Completely list the retrieval-hour partitions needed by the fixed window.

        Retrieval partitions bound discovery, never establish availability. Only
        original, conservatively bounded S3 write times admit an input. A failed
        page or resource limit aborts the decision rather than accepting a subset.
        """
        start = cutoff - WINDOW
        prefixes = list(window_prefixes(start, cutoff))
        rows, provenance, boundary, links = {}, [], [], {}
        listed = 0
        for prefix in prefixes:
            token, tokens, last = None, set(), ""
            while True:
                self.now()
                page = self.s3.list_objects_v2(
                    Bucket=self.bucket, Prefix=prefix, MaxKeys=1000,
                    **({"ContinuationToken": token} if token else {}))
                members = page.get("Contents", [])
                if type(page.get("IsTruncated")) is not bool or not isinstance(members, list) or len(members) > 1000:
                    raise ValueError("incomplete evidence inventory")
                for member in members:
                    listed += 1
                    if listed > 2000:
                        raise ValueError("evidence partitions exceed collector contract")
                    key, modified = member["Key"], member["LastModified"]
                    partition, identifier, kind = evidence_identity(key)
                    if not key.startswith(prefix) or key <= last:
                        raise ValueError("unordered or out-of-scope evidence inventory")
                    last = key
                    if not isinstance(modified, datetime) or modified.utcoffset() is None:
                        raise ValueError("missing evidence availability time")
                    if modified + SECOND > cutoff:
                        boundary.append(key)
                        continue
                    body, response, receipt = self.get(key, etag=member["ETag"])
                    if receipt["last_modified_utc"] != _utc(modified):
                        raise ValueError("canonical evidence changed during inventory")
                    if kind == "raw.bin":
                        metadata = response.get("Metadata", {})
                        retrieved = _time(metadata.get("retrieved-at"))
                        status, http = metadata.get("acquisition-status"), metadata.get("http-status")
                        if (metadata.get("source") != "YATA" or not metadata.get("source-url")
                                or metadata.get("lineage") != LINEAGE
                                or metadata.get("collection-id", "").replace("-", "") != identifier.hex
                                or metadata.get("sha256") != receipt["sha256"]
                                or status not in ("success", "collection_failed")
                                or (http is not None and not re.fullmatch(r"[1-5][0-9]{2}", http))
                                or status == "success" and (http is None or not 200 <= int(http) < 300)):
                            raise ValueError("invalid canonical raw acquisition context")
                        if status == "success":
                            normalized = json.loads(normalize_export(
                                body, retrieved, raw_evidence_id=f"s3://{self.bucket}/{key}", http_status=int(http)))
                            fields = normalized_target_fields(normalized, retrieved, "haw", 1485)
                            row = {"at": retrieved, "key": key, "source": fields["source_timestamp"],
                                   "quantity": fields["quantity"], "issues": fields["issues"]}
                        else:
                            row = {"at": retrieved, "key": key, "source": None,
                                   "quantity": None, "issues": ["collection_failed"]}
                    else:
                        document = json.loads(body)
                        retrieved = _time(document["retrieved_at"])
                        if (document.get("format") != "stock-evidence" or document.get("schema_version") != 1
                                or document.get("lineage") != LINEAGE
                                or document["collection_id"].replace("-", "") != identifier.hex
                                or document["acquisition"]["status"] not in ("success", "collection_failed")):
                            raise ValueError("normalized identity or contract mismatch")
                        raw_key = counterpart(key)
                        link = document["raw_evidence"]
                        if link is not None:
                            if link.get("key") != raw_key or link.get("bucket") != self.bucket:
                                raise ValueError("normalized raw identity mismatch")
                            links[raw_key] = (retrieved, link)
                        row = {"at": retrieved, "key": key, "source": None, "quantity": None,
                               "issues": ["collection_failed" if document["acquisition"]["status"] == "collection_failed"
                                          else "unavailable_target_evidence"]}
                    if evidence_keys(retrieved, identifier)[0] != key.rsplit("/", 1)[0] + "/raw.bin":
                        raise ValueError("retrieval partition mismatch")
                    if retrieved > modified + SECOND or retrieved > cutoff:
                        raise ValueError("retrieval is after evidence persistence/cutoff")
                    if retrieved < start:
                        continue
                    provenance.append({**receipt, "metadata": response.get("Metadata", {})})
                    raw_key = key if kind == "raw.bin" else counterpart(key)
                    if kind == "raw.bin":
                        if raw_key in rows and rows[raw_key]["at"] != retrieved:
                            raise ValueError("raw/normalized retrieval mismatch")
                        rows[raw_key] = row
                    else:
                        if raw_key in rows and rows[raw_key]["at"] != retrieved:
                            raise ValueError("raw/normalized retrieval mismatch")
                        rows.setdefault(raw_key, row)
                if not page["IsTruncated"]:
                    break
                following = page.get("NextContinuationToken")
                if not isinstance(following, str) or not following or following in tokens or not members:
                    raise ValueError("incomplete evidence pagination")
                tokens.add(following)
                token = following
        receipts = {p["key"]: p for p in provenance}
        for key, (retrieved, link) in links.items():
            if key in receipts and (receipts[key]["sha256"] != link.get("sha256")
                                    or receipts[key]["size_bytes"] != link.get("size_bytes")):
                raise ValueError("raw/normalized integrity mismatch")
        ordered = sorted(rows.values(), key=lambda r: r["at"])
        if len({r["at"] for r in ordered}) != len(ordered):
            raise ValueError("simultaneous retrievals have no established order")
        points, _, _ = _timeline(ordered)
        return {"cutoff_utc": _utc(cutoff), "window_seconds": int(WINDOW.total_seconds()),
                "coverage": {"retrieval_start_utc": _utc(start), "prefixes": prefixes,
                             "inventory_complete": True, "listed_objects": listed,
                             "collection_between_observations": "unknown"},
                "rows": [{**r, "at": _utc(r["at"])} for r in ordered],
                "objects": sorted(provenance, key=lambda r: r["key"]),
                "persistence_boundary_exclusions": sorted(boundary)}, points

    def attest(self, forecast, receipt):
        verified = _time(receipt["persisted_before_utc"]) < _time(forecast["target_utc"])
        value = {"forecast_id": forecast["forecast_id"], "evidence_lineage": LINEAGE,
                 "forecast_epoch": self.epoch, "forecast": receipt,
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

    def slot_key(self, slot, kind):
        return self.root + slot[:13].replace(":", "") + "/" + kind + ".json"

    def run(self):
        started = self.now()
        self.started = started
        if not self.acquire(started):
            logger.info("shadow skipped reason=lease_busy")
            return {"status": "skipped", "reason": "lease_busy"}
        slot = forecast_identity("haw", 1485, started)["utc_hour_slot"]
        head = self.get(self.pointer, optional=True)
        if head:
            reference = json.loads(head[0])
            if not reference["key"].startswith(self.root):
                raise ValueError("checkpoint is outside this forecast epoch")
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
                                   "evidence_lineage": LINEAGE, "forecast_epoch": self.epoch,
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
                checkpoint.update(evidence_lineage=LINEAGE, forecast_epoch=self.epoch,
                                  slot=slot, pending=pending + [slot], previous=reference,
                                  resolutions=resolutions, committed_at_utc=_utc(self.now()))
                self.create(checkpoint_key, checkpoint)
            reference = {"key": checkpoint_key, "sha256": _hash(checkpoint)}
            body = _bytes(reference)
            self.s3.put_object(Bucket=self.bucket, Key=self.pointer, Body=body,
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
            identity = {**forecast_identity("haw", 1485, issued),
                        "evidence_lineage": LINEAGE, "forecast_epoch": self.epoch}
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
                    remaining_millis=getattr(context, "get_remaining_time_in_millis", None),
                    epoch=os.environ.get("FORECAST_EPOCH", DEFAULT_EPOCH)).run()
