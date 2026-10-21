"""Offline conditional reads, immutable commits and interruption recovery."""
from dataclasses import replace
from datetime import timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
import evidence_backup as backup
import incremental_backup as inc
import incremental_transfer as transfer
from test_evidence_backup import FakeS3, BoundedBody, CUTOFF, NOW
from tornredeye.evidence_paths import evidence_keys, FORECAST_PREFIX
from uuid import UUID
RAW, NORM = evidence_keys(NOW, UUID(int=2))

class FakeAzure:
    def __init__(self, anchor=None):
        self.data = {} if anchor is None else {inc.ANCHOR_BLOB: transfer.wrapped(anchor)}
        self.reads, self.creates, self.lists, self.streams = [], [], [], []
        self.before_create = lambda name: None
        self.after_create = lambda name: None
        self.transform_read = lambda name, data: (data, len(data))
        self.etags = {}
        self.list_size = 5000
    def list_blobs(self, prefix, continuation_token, page_size):
        self.lists.append(continuation_token)
        names = sorted(n for n in self.data if n.startswith(prefix))
        start = int(continuation_token or 0)
        end = min(len(names), start + min(page_size, self.list_size))
        return {'blobs': [{'name': n, 'size': len(self.data[n])} for n in names[start:end]],
                'next_token': str(end) if end < len(names) else None}
    def open_blob(self, name):
        self.reads.append(name)
        if name not in self.data:
            raise transfer.BlobMissing()
        data, size = self.transform_read(name, self.data[name])
        body = BoundedBody(data)
        self.streams.append(body)
        return transfer.BlobRead(body, size, 'immutable-etag')
    def create_block_blob(self, name, stream, size):
        self.creates.append(name)
        self.before_create(name)
        if name in self.data:
            raise transfer.BlobExists()
        chunks = []
        while chunk := stream.read(backup.CHUNK):
            chunks.append(chunk)
        value = b''.join(chunks)
        assert len(value) == size
        self.data[name] = value
        self.after_create(name)

class TransferTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name)
        self.anchor = inc.empty_anchor("synthetic-private-bucket")
        self.azure = FakeAzure()
        self.s3 = FakeS3()

    def pages(self):
        return {prefix: [{"request_token": None, "next_token": None, "objects": [
            {"key": key, "size": v["ContentLength"], "etag": v["ETag"], "last_modified": v["LastModified"].isoformat()}
            for key,v in sorted(self.s3.objects.items()) if key.startswith(prefix)]}] for prefix in backup.PREFIXES}

    def run_transfer(self, pages=None, **limits):
        result = transfer.run_backup(s3=self.s3, azure=self.azure, bucket=self.anchor["bucket"],
            anchor_sha256=backup._digest(self.anchor), inventories=self.pages() if pages is None else pages,
            limits=replace(transfer.Limits(), **limits), scratch_parent=self.parent)
        self.assertFalse(list(self.parent.glob("backup-*")))
        return result

    def test_empty_lineage_anchor_and_no_historical_dependency(self):
        result = self.run_transfer()
        self.assertTrue(result["complete"], result)
        self.assertEqual(transfer.decode(self.azure.data[inc.ANCHOR_BLOB]), self.anchor)
        self.assertEqual(len(self.azure.data), 1)
        self.assertEqual(self.s3.get_calls, [])

    def test_full_evidence_and_immutable_forecast_coverage_restore_and_restart(self):
        for key, body in [(RAW,b"\x00\xffraw"), (NORM,b'{"raw_evidence":null}')]+[
                (FORECAST_PREFIX+"epoch-1/2026-10-11T00/"+kind+".json", b"{}")
                for kind in ("forecast","ack","outcome","checkpoint")]:
            self.s3.add(key,body,{"sha256":hashlib.sha256(body).hexdigest()})
        self.s3.add("forecast-state/live-v1/epoch-1/lease.json", b"excluded")
        first = self.run_transfer()
        self.assertTrue(first["complete"], first)
        blob = next(k for k in self.azure.data if k.endswith(".zip"))
        archive = self.parent/"batch.zip"; archive.write_bytes(self.azure.data[blob])
        proof = transfer.decode(self.azure.data[blob+".verified.json"])
        restored = self.parent/"isolated"
        result = transfer.restore_batch(archive, proof, directory=restored)
        self.assertEqual(result["verified_objects"],6)
        self.assertFalse(result["active_state_restored"])
        for row in proof["coverage"]:
            key = inc.source_key(*row[:2])
            self.assertEqual((restored/"objects"/(backup._key_id(key)+".bin")).read_bytes(),self.s3.objects[key]["body"])
            receipt = backup._read(restored,"objects/"+backup._key_id(key)+".json")
            self.assertEqual(receipt["last_modified"], NOW.isoformat())
            self.assertEqual(receipt["metadata"],self.s3.objects[key]["Metadata"])
        transfer.restore_batch(archive,proof,directory=restored)
        before = dict(self.azure.data); self.s3.get_calls.clear()
        self.assertTrue(self.run_transfer()["complete"])
        self.assertEqual(self.s3.get_calls,[])
        self.assertEqual(before,self.azure.data)
        (restored/"objects"/(backup._key_id(RAW)+".bin")).write_bytes(b"conflict")
        with self.assertRaises(transfer.TransferError):
            transfer.restore_batch(archive,proof,directory=restored)

    def test_lost_ack_and_interrupted_completion_recover_without_double_transfer(self):
        self.s3.add(RAW,b"raw")
        def interrupt(name):
            if name.endswith(".verified.json"):
                raise OSError("interrupted completion")
        self.azure.before_create = interrupt
        self.assertFalse(self.run_transfer()["complete"])
        self.azure.before_create = lambda name: None
        self.s3.get_calls.clear()
        self.assertTrue(self.run_transfer()["complete"])
        self.assertFalse(self.s3.get_calls)
        self.assertEqual(sum(k.endswith(".zip") for k in self.azure.data),1)

    def test_lost_archive_ack_is_read_back_and_verified(self):
        self.s3.add(RAW,b"raw")
        self.azure.after_create = lambda name: (_ for _ in ()).throw(OSError("lost ack")) if name.endswith(".zip") else None
        result = self.run_transfer()
        self.assertTrue(result["complete"],result)

    def test_partial_scope_inventory_and_changed_source_fail(self):
        self.s3.add(RAW,b"raw")
        pages=self.pages();pages.pop(backup.PREFIXES[1])
        self.assertFalse(self.run_transfer(pages)["complete"])
        pages=self.pages();pages[backup.PREFIXES[0]][0]["next_token"]="missing-page"
        self.assertFalse(self.run_transfer(pages)["complete"])
        self.assertFalse(self.s3.get_calls)
        self.s3.overrides[RAW]={"ETag":'"changed"'}
        self.assertFalse(self.run_transfer()["complete"])
        self.assertFalse(any(k.endswith(".verified.json") for k in self.azure.data))

    def test_checksum_destination_corruption_and_budget_fail_without_completion(self):
        for failure in ("source","destination","budget"):
            self.azure=FakeAzure();self.s3=FakeS3()
            self.s3.add(RAW,b"raw", {"sha256":"0"*64} if failure=="source" else {})
            if failure=="destination":
                self.azure.transform_read=lambda name,data:(b"x"+data[1:],len(data)) if name.endswith(".zip") else (data,len(data))
            result=self.run_transfer(**({"max_s3_gets":0} if failure=="budget" else {}))
            self.assertFalse(result["complete"],result)
            self.assertFalse(any(k.endswith(".verified.json") for k in self.azure.data))

    def test_late_write_to_old_partition_is_found_by_next_daily_reconciliation(self):
        self.assertTrue(self.run_transfer()["complete"])
        self.s3.add(RAW,b"late")
        self.assertTrue(self.run_transfer()["complete"])
        self.assertEqual(self.s3.get_calls,[RAW])

    def test_immutable_source_deletion_or_change_cannot_report_success(self):
        self.s3.add(RAW, b'original')
        self.assertTrue(self.run_transfer()['complete'])
        original = dict(self.azure.data)
        self.s3.add(RAW, b'changed')
        self.assertFalse(self.run_transfer()['complete'])
        self.s3.objects.clear()
        self.assertFalse(self.run_transfer()['complete'])
        self.assertEqual(original, self.azure.data)

    def test_corrupt_archive_cannot_publish_any_restored_payload(self):
        self.s3.add(RAW, b'original')
        self.assertTrue(self.run_transfer()['complete'])
        blob = next(k for k in self.azure.data if k.endswith('.zip'))
        path = self.parent/'corrupt.zip'
        data = self.azure.data[blob]
        path.write_bytes(b'x'+data[1:])
        proof = transfer.decode(self.azure.data[blob+'.verified.json'])
        destination = self.parent/'isolated'
        with self.assertRaises(transfer.TransferError):
            transfer.restore_batch(path, proof, directory=destination)
        self.assertFalse((destination/'objects').exists())

    def add(self, key=RAW, body=b"synthetic", metadata=None):
        self.s3.add(key, body, metadata)

    def unchanged(self):
        self.assertFalse(any(n.endswith(".verified.json") for n in self.azure.data))

    def archive(self):
        names=[n for n in self.azure.data if n.endswith(".zip")]
        self.assertEqual(len(names),1)
        archive=ZipFile(BytesIO(self.azure.data[names[0]]))
        return names[0],archive,transfer.decode(archive.read(transfer.MANIFEST))

    def test_corrupt_completion_missing_zip_and_orphan_fail_closed(self):
        self.add()
        self.assertTrue(self.run_transfer()['complete'])
        name, archive, _ = self.archive(); archive.close()
        proof = self.azure.data[name+'.verified.json']
        self.azure.data[name+'.verified.json'] = proof[:-1]
        self.assertFalse(self.run_transfer()['complete'])
        self.azure.data[name+'.verified.json'] = proof
        data = self.azure.data.pop(name)
        self.assertFalse(self.run_transfer()['complete'])
        self.azure.data[name] = data
        del self.azure.data[name+'.verified.json']; del self.azure.data[name+'.prepared.json']
        self.assertFalse(self.run_transfer()['complete'])

    def test_failed_second_batch_preserves_first_commit(self):
        self.add(); self.add(NORM, b'normalized only')
        self.azure.before_create = lambda name: (_ for _ in ()).throw(OSError('second batch')) if name.endswith('.zip') and any(n.endswith('.verified.json') for n in self.azure.data) else None
        result = self.run_transfer(max_batch_objects=1)
        self.assertFalse(result['complete']); self.assertEqual(result['committed_batches'], 1)
        covered = {row[1] for name, data in self.azure.data.items() if name.endswith('.verified.json')
                   for row in transfer.decode(data)['coverage']}
        self.azure.before_create = lambda name: None
        self.s3.get_calls.clear()
        self.assertTrue(self.run_transfer(max_batch_objects=1)['complete'])
        self.assertEqual(set(self.s3.get_calls), {RAW, NORM} - covered)
        self.assertEqual(len(self.s3.get_calls), 1)

    def test_archive_mapping_and_payload_checks_not_only_outer_checksum(self):
        self.add()
        self.assertTrue(self.run_transfer()['complete'])
        name, archive, manifest = self.archive()
        members = {info.filename: archive.read(info.filename) for info in archive.infolist()}
        archive.close()
        proof = transfer.decode(self.azure.data[name + '.verified.json'])
        with tempfile.TemporaryDirectory(dir=self.parent) as temporary:
            scratch = Path(temporary)
            runner = transfer.Runner(self.s3, self.azure, 'synthetic-private-bucket', scratch,
                                     transfer.Budget(transfer.Limits()))
            for failure in ('mapping', 'payload', 'manifest', 'completion'):
                with self.subTest(failure=failure):
                    changed = json.loads(json.dumps(manifest))
                    payloads = dict(members)
                    receipt = next(iter(changed['receipts'].values()))
                    if failure == 'mapping':
                        receipt['payload_member'] = '../escape.bin'
                    elif failure == 'payload':
                        body = payloads[receipt['payload_member']]
                        payloads[receipt['payload_member']] = b'x' + body[1:]
                    wrapped = transfer.wrapped(changed)
                    if failure == 'manifest':
                        wrapped = wrapped.replace(b'"sha256":"', b'"sha256":"x', 1)
                    payloads[transfer.MANIFEST] = wrapped
                    path = scratch / 'modified.zip'
                    with ZipFile(BytesIO(self.azure.data[name])) as source, ZipFile(path, 'w') as output:
                        for info in source.infolist():
                            output.writestr(info, payloads[info.filename])
                    expected = dict(proof, manifest_sha256=backup._digest(changed))
                    expected['size'], expected['sha256'] = backup._file_hash(path)
                    if failure == 'completion':
                        expected['coverage'] = [list(row) for row in expected['coverage']]
                        expected['coverage'][0][3] = '0' * 64
                    with self.assertRaises(transfer.TransferError):
                        runner.inspect_archive(path, expected)

    def test_source_returned_revision_mismatch(self):
        for field, value in [('ETag', '"changed"'), ('ContentLength', 999),
                             ('LastModified', NOW + timedelta(seconds=1))]:
            with self.subTest(field=field):
                self.add()
                self.s3.overrides[RAW] = {field: value}
                result = self.run_transfer()
                self.assertFalse(result['complete'])
                self.unchanged()

    def test_source_truncation_checksum_and_read_failure(self):
        self.add()
        self.s3.overrides[RAW] = {'Body': BoundedBody(b'')}
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.s3.overrides.clear()
        self.s3.objects[RAW]['Metadata']['sha256'] = '0' * 64
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.s3.objects[RAW]['Metadata'].clear()
        self.s3.interrupted.add(RAW)
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.s3.interrupted.clear()
        self.s3.read_errors.add(RAW)
        result = self.run_transfer(max_attempts=2)
        self.assertFalse(result['complete'])
        self.assertEqual(result['operations']['s3_gets'], 2)
        self.assertNotIn('secret', result['error'])
        self.unchanged()

    def test_s3_full_object_checksum_rejected(self):
        self.add()
        self.s3.overrides[RAW] = {'ChecksumType': 'FULL_OBJECT', 'ChecksumSHA256': 'invalid'}
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()

    def test_upload_failure_retries_bounded_and_restart(self):
        self.add()
        def fail(name):
            if name.endswith('.zip'):
                raise OSError('synthetic secret')
        self.azure.before_create = fail
        result = self.run_transfer(max_attempts=2)
        self.assertFalse(result['complete'])
        self.assertEqual(len([n for n in self.azure.creates if n.endswith('.zip')]), 2)
        self.unchanged()
        self.azure.before_create = lambda name: None
        self.assertTrue(self.run_transfer()['complete'])

    def test_upload_lost_ack_existing_matching_destination(self):
        self.add()
        def fail(name):
            if name.endswith('.zip'):
                raise OSError('lost acknowledgement')
        self.azure.after_create = fail
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(len([n for n in self.azure.creates if n.endswith('.zip')]), 1)

    def test_existing_conflicting_destination_rejected(self):
        self.add()
        def conflict(name):
            if name.endswith('.zip'):
                self.azure.data[name] = b'conflict'
                self.azure.etags[name] = 'conflict-etag'
        self.azure.before_create = conflict
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()

    def test_corrupt_or_truncated_readback_rejected(self):
        self.add()
        for truncate in (False, True):
            with self.subTest(truncated=truncate):
                def transform(name, data):
                    if name.endswith('.zip'):
                        return (data[:-1], len(data)) if truncate else (b'x' + data[1:], len(data))
                    return data, len(data)
                self.azure.transform_read = transform
                self.assertFalse(self.run_transfer()['complete'])
                self.unchanged()
        self.azure.transform_read = lambda name, data: (data, len(data))
        self.s3.get_calls.clear()
        self.assertTrue(self.run_transfer()['complete'])
        self.assertFalse(self.s3.get_calls)

    def test_readback_unavailable_never_publishes_coverage(self):
        self.add()
        def unavailable(name, data):
            if name.endswith('.zip'):
                raise OSError('readback failure')
            return data, len(data)
        self.azure.transform_read = unavailable
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()

    def test_resource_bounds_fail_closed(self):
        self.add()
        for limit in ('max_object_bytes', 'max_batch_bytes', 'max_archive_bytes',
                      'max_manifest_bytes', 'max_scratch_bytes', 'max_transfer_bytes',
                      'max_s3_gets', 'max_azure_reads', 'max_azure_writes'):
            with self.subTest(limit=limit):
                result = self.run_transfer(**{limit: 0})
                self.assertFalse(result['complete'], result)
                self.unchanged()
