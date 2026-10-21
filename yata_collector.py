"""Acquire one complete YATA export without persistence or stock validation.

Call collect() once per invocation. Preserve original bytes, UTC retrieval time,
source identity and acquisition/restriction context. Export validation and
country/item observations belong to the shared normalization adapter.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException, IncompleteRead
import math
import re
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SOURCE_URL = "https://yata.yt/api/v1/travel/export/"


@dataclass(frozen=True)
class CollectionResult:
    status: Literal["success", "collection_failed"]
    retrieved_at: datetime
    raw_response: bytes | None
    http_status: int | None = None
    source: str = "YATA"
    source_url: str = SOURCE_URL
    detail: str | None = None
    response_headers: tuple[tuple[str, str], ...] = ()
    retry_after_at: str | None = None
    retry_after_error: str | None = None


def normalize(raw_response: bytes, retrieved_at: datetime, http_status: int = 200) -> CollectionResult:
    """Capture acquisition context; export validation belongs to normalization."""
    if retrieved_at.utcoffset() is None or not isinstance(raw_response, bytes):
        raise ValueError("original bytes and aware retrieval time are required")
    if type(http_status) is not int or not 100 <= http_status <= 599:
        raise ValueError("invalid HTTP status")
    return CollectionResult(
        status="success" if 200 <= http_status < 300 else "collection_failed",
        retrieved_at=retrieved_at.astimezone(timezone.utc), raw_response=raw_response,
        http_status=http_status, detail=None if 200 <= http_status < 300 else f"HTTP {http_status}")


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
