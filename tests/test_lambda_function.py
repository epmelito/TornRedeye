"""Test the Lambda boundary and real collector/persistence flow without AWS."""

from datetime import datetime, timezone
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from uuid import UUID

import lambda_function as handler
from s3_persistence import PersistenceError, PersistenceReceipt, persist
from yata_collector import CollectionResult, collect, normalize


NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
RUN_ID = UUID("12345678-1234-4234-8234-123456789abc")
OTHER_RUN_ID = UUID("12345678-1234-4234-8234-123456789abd")
BUCKET = "test-evidence"
RAW = b'{"timestamp":1791460740,"stocks":{"jap":{"update":1791460500,"stocks":[{"id":206,"name":"Xanax","quantity":0,"cost":800000}]}}}'
RAW_KEY = f"raw/yata/jap/206/{RUN_ID.hex}.bin"
NORMALIZED_KEY = f"normalized/yata/jap/206/{RUN_ID.hex}.json"


class HandlerTests(unittest.TestCase):
    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def setUp(self):
        self.start_patch(patch.dict("os.environ", {
            "DESTINATION_BUCKET": BUCKET, "AWS_REGION": "eu-north-1",
        }, clear=True))
        self.s3 = Mock()
        self.client_factory = self.start_patch(patch(
            "lambda_function._s3_client", return_value=self.s3,
        ))
        self.result = normalize(RAW, NOW)
        self.collect = self.start_patch(patch("lambda_function.collect", return_value=self.result))
        self.persist = self.start_patch(patch("lambda_function.persist", return_value=PersistenceReceipt(
            RUN_ID, RAW_KEY, NORMALIZED_KEY, "written", "written",
        )))
        self.uuid = self.start_patch(patch("lambda_function.uuid4", return_value=RUN_ID))
        self.context = SimpleNamespace(aws_request_id="invocation-1")

    def test_success_passes_original_result_and_one_id_and_returns_serializable_summary(self):
        with self.assertLogs("lambda_function", level="INFO") as logs:
            summary = handler.lambda_handler({}, self.context)
        self.collect.assert_called_once_with(timeout=15.0)
        self.client_factory.assert_called_once_with("eu-north-1")
        self.uuid.assert_called_once_with()
        self.persist.assert_called_once_with(
            self.result, s3=self.s3, bucket=BUCKET, collection_id=RUN_ID,
        )
        self.assertIs(self.persist.call_args.args[0], self.result)
        self.assertEqual(summary, {
            "collection_id": str(RUN_ID), "status": "observed",
            "raw_key": RAW_KEY, "normalized_key": NORMALIZED_KEY,
        })
        self.assertEqual(json.loads(json.dumps(summary)), summary)
        output = "\n".join(logs.output)
        self.assertIn("status=observed", output)
        self.assertIn("request_id=invocation-1", output)
        self.assertIn(f"normalized_key={NORMALIZED_KEY}", output)
        self.assertNotIn(RAW.decode(), output)

    def test_configuration_is_read_from_environment(self):
        with patch.dict("os.environ", {
            "DESTINATION_BUCKET": "other-evidence", "YATA_TIMEOUT_SECONDS": "2.5",
        }):
            handler.lambda_handler({"bucket": "ignored", "timeout": 999}, self.context)
        self.collect.assert_called_once_with(timeout=2.5)
        self.assertEqual(self.persist.call_args.kwargs["bucket"], "other-evidence")

    def test_region_defaults_to_approved_region_when_not_supplied(self):
        with patch.dict("os.environ", {"DESTINATION_BUCKET": BUCKET}, clear=True):
            handler.lambda_handler({}, self.context)
        self.client_factory.assert_called_once_with("eu-north-1")

    def test_invalid_configuration_fails_before_client_or_collection(self):
        configurations = [
            {}, {"DESTINATION_BUCKET": ""}, {"DESTINATION_BUCKET": "   "},
            {"DESTINATION_BUCKET": BUCKET, "AWS_REGION": " "},
        ] + [
            {"DESTINATION_BUCKET": BUCKET, "YATA_TIMEOUT_SECONDS": value}
            for value in ("", "bad", "0", "-1", "nan", "inf", "-inf")
        ]
        for environment in configurations:
            with self.subTest(environment=environment):
                with patch.dict("os.environ", environment, clear=True):
                    with self.assertLogs("lambda_function", level="ERROR") as logs:
                        with self.assertRaises(ValueError):
                            handler.lambda_handler({}, self.context)
                self.assertIn("stage=configuration", "\n".join(logs.output))
        self.client_factory.assert_not_called()
        self.collect.assert_not_called()
        self.persist.assert_not_called()

    def test_sdk_initialization_failure_is_visible_before_collection(self):
        error = RuntimeError("SDK initialization failed")
        self.client_factory.side_effect = error
        with self.assertLogs("lambda_function", level="ERROR") as logs:
            with self.assertRaises(RuntimeError) as raised:
                handler.lambda_handler({}, self.context)
        self.assertIs(raised.exception, error)
        self.assertIn("stage=client_initialization", "\n".join(logs.output))
        self.collect.assert_not_called()
        self.persist.assert_not_called()

    def test_missing_record_is_persisted_and_warned_without_fabricating_zero(self):
        missing = normalize(b'{"timestamp":1,"stocks":{"jap":{"update":0,"stocks":[]}}}', NOW)
        self.collect.return_value = missing
        with self.assertLogs("lambda_function", level="WARNING") as logs:
            summary = handler.lambda_handler({}, self.context)
        self.assertEqual(summary["status"], "missing")
        self.assertIs(self.persist.call_args.args[0], missing)
        self.assertIsNone(missing.observation)
        self.assertIn("Xanax record absent", "\n".join(logs.output))

    def test_failed_and_malformed_results_are_persisted_before_invocation_error(self):
        cases = [
            CollectionResult("collection_failed", NOW, None, detail="URLError: offline"),
            normalize(b"malformed body", NOW),
        ]
        for result in cases:
            with self.subTest(status=result.status):
                self.collect.return_value = result
                with self.assertLogs("lambda_function", level="ERROR") as logs:
                    with self.assertRaises(handler.CollectionError) as raised:
                        handler.lambda_handler({}, self.context)
                self.assertIs(self.persist.call_args.args[0], result)
                self.assertIn(result.status, str(raised.exception))
                self.assertIn(NORMALIZED_KEY, str(raised.exception))
                self.assertIn("stage=collection_outcome", "\n".join(logs.output))
        self.assertEqual(self.collect.call_count, len(cases))
        self.assertEqual(self.persist.call_count, len(cases))

    def test_persistence_failure_propagates_original_cause_and_partial_progress(self):
        cause = OSError("S3 unavailable")
        error = PersistenceError(RUN_ID, "normalized", RAW_KEY, NORMALIZED_KEY, "written")
        error.__cause__ = cause
        self.persist.side_effect = error
        with self.assertLogs("lambda_function", level="ERROR") as logs:
            with self.assertRaises(PersistenceError) as raised:
                handler.lambda_handler({}, self.context)
        self.assertIs(raised.exception, error)
        self.assertIs(raised.exception.__cause__, cause)
        self.assertEqual(raised.exception.raw_state, "written")
        self.collect.assert_called_once()
        self.persist.assert_called_once()
        output = "\n".join(logs.output)
        self.assertIn("stage=persistence", output)
        self.assertIn("raw_state=written", output)
        self.assertIn("S3 unavailable", output)

    def test_storage_error_takes_precedence_when_collection_also_failed(self):
        self.collect.return_value = CollectionResult("collection_failed", NOW, None, detail="offline")
        error = PersistenceError(RUN_ID, "normalized", None, NORMALIZED_KEY, "absent")
        self.persist.side_effect = error
        with self.assertLogs("lambda_function", level="INFO") as logs:
            with self.assertRaises(PersistenceError) as raised:
                handler.lambda_handler({}, self.context)
        self.assertIs(raised.exception, error)
        self.assertIn("status=collection_failed", "\n".join(logs.output))
        self.assertIn("detail=offline", "\n".join(logs.output))
        self.assertIn("stage=persistence", "\n".join(logs.output))
        self.assertIs(self.persist.call_args.args[0], self.collect.return_value)

    def test_unexpected_collection_exception_is_visible_and_not_retried(self):
        error = RuntimeError("collector bug")
        self.collect.side_effect = error
        with self.assertLogs("lambda_function", level="ERROR") as logs:
            with self.assertRaises(RuntimeError) as raised:
                handler.lambda_handler({}, self.context)
        self.assertIs(raised.exception, error)
        self.assertIn("stage=collection", "\n".join(logs.output))
        self.collect.assert_called_once()
        self.persist.assert_not_called()

    def test_redelivered_event_makes_a_distinct_retrieval_with_new_id(self):
        self.uuid.side_effect = [RUN_ID, OTHER_RUN_ID]
        self.persist.side_effect = persist
        event = {"collection_id": str(RUN_ID), "timestamp": self.result.source_timestamp}
        first = handler.lambda_handler(event, self.context)
        second = handler.lambda_handler(event, self.context)
        self.assertNotEqual(first["raw_key"], second["raw_key"])
        self.assertNotEqual(first["normalized_key"], second["normalized_key"])
        self.assertEqual(self.s3.put_object.call_count, 4)
        self.assertEqual(self.collect.call_count, 2)
        self.assertEqual(self.persist.call_count, 2)
        self.assertEqual(
            [call.kwargs["collection_id"] for call in self.persist.call_args_list],
            [RUN_ID, OTHER_RUN_ID],
        )

    def test_real_collector_and_persistence_preserve_raw_and_zero_observation(self):
        self.collect.side_effect = collect
        self.persist.side_effect = persist
        with patch("yata_collector.urlopen") as urlopen, patch("yata_collector.datetime") as clock:
            clock.now.return_value = NOW
            response = urlopen.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = RAW
            handler.lambda_handler({}, self.context)
        urlopen.assert_called_once()
        self.assertEqual(self.s3.put_object.call_count, 2)
        raw_put, normalized_put = self.s3.put_object.call_args_list
        self.assertEqual(raw_put.kwargs["Body"], RAW)
        self.assertEqual(raw_put.kwargs["Key"], RAW_KEY)
        self.assertEqual(raw_put.kwargs["IfNoneMatch"], "*")
        document = json.loads(normalized_put.kwargs["Body"])
        self.assertEqual(document["collection_id"], str(RUN_ID))
        self.assertEqual(document["retrieved_at"], "2026-10-08T12:00:00.000000Z")
        self.assertEqual(document["observation"]["quantity"], 0)
        self.assertEqual(document["source_timestamp"], self.result.source_timestamp)
        self.assertEqual(document["source_url"], self.result.source_url)
        self.assertEqual(document["status"], "observed")

    def test_real_http_and_network_failures_are_stored_before_handler_raises(self):
        self.collect.side_effect = collect
        self.persist.side_effect = persist
        body = b'{"error":"unavailable"}'
        failures = [
            HTTPError("https://yata.yt/api/v1/travel/export/", 503, "Unavailable", {}, BytesIO(body)),
            URLError("DNS failed"),
        ]
        for error in failures:
            with self.subTest(error=error):
                self.s3.reset_mock()
                with patch("yata_collector.urlopen", side_effect=error) as urlopen:
                    with patch("yata_collector.datetime") as clock:
                        clock.now.return_value = NOW
                        with self.assertLogs("lambda_function", level="ERROR"):
                            with self.assertRaises(handler.CollectionError):
                                handler.lambda_handler({}, self.context)
                urlopen.assert_called_once()
                puts = self.s3.put_object.call_args_list
                document = json.loads(puts[-1].kwargs["Body"])
                self.assertEqual(document["status"], "collection_failed")
                self.assertIsNone(document["observation"])
                self.assertEqual(document["retrieved_at"], "2026-10-08T12:00:00.000000Z")
                if isinstance(error, HTTPError):
                    self.assertEqual(len(puts), 2)
                    self.assertEqual(puts[0].kwargs["Body"], body)
                    self.assertEqual(document["http_status"], 503)
                else:
                    self.assertEqual(len(puts), 1)
                    self.assertIsNone(document["raw_evidence"])

    def test_real_partial_storage_write_produces_invocation_failure(self):
        self.persist.side_effect = persist
        cause = OSError("normalized write failed")
        self.s3.put_object.side_effect = [{}, cause]
        with self.assertLogs("lambda_function", level="ERROR"):
            with self.assertRaises(PersistenceError) as raised:
                handler.lambda_handler({}, self.context)
        self.assertIs(raised.exception.__cause__, cause)
        self.assertEqual(raised.exception.raw_state, "written")
        self.assertEqual(raised.exception.stage, "normalized")
        self.assertEqual(self.s3.put_object.call_args_list[0].kwargs["Body"], RAW)
        self.collect.assert_called_once()
        self.persist.assert_called_once()


class SDKClientTests(unittest.TestCase):
    def test_uses_sdk_client_and_configured_region_without_credentials_or_aws_access(self):
        sdk = Mock()
        with patch.dict("sys.modules", {"boto3": sdk}):
            client = handler._s3_client("eu-north-1")
        sdk.client.assert_called_once_with("s3", region_name="eu-north-1")
        self.assertIs(client, sdk.client.return_value)


if __name__ == "__main__":
    unittest.main()
