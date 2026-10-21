"""Shared deterministic YATA export normalization; no I/O or clock reads."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json


_DUPLICATE = object()


def _object(pairs):
    result = {}
    for key, value in pairs:
        result[key] = _DUPLICATE if key in result else value
    return result


def _invalid_constant(value):
    raise ValueError("nonstandard JSON constant")


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _envelope(raw_response, retrieved_at, raw_evidence_id, http_status):
    result = {
        "schema_version": 1, "format": "yata-all-stock", "source": "YATA",
        "source_url": "https://yata.yt/api/v1/travel/export/",
        "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z"),
        "http_status": http_status, "export_timestamp": None,
        "raw_evidence": {
            "identity": raw_evidence_id,
            "sha256": hashlib.sha256(raw_response).hexdigest(),
            "size_bytes": len(raw_response),
        } if raw_response is not None else None,
        "status": "complete", "countries": {}, "diagnostics": [],
    }

    return result


def normalize_export(
    raw_response: bytes, retrieved_at: datetime, *, raw_evidence_id: str,
    http_status: int, acquisition_status: str = "success",
) -> bytes:
    """Return one deterministic compact UTF-8 JSON document; perform no I/O.

    Supply the original retrieval time and raw evidence identity, not processing
    time. HTTP status must be supplied explicitly from the original retrieval.
    The hash binds the result to the exact input bytes. Invalid fields
    become null with diagnostics; unidentifiable or duplicate items are omitted.
    Country update zero means unknown, not an epoch observation or fresh stock.
    A complete result describes validation only, never source coverage/freshness.
    """
    if not isinstance(raw_response, bytes):
        raise ValueError("raw_response must be bytes")
    if not isinstance(retrieved_at, datetime) or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone aware")
    if not isinstance(raw_evidence_id, str) or not raw_evidence_id.strip():
        raise ValueError("raw_evidence_id must be a nonempty string")
    if type(http_status) is not int or not 100 <= http_status <= 599:
        raise ValueError("http_status must be an integer from 100 to 599")
    result = _envelope(raw_response, retrieved_at, raw_evidence_id, http_status)

    def diagnostic(path, code):
        result["diagnostics"].append({"path": path, "code": code})

    def field(record, key, path, valid):
        if key not in record:
            diagnostic(path + [key], "missing")
        elif record[key] is _DUPLICATE:
            diagnostic(path + [key], "duplicate")
        elif not valid(record[key]):
            diagnostic(path + [key], "invalid")
        else:
            return record[key]
        return None

    def finish():
        if result["status"] == "complete" and result["diagnostics"]:
            result["status"] = "partial"
        return json.dumps(
            result, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")

    if acquisition_status not in ("success", "collection_failed"):
        raise ValueError("invalid acquisition status")
    if acquisition_status == "collection_failed" or not 200 <= http_status < 300:
        result["status"] = "collection_failed"
        diagnostic([], "http_failure")
        return finish()
    try:
        payload = json.loads(
            raw_response, parse_constant=_invalid_constant, object_pairs_hook=_object
        )
    except (ValueError, UnicodeDecodeError, RecursionError):
        result["status"] = "malformed"
        diagnostic([], "invalid_json")
        return finish()
    if not isinstance(payload, dict):
        result["status"] = "malformed"
        diagnostic([], "invalid")
        return finish()
    result["export_timestamp"] = field(payload, "timestamp", [], _integer)
    countries = field(payload, "stocks", [], lambda v: isinstance(v, dict))
    if countries is None:
        result["status"] = "malformed"
        return finish()
    for country in sorted(countries):
        path = ["stocks", country]
        if not country.strip():
            diagnostic(path, "invalid_country")
            continue
        record = field(countries, country, ["stocks"], lambda v: isinstance(v, dict))
        if record is None:
            continue
        normalized = {"source_timestamp": field(record, "update", path, _integer), "items": []}
        result["countries"][country] = normalized
        items = field(record, "stocks", path, lambda v: isinstance(v, list))
        if items is None:
            continue
        counts = Counter(
            item["id"] for item in items
            if isinstance(item, dict) and _integer(item.get("id"), 1)
        )
        for index, item in enumerate(items):
            item_path = path + ["stocks", index]
            if not isinstance(item, dict):
                diagnostic(item_path, "invalid")
                continue
            item_id = field(item, "id", item_path, lambda v: _integer(v, 1))
            if item_id is None:
                continue
            if counts[item_id] > 1:
                diagnostic(item_path + ["id"], "duplicate_item_id")
                continue
            normalized["items"].append({
                "item_id": item_id,
                "name": field(item, "name", item_path, lambda v: isinstance(v, str) and bool(v.strip())),
                "quantity": field(item, "quantity", item_path, _integer),
                "cost": field(item, "cost", item_path, _integer),
            })
        normalized["items"].sort(key=lambda item: item["item_id"])
    return finish()


def normalize_collection(result, *, raw_evidence_id):
    """YATA acquisition adapter into the source-neutral stock-evidence contract."""
    if result.status not in ("success", "collection_failed"):
        raise ValueError("unknown acquisition status")
    if result.source != "YATA" or not isinstance(result.source_url, str) or not result.source_url.strip():
        raise ValueError("unsupported acquisition adapter or missing source locator")
    if result.retrieved_at.utcoffset() is None:
        raise ValueError("original aware retrieval time is required")
    if result.http_status is not None and (type(result.http_status) is not int
                                         or not 100 <= result.http_status <= 599):
        raise ValueError("invalid original HTTP status")
    if result.status == "success" and (result.http_status is None or not 200 <= result.http_status < 300):
        raise ValueError("successful acquisition requires a successful HTTP response")
    if result.raw_response is None:
        if raw_evidence_id is not None or result.status != "collection_failed":
            raise ValueError("absent response must be a failure without a raw identity")
        document = _envelope(None, result.retrieved_at, None, result.http_status)
        document.update(status="collection_failed", diagnostics=[{"path": [], "code": "collection_failure"}])
    else:
        if result.http_status is None and result.status == "collection_failed":
            if not isinstance(result.raw_response, bytes) or not raw_evidence_id:
                raise ValueError("invalid retained failure evidence")
            document = _envelope(result.raw_response, result.retrieved_at, raw_evidence_id, None)
            document.update(status="collection_failed", diagnostics=[{"path": [], "code": "collection_failure"}])
        else:
            document = json.loads(normalize_export(
                result.raw_response, result.retrieved_at, raw_evidence_id=raw_evidence_id,
                http_status=result.http_status, acquisition_status=result.status))
    document.update(format="stock-evidence", source=result.source, source_url=result.source_url)
    document["validation_status"] = document.pop("status")
    document["acquisition"] = {
        "status": result.status, "detail": result.detail, "response_headers": result.response_headers,
        "retry_after_at": result.retry_after_at, "retry_after_error": result.retry_after_error,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def normalized_target_fields(normalized, retrieved, country, item_id):
    """Shared target validation for retained raw exports, without I/O."""
    row = {"issues": [], "quantity": None, "name": None, "cost": None}
    row["normalization_status"] = normalized.get("validation_status", normalized.get("status"))
    row["diagnostics"] = normalized["diagnostics"]
    row["export_timestamp"] = normalized["export_timestamp"]
    country_record = normalized["countries"].get(country, {})
    row["source_timestamp"] = country_record.get("source_timestamp")
    item = next((item for item in country_record.get("items", []) if item["item_id"] == item_id), None)
    if item is None:
        row["issues"].append("target_missing_or_invalid")
    else:
        row.update({k: item[k] for k in ("quantity", "name", "cost")})
        if any(item[k] is None for k in ("quantity", "name", "cost")):
            row["issues"].append("invalid_target_fields")
    for field in ("source", "export"):
        stamp = row[field + "_timestamp"]
        if stamp is None or (field == "source" and stamp == 0):
            row["issues"].append(field + "_time_unknown")
        else:
            age = retrieved.timestamp() - stamp
            row[field + "_age_seconds"] = age
            if age < 0:
                row["issues"].append(field + "_time_in_future")
    return row
