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
from tornredeye.evidence_paths import LINEAGE, evidence_keys


BUCKET = "test-evidence"
RUN_ID = UUID("12345678-1234-4234-8234-123456789abc")
OTHER_RUN_ID = UUID("12345678-1234-4234-8234-123456789abd")
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
RAW = b'{"timestamp":1791460740,"stocks":{"jap":{"update":1791460500,"stocks":[{"id":206,"name":"Xanax","quantity":0,"cost":800000}]}}}'
RAW_KEY, NORMALIZED_KEY = evidence_keys(NOW, RUN_ID)


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
        self.list_calls = []

    def list_objects_v2(self, *, Bucket, Prefix, MaxKeys):
        self.list_calls.append(Prefix)
        return {"IsTruncated": False, "Contents": [{"Key": k} for b, k in sorted(self.objects)
                if b == Bucket and k.startswith(Prefix)][:MaxKeys]}

    def put_object(self, *, Bucket, Key, Body, ContentType, Metadata, IfNoneMatch):
        self.calls.append(("put", Key))
        if sum(len(k.encode("utf-8")) + len(v.encode("utf-8")) for k, v in Metadata.items()) > 2048:
            raise S3Error(400, "MetadataTooLarge")
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
        return persist(self.result if result is None else result, s3=self.s3, bucket=BUCKET, collection_id=run_id)

    def document(self):
        return json.loads(self.s3.objects[BUCKET, NORMALIZED_KEY]["Body"])

    def test_primary_all_stock_contract_two_writes_and_no_namespace_probes(self):
        receipt = self.save()
        doc = self.document()
        self.assertEqual(doc["format"], "stock-evidence")
        self.assertEqual(doc["lineage"], LINEAGE)
        self.assertEqual(doc["acquisition"]["status"], "success")
        self.assertEqual(doc["validation_status"], "complete")
        self.assertEqual(doc["countries"]["jap"]["items"][0]["quantity"], 0)
        self.assertEqual(doc["raw_evidence"]["sha256"], hashlib.sha256(RAW).hexdigest())
        self.assertNotIn("stock_export", doc)
        self.assertNotIn("country", doc)
        self.assertEqual(receipt.usable_observations, 1)
        self.assertEqual(self.s3.calls, [("put", RAW_KEY), ("put", NORMALIZED_KEY)])
        self.assertEqual(self.s3.list_calls, [])

    def test_japan_absence_and_invalid_neighbour_do_not_discard_other_stock(self):
        body = b'{"timestamp":1,"stocks":{"bad":null,"haw":{"update":1,"stocks":[{"id":1485,"name":"Shark Fin","quantity":0,"cost":0}]}}}'
        receipt = self.save(normalize(body, NOW))
        doc = self.document()
        self.assertEqual(receipt.validation_status, "partial")
        self.assertEqual(doc["acquisition"]["status"], "success")
        self.assertEqual(doc["countries"]["haw"]["items"][0]["quantity"], 0)
        self.assertTrue(doc["diagnostics"])

    def test_long_acquisition_context_stays_in_document_not_raw_metadata(self):
        detail = "failure detail \u2603 " * 3000
        headers = (("Date", "d" * 6000), ("Retry-After", "9" * 6000))
        for status, http in (("success", 200), ("collection_failed", 200), ("collection_failed", 503)):
            with self.subTest(status=status, http=http):
                self.s3 = FakeS3()
                result = replace(self.result, status=status, http_status=http, detail=detail,
                                 response_headers=headers, retry_after_error=detail)
                self.save(result)
                metadata = self.s3.objects[BUCKET, RAW_KEY]["Metadata"]
                self.assertEqual(set(metadata), {"lineage", "collection-id", "source", "source-url",
                                                 "retrieved-at", "acquisition-status", "sha256", "http-status",
                                                 "acquisition-sha256"})
                self.assertLessEqual(sum(len(k.encode()) + len(v.encode()) for k, v in metadata.items()), 2048)
                self.assertEqual(metadata["acquisition-status"], status)
                self.assertEqual(metadata["http-status"], str(http))
                self.assertEqual(self.s3.objects[BUCKET, RAW_KEY]["Body"], RAW)
                context = self.document()["acquisition"]
                self.assertEqual(context["detail"], detail)
                self.assertEqual(context["response_headers"], list(map(list, headers)))
                self.assertEqual(context["retry_after_error"], detail)
                self.assertEqual(metadata["acquisition-sha256"], hashlib.sha256(json.dumps(
                    context, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                ).encode("utf-8")).hexdigest())
                if status == "collection_failed":
                    self.assertEqual(self.document()["countries"], {})

    def test_long_context_partial_write_and_lost_ack_retries_are_exact(self):
        result = replace(self.result, status="collection_failed", detail="diagnostic" * 3000,
                         response_headers=(("Retry-After", "9" * 6000),))
        for key in (RAW_KEY, NORMALIZED_KEY):
            for committed in (False, True):
                with self.subTest(key=key, committed=committed):
                    self.s3 = FakeS3()
                    self.s3.put_errors[key] = (TimeoutError("lost acknowledgement"), committed)
                    with self.assertRaises(PersistenceError):
                        self.save(result)
                    raw_before = self.s3.objects.get((BUCKET, RAW_KEY))
                    if raw_before is not None and (BUCKET, NORMALIZED_KEY) not in self.s3.objects:
                        for changed in (replace(result, detail="changed"),
                                        replace(result, response_headers=(("Retry-After", "changed"),))):
                            with self.assertRaises(PersistenceError) as caught:
                                self.save(changed)
                            self.assertEqual(caught.exception.stage, "raw")
                            self.assertIsInstance(caught.exception.__cause__, ObjectConflictError)
                            self.assertNotIn((BUCKET, NORMALIZED_KEY), self.s3.objects)
                    self.save(result)
                    original = dict(self.s3.objects)
                    if raw_before is not None:
                        self.assertEqual(self.s3.objects[BUCKET, RAW_KEY], raw_before)
                    self.assertEqual(self.document()["acquisition"]["detail"], result.detail)
                    self.assertEqual(self.document()["countries"], {})
                    with self.assertRaises(PersistenceError) as caught:
                        self.save(replace(result, detail=result.detail + "changed"))
                    self.assertEqual(caught.exception.stage, "raw")
                    self.assertIsInstance(caught.exception.__cause__, ObjectConflictError)
                    self.assertEqual(self.s3.objects, original)

    def test_matching_retries_and_conflicts_preserve_original_bytes(self):
        self.save()
        original = dict(self.s3.objects)
        receipt = self.save()
        self.assertEqual((receipt.raw_state, receipt.normalized_state), ("existing", "existing"))
        self.assertEqual(original, self.s3.objects)
        for result in (normalize(RAW+b" ", NOW), replace(self.result, detail="changed")):
            with self.assertRaises(PersistenceError) as caught:
                self.save(result)
            self.assertIsInstance(caught.exception.__cause__, ObjectConflictError)
        self.assertEqual(original, self.s3.objects)

    def test_partial_failure_and_lost_ack_retry_original_identity(self):
        for key in (RAW_KEY, NORMALIZED_KEY):
            for committed in (False, True):
                with self.subTest(key=key, committed=committed):
                    self.s3 = FakeS3()
                    self.s3.put_errors[key] = (TimeoutError("lost ack"), committed)
                    with self.assertRaises(PersistenceError):
                        self.save()
                    self.save()
                    self.assertEqual(len(self.s3.objects), 2)
                    self.assertEqual(self.document()["collection_id"], str(RUN_ID))

    def test_failure_empty_bytes_and_partial_transport_are_distinct(self):
        for raw in (None, b"", RAW):
            self.s3 = FakeS3()
            receipt = self.save(CollectionResult("collection_failed", NOW, raw, http_status=200))
            doc = self.document()
            self.assertEqual(doc["countries"], {})
            self.assertEqual(doc["validation_status"], "collection_failed")
            self.assertEqual(receipt.raw_key is None, raw is None)
            self.assertEqual(len(self.s3.objects), 1 if raw is None else 2)
            self.assertEqual(receipt.usable_observations, 0)

    def test_changed_no_body_retry_cannot_hide_raw_only_write(self):
        self.s3.put_errors[NORMALIZED_KEY] = (TimeoutError(), False)
        with self.assertRaises(PersistenceError):
            self.save()
        with self.assertRaises(PersistenceError):
            self.save(CollectionResult("collection_failed", NOW, None))
        self.assertNotIn((BUCKET, NORMALIZED_KEY), self.s3.objects)

    def test_retry_uses_original_hour_across_timezone_and_processing_boundary(self):
        local = NOW.astimezone(timezone(timedelta(hours=2)))
        receipt = self.save(replace(self.result, retrieved_at=local))
        self.assertEqual(receipt.raw_key, RAW_KEY)
        self.assertEqual(self.document()["retrieved_at"], "2026-10-08T12:00:00.000000Z")

    def test_bad_identity_and_naive_retrieval_fail_before_write(self):
        for result, identity in ((self.result, "uuid"), (replace(self.result, retrieved_at=NOW.replace(tzinfo=None)), RUN_ID)):
            with self.assertRaises(ValueError):
                persist(result, s3=self.s3, bucket=BUCKET, collection_id=identity)
        self.assertFalse(self.s3.calls)
