"""S3 persistence tests with a deterministic in-memory conditional-write client."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from s3_persistence import ObjectConflictError, PersistenceError, persist
from yata_collector import CollectionResult, normalize


BUCKET = "test-evidence"
RUN_ID = UUID("12345678-1234-4234-8234-123456789abc")
OTHER_RUN_ID = UUID("12345678-1234-4234-8234-123456789abd")
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
RAW = b'{"timestamp":1791460740,"stocks":{"jap":{"update":1791460500,"stocks":[{"id":206,"name":"Xanax","quantity":0,"cost":800000}]}}}'
RAW_KEY = f"raw/yata/jap/206/{RUN_ID.hex}.bin"
NORMALIZED_KEY = f"normalized/yata/jap/206/{RUN_ID.hex}.json"


class S3Error(Exception):
    def __init__(self, status, code):
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code},
        }
        super().__init__(code)


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.put_errors = {}
        self.get_errors = {}
        self.streams = []

    def put_object(self, *, Bucket, Key, Body, ContentType, Metadata, IfNoneMatch):
        self.calls.append(("put", Key))
        if IfNoneMatch != "*":
            raise AssertionError("every write must be conditional")
        if (Bucket, Key) in self.objects:
            raise S3Error(412, "PreconditionFailed")
        error, commit_first = self.put_errors.pop(Key, (None, False))
        if error is None or commit_first:
            self.objects[Bucket, Key] = {
                "Body": Body, "Metadata": dict(Metadata), "ContentType": ContentType
            }
        if error is not None:
            raise error
        return {}

    def get_object(self, *, Bucket, Key):
        self.calls.append(("get", Key))
        if Key in self.get_errors:
            raise self.get_errors.pop(Key)
        if (Bucket, Key) not in self.objects:
            raise S3Error(404, "NoSuchKey")
        stored = self.objects[Bucket, Key]
        stream = BytesIO(stored["Body"])
        self.streams.append(stream)
        return dict(stored, Body=stream)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.result = normalize(RAW, NOW)

    def save(self, result=None, run_id=RUN_ID):
        return persist(
            self.result if result is None else result,
            s3=self.s3, bucket=BUCKET, collection_id=run_id,
        )

    def document(self, key=NORMALIZED_KEY):
        return json.loads(self.s3.objects[BUCKET, key]["Body"])

    def test_raw_and_normalized_preserve_zero_and_full_provenance(self):
        receipt = self.save()
        self.assertEqual(receipt.raw_key, RAW_KEY)
        self.assertEqual(receipt.normalized_key, NORMALIZED_KEY)
        self.assertEqual(receipt.raw_state, "written")
        self.assertEqual(receipt.normalized_state, "written")
        self.assertEqual(self.s3.calls, [("put", RAW_KEY), ("put", NORMALIZED_KEY)])
        raw_object = self.s3.objects[BUCKET, RAW_KEY]
        self.assertEqual(raw_object["Body"], RAW)
        self.assertEqual(raw_object["ContentType"], "application/octet-stream")
        document = self.document()
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["collection_id"], str(RUN_ID))
        self.assertEqual(document["retrieved_at"], "2026-10-08T12:00:00.000000Z")
        for field in (
            "status", "source", "source_url", "source_path", "country", "item_id",
            "source_timestamp", "export_timestamp", "http_status", "detail",
        ):
            self.assertEqual(document[field], getattr(self.result, field))
        self.assertEqual(document["observation"], {
            "item_id": 206, "name": "Xanax", "quantity": 0, "cost": 800000,
        })
        self.assertNotIn("raw_response", document)
        self.assertEqual(document["raw_evidence"], {
            "bucket": BUCKET, "key": RAW_KEY,
            "sha256": hashlib.sha256(RAW).hexdigest(), "size_bytes": len(RAW),
        })
        self.assertEqual(raw_object["Metadata"], {
            "collection-id": str(RUN_ID), "retrieved-at": document["retrieved_at"],
            "source": "YATA", "source-url": self.result.source_url,
            "source-path": "stocks.jap.stocks", "country": "jap", "item-id": "206",
            "status": "observed", "sha256": document["raw_evidence"]["sha256"],
            "source-timestamp": str(self.result.source_timestamp),
            "export-timestamp": str(self.result.export_timestamp), "http-status": "200",
        })
        self.assertEqual(self.s3.objects[BUCKET, NORMALIZED_KEY]["ContentType"], "application/json")

    def test_matching_retry_is_idempotent_and_closes_readback_streams(self):
        self.save()
        original = dict(self.s3.objects)
        receipt = self.save()
        self.assertEqual(receipt.raw_state, "existing")
        self.assertEqual(receipt.normalized_state, "existing")
        self.assertEqual(self.s3.objects, original)
        self.assertEqual(len(self.s3.objects), 2)
        self.assertTrue(all(stream.closed for stream in self.s3.streams))

    def test_separate_cached_runs_remain_distinct_even_with_same_retrieval_time(self):
        first = self.save()
        second = self.save(run_id=OTHER_RUN_ID)
        self.assertNotEqual(first.raw_key, second.raw_key)
        self.assertNotEqual(first.normalized_key, second.normalized_key)
        self.assertEqual(len(self.s3.objects), 4)
        self.assertEqual(self.s3.objects[BUCKET, second.raw_key]["Body"], RAW)
        self.assertEqual(self.document(second.normalized_key)["source_timestamp"], self.result.source_timestamp)

    def test_cached_run_with_later_retrieval_keeps_both_times(self):
        first = self.save()
        second = self.save(replace(self.result, retrieved_at=NOW + timedelta(minutes=5)), OTHER_RUN_ID)
        self.assertEqual(self.document(first.normalized_key)["retrieved_at"], "2026-10-08T12:00:00.000000Z")
        self.assertEqual(self.document(second.normalized_key)["retrieved_at"], "2026-10-08T12:05:00.000000Z")
        self.assertEqual(self.document(second.normalized_key)["source_timestamp"], self.document()["source_timestamp"])

    def test_all_collection_outcomes_remain_distinct(self):
        cases = [
            normalize(RAW, NOW),
            normalize(b'{"timestamp":1,"stocks":{"jap":{"update":0,"stocks":[]}}}', NOW),
            normalize(b"not JSON", NOW),
            normalize(b'{"error":"unavailable"}', NOW, 503),
        ]
        for result in cases:
            with self.subTest(status=result.status):
                self.s3 = FakeS3()
                self.save(result)
                document = self.document()
                self.assertEqual(document["status"], result.status)
                self.assertEqual(document["detail"], result.detail)
                self.assertEqual(self.s3.objects[BUCKET, RAW_KEY]["Body"], result.raw_response)
                if result.status != "observed":
                    self.assertIsNone(document["observation"])
                if result.status == "missing":
                    self.assertEqual(document["source_timestamp"], 0)
                    self.assertEqual(self.s3.objects[BUCKET, RAW_KEY]["Metadata"]["source-timestamp"], "0")

    def test_failure_without_response_persists_only_normalized_result(self):
        failed = CollectionResult("collection_failed", NOW, None, detail="URLError: offline")
        receipt = self.save(failed)
        self.assertIsNone(receipt.raw_key)
        self.assertEqual(receipt.raw_state, "absent")
        self.assertEqual(self.s3.calls, [("put", NORMALIZED_KEY)])
        document = self.document()
        self.assertEqual(document["status"], "collection_failed")
        self.assertEqual(document["detail"], failed.detail)
        self.assertIsNone(document["raw_evidence"])
        self.assertIsNone(document["source_timestamp"])
        self.assertIsNone(document["observation"])
        self.assertEqual(self.save(failed).normalized_state, "existing")

    def test_empty_and_partial_response_bytes_are_evidence(self):
        for body in (b"", b'{"stocks":'):
            with self.subTest(body=body):
                self.s3 = FakeS3()
                failed = CollectionResult("collection_failed", NOW, body, 200, detail="IncompleteRead")
                self.save(failed)
                self.assertEqual(self.s3.objects[BUCKET, RAW_KEY]["Body"], body)
                self.assertEqual(self.document()["raw_evidence"]["size_bytes"], len(body))

    def test_raw_failure_stops_before_normalized_and_reports_unknown_outcome(self):
        denied = S3Error(403, "AccessDenied")
        self.s3.put_errors[RAW_KEY] = (denied, False)
        with self.assertRaises(PersistenceError) as raised:
            self.save()
        error = raised.exception
        self.assertEqual(error.stage, "raw")
        self.assertEqual(error.key, RAW_KEY)
        self.assertEqual(error.raw_state, "unknown")
        self.assertEqual(error.collection_id, RUN_ID)
        self.assertIs(error.__cause__, denied)
        self.assertEqual(self.s3.calls, [("put", RAW_KEY)])
        self.assertEqual(self.s3.objects, {})
        self.assertEqual(self.save().normalized_state, "written")

    def test_normalized_failure_preserves_raw_and_retry_completes_missing_write(self):
        denied = S3Error(403, "AccessDenied")
        self.s3.put_errors[NORMALIZED_KEY] = (denied, False)
        with self.assertRaises(PersistenceError) as raised:
            self.save()
        error = raised.exception
        self.assertEqual(error.stage, "normalized")
        self.assertEqual(error.raw_state, "written")
        self.assertEqual(error.raw_key, RAW_KEY)
        self.assertEqual(error.normalized_key, NORMALIZED_KEY)
        self.assertIs(error.__cause__, denied)
        self.assertEqual(self.s3.objects[BUCKET, RAW_KEY]["Body"], RAW)
        self.assertNotIn((BUCKET, NORMALIZED_KEY), self.s3.objects)
        receipt = self.save()
        self.assertEqual(receipt.raw_state, "existing")
        self.assertEqual(receipt.normalized_state, "written")
        self.assertEqual(len(self.s3.objects), 2)

    def test_lost_acknowledgements_recover_for_either_write(self):
        for key in (RAW_KEY, NORMALIZED_KEY):
            with self.subTest(key=key):
                self.s3 = FakeS3()
                timeout = TimeoutError("acknowledgement lost")
                self.s3.put_errors[key] = (timeout, True)
                with self.assertRaises(PersistenceError) as raised:
                    self.save()
                self.assertIs(raised.exception.__cause__, timeout)
                self.assertIn((BUCKET, key), self.s3.objects)
                receipt = self.save()
                self.assertEqual(len(self.s3.objects), 2)
                self.assertEqual(receipt.raw_state, "existing")
                self.assertEqual(receipt.normalized_state, "existing" if key == NORMALIZED_KEY else "written")

    def test_conflicting_raw_body_never_overwrites_history(self):
        self.save()
        original = dict(self.s3.objects)
        with self.assertRaises(PersistenceError) as raised:
            self.save(replace(self.result, raw_response=RAW + b"\n"))
        self.assertEqual(raised.exception.stage, "raw")
        self.assertIsInstance(raised.exception.__cause__, ObjectConflictError)
        self.assertEqual(self.s3.objects, original)

    def test_recollected_same_body_cannot_reuse_id_after_partial_write(self):
        self.s3.put_errors[NORMALIZED_KEY] = (S3Error(503, "Unavailable"), False)
        with self.assertRaises(PersistenceError):
            self.save()
        original_raw = self.s3.objects[BUCKET, RAW_KEY]
        with self.assertRaises(PersistenceError) as raised:
            self.save(replace(self.result, retrieved_at=NOW + timedelta(seconds=1)))
        self.assertEqual(raised.exception.stage, "raw")
        self.assertIsInstance(raised.exception.__cause__, ObjectConflictError)
        self.assertEqual(self.s3.objects[BUCKET, RAW_KEY], original_raw)
        self.assertNotIn((BUCKET, NORMALIZED_KEY), self.s3.objects)
        self.assertEqual(self.save().normalized_state, "written")

    def test_conflicting_normalized_result_is_not_replaced(self):
        self.save()
        original = dict(self.s3.objects)
        with self.assertRaises(PersistenceError) as raised:
            self.save(replace(self.result, detail="different result"))
        self.assertEqual(raised.exception.stage, "normalized")
        self.assertEqual(raised.exception.raw_state, "existing")
        self.assertIsInstance(raised.exception.__cause__, ObjectConflictError)
        self.assertEqual(self.s3.objects, original)

    def test_existing_object_read_failures_are_not_successful_retries(self):
        for key in (RAW_KEY, NORMALIZED_KEY):
            with self.subTest(key=key):
                self.s3 = FakeS3()
                self.save()
                denied = S3Error(403, "AccessDenied")
                self.s3.get_errors[key] = denied
                with self.assertRaises(PersistenceError) as raised:
                    self.save()
                self.assertIs(raised.exception.__cause__, denied)
                self.assertEqual(raised.exception.stage, "raw" if key == RAW_KEY else "normalized")
                self.assertEqual(len(self.s3.objects), 2)
                self.assertEqual(self.save().normalized_state, "existing")

    def test_readback_stream_error_is_visible_and_stream_is_closed(self):
        self.save()
        with patch.object(self.s3, "get_object") as get:
            stream = BytesIO(RAW)
            get.return_value = {"Body": stream, "Metadata": {}}
            with patch.object(stream, "read", side_effect=OSError("read interrupted")):
                with self.assertRaises(PersistenceError) as raised:
                    self.save()
            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertTrue(stream.closed)

    def test_conditional_conflict_is_visible_and_can_be_retried(self):
        conflict = S3Error(409, "ConditionalRequestConflict")
        self.s3.put_errors[RAW_KEY] = (conflict, False)
        with self.assertRaises(PersistenceError) as raised:
            self.save()
        self.assertIs(raised.exception.__cause__, conflict)
        self.assertEqual(self.s3.calls, [("put", RAW_KEY)])
        self.assertEqual(self.save().normalized_state, "written")

    def test_storage_failure_for_later_run_preserves_prior_history(self):
        self.save()
        prior = dict(self.s3.objects)
        failed = CollectionResult("collection_failed", NOW, None, detail="offline")
        other_key = f"normalized/yata/jap/206/{OTHER_RUN_ID.hex}.json"
        denied = S3Error(403, "AccessDenied")
        self.s3.put_errors[other_key] = (denied, False)
        with self.assertRaises(PersistenceError) as raised:
            self.save(failed, OTHER_RUN_ID)
        self.assertEqual(raised.exception.raw_state, "absent")
        self.assertEqual(raised.exception.stage, "normalized")
        self.assertIs(raised.exception.__cause__, denied)
        self.assertEqual(self.s3.objects, prior)
        receipt = self.save(failed, OTHER_RUN_ID)
        self.assertEqual(receipt.normalized_state, "written")
        self.assertEqual(len(self.s3.objects), 3)
        for key, value in prior.items():
            self.assertEqual(self.s3.objects[key], value)

    def test_timezone_equivalent_result_has_identical_retry_encoding(self):
        self.save()
        local_time = NOW.astimezone(timezone(timedelta(hours=2)))
        receipt = self.save(replace(self.result, retrieved_at=local_time))
        self.assertEqual(receipt.normalized_state, "existing")

    def test_invalid_inputs_fail_before_any_write(self):
        for run_id in (None, "timestamp-123", 1791460500, str(RUN_ID)):
            with self.subTest(run_id=run_id):
                with self.assertRaisesRegex(ValueError, "UUID"):
                    self.save(run_id=run_id)
        for bucket in (None, "", " "):
            with self.subTest(bucket=bucket):
                with self.assertRaisesRegex(ValueError, "bucket"):
                    persist(self.result, s3=self.s3, bucket=bucket, collection_id=RUN_ID)
        with self.assertRaisesRegex(ValueError, "timezone aware"):
            self.save(replace(self.result, retrieved_at=NOW.replace(tzinfo=None)))
        with self.assertRaisesRegex(ValueError, "bytes or None"):
            self.save(replace(self.result, raw_response="not bytes"))
        self.assertEqual(self.s3.calls, [])


if __name__ == "__main__":
    unittest.main()
