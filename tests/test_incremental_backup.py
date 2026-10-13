"""Synthetic verified baseline, disposable coverage and complete sweep tests."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import evidence_backup as backup
import incremental_backup as inc
from test_evidence_backup import FakeS3, CUTOFF
IDENTIFIER = '00' + '1' * 30
RAW = inc.source_key('r', IDENTIFIER)
NORMALIZED = inc.source_key('n', IDENTIFIER)

def observation(kind, identifier, **changes):
    item = {'key': inc.source_key(kind, identifier), 'size': 1024,
            'etag': '"synthetic-multipart-etag-2"', 'last_modified': CUTOFF}
    item.update(changes)
    return item

class IncrementalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root, self.index = self.parent / 'baseline', self.parent / 'coverage'
        self.s3 = FakeS3()
        self.s3.add(RAW, b'synthetic raw', {'source': 'synthetic', 'retrieved-at': CUTOFF})
        self.s3.add(NORMALIZED, b'{"raw_evidence":null}')
        backup.inventory(s3=self.s3, bucket='synthetic-private-bucket', directory=self.root,
                         paused_cutoff_utc=CUTOFF, page_size=1)
        backup.export(s3=self.s3, directory=self.root)
        self.baseline = self.build(self.parent / 'catalog')
        self.pin = backup._digest(self.baseline)
        inc.reconstruct_coverage(self.baseline, [], trusted_catalog_sha256=self.pin,
                                 output_directory=self.index)
        self.runs = 0

    def build(self, target=None, **kwargs):
        return inc.build_baseline_catalog(self.root, output_directory=target or self.parent / 'rebuilt',
                                         verified_archive_sha256=inc.BASELINE_SHA256.upper(), **kwargs)

    def pages(self):
        manifest = backup._manifest(self.root)
        return {prefix: [backup._read(self.root, backup._page_name(i, n))
                         for n in range(manifest['inventory'][i]['pages'])]
                for i, prefix in enumerate(backup.PREFIXES)}

    def run_plan(self, pages=None, **kwargs):
        self.runs += 1
        output = self.parent / f'plan-{self.runs}'
        return inc.plan_reconciliation(self.index, self.pages() if pages is None else pages,
                                      output_directory=output, **kwargs), output

    def object_path(self, key, extension):
        return self.root / 'objects' / (backup._key_id(key) + '.' + extension)

    def test_reconstruction_limits_conflicting_revisions_and_input_isolation(self):
        for n, kwargs in enumerate(({'max_records_per_partition': 1}, {'max_partition_bytes': 1},
                                   {'max_spool_bytes': 1})):
            output = self.parent / f'bounded-{n}'
            with self.assertRaises(inc.CatalogError):
                inc.reconstruct_coverage(self.baseline, [], trusted_catalog_sha256=self.pin,
                                         output_directory=output, **kwargs)
            self.assertFalse((output / 'coverage.json').exists())
        original = deepcopy(self.baseline)
        header = inc.reconstruct_coverage(self.baseline, [], trusted_catalog_sha256=self.pin,
                                          output_directory=self.parent / 'isolated')
        header['baseline']['bytes'] = -1
        header['batches']['baseline']['blob'] = 'modified'
        self.assertEqual(original, self.baseline)
        with self.assertRaises(inc.CatalogError):
            inc.reconstruct_coverage(self.baseline, [], trusted_catalog_sha256=self.pin,
                output_directory=self.parent / 'timeout',
                check_runtime=lambda: inc._require(False, 'runtime exceeded'))

    def test_baseline_rows_refer_to_original_verified_receipts(self):
        coverage = inc.load_coverage(self.index)
        for entry in inc.read_coverage_partition(self.index, '00')['entries']:
            reference = inc._reference(entry, coverage)
            receipt = backup._read(self.root, reference['receipt_member'])
            self.assertEqual(receipt['sha256'], reference['sha256'])
            self.assertEqual(receipt['size'], reference['size'])
            self.assertEqual(receipt['metadata'], self.s3.objects[reference['key']]['Metadata'])
            self.assertEqual(reference['batch_sha256'], inc.BASELINE_SHA256)
        with self.assertRaises(inc.CatalogError):
            inc.validate_baseline(self.baseline, '0' * 64)

    def test_incomplete_or_corrupted_disposable_coverage_rejected(self):
        path = self.index / 'records' / '00.jsonl'
        path.write_bytes(path.read_bytes()[:-1])
        result, output = self.run_plan()
        self.assertFalse(result['complete'])
        with self.assertRaises(inc.CatalogError):
            inc.read_plan_partition(output, '00')

    def test_duplicate_coverage_cannot_establish_complete_catalog(self):
        row = self.baseline['coverage'][0]
        batch = backup._digest({'bucket': self.baseline['bucket'], 'objects': ['/'.join(row[:3])]})
        completion = {'schema_version': 2, 'status': 'destination_verified', 'bucket': self.baseline['bucket'],
                      'batch': batch, 'blob': inc.batch_blob(batch), 'size': 1000,
                      'sha256': 'a'*64, 'manifest_sha256': 'b'*64, 'coverage': [row]}
        target = self.parent / 'duplicate'
        with self.assertRaises(inc.CatalogError):
            inc.reconstruct_coverage(self.baseline, [completion], trusted_catalog_sha256=self.pin,
                                     output_directory=target)
        self.assertFalse((target / 'coverage.json').exists())

    def test_five_year_local_partition_memory_and_changed_boundary(self):
        for count in (math.ceil((4782 + 2880*365*5)/256), inc.MAX_RECORDS):
            tracemalloc.start()
            observations = [observation('r' if n%2==0 else 'n', '00'+f'{n//2:030x}') for n in range(count)]
            entries = [[*inc.key_parts(o['key']), inc.revision_fingerprint(o), 'a'*64, o['size'], 'baseline']
                       for o in observations]
            local = {'batches': {'baseline': {'blob': inc.BASELINE_BLOB, 'sha256': inc.BASELINE_SHA256}}}
            for o in observations:
                o['size'] += 1
            result = inc._plan_partition({'entries': entries}, observations, local)
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            self.assertEqual(len(result['changed']), count)
            self.assertEqual(len(result['previous_revision_not_visible']), count)
            self.assertLess(peak, 300*1024**2)
            print('compact_coverage_memory', count, peak)
            del observations, entries, result

    def test_all_256_boundaries_and_both_kinds(self):
        for shard in inc.PARTITIONS:
            for identifier in (shard + '0' * 30, shard + 'f' * 30):
                for kind in inc.KINDS:
                    key = inc.source_key(kind, identifier)
                    self.assertEqual(inc.key_parts(key), (kind, identifier))
        pages = {prefix: [{'request_token': None, 'next_token': None, 'objects': []}]
                 for prefix in backup.PREFIXES}
        for shard in inc.PARTITIONS:
            for kind, (prefix, _) in inc.KINDS.items():
                pages[prefix][0]['objects'].append(observation(kind, shard + 'f' * 30))
        result, output = self.run_plan(pages)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['partitions']), 256)
        self.assertEqual(sum(d['new'] for d in result['partitions'].values()), 512)
        for shard in inc.PARTITIONS:
            self.assertEqual(len(inc.read_plan_partition(output, shard)['new']), 2)

    def test_unknown_key_formats_fail_visibly(self):
        for key in (backup.PREFIXES[0], backup.PREFIXES[0] + 'a.bin', RAW.upper(),
                    RAW.replace(IDENTIFIER, 'g' * 32), RAW.replace('.bin', '.json'),
                    RAW.replace(IDENTIFIER, IDENTIFIER[:8] + '-' + IDENTIFIER[8:]),
                    RAW.replace(IDENTIFIER, '../' + IDENTIFIER), 'unrelated/key'):
            with self.subTest(key=key), self.assertRaises(inc.CatalogError):
                inc.key_parts(key)
        pages = self.pages()
        pages[backup.PREFIXES[0]][0]['objects'][0]['key'] = backup.PREFIXES[0] + 'z-unknown.bin'
        result, output = self.run_plan(pages)
        self.assertFalse(result['complete'])
        self.assertIn('unknown', result['error'])
        self.assertEqual(result['partitions'], {})
        with self.assertRaises(inc.CatalogError):
            inc.read_plan_partition(output, '00')

    def test_invalid_baseline_never_publishes_index_root(self):
        paths = [self.root / backup._page_name(0, 0), self.object_path(RAW, 'json'),
                 self.object_path(RAW, 'bin')]
        for n, path in enumerate(paths):
            data = path.read_bytes()
            for corruption in ('missing', 'corrupt'):
                target = self.parent / f'bad-{n}-{corruption}'
                if corruption == 'missing':
                    path.unlink()
                else:
                    path.write_bytes(b'corrupted')
                with self.assertRaises(inc.CatalogError):
                    self.build(target)
                self.assertFalse((target / 'baseline.json').exists())
                path.write_bytes(data)
        with self.assertRaises(inc.CatalogError):
            inc.build_baseline_catalog(self.root, output_directory=self.parent / 'untrusted',
                                     verified_archive_sha256='0' * 64)

    def test_partial_baseline_manifest_receipt_and_page_chain_rejected(self):
        original = backup._read(self.root, 'manifest.json')
        for field, value in (('complete', False), ('error', 'interrupted')):
            manifest = deepcopy(original)
            manifest['inventory'][0][field] = value
            backup._write(self.root, 'manifest.json', manifest)
            with self.assertRaises(inc.CatalogError):
                self.build(self.parent / field)
        manifest = deepcopy(original)
        manifest['paused_cutoff_utc'] = None
        backup._write(self.root, 'manifest.json', manifest)
        with self.assertRaises(inc.CatalogError):
            self.build(self.parent / 'cutoff')
        backup._write(self.root, 'manifest.json', original)
        name = 'objects/' + backup._key_id(RAW) + '.json'
        receipt = backup._read(self.root, name)
        receipt['status'] = 'prepared'
        backup._write(self.root, name, receipt)
        with self.assertRaises(inc.CatalogError):
            self.build(self.parent / 'prepared')

    def test_inconsistent_page_and_unindexed_payload_rejected(self):
        name = backup._page_name(0, 0)
        original = backup._read(self.root, name)
        changed = deepcopy(original)
        changed['objects'][0]['etag'] = 'changed-without-checkpoint'
        backup._write(self.root, name, changed)
        with self.assertRaises(inc.CatalogError):
            self.build(self.parent / 'chain')
        backup._write(self.root, name, original)
        for n, path in enumerate((self.root / 'objects' / 'unindexed.bin',
                                  self.root / 'inventory' / '0' / '99999999.json')):
            path.write_bytes(b'unindexed evidence')
            with self.assertRaises(inc.CatalogError):
                self.build(self.parent / f'extra-{n}')
            path.unlink()

    def test_unchanged_no_transfer_and_results_are_independent(self):
        pages, before = self.pages(), inc.load_coverage(self.index)
        calls = (len(self.s3.get_calls), len(self.s3.list_calls))
        original = deepcopy(pages)
        result, output = self.run_plan(pages)
        self.assertTrue(result['complete'])
        self.assertFalse(result['consistent_cutoff'])
        self.assertEqual(result['partitions']['00']['already_covered'], 2)
        for field in ('new', 'changed', 'previous_revision_not_visible'):
            self.assertEqual(inc.read_plan_partition(output, '00')[field], [])
        self.assertEqual(inc.load_coverage(self.index), before)
        self.assertEqual(original, pages)
        self.assertEqual(calls, (len(self.s3.get_calls), len(self.s3.list_calls)))
        value = inc.read_coverage_partition(self.index, '00')
        value['entries'][0][2] = 'a' * 64
        self.assertNotEqual(inc.read_coverage_partition(self.index, '00'), value)

    def test_changed_size_etag_timestamp_and_historical_revisions(self):
        for field, value in (('size', 999), ('etag', '"changed"'),
                             ('last_modified', '2026-10-12T00:00:00Z')):
            pages = self.pages()
            pages[backup.PREFIXES[0]][0]['objects'][0][field] = value
            result, output = self.run_plan(pages)
            self.assertTrue(result['complete'])
            plan = inc.read_plan_partition(output, '00')
            self.assertEqual([x['key'] for x in plan['changed']], [RAW])
            self.assertEqual([x['key'] for x in plan['previous_revision_not_visible']], [RAW])
            plan['previous_revision_not_visible'][0]['blob'] = 'mutated result'
            self.assertEqual(inc.load_coverage(self.index)['batches']['baseline']['blob'], inc.BASELINE_BLOB)

    def test_new_key_behind_listing_position_is_found_after_restart(self):
        pages = self.pages()
        pages[backup.PREFIXES[0]][0]['next_token'] = 'interrupted'
        result, _ = self.run_plan(pages)
        self.assertFalse(result['complete'])
        pages = self.pages()
        new = observation('r', '0' * 32)
        pages[backup.PREFIXES[0]][0]['objects'].insert(0, new)
        result, output = self.run_plan(pages)
        self.assertTrue(result['complete'])
        self.assertEqual([x['key'] for x in inc.read_plan_partition(output, '00')['new']], [new['key']])

    def test_raw_only_normalized_only_and_empty_prefixes(self):
        for key in (RAW, NORMALIZED):
            pages = self.pages()
            for prefix in backup.PREFIXES:
                pages[prefix][0]['objects'] = [x for x in pages[prefix][0]['objects'] if x['key'] == key]
            result, output = self.run_plan(pages)
            self.assertTrue(result['complete'])
            self.assertEqual(inc.read_plan_partition(output, '00')['already_covered'], 1)
            self.assertEqual(len(inc.read_plan_partition(output, '00')['previous_revision_not_visible']), 1)
        empty = {p: [{'request_token': None, 'next_token': None, 'objects': []}] for p in backup.PREFIXES}
        result, output = self.run_plan(empty)
        self.assertTrue(result['complete'])
        self.assertEqual(len(inc.read_plan_partition(output, '00')['previous_revision_not_visible']), 2)
        for pages in ({}, {p: [] for p in backup.PREFIXES}):
            result, output = self.run_plan(pages)
            self.assertFalse(result['complete'])
            with self.assertRaises(inc.CatalogError):
                inc.read_plan_partition(output, '00')

    def test_duplicate_conflicting_and_partial_inventory_fail_closed(self):
        for conflict in (False, True):
            pages = self.pages()
            item = deepcopy(pages[backup.PREFIXES[0]][0]['objects'][0])
            if conflict:
                item['etag'] = 'conflicting'
            pages[backup.PREFIXES[0]][0]['objects'].append(item)
            result, _ = self.run_plan(pages)
            self.assertFalse(result['complete'])
            self.assertIn('duplicate', result['error'])
        for change in (lambda p: p.pop(backup.PREFIXES[1]),
                       lambda p: p[backup.PREFIXES[0]][0].update(request_token='old-cursor'),
                       lambda p: p[backup.PREFIXES[0]][0]['objects'][0].pop('etag'),
                       lambda p: p[backup.PREFIXES[0]].append(deepcopy(p[backup.PREFIXES[0]][0]))):
            pages = self.pages()
            change(pages)
            result, output = self.run_plan(pages)
            self.assertFalse(result['complete'])
            self.assertEqual(result['partitions'], {})
            with self.assertRaises(inc.CatalogError):
                inc.read_plan_partition(output, '00')

    def test_limits_and_boundary_fail_closed(self):
        for kwargs in ({'max_objects_per_partition': 1}, {'max_records_per_partition': 1},
                       {'max_pages': 1}, {'max_spool_bytes': 1}, {'max_output_bytes': 1},
                       {'max_partition_bytes': 100}):
            result, output = self.run_plan(**kwargs)
            self.assertFalse(result['complete'])
            self.assertIsNotNone(result['error'])
            with self.assertRaises(inc.CatalogError):
                inc.read_plan_partition(output, '00')
        result, _ = self.run_plan(max_objects_per_partition=2, max_records_per_partition=2, max_pages=2)
        self.assertTrue(result['complete'])
        for kwargs in ({'max_pages': True}, {'max_records_per_partition': 0}, {'max_seconds': float('inf')}):
            with self.assertRaises(inc.CatalogError):
                self.run_plan(**kwargs)
        with patch.object(inc.time, 'monotonic', side_effect=range(10000)):
            result, _ = self.run_plan(max_seconds=0.5)
        self.assertFalse(result['complete'])
        self.assertIn('runtime', result['error'])

    def test_serialized_limit_precedes_publication(self):
        value = {'synthetic': 'large metadata' * 1000}
        with patch.object(backup, '_write') as writer:
            with self.assertRaises(inc.CatalogError):
                inc._write_bounded(self.parent, 'oversized.json', value, 100)
            writer.assert_not_called()
        expected = len(backup._bytes({'value': {'small': 1}, 'sha256': 'a' * 64}))
        self.assertEqual(inc._metadata_size({'small': 1}, expected), expected)
        with self.assertRaises(inc.CatalogError):
            inc._metadata_size({'small': 1}, expected - 1)

    def test_baseline_normalized_links_require_verified_raw_evidence(self):
        import hashlib
        body = b'synthetic raw'
        link = {'bucket': 'synthetic-private-bucket', 'key': RAW,
                'sha256': hashlib.sha256(body).hexdigest(), 'size_bytes': len(body)}
        for n, mode in enumerate(('absent', 'unverified', 'malformed', 'unreadable',
                                   'unassessed', 'mismatched', 'verified')):
            directory = self.parent / f'link-{mode}'
            target = self.parent / f'link-index-{mode}'
            s3 = FakeS3()
            document = {'raw_evidence': link}
            if mode == 'malformed':
                document['raw_evidence'] = []
            if mode == 'mismatched':
                document['raw_evidence'] = dict(link, bucket='wrong-bucket')
            payload = json.dumps(document).encode()
            if mode == 'unreadable':
                payload = b'invalid json'
            if mode == 'unassessed':
                payload = b' ' * (1024 ** 2 + 1)
            if mode == 'verified':
                s3.add(RAW, body)
            s3.add(NORMALIZED, payload)
            backup.inventory(s3=s3, bucket='synthetic-private-bucket', directory=directory,
                             paused_cutoff_utc=CUTOFF)
            backup.export(s3=s3, directory=directory)
            if mode == 'unverified':
                raw_receipt = backup._read(self.root, 'objects/' + backup._key_id(RAW) + '.json')
                raw_receipt['status'] = 'failed'
                backup._write(directory, 'objects/' + backup._key_id(RAW) + '.json', raw_receipt)
                (directory / 'objects' / (backup._key_id(RAW) + '.bin')).write_bytes(body)
            if mode == 'verified':
                index = inc.build_baseline_catalog(directory, output_directory=target,
                                                 verified_archive_sha256=inc.BASELINE_SHA256)
                self.assertEqual(index['baseline']['objects'], 2)
            else:
                with self.subTest(mode=mode), self.assertRaisesRegex(inc.CatalogError, 'relationship unresolved'):
                    inc.build_baseline_catalog(directory, output_directory=target,
                                             verified_archive_sha256=inc.BASELINE_SHA256)
                self.assertFalse((target / 'baseline.json').exists())

if __name__ == "__main__":
    unittest.main()
