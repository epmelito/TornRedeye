"""Deterministic contract and transport tests; never call the live API."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
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


class NormalizeTests(unittest.TestCase):
    def test_observation_preserves_evidence_and_provenance(self):
        raw = encode(PAYLOAD) + b"\n"
        result = collector.normalize(raw, NOW)
        self.assertEqual(result.status, "observed")
        self.assertEqual(result.raw_response, raw)
        self.assertEqual(result.retrieved_at, NOW)
        self.assertEqual(result.http_status, 200)
        self.assertEqual(result.source, "YATA")
        self.assertEqual(result.source_url, collector.SOURCE_URL)
        self.assertEqual(result.country, "jap")
        self.assertEqual(result.item_id, 206)
        self.assertEqual(result.source_path, "stocks.jap.stocks")
        self.assertEqual(result.export_timestamp, EXPORT_TIMESTAMP)
        self.assertEqual(result.source_timestamp, SOURCE_TIMESTAMP)
        self.assertEqual(result.observation, collector.Observation(206, "Xanax", 42, 800000))
        self.assertIsNone(result.detail)

    def test_zero_quantity_is_observed(self):
        payload = deepcopy(PAYLOAD)
        payload["stocks"]["jap"]["stocks"][1]["quantity"] = 0
        result = collector.normalize(encode(payload), NOW)
        self.assertEqual(result.status, "observed")
        self.assertEqual(result.observation.quantity, 0)

    def test_missing_records_do_not_fabricate_zero(self):
        for case in ("japan", "xanax", "empty"):
            with self.subTest(case=case):
                payload = deepcopy(PAYLOAD)
                if case == "japan":
                    del payload["stocks"]["jap"]
                elif case == "xanax":
                    payload["stocks"]["jap"]["stocks"].pop()
                else:
                    payload["stocks"]["jap"] = {"update": 0, "stocks": []}
                raw = encode(payload)
                result = collector.normalize(raw, NOW)
                self.assertEqual(result.status, "missing")
                self.assertIsNone(result.observation)
                self.assertEqual(result.raw_response, raw)
                self.assertEqual(result.export_timestamp, EXPORT_TIMESTAMP)
                self.assertIn("absent", result.detail)
                if case == "empty":
                    self.assertEqual(result.source_timestamp, 0)
                    self.assertIsNone(result.source_age_seconds)

    def test_wrong_country_is_not_used_as_fallback(self):
        payload = deepcopy(PAYLOAD)
        payload["stocks"]["can"] = payload["stocks"].pop("jap")
        result = collector.normalize(encode(payload), NOW)
        self.assertEqual(result.status, "missing")
        self.assertIsNone(result.source_timestamp)
        self.assertIsNone(result.observation)

    def test_malformed_json_and_structure(self):
        payloads = [
            b"not json", b"", b"\xff", b"null", b"[]", b'{"timestamp": NaN}',
            encode({}), encode({"timestamp": EXPORT_TIMESTAMP, "stocks": []}),
            encode({"timestamp": EXPORT_TIMESTAMP, "stocks": {"jap": None}}),
            encode({"timestamp": EXPORT_TIMESTAMP, "stocks": {"jap": {"update": 1}}}),
        ]
        for raw in payloads:
            with self.subTest(raw=raw):
                result = collector.normalize(raw, NOW)
                self.assertEqual(result.status, "malformed")
                self.assertEqual(result.raw_response, raw)
                self.assertIsNone(result.observation)
                self.assertTrue(result.detail)

    def test_invalid_timestamps_preserve_other_valid_timestamp(self):
        for field in ("timestamp", "update"):
            for value in (None, True, "123", -1, 1.5):
                with self.subTest(field=field, value=value):
                    payload = deepcopy(PAYLOAD)
                    parent = payload if field == "timestamp" else payload["stocks"]["jap"]
                    parent[field] = value
                    result = collector.normalize(encode(payload), NOW)
                    self.assertEqual(result.status, "malformed")
                    self.assertIsNone(result.observation)
                    if field == "timestamp":
                        self.assertIsNone(result.export_timestamp)
                        self.assertEqual(result.source_timestamp, SOURCE_TIMESTAMP)
                    else:
                        self.assertIsNone(result.source_timestamp)
                        self.assertEqual(result.export_timestamp, EXPORT_TIMESTAMP)
        for field in ("timestamp", "update"):
            payload = deepcopy(PAYLOAD)
            parent = payload if field == "timestamp" else payload["stocks"]["jap"]
            del parent[field]
            self.assertEqual(collector.normalize(encode(payload), NOW).status, "malformed")

    def test_invalid_target_fields(self):
        invalid_values = {
            "id": (None, True, "206", -1, 206.0),
            "quantity": (None, False, "0", -1, 0.5),
            "cost": (None, False, "800000", -1, 0.5),
            "name": (None, 206, "", "   "),
        }
        for field, values in invalid_values.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = deepcopy(PAYLOAD)
                    payload["stocks"]["jap"]["stocks"][1][field] = value
                    result = collector.normalize(encode(payload), NOW)
                    self.assertEqual(result.status, "malformed")
                    self.assertIsNone(result.observation)
                    self.assertEqual(result.export_timestamp, EXPORT_TIMESTAMP)
                    self.assertEqual(result.source_timestamp, SOURCE_TIMESTAMP)
            payload = deepcopy(PAYLOAD)
            del payload["stocks"]["jap"]["stocks"][1][field]
            self.assertEqual(collector.normalize(encode(payload), NOW).status, "malformed")

    def test_invalid_lists_and_item_identity(self):
        for items in (None, {}, [None], [{}], [{"id": "206"}]):
            with self.subTest(items=items):
                payload = deepcopy(PAYLOAD)
                payload["stocks"]["jap"]["stocks"] = items
                self.assertEqual(collector.normalize(encode(payload), NOW).status, "malformed")

    def test_duplicate_target_is_ambiguous(self):
        payload = deepcopy(PAYLOAD)
        items = payload["stocks"]["jap"]["stocks"]
        items.append(dict(items[1], quantity=0))
        result = collector.normalize(encode(payload), NOW)
        self.assertEqual(result.status, "malformed")
        self.assertIn("duplicate", result.detail)
        self.assertIsNone(result.observation)

    def test_cached_and_future_timestamps_remain_visible(self):
        result = collector.normalize(encode(PAYLOAD), NOW)
        later = collector.normalize(encode(PAYLOAD), NOW + timedelta(hours=2))
        self.assertEqual(result.source_age_seconds, 300)
        self.assertEqual(result.export_age_seconds, 60)
        self.assertEqual(later.source_age_seconds, 7500)
        self.assertEqual(later.export_age_seconds, 7260)
        self.assertEqual(later.observation, result.observation)
        payload = deepcopy(PAYLOAD)
        payload["stocks"]["jap"]["update"] = int(NOW.timestamp()) + 10
        self.assertEqual(collector.normalize(encode(payload), NOW).source_age_seconds, -10)

    def test_retrieval_time_requires_awareness_and_normalizes_to_utc(self):
        local_time = NOW.astimezone(timezone(timedelta(hours=2)))
        result = collector.normalize(encode(PAYLOAD), local_time)
        self.assertEqual(result.retrieved_at, NOW)
        self.assertIs(result.retrieved_at.tzinfo, timezone.utc)
        with self.assertRaisesRegex(ValueError, "timezone aware"):
            collector.normalize(encode(PAYLOAD), NOW.replace(tzinfo=None))

    def test_http_failure_is_not_parsed_as_an_observation(self):
        raw = encode(PAYLOAD)
        result = collector.normalize(raw, NOW, 503)
        self.assertEqual(result.status, "collection_failed")
        self.assertEqual(result.raw_response, raw)
        self.assertEqual(result.http_status, 503)
        self.assertIsNone(result.observation)


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
        self.assertEqual(result.status, "observed")
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

    def test_malformed_success_retains_body(self):
        self.response.read.return_value = b'{"stocks":'
        result = collector.collect()
        self.assertEqual(result.status, "malformed")
        self.assertEqual(result.raw_response, b'{"stocks":')
        self.assertEqual(result.http_status, 200)

    def test_http_error_retains_error_body_and_status(self):
        body = BytesIO(b'{"error": "rate limited"}')
        self.urlopen.side_effect = HTTPError(collector.SOURCE_URL, 429, "Too Many Requests", {}, body)
        result = collector.collect()
        self.assertEqual(result.status, "collection_failed")
        self.assertEqual(result.http_status, 429)
        self.assertEqual(result.raw_response, b'{"error": "rate limited"}')
        self.assertIsNone(result.observation)
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
                self.assertIsNone(result.observation)
                self.assertIn(str(error), result.detail)

    def test_interrupted_body_preserves_partial_evidence(self):
        self.response.read.side_effect = IncompleteRead(b'{"timestamp":', 20)
        result = collector.collect()
        self.assertEqual(result.status, "collection_failed")
        self.assertEqual(result.raw_response, b'{"timestamp":')
        self.assertEqual(result.http_status, 200)
        self.assertIsNone(result.observation)
        self.assertIn("IncompleteRead", result.detail)
        self.urlopen.return_value.__exit__.assert_called_once()

    def test_repeated_cached_collection_and_failure_do_not_change_prior_result(self):
        self.clock.now.side_effect = [NOW, NOW + timedelta(minutes=5), NOW + timedelta(minutes=10)]
        first = collector.collect()
        second = collector.collect()
        self.urlopen.side_effect = URLError("offline")
        failed = collector.collect()
        self.assertEqual(first.status, "observed")
        self.assertEqual(second.observation, first.observation)
        self.assertEqual(second.raw_response, first.raw_response)
        self.assertEqual(first.retrieved_at, NOW)
        self.assertEqual(second.retrieved_at, NOW + timedelta(minutes=5))
        self.assertEqual(second.source_timestamp, first.source_timestamp)
        self.assertEqual(second.source_age_seconds, first.source_age_seconds + 300)
        self.assertEqual(failed.status, "collection_failed")
        self.assertIsNone(failed.observation)

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
