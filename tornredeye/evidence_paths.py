"""Fresh operational lineage and immutable, UTC-partitioned object identities."""

from datetime import datetime, timedelta, timezone
import re
from uuid import UUID

LINEAGE = "live-v1"
EVIDENCE_PREFIX = f"evidence/{LINEAGE}/"
FORECAST_PREFIX = f"forecasts/{LINEAGE}/records/"
STATE_PREFIX = f"forecast-state/{LINEAGE}/"
PREFIXES = (EVIDENCE_PREFIX, FORECAST_PREFIX)


def evidence_keys(retrieved_at, collection_id):
    if not isinstance(collection_id, UUID) or retrieved_at.utcoffset() is None:
        raise ValueError("original aware retrieval time and UUID are required")
    directory = EVIDENCE_PREFIX + retrieved_at.astimezone(timezone.utc).strftime("%Y/%m/%d/%H/")
    directory += collection_id.hex + "/"
    return directory + "raw.bin", directory + "attempt.json"


def evidence_identity(key):
    match = re.fullmatch(re.escape(EVIDENCE_PREFIX)
                         + r"(\d{4}/\d{2}/\d{2}/\d{2})/([0-9a-f]{32})/(raw\.bin|attempt\.json)", key)
    if not match:
        raise ValueError("invalid operational evidence key")
    partition = datetime.strptime(match[1], "%Y/%m/%d/%H").replace(tzinfo=timezone.utc)
    return partition, UUID(hex=match[2]), match[3]


def is_raw(key):
    return key.startswith(EVIDENCE_PREFIX) and key.endswith("/raw.bin")


def is_normalized(key):
    return key.startswith(EVIDENCE_PREFIX) and key.endswith("/attempt.json")


def counterpart(key):
    _, _, kind = evidence_identity(key)
    return key.rsplit("/", 1)[0] + ("/attempt.json" if kind == "raw.bin" else "/raw.bin")


def window_prefixes(start, cutoff):
    if start.utcoffset() is None or cutoff.utcoffset() is None or start > cutoff:
        raise ValueError("invalid UTC evidence window")
    hour = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = cutoff.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    while hour <= end:
        yield EVIDENCE_PREFIX + hour.strftime("%Y/%m/%d/%H/")
        hour += timedelta(hours=1)


def validate_backup_key(key):
    if not isinstance(key, str):
        raise ValueError("invalid backup key")
    if key.startswith(EVIDENCE_PREFIX):
        evidence_identity(key)
    else:
        requested = re.fullmatch(re.escape(FORECAST_PREFIX)
                                 + r"requested-[a-z0-9][a-z0-9-]{0,52}/requests/[0-9a-f]{64}/"
                                 + r"(forecast|ack|outcome)\.json", key)
        if requested:
            return key
        match = re.fullmatch(re.escape(FORECAST_PREFIX)
                             + r"[a-z0-9][a-z0-9-]{0,62}/(\d{4}-\d{2}-\d{2}T\d{2})/"
                             + r"(forecast|ack|outcome|checkpoint)\.json", key)
        if not match:
            raise ValueError("object outside immutable operational backup scope")
        datetime.strptime(match[1], "%Y-%m-%dT%H")
    return key
