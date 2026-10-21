"""Synthetic offline backup tests; no SDK or network client is constructed."""

import base64
from datetime import datetime, timezone
import hashlib
from contextlib import redirect_stderr
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import UUID

import evidence_backup as backup
from s3_persistence import persist
from yata_collector import normalize


NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)
CUTOFF = NOW.isoformat()
from tornredeye.evidence_paths import evidence_keys, FORECAST_PREFIX
RAW, NORMALIZED = evidence_keys(NOW, UUID(int=1))


class BoundedBody(BytesIO):
    def read(self, size=-1):
        if not 0 < size <= backup.CHUNK:
            raise AssertionError("unbounded read")
        return super().read(size)


class InterruptedBody(BoundedBody):
    def read(self, size=-1):
        if self.tell():
            raise OSError("synthetic secret must not be printed")
        return super().read(min(size, 2))


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.list_calls = []
        self.get_calls = []
        self.list_error_token = None
        self.read_errors = set()
        self.interrupted = set()
        self.overrides = {}
        self.page_transform = lambda page: page
        self.streams = []

    def add(self, key, body, metadata=None):
        self.objects[key] = {"body": body, "Metadata": metadata or {},
                             "ContentType": "application/octet-stream" if backup.is_raw(key) else "application/json",
                             "ContentLength": len(body), "LastModified": NOW,
                             "ETag": '"synthetic-multipart-etag-2"'}

    def list_objects_v2(self, **request):
        self.list_calls.append(request)
        self.assert_scope(request)
        token = request.get("ContinuationToken")
        if token is not None and token == self.list_error_token:
            raise OSError("synthetic secret")
        keys = sorted(key for key in self.objects if key.startswith(request["Prefix"]))
        start = int(token or 0)
        selected = keys[start:start + request["MaxKeys"]]
        truncated = start + len(selected) < len(keys)
        page = {"Contents": [{"Key": key, "Size": self.objects[key]["ContentLength"],
                              "LastModified": NOW, "ETag": self.objects[key]["ETag"]}
                             for key in selected],
                "KeyCount": len(selected), "IsTruncated": truncated}
        if truncated:
            page["NextContinuationToken"] = str(start + len(selected))
        return self.page_transform(page)

    def assert_scope(self, request):
        assert request["Bucket"] == "synthetic-private-bucket"
        assert any(request["Prefix"].startswith(p) for p in backup.PREFIXES)
        assert 1 <= request["MaxKeys"] <= 1000

    def put_object(self, *, Bucket, Key, Body, ContentType, Metadata, IfNoneMatch):
        assert IfNoneMatch == "*" and Key not in self.objects
        self.add(Key, Body, Metadata)
        self.objects[Key]["ContentType"] = ContentType

    def get_object(self, *, Bucket, Key, IfMatch):
        self.get_calls.append(Key)
        if Key in self.read_errors or Key not in self.objects:
            raise OSError("synthetic secret")
        stored = self.objects[Key]
        assert IfMatch == stored["ETag"]
        body_type = InterruptedBody if Key in self.interrupted else BoundedBody
        body = body_type(stored["body"])
        self.streams.append(body)
        return dict({k: v for k, v in stored.items() if k != "body"},
                    Body=body, **self.overrides.get(Key, {}))


class BackupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/"export"
        self.s3 = FakeS3()

    def test_complete_inventory_exact_export_and_offline_verification(self):
        self.s3.add(RAW, b"", {"sha256": hashlib.sha256(b"").hexdigest()})
        self.s3.add(NORMALIZED, b'{"raw_evidence":null}')
        self.s3.add(FORECAST_PREFIX+"epoch-1/2026-10-11T00/ack.json", b"{}")
        self.s3.add("control/yata/polling.json", b"excluded")
        manifest = backup.inventory(s3=self.s3, bucket="synthetic-private-bucket", directory=self.root, paused_cutoff_utc=CUTOFF, page_size=1)
        self.assertEqual(sum(x["objects"] for x in manifest["inventory"]), 3)
        backup.export(s3=self.s3, directory=self.root)
        for page in backup._pages(self.root, backup._manifest(self.root)):
            for item in page["objects"]:
                receipt = backup._receipt(self.root, manifest, item)
                self.assertEqual(backup._verified(self.root, item, receipt), "verified")
                self.assertEqual((self.root/"objects"/(backup._key_id(item["key"])+".bin")).read_bytes(), self.s3.objects[item["key"]]["body"])
        calls = list(self.s3.get_calls)
        backup.export(s3=self.s3, directory=self.root)
        self.assertEqual(calls, self.s3.get_calls)

    def test_partial_inventory_and_checksum_failures_stay_visible(self):
        self.s3.add(RAW, b"bad", {"sha256": "0"*64})
        manifest = backup.inventory(s3=self.s3, bucket="synthetic-private-bucket", directory=self.root)
        backup.export(s3=self.s3, directory=self.root)
        item = next(backup._pages(self.root, manifest))["objects"][0]
        self.assertNotEqual(backup._receipt(self.root, manifest, item)["status"], "verified")

    def test_unknown_and_archived_keys_do_not_enter_operational_contract(self):
        for key in ("raw/yata/jap/206/a.bin", backup.PREFIXES[0]+"../secret", "forecast-state/live-v1/epoch-1/head.json"):
            with self.assertRaises(backup.BackupError):
                backup._key_id(key)


class ExportSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "backup"
        self.s3 = FakeS3()

    def inventory(self, cutoff=CUTOFF, **kwargs):
        return backup.inventory(s3=self.s3, bucket="synthetic-private-bucket",
                                directory=self.root, paused_cutoff_utc=cutoff, **kwargs)

    def export(self, **kwargs):
        return backup.export(s3=self.s3, directory=self.root, **kwargs)

    def results(self):
        records = list(backup.verify(self.root))
        return {item["key"]: item["status"] for item in records[:-1]}, records[-1]

    def object_path(self, key=RAW, extension="bin"):
        return self.root / "objects" / (backup._key_id(key) + "." + extension)

    def test_interrupted_read_and_safe_rerun(self):
        self.s3.add(RAW, b"synthetic evidence")
        self.s3.add(NORMALIZED, b"{}")
        self.inventory()
        self.s3.interrupted.add(RAW)
        self.export()
        self.assertFalse(self.object_path().exists())
        self.assertEqual(self.results()[0][RAW], "failed")
        self.assertTrue(self.object_path(RAW, "part").exists())
        self.s3.interrupted.clear()
        self.export()
        self.assertEqual(self.object_path().read_bytes(), b"synthetic evidence")
        self.assertEqual(self.s3.get_calls.count(NORMALIZED), 1)

    def test_process_interruption_leaves_pending_then_resumes(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        with patch.object(self.s3, "get_object", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.export()
        self.assertEqual(self.results()[0][RAW], "downloading")
        self.export()
        self.assertEqual(self.results()[0][RAW], "verified")

    def test_prepared_publication_recovers_without_source_read(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        original = backup._write
        def interrupted(root, relative, value):
            if relative.startswith("objects/") and value["status"] == "verified":
                raise KeyboardInterrupt()
            return original(root, relative, value)
        with patch.object(backup, "_write", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.export()
        self.assertEqual(self.results()[0][RAW], "incomplete_publication")
        self.export()
        self.assertEqual(self.results()[0][RAW], "verified")
        self.assertEqual(len(self.s3.get_calls), 1)

    def test_prepared_staging_file_recovers_without_source_read(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        with patch.object(backup.os, "link", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.export()
        self.assertFalse(self.object_path().exists())
        self.assertTrue(self.object_path(RAW, "part").exists())
        self.export()
        self.assertEqual(self.results()[0][RAW], "verified")
        self.assertEqual(len(self.s3.get_calls), 1)

    def test_corrupt_prepared_staging_is_preserved_as_conflict(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        with patch.object(backup.os, "link", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.export()
        self.object_path(RAW, "part").write_bytes(b"bad")
        self.export()
        self.assertEqual(self.results()[0][RAW], "staging_hash_mismatch")
        self.assertEqual(self.object_path(RAW, "part").read_bytes(), b"bad")
        self.assertEqual(len(self.s3.get_calls), 1)

    def test_conflicting_preexisting_file_never_overwritten(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        self.object_path().parent.mkdir()
        self.object_path().write_bytes(b"conflict")
        self.export()
        self.assertEqual(self.object_path().read_bytes(), b"conflict")
        self.assertEqual(self.results()[0][RAW], "conflicting_file")
        self.assertFalse(self.s3.get_calls)

    def test_missing_and_altered_files_detected_without_repair(self):
        for changed, expected in ((b"def", "hash_mismatch"), (b"abcd", "size_mismatch"), (None, "missing")):
            with self.subTest(expected=expected):
                self.root = Path(self.temporary.name) / expected
                self.s3.add(RAW, b"abc")
                self.inventory()
                self.export()
                if changed is None:
                    self.object_path().unlink()
                else:
                    self.object_path().write_bytes(changed)
                reads = len(self.s3.get_calls)
                self.assertEqual(self.results()[0][RAW], expected)
                self.export()
                self.assertEqual(len(self.s3.get_calls), reads)

    def test_metadata_and_inventory_corruption_detected(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        self.export()
        path = self.object_path(RAW, "json")
        wrapper = json.loads(path.read_bytes())
        wrapper["value"]["metadata"]["source"] = "changed"
        path.write_text(json.dumps(wrapper))
        self.assertEqual(self.results()[0][RAW], "invalid_receipt_or_path")
        page = self.root / "inventory/0/00000000.json"
        page.unlink()
        with self.assertRaises(FileNotFoundError):
            list(backup.verify(self.root))

    def test_existing_persistence_contract_and_raw_links(self):
        result = normalize(b"synthetic malformed source response", NOW)
        receipt = persist(result, s3=self.s3, bucket="synthetic-private-bucket",
                          collection_id=UUID(int=1))
        self.inventory()
        self.export()
        records = {item["key"]: item for item in backup.verify(self.root) if "key" in item}
        self.assertEqual(records[receipt.normalized_key]["relationship"], "raw_link_verified")
        self.assertEqual(records[receipt.raw_key]["relationship"], "normalized_receipt_present")
        self.assertEqual(self.object_path(receipt.raw_key).read_bytes(), result.raw_response)
        self.assertEqual(self.object_path(receipt.normalized_key).read_bytes(),
                         self.s3.objects[receipt.normalized_key]["body"])

    def test_receipt_semantic_metadata_inconsistency(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        self.export()
        name = str(self.object_path(RAW, "json").relative_to(self.root))
        receipt = backup._read(self.root, name)
        receipt["source_content_length"] = 9
        backup._write(self.root, name, receipt)
        self.assertEqual(self.results()[0][RAW], "metadata_inconsistent")
        self.export()
        self.assertEqual(len(self.s3.get_calls), 1)

    def test_wrong_lengths_and_checksums_fail(self):
        scenarios = (({}, b"ab", {}), ({}, b"abcd", {}),
                     ({"ContentLength": 4}, b"abc", {}),
                     ({}, b"abc", {"sha256": "0" * 64}),
                     ({"ChecksumType": "FULL_OBJECT", "ChecksumSHA256": base64.b64encode(b"wrong").decode()}, b"abc", {}))
        for number, (overrides, actual, metadata) in enumerate(scenarios):
            with self.subTest(number=number):
                self.root = Path(self.temporary.name) / str(number)
                self.s3.add(RAW, b"abc", metadata)
                self.inventory()
                self.s3.objects[RAW]["body"] = actual
                self.s3.overrides[RAW] = overrides
                self.export()
                self.assertEqual(self.results()[0][RAW], "failed")
                self.assertFalse(self.object_path().exists())

    def test_source_changed_or_disappeared_reported(self):
        for scenario in ("changed", "disappeared", "read_error"):
            with self.subTest(scenario=scenario):
                self.root = Path(self.temporary.name) / scenario
                self.s3.add(RAW, b"abc")
                self.inventory()
                if scenario == "changed":
                    self.s3.overrides[RAW] = {"ETag": '"changed"'}
                elif scenario == "disappeared":
                    del self.s3.objects[RAW]
                else:
                    self.s3.read_errors.add(RAW)
                self.export()
                self.assertEqual(self.results()[0][RAW], "failed")
                self.s3.overrides.clear()

    def test_partial_listing_error_and_resume_no_completeness_claim(self):
        self.s3.add(RAW, b"a")
        self.s3.add(evidence_keys(NOW, UUID(int=2))[0], b"b")
        self.s3.list_error_token = "1"
        manifest = self.inventory(page_size=1)
        self.assertEqual(manifest["inventory"][0]["pages"], 1)
        self.assertEqual(manifest["inventory"][0]["error"], "OSError")
        self.export()
        self.assertFalse(self.results()[1]["inventory_complete"])
        self.s3.list_error_token = None
        self.inventory(page_size=1)
        self.export()
        self.assertTrue(self.results()[1]["inventory_complete"])
        self.assertEqual(self.s3.get_calls.count(RAW), 1)

    def test_invalid_pagination_and_out_of_scope_keys(self):
        def no_token(page):
            page["IsTruncated"] = True
            page.pop("NextContinuationToken", None)
            return page
        def wrong_count(page):
            page["KeyCount"] = 100
            return page
        def unsafe_scope(page):
            page["Contents"][0]["Key"] = "../outside"
            return page
        def duplicate_key(page):
            if page["Contents"]:
                page["Contents"].append(dict(page["Contents"][0]))
                page["KeyCount"] += 1
            return page
        def repeated_token(page):
            page["IsTruncated"] = True
            page["NextContinuationToken"] = "repeated"
            return page
        for transform in (no_token, wrong_count, unsafe_scope, duplicate_key, repeated_token):
            self.root = Path(self.temporary.name) / transform.__name__
            self.s3.add(RAW, b"abc")
            self.s3.page_transform = transform
            manifest = self.inventory()
            self.assertFalse(manifest["inventory"][0]["complete"])
            self.assertTrue(manifest["inventory"][0]["error"])

    def test_inventory_interruption_after_page_write_resumes_checkpoint(self):
        self.s3.add(RAW, b"abc")
        original = backup._write
        def interrupted(root, name, value):
            if name == "manifest.json" and value["inventory"][0]["pages"]:
                raise KeyboardInterrupt()
            return original(root, name, value)
        with patch.object(backup, "_write", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.inventory()
        self.assertFalse(self.results()[1]["inventory_complete"])
        self.inventory()
        self.export()
        self.assertEqual(self.results()[0][RAW], "verified")

    def test_publication_failure_is_diagnostic_and_can_resume(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        with patch.object(backup.os, "link", side_effect=OSError("synthetic secret")):
            self.export()
        receipt = backup._read(self.root, str(self.object_path(RAW, "json").relative_to(self.root)))
        self.assertEqual(receipt["status"], "prepared")
        self.assertEqual(receipt["error"], "OSError")
        self.assertNotIn(b"synthetic secret", self.object_path(RAW, "json").read_bytes())
        self.export()
        receipt = backup._read(self.root, str(self.object_path(RAW, "json").relative_to(self.root)))
        self.assertIsNone(receipt["error"])
        self.assertEqual(len(self.s3.get_calls), 1)

    def test_digest_collision_detects_identity_conflict(self):
        self.s3.add(RAW, b"a")
        other = evidence_keys(NOW, UUID(int=2))[0]
        self.s3.add(other, b"b")
        self.inventory()
        with patch.object(backup, "_key_id", return_value="0" * 64):
            with self.assertRaisesRegex(backup.BackupError, "identity conflict"):
                self.export()
        self.assertEqual((self.root / ("objects/" + "0" * 64 + ".bin")).read_bytes(), b"a")

    def test_symlink_and_repository_destinations_rejected(self):
        self.root.mkdir()
        (self.root / ".git").mkdir()
        with self.assertRaisesRegex(backup.BackupError, "outside Git"):
            self.inventory()
        (self.root / ".git").rmdir()
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        try:
            (self.root / "objects").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("host does not allow symlink creation")
        self.s3.add(RAW, b"a")
        self.inventory()
        with self.assertRaisesRegex(backup.BackupError, "unsafe"):
            self.export()
        self.assertFalse(list(outside.iterdir()))

    def test_simulated_symlink_rejection_without_host_privilege(self):
        self.s3.add(RAW, b"a")
        self.inventory()
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path.name == "objects" or original(path)):
            with self.assertRaisesRegex(backup.BackupError, "unsafe"):
                self.export()
        self.assertFalse(self.s3.get_calls)

    def test_stale_writer_lock_and_source_identity_rejected(self):
        self.inventory()
        (self.root / "writer.lock").write_text("stale")
        with self.assertRaisesRegex(backup.BackupError, "locked"):
            self.export()
        (self.root / "writer.lock").unlink()
        with self.assertRaisesRegex(backup.BackupError, "source or cutoff"):
            backup.inventory(s3=self.s3, bucket="other", directory=self.root,
                             paused_cutoff_utc=CUTOFF)

    def test_unattested_cutoff_never_claims_consistent_inventory(self):
        self.s3.add(RAW, b"a")
        self.inventory(cutoff=None)
        self.s3.add(evidence_keys(NOW, UUID(int=3))[0], b"later")
        self.export()
        statuses, coverage = self.results()
        self.assertEqual(statuses, {RAW: "verified"})
        self.assertTrue(coverage["inventory_complete"])
        self.assertFalse(coverage["consistent_cutoff_attested"])
        with self.assertRaisesRegex(backup.BackupError, "cutoff differs"):
            self.inventory()

    def test_runtime_and_transfer_budgets_remain_incomplete(self):
        self.s3.add(RAW, b"abcdef")
        self.inventory()
        self.export(max_bytes=2)
        self.assertEqual(self.results()[0][RAW], "failed")
        self.export()
        self.assertEqual(self.results()[0][RAW], "verified")

    def test_exact_size_budget_and_inventory_runtime_limit(self):
        self.s3.add(RAW, b"abc")
        with patch.object(backup.time, "monotonic", side_effect=[0, 2, 2]):
            manifest = self.inventory(max_seconds=1)
        self.assertFalse(any(state["complete"] for state in manifest["inventory"]))
        self.assertFalse(self.s3.list_calls)
        self.inventory()
        self.export(max_bytes=3)
        self.assertEqual(self.results()[0][RAW], "verified")

    def test_offline_verify_and_cli_gate_do_not_import_sdk(self):
        self.s3.add(RAW, b"abc")
        self.inventory()
        self.export()
        with patch.dict("sys.modules", {"boto3": None, "botocore": None}), patch("builtins.print"), redirect_stderr(StringIO()):
            self.assertEqual(backup.main(["verify", str(self.root)]), 0)
            with self.assertRaises(SystemExit) as error:
                backup.main(["export", str(self.root)])
            self.assertEqual(error.exception.code, 2)
