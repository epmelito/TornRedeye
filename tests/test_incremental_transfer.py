"""Entirely offline source, immutable destination and publication fault tests."""
from dataclasses import replace
from datetime import timedelta
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import evidence_backup as backup
import incremental_backup as inc
import incremental_transfer as transfer
from test_evidence_backup import FakeS3, BoundedBody, CUTOFF, NOW

BASE = inc.source_key('r', '00' + '1' * 30)
RAW = inc.source_key('r', '01' + '2' * 30)
NORM = inc.source_key('n', '02' + '3' * 30)


class FakeAzure:
    def __init__(self, directory):
        self.data = {transfer.ROOT_BLOB: (directory / 'index.json').read_bytes()}
        root = inc.load_index(directory)
        for descriptor in root['shards'].values():
            self.data['index/' + descriptor['location']] = (directory / descriptor['location']).read_bytes()
        self.etags = dict.fromkeys(self.data, 'etag-0')
        self.reads, self.creates, self.roots = [], [], []
        self.before_create = lambda name: None
        self.after_create = lambda name: None
        self.transform_read = lambda name, data: (data, len(data))
        self.before_root = lambda: None
        self.after_root = lambda: None
        self.streams = []

    def open_blob(self, name):
        self.reads.append(name)
        if name not in self.data:
            raise transfer.BlobMissing()
        data, size = self.transform_read(name, self.data[name])
        body = BoundedBody(data)
        self.streams.append(body)
        return transfer.BlobRead(body, size, self.etags[name])

    def consume(self, stream, size):
        chunks = []
        while chunk := stream.read(backup.CHUNK):
            chunks.append(chunk)
        value = b''.join(chunks)
        assert len(value) == size
        return value

    def create_block_blob(self, name, stream, size):
        self.creates.append(name)
        self.before_create(name)
        if name in self.data:
            raise transfer.BlobExists()
        self.data[name] = self.consume(stream, size)
        self.etags[name] = 'etag-immutable'
        self.after_create(name)

    def replace_root(self, name, stream, size, etag):
        self.roots.append(name)
        self.before_root()
        if self.etags[name] != etag:
            raise transfer.RootChanged('concurrent root update')
        self.data[name] = self.consume(stream, size)
        self.etags[name] += '-next'
        self.after_root()


class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        parent = Path(cls.fixture.name)
        s3 = FakeS3()
        s3.add(BASE, b'baseline')
        baseline = parent / 'baseline'
        backup.inventory(s3=s3, bucket='synthetic-private-bucket', directory=baseline,
                         paused_cutoff_utc=CUTOFF)
        backup.export(s3=s3, directory=baseline)
        cls.initial = parent / 'index'
        inc.build_baseline_index(baseline, index_directory=cls.initial,
                                 verified_archive_sha256=inc.BASELINE_SHA256)

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.cache = self.parent / 'index'
        shutil.copytree(self.initial, self.cache)
        self.azure = FakeAzure(self.cache)
        self.s3 = FakeS3()
        self.s3.add(BASE, b'baseline')
        self.original = self.azure.data[transfer.ROOT_BLOB]
        self.sequence = 0

    def add(self, key=RAW, body=b'\x00\xffsynthetic', metadata=None):
        self.s3.add(key, body, metadata)
        return key

    def plan(self):
        self.sequence += 1
        pages = {}
        for prefix in backup.PREFIXES:
            objects = [{'key': key, 'size': value['ContentLength'], 'etag': value['ETag'],
                        'last_modified': value['LastModified'].isoformat()}
                       for key, value in sorted(self.s3.objects.items()) if key.startswith(prefix)]
            pages[prefix] = [{'request_token': None, 'next_token': None, 'objects': objects}]
        output = self.parent / f'plan-{self.sequence}'
        result = inc.plan_reconciliation(self.cache, pages, output_directory=output)
        self.assertTrue(result['complete'], result)
        return output

    def run_transfer(self, plan=None, **limits):
        result = transfer.transfer_plan(s3=self.s3, azure=self.azure, index_directory=self.cache,
            plan_directory=plan or self.plan(), limits=replace(transfer.Limits(), **limits))
        self.assertFalse(list(self.parent.glob('transfer-*')), 'payload scratch leaked')
        return result

    def unchanged(self):
        self.assertEqual(self.azure.data[transfer.ROOT_BLOB], self.original)
        self.assertEqual((self.cache / 'index.json').read_bytes(), self.original)
        inc.read_shard(self.cache, '00')

    def archive(self):
        names = [n for n in self.azure.data if n.endswith('.zip')]
        self.assertEqual(len(names), 1)
        archive = ZipFile(BytesIO(self.azure.data[names[0]]))
        manifest = transfer.decode(archive.read(transfer.MANIFEST))
        return names[0], archive, manifest

    def test_exact_binary_empty_independent_metadata_and_verified_index(self):
        payload = b'\x00\xffsynthetic'
        self.add(metadata={'sha256': hashlib.sha256(payload).hexdigest(), 'unrestricted-key': 'Unicode \u00e9'})
        self.add(NORM, b'')
        self.s3.objects[RAW]['VersionId'] = 'version-1'
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertEqual(result['committed_batches'], 1)
        self.assertEqual(set(self.s3.get_calls), {RAW, NORM})
        self.assertEqual(result['operations']['s3_list'], 0)
        self.assertEqual(result['operations']['azure_reads'], 266)
        self.assertEqual(result['operations']['azure_writes'], 8)
        name, archive, manifest = self.archive()
        with archive:
            by_key = {r['key']: r for r in manifest['receipts'].values()}
            for key in (RAW, NORM):
                receipt = by_key[key]
                self.assertEqual(archive.read(receipt['payload_member']), self.s3.objects[key]['body'])
                self.assertEqual(receipt['metadata'], self.s3.objects[key]['Metadata'])
                self.assertEqual(receipt['content_type'], self.s3.objects[key]['ContentType'])
            self.assertEqual(by_key[RAW]['version_id'], 'version-1')
        root = inc.load_index(self.cache)
        descriptor = root['batches'][manifest['batch']]
        proof = transfer.decode(self.azure.data[descriptor['verification_blob']])
        self.assertEqual(proof['status'], 'destination_verified')
        self.assertEqual(proof['sha256'], hashlib.sha256(self.azure.data[name]).hexdigest())
        self.assertEqual(root['baseline'], transfer.decode(self.original)['baseline'])
        self.assertTrue(all(body.closed for body in self.s3.streams + self.azure.streams))

    def test_archive_mapping_and_payload_checks_not_only_outer_checksum(self):
        self.add()
        self.assertTrue(self.run_transfer()['complete'])
        name, archive, manifest = self.archive()
        members = {info.filename: archive.read(info.filename) for info in archive.infolist()}
        archive.close()
        proof = transfer.decode(self.azure.data[name + '.verified.json'])
        with tempfile.TemporaryDirectory(dir=self.parent) as temporary:
            scratch = Path(temporary)
            runner = transfer.Runner(self.s3, self.azure, self.cache, scratch,
                                     transfer.Budget(transfer.Limits()))
            for failure in ('mapping', 'payload', 'manifest'):
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
                    with self.assertRaises(transfer.TransferError):
                        runner.inspect_archive(path, expected)

    def test_unchanged_restart_no_source_get_or_shard_write(self):
        self.add()
        plan = self.plan()
        self.assertTrue(self.run_transfer(plan)['complete'])
        self.s3.get_calls.clear()
        self.azure.creates.clear()
        self.assertTrue(self.run_transfer(plan)['complete'])
        self.assertFalse(self.s3.get_calls)
        self.assertFalse([n for n in self.azure.creates if n.startswith('index/shards/')])
        self.assertEqual(len(self.azure.roots), 1)

    def test_changed_revision_appended(self):
        self.add()
        self.assertTrue(self.run_transfer()['complete'])
        self.s3.objects[RAW]['body'] += b'changed'
        self.s3.objects[RAW]['ContentLength'] += 7
        self.s3.objects[RAW]['ETag'] = '"new opaque etag"'
        self.assertTrue(self.run_transfer()['complete'])
        self.assertEqual(len(inc.read_shard(self.cache, '01')['entries']), 2)

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

    def test_verified_batch_restart_after_shard_failure_no_redownload(self):
        self.add()
        def fail(name):
            if name.startswith('index/shards/'):
                raise OSError('shard failure')
        self.azure.before_create = fail
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.assertTrue(any(n.endswith('.verified.json') for n in self.azure.data))
        self.azure.before_create = lambda name: None
        self.s3.get_calls.clear()
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertFalse(self.s3.get_calls)

    def test_root_failure_previous_coverage_readable(self):
        self.add()
        self.azure.before_root = lambda: (_ for _ in ()).throw(OSError('root unavailable'))
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.azure.before_root = lambda: None
        self.s3.get_calls.clear()
        self.assertTrue(self.run_transfer()['complete'])
        self.assertFalse(self.s3.get_calls)

    def test_root_lost_ack_confirmed_by_readback(self):
        self.add()
        self.azure.after_root = lambda: (_ for _ in ()).throw(OSError('lost root ack'))
        self.assertTrue(self.run_transfer()['complete'])
        self.assertEqual(len(self.azure.roots), 1)

    def test_concurrent_root_change_aborts_cas(self):
        self.add()
        self.azure.before_root = lambda: self.azure.etags.update({transfer.ROOT_BLOB: 'competing-etag'})
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.azure.before_root = lambda: None
        self.assertTrue(self.run_transfer()['complete'])

    def test_remote_advance_after_local_sync_failure_verified_before_adoption(self):
        self.add()
        original_sync = transfer.Runner.sync_cache
        def fail_after_commit(runner, source, candidate):
            if self.azure.roots:
                raise OSError('local cache failure')
            return original_sync(runner, source, candidate)
        with patch.object(transfer.Runner, 'sync_cache', fail_after_commit):
            result = self.run_transfer()
        self.assertFalse(result['complete'])
        self.assertEqual(result['committed_batches'], 1)
        self.assertEqual((self.cache / 'index.json').read_bytes(), self.original)
        self.s3.get_calls.clear()
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertFalse(self.s3.get_calls)
        self.assertEqual(len(inc.read_shard(self.cache, '01')['entries']), 1)

    def test_remote_added_coverage_missing_evidence_refused(self):
        self.add()
        def fail_sync(runner, source, candidate):
            if self.azure.roots:
                raise OSError('local cache failure')
        with patch.object(transfer.Runner, 'sync_cache', fail_sync):
            self.assertFalse(self.run_transfer()['complete'])
        name, archive, _ = self.archive()
        archive.close()
        del self.azure.data[name]
        self.assertFalse(self.run_transfer()['complete'])
        self.assertEqual((self.cache / 'index.json').read_bytes(), self.original)

    def test_missing_or_corrupt_referenced_shard_refused(self):
        shard = inc.load_index(self.cache)['shards']['01']['location']
        name = 'index/' + shard
        data = self.azure.data.pop(name)
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()
        self.azure.data[name] = data[:-1]
        self.assertFalse(self.run_transfer()['complete'])
        self.unchanged()

    def test_failed_second_batch_preserves_first_committed_progress(self):
        self.add()
        self.add(NORM, b'normalized only')
        def fail(name):
            if name.endswith('.zip') and len(self.azure.roots):
                raise OSError('second batch unavailable')
        self.azure.before_create = fail
        result = self.run_transfer(max_batch_objects=1)
        self.assertFalse(result['complete'])
        self.assertEqual(result['committed_batches'], 1)
        self.assertEqual(len(inc.read_shard(self.cache, '01')['entries']), 1)
        self.assertFalse(inc.read_shard(self.cache, '02')['entries'])
        self.azure.before_create = lambda name: None
        self.s3.get_calls.clear()
        self.assertTrue(self.run_transfer(max_batch_objects=1)['complete'])
        self.assertEqual(self.s3.get_calls, [NORM])

    def test_incomplete_sweep_no_get_or_publication(self):
        self.add()
        output = self.plan()
        summary = backup._read(output, 'sweep.json')
        summary['complete'] = False
        backup._write(output, 'sweep.json', summary)
        self.assertFalse(self.run_transfer(output)['complete'])
        self.assertFalse(self.s3.get_calls)
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

    def test_runtime_and_invalid_retry_bounds(self):
        self.add()
        plan = self.plan()
        with patch.object(transfer.time, 'monotonic', side_effect=[0, 2]):
            self.assertFalse(self.run_transfer(plan, max_seconds=1)['complete'])
        with self.assertRaises(transfer.TransferError):
            self.run_transfer(max_attempts=0)

    def test_empty_complete_sweep_no_transfer(self):
        result = self.run_transfer()
        self.assertTrue(result['complete'], result)
        self.assertFalse(self.s3.get_calls)
        self.assertFalse(self.azure.roots)
        self.unchanged()


if __name__ == '__main__':
    unittest.main()
