"""Coordinate YATA requests through one existing S3 object; never replay evidence."""
from datetime import datetime, timedelta, timezone
import json
import logging
import time
from uuid import UUID, uuid4

CONTROL_KEY = "control/yata/jap/206/polling.json"
MINIMUM_SPACING_SECONDS = 60
LEASE_SECONDS = 180
MAX_WAIT_SECONDS = 15
WAIT_RESERVE_SECONDS = 60

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ControlError(RuntimeError):
    """Control state is unavailable or unsafe; do not contact YATA."""


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("control timestamp must be a string")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta():
        raise ValueError("control timestamp must be UTC")
    return stamp


def _utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("guard clock must be timezone aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate control field")
        result[key] = value
    return result


def _etag(value):
    if not isinstance(value, str) or len(value) < 3 or not value.startswith('"') or not value.endswith('"'):
        raise ValueError("missing or malformed control ETag")
    return value


def _validate(state):
    fields = {"schema_version", "revision", "updated_at", "not_before", "halt_reason", "attempt"}
    if not isinstance(state, dict) or set(state) not in (fields, fields | {"spacing_only"}):
        raise ValueError("unexpected control fields")
    if type(state["schema_version"]) is not int or state["schema_version"] != 1:
        raise ValueError("unsupported control version")
    state = {"spacing_only": False, **state}
    if type(state["spacing_only"]) is not bool:
        raise ValueError("invalid spacing-only flag")
    UUID(state["revision"])
    _stamp(state["updated_at"])
    _stamp(state["not_before"])
    reason = state["halt_reason"]
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("invalid halt reason")
    attempt = state["attempt"]
    if attempt is not None:
        if not isinstance(attempt, dict) or set(attempt) != {"collection_id", "acquired_at", "expires_at"}:
            raise ValueError("invalid in-flight attempt")
        UUID(attempt["collection_id"])
        acquired = _stamp(attempt["acquired_at"])
        if _stamp(attempt["expires_at"]) != acquired + timedelta(seconds=LEASE_SECONDS):
            raise ValueError("invalid lease expiry")
        if acquired > _stamp(state["updated_at"]):
            raise ValueError("attempt starts after state update")
    return state


class PollingGuard:
    """One conditional acquisition, policy update and release; no retry loops."""

    def __init__(self, *, s3, bucket, clock=None, sleeper=None):
        self.s3 = s3
        self.bucket = bucket
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper = sleeper or time.sleep
        self.state = None
        self.etag = None

    def _read(self):
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=CONTROL_KEY)
            stream = response["Body"]
            try:
                raw = stream.read(16385)
            finally:
                stream.close()
            if len(raw) > 16384:
                raise ValueError("control object exceeds 16 KiB")
            state = _validate(json.loads(raw, object_pairs_hook=_unique_object))
            etag = _etag(response.get("ETag"))
            if _stamp(state["updated_at"]) > self.clock():
                raise ValueError("control update is in the future")
            return state, etag
        except Exception as error:
            raise ControlError("cannot read valid control state; operator inspection required") from error

    def _write(self, state):
        try:
            state = {**state, "revision": str(uuid4()), "updated_at": _utc(self.clock())}
            _validate(state)
            response = self.s3.put_object(
                Bucket=self.bucket, Key=CONTROL_KEY,
                Body=json.dumps(state, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(),
                ContentType="application/json", IfMatch=self.etag,
            )
            self.etag = _etag(response.get("ETag"))
            self.state = state
        except Exception as error:
            raise ControlError("conditional control update failed; no automatic retry") from error

    def acquire(self, collection_id, *, remaining_millis=None):
        return self._acquire(collection_id, remaining_millis, allow_wait=True)

    def _acquire(self, collection_id, remaining_millis, *, allow_wait):
        state, self.etag = self._read()
        self.state = state
        current = self.clock()
        if state["halt_reason"]:
            return "halted: " + state["halt_reason"]
        if state["attempt"]:
            if current < _stamp(state["attempt"]["expires_at"]):
                return "in_flight"
            try:
                self._write({**state, "halt_reason": "stale in-flight attempt; response/restrictions unknown"})
            except ControlError as error:
                cause = getattr(error.__cause__, "response", {})
                if cause.get("ResponseMetadata", {}).get("HTTPStatusCode") in (409, 412):
                    return "control_conflict"
                raise
            return "halted: stale in-flight attempt; operator review required"
        if current < _stamp(state["not_before"]):
            delay = (_stamp(state["not_before"]) - current).total_seconds()
            if (allow_wait and state["spacing_only"] and delay <= MAX_WAIT_SECONDS
                    and remaining_millis is not None
                    and remaining_millis() >= (delay + WAIT_RESERVE_SECONDS) * 1000):
                logger.info("waiting for minimum request spacing seconds=%s", delay)
                self.sleeper(delay)
                # re-read all restrictions and acquire conditionally; never wait twice
                return self._acquire(collection_id, remaining_millis, allow_wait=False)
            return "not_before=" + state["not_before"]
        if remaining_millis is not None and remaining_millis() < WAIT_RESERVE_SECONDS * 1000:
            return "insufficient_execution_time"
        attempt = {"collection_id": str(collection_id), "acquired_at": _utc(current),
                   "expires_at": _utc(current + timedelta(seconds=LEASE_SECONDS))}
        try:
            self._write({**state, "attempt": attempt})
        except ControlError as error:
            cause = getattr(error.__cause__, "response", {})
            if cause.get("ResponseMetadata", {}).get("HTTPStatusCode") in (409, 412):
                return "control_conflict"
            raise
        return None

    def record_outcome(self, result):
        """Persist restrictions before evidence, while retaining the in-flight guard."""
        deadline = max(self.clock(), result.retrieved_at) + timedelta(seconds=MINIMUM_SPACING_SECONDS)
        reason = None
        if result.http_status in (401, 403):
            reason = f"HTTP {result.http_status}; operator intervention required"
        elif result.http_status == 429:
            if result.retry_after_at is None or result.retry_after_error:
                reason = "HTTP 429 without valid Retry-After; operator intervention required"
            else:
                deadline = max(deadline, _stamp(result.retry_after_at))
        elif result.http_status is not None and result.http_status >= 500 and result.retry_after_at:
            deadline = max(deadline, _stamp(result.retry_after_at))
        spacing_only = reason is None and result.http_status != 429 and not (
            result.http_status is not None and result.http_status >= 500 and result.retry_after_at)
        self._write({**self.state, "not_before": _utc(deadline), "halt_reason": reason,
                     "spacing_only": spacing_only})

    def finish(self):
        """Release only after both evidence writes; failures leave an uncertain attempt."""
        deadline = max(_stamp(self.state["not_before"]), self.clock() + timedelta(seconds=MINIMUM_SPACING_SECONDS))
        self._write({**self.state, "attempt": None, "not_before": _utc(deadline)})
