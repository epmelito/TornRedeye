"""Allowlisted stock publication from exact, persisted normalized evidence keys."""

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import unquote_plus

from tornredeye.evidence_paths import LINEAGE, evidence_identity

KEY = "public/stock.json"
MAX_BYTES = 256 * 1024
EVIDENCE_BYTES = 1024 * 1024
MAX_ITEMS = 4096
SAFE_INTEGER = 2 ** 53 - 1


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("aware time required")
    return parsed.astimezone(timezone.utc)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _number(value):
    return value if type(value) is int and 0 <= value <= SAFE_INTEGER else None


def _source_time(value, retrieved):
    if type(value) is not int or value <= 0:
        return None
    try:
        parsed = datetime.fromtimestamp(value, timezone.utc)
        return _stamp(parsed) if parsed <= retrieved else None
    except (ValueError, OverflowError, OSError):
        return None


def _name(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        return None
    return " ".join(value.split())


def _code(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,15}", value):
        raise ValueError("invalid country key")
    return value


def _old_countries(previous):
    if previous is None:
        return {}
    if (previous.get("kind") != "stock_snapshot" or type(previous.get("schema_version")) is not int
            or previous["schema_version"] != 1 or not isinstance(previous.get("countries"), list)):
        raise ValueError("invalid previous stock snapshot")
    _time(previous["retrieved_at_utc"])
    result, count = {}, 0
    for country in previous["countries"]:
        code = _code(country["code"])
        if code in result or len(result) >= 64:
            raise ValueError("ambiguous or excessive countries")
        items = {}
        for item in country["items"]:
            ident = item["id"]
            if _number(ident) is None or ident == 0 or ident in items:
                raise ValueError("ambiguous item identity")
            items[ident] = item
            count += 1
        result[code] = {**country, "items": items}
    if count > MAX_ITEMS:
        raise ValueError("too many items")
    return result


def _comparison(item, old, source, old_source, retrieved, old_retrieved):
    result = {"state": "unavailable", "quantity_change": None, "price_change": None,
              "source_interval_utc": None, "retrieval_interval_utc": None}
    if not old or not source or not old_source or not item["reported"] or not old.get("reported"):
        return result
    before, after = _time(old_source), _time(source)
    if after < before:
        result["state"] = "source_regressed"
    elif after == before:
        result["state"] = "cached"
        prior = old.get("comparison", {})
        if (prior.get("state") == "source_conflict"
                or any(item[k] is not None and old.get(k) is not None and item[k] != old[k]
                       for k in ("quantity", "price"))):
            result["state"] = "source_conflict"
        elif prior.get("state") == "source_regressed":
            result["state"] = "source_regressed"
        elif (prior.get("state") in ("compared", "cached")
                and all(item[k] == old.get(k) for k in ("quantity", "price"))
                and any(type(prior.get(k)) is int and prior[k] != 0
                        for k in ("quantity_change", "price_change"))):
            for key in ("quantity_change", "price_change", "source_interval_utc", "retrieval_interval_utc"):
                result[key] = prior[key]
    else:
        if old.get("comparison", {}).get("state") in ("source_conflict", "source_regressed"):
            return result
        result["state"] = "compared"
        result["source_interval_utc"] = [old_source, source]
        result["retrieval_interval_utc"] = [old_retrieved, retrieved]
        for field in ("quantity", "price"):
            if item[field] is not None and _number(old.get(field)) is not None:
                result[field + "_change"] = item[field] - old[field]
    return result


def build_snapshot(document, now, previous=None):
    """Compare adjacent published reports; missing fields never become zero."""
    if (document.get("format") != "stock-evidence" or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or document.get("lineage") != LINEAGE
            or document.get("validation_status") not in ("complete", "partial", "malformed", "collection_failed")
            or document.get("acquisition", {}).get("status") not in ("success", "collection_failed")):
        raise ValueError("invalid normalized stock contract")
    retrieved = _time(document["retrieved_at"])
    if now.utcoffset() is None or retrieved > now:
        raise ValueError("invalid retrieval time")
    old = _old_countries(previous)
    status = document["validation_status"]
    usable = document["acquisition"]["status"] == "success" and status in ("complete", "partial")
    current = document.get("countries") if usable else {}
    if not isinstance(current, dict) or len(current) > 64:
        raise ValueError("invalid country records")
    result = {"kind": "stock_snapshot", "schema_version": 1, "published_at_utc": _stamp(now),
              "retrieved_at_utc": _stamp(retrieved),
              "export_timestamp_utc": _source_time(document.get("export_timestamp"), retrieved),
              "state": status if usable else "collection_failed" if document["acquisition"]["status"] == "collection_failed" else "unavailable",
              "countries": []}
    count = 0
    for code in sorted(set(current) | set(old)):
        _code(code)
        record = current.get(code)
        if record is not None and (not isinstance(record, dict) or not isinstance(record.get("items"), list)):
            raise ValueError("invalid country record")
        source = _source_time(record.get("source_timestamp"), retrieved) if record else None
        before = old.get(code, {})
        items = {}
        for item in record["items"] if record else []:
            ident = item.get("item_id")
            if _number(ident) is None or ident == 0 or ident in items:
                raise ValueError("ambiguous item identity")
            items[ident] = item
        public = {"code": code, "source_timestamp_utc": source,
                  "reported": record is not None, "items": []}
        for ident in sorted(set(items) | set(before.get("items", {}))):
            value, prior = items.get(ident), before.get("items", {}).get(ident)
            row = {"id": ident, "name": _name(value.get("name")) if value else _name(prior.get("name")),
                   "quantity": _number(value.get("quantity")) if value else None,
                   "price": _number(value.get("cost")) if value else None, "reported": value is not None}
            row["comparison"] = _comparison(row, prior, source, before.get("source_timestamp_utc"),
                                              result["retrieved_at_utc"], previous["retrieved_at_utc"] if previous else None)
            public["items"].append(row)
            count += 1
            if value is None or any(row[k] is None for k in ("name", "quantity", "price")):
                if usable:
                    result["state"] = "partial"
        if (not source or record is None) and usable:
            result["state"] = "partial"
        result["countries"].append(public)
    if count > MAX_ITEMS or len(result["countries"]) > 64:
        raise ValueError("stock snapshot exceeds population bound")
    return result


def _read(response, limit):
    stream = response["Body"]
    try:
        body = stream.read(limit + 1)
    finally:
        stream.close()
    if len(body) > limit:
        raise ValueError("stock read exceeds byte bound")
    return body


def publish(s3, config, key, now, budget=lambda: None):
    """Read one canonical attempt and conditionally replace one public object."""
    partition, identifier, kind = evidence_identity(key)
    if kind != "attempt.json":
        raise ValueError("normalized attempt key required")
    budget()
    response = s3.get_object(Bucket=config["EVIDENCE_BUCKET"], Key=key)
    body = _read(response, EVIDENCE_BYTES)
    if response.get("Metadata", {}).get("sha256") != hashlib.sha256(body).hexdigest():
        raise ValueError("normalized evidence digest mismatch")
    document = json.loads(body)
    retrieved = _time(document["retrieved_at"])
    if (str(document.get("collection_id", "")).replace("-", "") != identifier.hex
            or retrieved.replace(minute=0, second=0, microsecond=0) != partition):
        raise ValueError("normalized evidence identity mismatch")
    old, condition = None, {"IfNoneMatch": "*"}
    budget()
    try:
        response = s3.get_object(Bucket=config["DASHBOARD_BUCKET"], Key=KEY)
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") not in ("NoSuchKey", "404", "AccessDenied", "403"):
            raise
    else:
        old = json.loads(_read(response, MAX_BYTES))
        _old_countries(old)
        if _time(old["retrieved_at_utc"]) >= retrieved:
            return False
        condition = {"IfMatch": response["ETag"]}
    snapshot = build_snapshot(document, now, old)
    body = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise ValueError("public stock snapshot exceeds byte bound")
    budget()
    s3.put_object(Bucket=config["DASHBOARD_BUCKET"], Key=KEY, Body=body,
                  ContentType="application/json", CacheControl="public,max-age=15", **condition)
    return True


def process_event(s3, config, event, now, budget=lambda: None):
    """S3 notifications are hints; validate exact bucket, key, bytes and identity."""
    if event.get("Event") == "s3:TestEvent":
        return {"updated": 0}
    records = event.get("Records")
    if not isinstance(records, list) or not 1 <= len(records) <= 10:
        raise ValueError("invalid stock event batch")
    keys = []
    for record in records:
        if (record.get("eventSource") != "aws:s3" or not record.get("eventName", "").startswith("ObjectCreated:")
                or record["s3"]["bucket"]["name"] != config["EVIDENCE_BUCKET"]):
            raise ValueError("unexpected stock event source")
        key = unquote_plus(record["s3"]["object"]["key"])
        if evidence_identity(key)[2] != "attempt.json":
            raise ValueError("unexpected stock event key")
        keys.append(key)
    return {"updated": sum(publish(s3, config, key, now, budget) for key in dict.fromkeys(keys))}
