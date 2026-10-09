"""Collect YATA Japan Xanax evidence without persistence or event inference.

Call collect() once per invocation. The returned envelope binds the raw bytes,
UTC retrieval time, provenance, source timestamps, and optional observation.
A caller must inspect status before consuming observation. Timestamp ages can
help assess freshness; no freshness cutoff or exact stock event time is assumed.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException, IncompleteRead
import json
import math
import re
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SOURCE_URL = "https://yata.yt/api/v1/travel/export/"
COUNTRY = "jap"
ITEM_ID = 206


@dataclass(frozen=True)
class Observation:
    item_id: int
    name: str
    quantity: int
    cost: int


@dataclass(frozen=True)
class CollectionResult:
    status: Literal["observed", "missing", "malformed", "collection_failed"]
    retrieved_at: datetime
    raw_response: bytes | None
    http_status: int | None = None
    source: str = "YATA"
    source_url: str = SOURCE_URL
    country: str = COUNTRY
    item_id: int = ITEM_ID
    source_path: str = "stocks.jap.stocks"
    export_timestamp: int | None = None
    source_timestamp: int | None = None
    observation: Observation | None = None
    detail: str | None = None
    response_headers: tuple[tuple[str, str], ...] = ()
    retry_after_at: str | None = None
    retry_after_error: str | None = None

    @property
    def source_age_seconds(self) -> float | None:
        """Age of Japan's update; zero means no known update, not Unix epoch."""
        if not self.source_timestamp:
            return None
        return self.retrieved_at.timestamp() - self.source_timestamp

    @property
    def export_age_seconds(self) -> float | None:
        if self.export_timestamp is None:
            return None
        return self.retrieved_at.timestamp() - self.export_timestamp


def _integer(value: object, path: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{path} must be an integer >= {minimum}")
    return value


def _object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _invalid_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def normalize(
    raw_response: bytes, retrieved_at: datetime, http_status: int = 200
) -> CollectionResult:
    """Validate a complete response, preserving bytes and available timestamps.

    Missing Japan or Xanax records are not observed zero. Missing required
    structure/fields and ambiguous duplicate Xanax records are malformed.
    Source timestamps are Unix seconds reported by YATA, not event times.
    Aware retrieval times are converted to UTC; naive times are rejected.
    """
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone aware")
    result = CollectionResult(
        status="malformed",
        retrieved_at=retrieved_at.astimezone(timezone.utc),
        raw_response=raw_response,
        http_status=http_status,
    )
    if not 200 <= http_status < 300:
        return replace(result, status="collection_failed", detail=f"HTTP {http_status}")
    try:
        payload = _object(
            json.loads(raw_response, parse_constant=_invalid_constant), "response"
        )
        # retain independently valid timestamps even if later fields fail
        export_timestamp = payload.get("timestamp")
        if type(export_timestamp) is int and export_timestamp >= 0:
            result = replace(result, export_timestamp=export_timestamp)
        stocks = _object(payload.get("stocks"), "stocks")
        japan = stocks.get(COUNTRY)
        if isinstance(japan, dict):
            source_timestamp = japan.get("update")
            if type(source_timestamp) is int and source_timestamp >= 0:
                result = replace(result, source_timestamp=source_timestamp)
        _integer(export_timestamp, "timestamp")
        if COUNTRY not in stocks:
            return replace(result, status="missing", detail="Japan record absent")
        japan = _object(japan, "stocks.jap")
        _integer(japan.get("update"), "stocks.jap.update")
        items = japan.get("stocks")
        if not isinstance(items, list):
            raise ValueError("stocks.jap.stocks must be a list")
        target = None
        for index, item in enumerate(items):
            path = f"stocks.jap.stocks[{index}]"
            item = _object(item, path)
            item_id = _integer(item.get("id"), f"{path}.id", minimum=1)
            if item_id != ITEM_ID:
                continue
            if target is not None:
                raise ValueError("duplicate Xanax records")
            target = item
        if target is None:
            return replace(result, status="missing", detail="Xanax record absent")
        name = target.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Xanax name must be a nonempty string")
        observation = Observation(
            item_id=ITEM_ID,
            name=name,
            quantity=_integer(target.get("quantity"), "Xanax quantity"),
            cost=_integer(target.get("cost"), "Xanax cost"),
        )
        return replace(result, status="observed", observation=observation)
    except (ValueError, UnicodeDecodeError) as error:
        return replace(result, detail=f"{type(error).__name__}: {error}")


def _http_date(value: str) -> datetime:
    """Accept the three HTTP-date formats, including obsolete asctime UTC."""
    patterns = (
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), [0-9]{2} [A-Z][a-z]{2} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT",
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), [0-9]{2}-[A-Z][a-z]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} GMT",
        r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) [A-Z][a-z]{2} (?: [0-9]|[0-9]{2}) [0-9]{2}:[0-9]{2}:[0-9]{2} [0-9]{4}",
    )
    if not any(re.fullmatch(pattern, value) for pattern in patterns):
        raise ValueError("invalid HTTP-date")
    parsed = parsedate_to_datetime(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _with_headers(result: CollectionResult, headers) -> CollectionResult:
    retained = tuple((key, value) for key, value in headers
                     if key.lower() in ("retry-after", "date"))
    result = replace(result, response_headers=retained)
    values = [value.strip() for key, value in retained if key.lower() == "retry-after"]
    if not values:
        return result
    try:
        if len(values) != 1:
            raise ValueError("ambiguous Retry-After")
        value = values[0]
        if value.isascii() and value.isdigit():
            deadline = result.retrieved_at + timedelta(seconds=int(value))
        else:
            deadline = _http_date(value)
            dates = [value.strip() for key, value in retained if key.lower() == "date"]
            if len(dates) == 1:
                try:
                    server_now = _http_date(dates[0])
                except (ValueError, OverflowError):
                    server_now = None
                if server_now is not None:
                    deadline = max(deadline, result.retrieved_at + max(deadline - server_now, timedelta()))
            deadline = max(deadline, result.retrieved_at)
        return replace(result, retry_after_at=deadline.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
    except (ValueError, OverflowError) as error:
        return replace(result, retry_after_error=str(error))


def collect(timeout: float = 15.0) -> CollectionResult:
    """Fetch once with a bounded timeout; return evidence or a visible failure.

    No retries, storage, or logging are performed. HTTP error bodies and partial
    bodies from interrupted reads are retained when available. Unexpected code
    errors propagate rather than being disguised as source failures.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite positive number")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number")
    raw_response = None
    http_status = None
    response_headers = ()
    request = Request(
        SOURCE_URL,
        headers={"User-Agent": "TornRedeye/0.1", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            http_status = response.status
            response_headers = tuple(response.headers.items())
            raw_response = response.read()
    except HTTPError as error:
        http_status = error.code
        response_headers = tuple(error.headers.items()) if error.headers is not None else ()
        detail = f"HTTPError: {error}"
        try:
            raw_response = error.read()
        except IncompleteRead as read_error:
            raw_response = read_error.partial
            detail += f"; IncompleteRead: {read_error}"
        except (OSError, URLError, HTTPException) as read_error:
            detail += f"; {type(read_error).__name__}: {read_error}"
        finally:
            error.close()
    except IncompleteRead as error:
        raw_response = error.partial
        detail = f"IncompleteRead: {error}"
    except (OSError, URLError, HTTPException) as error:
        detail = f"{type(error).__name__}: {error}"
    else:
        return _with_headers(normalize(raw_response, datetime.now(timezone.utc), http_status), response_headers)
    return _with_headers(CollectionResult(
        status="collection_failed",
        retrieved_at=datetime.now(timezone.utc),
        raw_response=raw_response,
        http_status=http_status,
        detail=detail,
    ), response_headers)
