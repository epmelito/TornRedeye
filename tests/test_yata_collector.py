"""Deterministic contract and transport tests; never call the live API."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.message import Message
from http.client import BadStatusLine, IncompleteRead
from io import BytesIO
import json
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import yata_collector as collector


NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
EXPORT_TIMESTAMP = int(NOW.timestamp()) - 60
SOURCE_TIMESTAMP = int(NOW.timestamp()) - 300
PAYLOAD = {
    "timestamp": EXPORT_TIMESTAMP,
    "stocks": {
        "jap": {
            "update": SOURCE_TIMESTAMP,
            "stocks": [
                {"id": 205, "name": "Other item", "quantity": 1, "cost": 100},
                {"id": 206, "name": "Xanax", "quantity": 42, "cost": 800000},
            ],
        },
        "can": {"update": SOURCE_TIMESTAMP + 100, "stocks": []},
    },
}


def encode(payload):
    return json.dumps(payload, indent=2).encode("utf-8")


class AcquisitionTests(unittest.TestCase):
    def test_acquisition_is_independent_of_export_and_country_validation(self):
        for raw in (b"invalid", b"{}", encode(PAYLOAD), b""):
            result = collector.normalize(raw, NOW)
            self.assertEqual(result.status, "success")
            self.assertEqual(result.raw_response, raw)
            self.assertFalse(hasattr(result, "country"))
            self.assertFalse(hasattr(result, "observation"))
        with self.assertRaises(ValueError):
            collector.normalize(b"{}", NOW.replace(tzinfo=None))

class CollectTests(unittest.TestCase):
    def setUp(self):
        self.urlopen = patch("yata_collector.urlopen").start()
        self.addCleanup(patch.stopall)
        self.clock = patch("yata_collector.datetime").start()
        self.clock.now.return_value = NOW
        self.response = MagicMock()
        self.response.status = 200
        self.response.read.return_value = encode(PAYLOAD)
        self.urlopen.return_value.__enter__.return_value = self.response

    def test_success_uses_identified_get_request_timeout_and_closes_response(self):
        result = collector.collect(timeout=5)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.retrieved_at, NOW)
        self.urlopen.assert_called_once()
        request = self.urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://yata.yt/api/v1/travel/export/")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(
            {name.lower(): value for name, value in request.header_items()},
            {"user-agent": "TornRedeye/0.1", "accept": "application/json"},
        )
        self.assertEqual(self.urlopen.call_args.kwargs, {"timeout": 5})
        self.clock.now.assert_called_once_with(timezone.utc)
        self.urlopen.return_value.__exit__.assert_called_once()

    def test_default_timeout_remains_fifteen_seconds(self):
        collector.collect()
        self.urlopen.assert_called_once()
        self.assertEqual(self.urlopen.call_args.kwargs, {"timeout": 15.0})

    def test_retry_after_delay_and_http_date_are_retained_and_parsed(self):
        cases = (
            ("120", "2026-10-08T12:02:00Z"),
            ("0", "2026-10-08T12:00:00Z"),
            ("00120", "2026-10-08T12:02:00Z"),
            ("Thu, 08 Oct 2026 12:02:00 GMT", "2026-10-08T12:02:00Z"),
            ("Thursday, 08-Oct-26 12:02:00 GMT", "2026-10-08T12:02:00Z"),
            ("Thu Oct  8 12:02:00 2026", "2026-10-08T12:02:00Z"),
            ("Thu, 08 Oct 2026 11:00:00 GMT", "2026-10-08T12:00:00Z"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                headers = Message()
                headers["Retry-After"] = value
                headers["X-Unrelated"] = "ignored"
                self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 429, "limited", headers, BytesIO(b"limit"))
                result = collector.collect()
                self.assertEqual(result.status, "collection_failed")
                self.assertEqual(result.http_status, 429)
                self.assertEqual(result.raw_response, b"limit")
                self.assertEqual(result.response_headers, (("Retry-After", value),))
                self.assertEqual(result.retry_after_at, expected)
                self.assertIsNone(result.retry_after_error)

    def test_retry_after_missing_invalid_duplicate_and_date_clock_skew(self):
        for value in ("", "-1", "1.5", "NaN", "tomorrow", "2026-10-08T12:00:00Z", "9" * 100):
            with self.subTest(value=value):
                self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 429, "limited", {"Retry-After": value}, BytesIO(b"limit"))
                result = collector.collect()
                self.assertIsNone(result.retry_after_at)
                self.assertTrue(result.retry_after_error)
        headers = Message()
        headers["Retry-After"] = "1"
        headers["Retry-After"] = "2"
        self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 429, "limited", headers, BytesIO())
        self.assertIn("ambiguous", collector.collect().retry_after_error)
        self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 429, "limited", {}, BytesIO())
        self.assertIsNone(collector.collect().retry_after_at)
        headers = {"Retry-After": "Thu, 08 Oct 2026 12:02:00 GMT", "Date": "Thu, 08 Oct 2026 11:00:00 GMT"}
        self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 503, "unavailable", headers, BytesIO())
        self.assertEqual(collector.collect().retry_after_at, "2026-10-08T13:02:00Z")

    def test_headers_survive_error_body_read_failure(self):
        body = MagicMock()
        body.read.side_effect = IncompleteRead(b"partial", 10)
        self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 429, "limited", {"Retry-After": "120"}, body)
        result = collector.collect()
        self.assertEqual(result.raw_response, b"partial")
        self.assertEqual(result.retry_after_at, "2026-10-08T12:02:00Z")

    def test_malformed_success_retains_body(self):
        self.response.read.return_value = b'{"stocks":'
        result = collector.collect()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.raw_response, b'{"stocks":')
        self.assertEqual(result.http_status, 200)

    def test_http_error_retains_error_body_and_status(self):
        body = BytesIO(b'{"error": "rate limited"}')
        self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 429, "Too Many Requests", {}, body)
        result = collector.collect()
        self.assertEqual(result.status, "collection_failed")
        self.assertEqual(result.http_status, 429)
        self.assertEqual(result.raw_response, b'{"error": "rate limited"}')
        self.assertIn("429", result.detail)
        self.assertTrue(body.closed)

    def test_http_error_body_read_failure_remains_diagnosable(self):
        for error in (OSError("body read failed"), IncompleteRead(b"partial", 10)):
            with self.subTest(error=error):
                body = MagicMock()
                body.read.side_effect = error
                self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 503, "Unavailable", {}, body)
                result = collector.collect()
                self.assertEqual(result.status, "collection_failed")
                self.assertEqual(result.http_status, 503)
                self.assertIn(type(error).__name__, result.detail)
                self.assertEqual(result.raw_response, b"partial" if isinstance(error, IncompleteRead) else None)
                body.close.assert_called_once()

    def test_network_and_timeout_failures_have_no_observation(self):
        for error in (
            URLError("DNS failed"), TimeoutError("timed out"),
            OSError("connection reset"), BadStatusLine("invalid HTTP status"),
        ):
            with self.subTest(error=error):
                self.urlopen.side_effect = error
                result = collector.collect()
                self.assertEqual(result.status, "collection_failed")
                self.assertEqual(result.retrieved_at, NOW)
                self.assertIsNone(result.raw_response)
                self.assertIsNone(result.http_status)
                self.assertIn(str(error), result.detail)

    def test_interrupted_body_preserves_partial_evidence(self):
        self.response.read.side_effect = IncompleteRead(b'{"timestamp":', 20)
        result = collector.collect()
        self.assertEqual(result.status, "collection_failed")
        self.assertEqual(result.raw_response, b'{"timestamp":')
        self.assertEqual(result.http_status, 200)
        self.assertIn("IncompleteRead", result.detail)
        self.urlopen.return_value.__exit__.assert_called_once()

    def test_repeated_cached_collection_and_failure_do_not_change_prior_result(self):
        self.clock.now.side_effect = [NOW, NOW + timedelta(minutes=5), NOW + timedelta(minutes=10)]
        first = collector.collect()
        second = collector.collect()
        self.urlopen.side_effect = URLError("offline")
        failed = collector.collect()
        self.assertEqual(first.status, "success")
        self.assertEqual(second.raw_response, first.raw_response)
        self.assertEqual(first.retrieved_at, NOW)
        self.assertEqual(second.retrieved_at, NOW + timedelta(minutes=5))
        self.assertEqual(failed.status, "collection_failed")

    def test_invalid_timeout_fails_before_request(self):
        for timeout in (0, -1, True, "15", None, float("inf"), float("nan")):
            with self.subTest(timeout=timeout):
                with self.assertRaisesRegex(ValueError, "finite positive"):
                    collector.collect(timeout=timeout)
        self.urlopen.assert_not_called()

    def test_unexpected_programming_errors_propagate(self):
        self.urlopen.side_effect = RuntimeError("bug")
        with self.assertRaisesRegex(RuntimeError, "bug"):
            collector.collect()


if __name__ == "__main__":
    unittest.main()
