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
BASE = inc.source_key('r', '00' + '1'*30)
RAW = inc.source_key('r', '01' + '2'*30)
NORM = inc.source_key('n', '02' + '3'*30)

class FakeAzure:
    def __init__(self, baseline):
        self.data = {inc.BASELINE_CATALOG_BLOB: transfer.wrapped(baseline)}
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
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        parent = Path(cls.fixture.name)
        s3 = FakeS3()
        s3.add(BASE, b'baseline')
        directory = parent / 'baseline'
        backup.inventory(s3=s3, bucket='synthetic-private-bucket', directory=directory, paused_cutoff_utc=CUTOFF)
        backup.export(s3=s3, directory=directory)
        cls.baseline = inc.build_baseline_catalog(directory, output_directory=parent/'catalog',
                                                  verified_archive_sha256=inc.BASELINE_SHA256)
    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name)
        self.azure = FakeAzure(self.baseline)
        self.s3 = FakeS3()
        self.s3.add(BASE, b'baseline')
        self.original = self.azure.data[inc.BASELINE_CATALOG_BLOB]
    def add(self, key=RAW, body=b'\x00\xffsynthetic', metadata=None):
        self.s3.add(key, body, metadata)
    def pages(self):
        return {prefix: [{'request_token': None, 'next_token': None, 'objects': [
            {'key': key, 'size': v['ContentLength'], 'etag': v['ETag'], 'last_modified': v['LastModified'].isoformat()}
            for key,v in sorted(self.s3.objects.items()) if key.startswith(prefix)]}] for prefix in backup.PREFIXES}
    def run_transfer(self, pages=None, **limits):
        result = transfer.run_backup(s3=self.s3, azure=self.azure, bucket='synthetic-private-bucket',
            baseline_catalog_sha256=backup._digest(self.baseline), inventories=self.pages() if pages is None else pages,
            limits=replace(transfer.Limits(), **limits), scratch_parent=self.parent)
        self.assertFalse(list(self.parent.glob('backup-*')), 'scratch leaked')
        return result
    def unchanged(self):
        self.assertEqual(self.azure.data[inc.BASELINE_CATALOG_BLOB], self.original)
        self.assertFalse(any(n.endswith('.verified.json') for n in self.azure.data))
    def archive(self):
        names = [n for n in self.azure.data if n.endswith('.zip')]
        self.assertEqual(len(names), 1)
        archive = ZipFile(BytesIO(self.azure.data[names[0]]))
        return names[0], archive, transfer.decode(archive.read(transfer.MANIFEST))

    def test_exact_bytes_empty_metadata_and_completion_commit(self):
        payload = b'\x00\xffsynthetic'
        self.add(metadata={'sha256': hashlib.sha256(payload).hexdigest(), 'unrestricted-key': 'Unicode \u00e9'})
        self.add(NORM, b'')
        self.s3.objects[RAW]['VersionId'] = 'version-1'
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['operations']['azure_writes'], 3)
        self.assertEqual(result['operations']['azure_reads'], 5)
        self.assertEqual(set(self.s3.get_calls), {RAW, NORM})
        name, archive, manifest = self.archive()
        with archive:
            for receipt in manifest['receipts'].values():
                source = self.s3.objects[receipt['key']]
                self.assertEqual(archive.read(receipt['payload_member']), source['body'])
                self.assertEqual(receipt['metadata'], source['Metadata'])
                self.assertEqual(receipt['content_type'], source['ContentType'])
        proof = transfer.decode(self.azure.data[name + '.verified.json'])
        inc.validate_completion(proof, 'synthetic-private-bucket')
        self.assertEqual(proof['sha256'], hashlib.sha256(self.azure.data[name]).hexdigest())
        with tempfile.TemporaryDirectory(dir=self.parent) as recovered:
            path = Path(recovered) / 'batch.zip'
            path.write_bytes(self.azure.data[name])
            runner = transfer.Runner(self.s3, self.azure, 'synthetic-private-bucket', Path(recovered),
                                     transfer.Budget(transfer.Limits()))
            restored = runner.inspect_archive(path, proof)
            self.assertEqual(restored['receipts'], manifest['receipts'])
            self.assertEqual(next(r['version_id'] for r in restored['receipts'].values() if r['key']==RAW), 'version-1')
        self.assertTrue(all(body.closed for body in self.s3.streams + self.azure.streams))
        self.s3.get_calls.clear(); self.azure.reads.clear(); self.azure.creates.clear()
        again = self.run_transfer()
        self.assertTrue(again['complete'], again)
        self.assertFalse(self.s3.get_calls)
        self.assertFalse(self.azure.creates)
        self.assertEqual(self.azure.reads, [inc.BASELINE_CATALOG_BLOB, name + '.verified.json'])

    def test_changed_revision_retained_and_missing_source_reported(self):
        self.add()
        self.assertTrue(self.run_transfer()['complete'])
        old = dict(self.azure.data)
        self.s3.objects[RAW]['ETag'] = 'changed'
        self.assertTrue(self.run_transfer()['complete'])
        self.assertEqual(len([n for n in self.azure.data if n.endswith('.verified.json')]), 2)
        self.assertTrue(all(self.azure.data[n] == data for n,data in old.items()))
        del self.s3.objects[RAW]
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(sum(p['previous_revision_not_visible'] for p in result['sweep']['partitions'].values()), 2)

    def test_interruption_before_completion_reuses_zip_even_after_cohort_changes(self):
        self.add()
        def fail(name):
            if name.endswith('.verified.json'):
                raise OSError('interrupted completion')
        self.azure.before_create = fail
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.azure.before_create = lambda name: None
        self.add(NORM, b'new cohort member')
        self.s3.get_calls.clear()
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(self.s3.get_calls, [NORM])
        self.assertEqual(result['committed_batches'], 2)

    def prepare_without_archive(self):
        self.add()
        def interrupted(name):
            if name.endswith('.zip'):
                raise OSError('interrupted before archive upload')
        self.azure.before_create = interrupted
        result = self.run_transfer(max_attempts=1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['pending_intents'], 1)
        self.azure.before_create = lambda name: None
        prepared = {n: data for n, data in self.azure.data.items() if n.endswith('.prepared.json')}
        self.assertEqual(len(prepared), 1)
        self.assertFalse(any(n.endswith('.zip') for n in self.azure.data))
        return prepared

    def test_prepared_only_unavailable_original_revision_remains_incomplete(self):
        prepared = self.prepare_without_archive()
        self.s3.objects[RAW]['ETag'] = 'changed revision'
        result = self.run_transfer()
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['pending_intents'], 1)
        self.assertEqual(result['committed_batches'], 1)
        self.assertIn('unresolved prepared intents', result['error'])
        self.assertTrue(all(self.azure.data[n] == data for n, data in prepared.items()))
        del self.s3.objects[RAW]
        self.s3.get_calls.clear()
        result = self.run_transfer()
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['pending_intents'], 1)
        self.assertFalse(self.s3.get_calls)
        self.assertTrue(result['sweep']['complete'])

    def test_prepared_only_retry_resolves_current_pending_count(self):
        prepared = self.prepare_without_archive()
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['pending_intents'], 0)
        self.assertIsNone(result['error'])
        self.assertEqual(result['committed_batches'], 1)
        for name, data in prepared.items():
            self.assertEqual(self.azure.data[name], data)
            proof = transfer.decode(self.azure.data[name.replace('.prepared.json', '.verified.json')])
            self.assertEqual(proof['status'], 'destination_verified')
        self.s3.get_calls.clear()
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['pending_intents'], 0)
        self.assertFalse(self.s3.get_calls)

    def test_prepared_only_changed_batch_composition_does_not_clear_old_intent(self):
        prepared = self.prepare_without_archive()
        self.add(NORM, b'new cohort member')
        result = self.run_transfer()
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['pending_intents'], 1)
        self.assertEqual(result['committed_batches'], 1)
        proofs = [transfer.decode(data) for n, data in self.azure.data.items() if n.endswith('.verified.json')]
        self.assertEqual(len(proofs), 1)
        self.assertEqual(len(proofs[0]['coverage']), 2)
        for name, data in prepared.items():
            self.assertEqual(self.azure.data[name], data)
            self.assertNotIn(name.replace('.prepared.json', '.verified.json'), self.azure.data)
        self.s3.get_calls.clear()
        result = self.run_transfer()
        self.assertFalse(result['complete'], result)
        self.assertEqual(result['pending_intents'], 1)
        self.assertFalse(self.s3.get_calls)

    def test_lost_completion_ack_recovers_without_source_or_archive_reads(self):
        self.add()
        self.azure.after_create = lambda name: (_ for _ in ()).throw(OSError('lost ack')) if name.endswith('.verified.json') else None
        self.assertTrue(self.run_transfer()['complete'])
        self.azure.after_create = lambda name: None
        self.s3.get_calls.clear(); self.azure.reads.clear()
        self.assertTrue(self.run_transfer()['complete'])
        self.assertFalse(self.s3.get_calls)
        self.assertFalse(any(n.endswith('.zip') for n in self.azure.reads))

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
        self.azure.before_create = lambda name: None
        self.s3.get_calls.clear()
        self.assertTrue(self.run_transfer(max_batch_objects=1)['complete'])
        self.assertEqual(self.s3.get_calls, [NORM])

    def test_empty_partial_over_limit_listing_and_invalid_baseline_fail_closed(self):
        self.assertTrue(self.run_transfer()['complete'])
        self.assertFalse(self.s3.get_calls); self.assertFalse(self.azure.creates)
        pages = self.pages(); pages[backup.PREFIXES[0]][0]['next_token'] = 'interrupted'
        self.assertFalse(self.run_transfer(pages)['complete']); self.assertFalse(self.s3.get_calls)
        self.assertFalse(self.run_transfer(max_azure_lists=0)['complete'])
        self.azure.data[inc.BASELINE_CATALOG_BLOB] = transfer.wrapped(dict(self.baseline, bucket='fabricated'))
        self.assertFalse(self.run_transfer()['complete'])

    def test_paged_metadata_and_completion_immutable_conflict(self):
        self.add(); self.assertTrue(self.run_transfer()['complete'])
        self.azure.list_size = 1
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['operations']['azure_lists'], 3)
        self.assertFalse(self.run_transfer(max_azure_lists=2)['complete'])
        name, archive, _ = self.archive(); archive.close()
        del self.azure.data[name+'.verified.json']
        def conflict(blob):
            if blob.endswith('.verified.json'):
                self.azure.data[blob] = b'conflicting completion'
        self.azure.before_create = conflict
        self.assertFalse(self.run_transfer()['complete'])
        self.assertEqual(self.azure.data[name+'.verified.json'], b'conflicting completion')

    def test_runtime_retry_and_invalid_listing_fail_closed(self):
        self.add()
        with patch.object(transfer.time, 'monotonic', side_effect=[0, 2]):
            self.assertFalse(self.run_transfer(max_seconds=1)['complete'])
        with self.assertRaises(transfer.TransferError):
            self.run_transfer(max_attempts=0)
        for page in ({'blobs': [], 'next_token': 'empty-truncated'},
                     {'blobs': [{'name': 'incremental/batches/not-a-batch', 'size': 1}], 'next_token': None}):
            with patch.object(self.azure, 'list_blobs', return_value=page):
                self.assertFalse(self.run_transfer()['complete'])
        self.assertFalse(self.s3.get_calls)

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

if __name__ == "__main__":
    unittest.main()
