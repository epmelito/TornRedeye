"""Synthetic offline shard, baseline and reconciliation tests."""

from copy import deepcopy
import gc
import json
import math
from pathlib import Path
import tempfile
import time
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


def measure_growth(years, count=None, batch_id='daily-20261011', scenario='unchanged'):
    """Bounded, paired UUID fixtures; no million-record allocations or cloud reads."""
    total = 4782 + 2880 * 365 * years
    count = math.ceil(total / 256) if count is None else count
    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    observations = [observation('r' if n % 2 == 0 else 'n', '00' + f'{n // 2:030x}')
                    for n in range(count)]
    entries = [[*inc.key_parts(item['key']), inc.revision_fingerprint(item), batch_id]
               for item in observations]
    value = {'schema_version': inc.SCHEMA, 'bucket': 'synthetic-private-bucket',
             'shard': '00', 'entries': entries}
    index = {'bucket': value['bucket'], 'batches': {batch_id: {
        'kind': 'increment', 'blob': f'incremental/batches/{batch_id}.7z',
        'sha256': 'a' * 64, 'receipts_member': 'receipts.json'}}}
    serialized = backup._bytes({'value': value, 'sha256': backup._digest(value)})
    decoded = json.loads(serialized)['value']
    inc._validate_entries(decoded, index, '00', inc.MAX_RECORDS)
    if scenario == 'changed':
        for item in observations:
            item['size'] += 1
    result = inc._plan_shard(decoded, observations, index)
    if scenario == 'changed':
        assert len(result['changed']) == count and len(result['previous_revision_not_visible']) == count
    else:
        assert result['already_covered'] == count and not result['changed'] and not result['new']
    output_bytes = len(backup._bytes(result))
    seconds = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    per_record = (len(serialized) - 200) / count
    return {'years': years, 'scenario': scenario, 'result_bytes': output_bytes,
            'projected_objects': total, 'records_per_shard': count,
            'serialized_shard_bytes': len(serialized), 'peak_traced_bytes': peak,
            'processing_seconds': round(seconds, 3),
            'estimated_total_index_bytes': round(per_record * total + 200 * 256),
            'estimated_daily_read_plus_full_rewrite_bytes': round(2 * (per_record * total + 200 * 256)),
            'estimated_scan_spool_bytes': round(sum(len(backup._bytes(x)) + 1 for x in observations) / count * total)}


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.root, self.index = self.parent / 'baseline', self.parent / 'index'
        self.s3 = FakeS3()
        self.s3.add(RAW, b'synthetic raw', {'source': 'synthetic', 'retrieved-at': CUTOFF})
        self.s3.add(NORMALIZED, b'{"raw_evidence":null}')
        backup.inventory(s3=self.s3, bucket='synthetic-private-bucket', directory=self.root,
                         paused_cutoff_utc=CUTOFF, page_size=1)
        backup.export(s3=self.s3, directory=self.root)
        self.build(self.index)
        self.runs = 0

    def build(self, target=None, **kwargs):
        if target is None:
            target = self.parent / 'rebuilt'
        return inc.build_baseline_index(self.root, index_directory=target,
                                       verified_archive_sha256=inc.BASELINE_SHA256.upper(), **kwargs)

    def pages(self):
        manifest = backup._manifest(self.root)
        return {prefix: [backup._read(self.root, backup._page_name(i, n))
                         for n in range(manifest['inventory'][i]['pages'])]
                for i, prefix in enumerate(backup.PREFIXES)}

    def run_plan(self, pages=None, **kwargs):
        self.runs += 1
        output = self.parent / f'plan-{self.runs}'
        result = inc.plan_reconciliation(self.index, self.pages() if pages is None else pages,
                                         output_directory=output, **kwargs)
        return result, output

    def object_path(self, key, extension):
        return self.root / 'objects' / (backup._key_id(key) + '.' + extension)

    def shard_path(self, shard):
        return self.index / inc.load_index(self.index)['shards'][shard]['location']

    def update_shard(self, mutate):
        # deliberately bypass publication validation to test malformed index inputs
        index = inc.load_index(self.index)
        value = inc.read_shard(self.index, '00', index=index)
        mutate(value)
        digest = backup._digest(value)
        location = f'shards/00/{digest}.json'
        backup._write(self.index, location, value)
        index['shards']['00'].update(location=location, sha256=digest, records=len(value['entries']))
        backup._write(self.index, 'index.json', index)

    def add_increment(self, item):
        index = inc.load_index(self.index)
        index['batches']['d0001'] = {'kind': 'increment', 'blob': 'incremental/batches/d0001.7z',
                                   'sha256': 'a' * 64, 'receipts_member': 'receipts.json'}
        backup._write(self.index, 'index.json', index)
        entry = [*inc.key_parts(item['key']), inc.revision_fingerprint(item), 'd0001']
        self.update_shard(lambda v: v['entries'].append(entry))

    def test_baseline_index_and_receipt_fidelity(self):
        index = inc.load_index(self.index)
        self.assertEqual(set(index['shards']), set(inc.SHARDS))
        self.assertEqual(index['baseline']['objects'], 2)
        entries = inc.read_shard(self.index, '00')['entries']
        self.assertEqual({inc.source_key(e[0], e[1]) for e in entries}, {RAW, NORMALIZED})
        for entry in entries:
            reference = inc._reference(entry, index)
            receipt = backup._read(self.root, reference['receipt_member'])
            key = reference['key']
            self.assertEqual(receipt['key'], key)
            self.assertEqual(receipt['bucket'], index['bucket'])
            self.assertEqual(receipt['metadata'], self.s3.objects[key]['Metadata'])
            self.assertEqual(receipt['content_type'], self.s3.objects[key]['ContentType'])
            self.assertEqual(receipt['size'], len(self.s3.objects[key]['body']))
            self.assertEqual(reference['batch_sha256'], inc.BASELINE_SHA256)

    def test_all_256_boundaries_and_both_kinds(self):
        for shard in inc.SHARDS:
            for identifier in (shard + '0' * 30, shard + 'f' * 30):
                for kind in inc.KINDS:
                    key = inc.source_key(kind, identifier)
                    self.assertEqual(inc.key_parts(key), (kind, identifier))
        pages = {prefix: [{'request_token': None, 'next_token': None, 'objects': []}]
                 for prefix in backup.PREFIXES}
        for shard in inc.SHARDS:
            for kind, (prefix, _) in inc.KINDS.items():
                pages[prefix][0]['objects'].append(observation(kind, shard + 'f' * 30))
        result, output = self.run_plan(pages)
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['shards']), 256)
        self.assertEqual(sum(d['new'] for d in result['shards'].values()), 512)
        for shard in inc.SHARDS:
            self.assertEqual(len(inc.read_plan_shard(output, shard)['new']), 2)

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
        self.assertEqual(result['shards'], {})
        with self.assertRaises(inc.CatalogError):
            inc.read_plan_shard(output, '00')

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
                self.assertFalse((target / 'index.json').exists())
                path.write_bytes(data)
        with self.assertRaises(inc.CatalogError):
            inc.build_baseline_index(self.root, index_directory=self.parent / 'untrusted',
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
        pages, before = self.pages(), inc.load_index(self.index)
        calls = (len(self.s3.get_calls), len(self.s3.list_calls))
        original = deepcopy(pages)
        result, output = self.run_plan(pages)
        self.assertTrue(result['complete'])
        self.assertFalse(result['consistent_cutoff'])
        self.assertEqual(result['shards']['00']['already_covered'], 2)
        for field in ('new', 'changed', 'previous_revision_not_visible'):
            self.assertEqual(inc.read_plan_shard(output, '00')[field], [])
        self.assertEqual(inc.load_index(self.index), before)
        self.assertEqual(original, pages)
        self.assertEqual(calls, (len(self.s3.get_calls), len(self.s3.list_calls)))
        value = inc.read_shard(self.index, '00')
        value['entries'][0][2] = 'a' * 64
        self.assertNotEqual(inc.read_shard(self.index, '00'), value)

    def test_changed_size_etag_timestamp_and_historical_revisions(self):
        for field, value in (('size', 999), ('etag', '"changed"'),
                             ('last_modified', '2026-10-12T00:00:00Z')):
            pages = self.pages()
            pages[backup.PREFIXES[0]][0]['objects'][0][field] = value
            result, output = self.run_plan(pages)
            self.assertTrue(result['complete'])
            plan = inc.read_plan_shard(output, '00')
            self.assertEqual([x['key'] for x in plan['changed']], [RAW])
            self.assertEqual([x['key'] for x in plan['previous_revision_not_visible']], [RAW])
            plan['previous_revision_not_visible'][0]['blob'] = 'mutated result'
            self.assertEqual(inc.load_index(self.index)['batches']['baseline']['blob'], inc.BASELINE_BLOB)

    def test_verified_increment_survives_interruption_and_preserves_baseline(self):
        pages = self.pages()
        item = pages[backup.PREFIXES[0]][0]['objects'][0]
        item['etag'] = '"verified-new-revision"'
        self.add_increment(item)
        saved = (self.shard_path('00')).read_bytes()
        incomplete = deepcopy(pages)
        incomplete[backup.PREFIXES[0]][0]['next_token'] = 'interrupted'
        result, _ = self.run_plan(incomplete)
        self.assertFalse(result['complete'])
        self.assertEqual((self.shard_path('00')).read_bytes(), saved)
        result, output = self.run_plan(pages)
        self.assertTrue(result['complete'])
        plan = inc.read_plan_shard(output, '00')
        self.assertEqual(plan['already_covered'], 2)
        self.assertEqual(plan['changed'], [])
        self.assertEqual(plan['previous_revision_not_visible'][0]['batch'], 'baseline')
        old_result, old_output = self.run_plan()
        self.assertTrue(old_result['complete'])
        reference = inc.read_plan_shard(old_output, '00')['previous_revision_not_visible'][0]
        self.assertEqual(reference['batch'], 'd0001')
        self.assertTrue(reference['receipt_entry'].startswith('r/' + IDENTIFIER + '/'))

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
        self.assertEqual([x['key'] for x in inc.read_plan_shard(output, '00')['new']], [new['key']])

    def test_raw_only_normalized_only_and_empty_prefixes(self):
        for key in (RAW, NORMALIZED):
            pages = self.pages()
            for prefix in backup.PREFIXES:
                pages[prefix][0]['objects'] = [x for x in pages[prefix][0]['objects'] if x['key'] == key]
            result, output = self.run_plan(pages)
            self.assertTrue(result['complete'])
            self.assertEqual(inc.read_plan_shard(output, '00')['already_covered'], 1)
            self.assertEqual(len(inc.read_plan_shard(output, '00')['previous_revision_not_visible']), 1)
        empty = {p: [{'request_token': None, 'next_token': None, 'objects': []}] for p in backup.PREFIXES}
        result, output = self.run_plan(empty)
        self.assertTrue(result['complete'])
        self.assertEqual(len(inc.read_plan_shard(output, '00')['previous_revision_not_visible']), 2)
        for pages in ({}, {p: [] for p in backup.PREFIXES}):
            result, output = self.run_plan(pages)
            self.assertFalse(result['complete'])
            with self.assertRaises(inc.CatalogError):
                inc.read_plan_shard(output, '00')

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
            self.assertEqual(result['shards'], {})
            with self.assertRaises(inc.CatalogError):
                inc.read_plan_shard(output, '00')

    def test_missing_corrupt_or_conflicting_shard_data_blocks_completion(self):
        path = self.shard_path('ff')
        original = path.read_bytes()
        for corruption in ('missing', 'corrupted'):
            path.unlink() if corruption == 'missing' else path.write_bytes(b'corrupt')
            result, output = self.run_plan()
            self.assertFalse(result['complete'])
            self.assertEqual(len(result['shards']), 255)
            with self.assertRaises(inc.CatalogError):
                inc.read_plan_shard(output, '00')
            path.write_bytes(original)
        self.update_shard(lambda v: v['entries'].append(list(v['entries'][0])))
        with self.assertRaises(inc.CatalogError):
            inc.read_shard(self.index, '00')

    def test_missing_baseline_entry_descriptor_or_batch_rejected(self):
        index = inc.load_index(self.index)
        index['shards'].pop('ff')
        backup._write(self.index, 'index.json', index)
        with self.assertRaises(inc.CatalogError):
            inc.load_index(self.index)
        self.build(self.parent / 'restored')
        self.index = self.parent / 'restored'
        self.update_shard(lambda v: v['entries'].pop())
        with self.assertRaises(inc.CatalogError):
            inc.read_shard(self.index, '00')

    def test_resealed_wrong_shard_unknown_batch_and_conflicting_baseline_rejected(self):
        original_shard = (self.shard_path('00')).read_bytes()
        original_root = (self.index / 'index.json').read_bytes()
        for mutate in (lambda v: v['entries'][0].__setitem__(1, 'ff' + '1' * 30),
                       lambda v: v['entries'][0].__setitem__(3, 'missing-batch'),
                       lambda v: v['entries'].append([*v['entries'][0][:2], 'a' * 64, 'baseline'])):
            self.update_shard(mutate)
            with self.assertRaises(inc.CatalogError):
                inc.read_shard(self.index, '00')
            (self.shard_path('00')).write_bytes(original_shard)
            (self.index / 'index.json').write_bytes(original_root)
        value = inc.read_shard(self.index, '00')
        value['entries'][0][2] = 'a' * 64
        backup._write(self.index, inc.load_index(self.index)['shards']['00']['location'], value)
        with self.assertRaises(inc.CatalogError):
            inc.read_shard(self.index, '00')

    def test_single_kind_baselines_and_verification_limits(self):
        for n, key in enumerate((RAW, NORMALIZED)):
            directory, target = self.parent / f'single-{n}', self.parent / f'single-index-{n}'
            s3 = FakeS3()
            s3.add(key, b'raw' if key == RAW else b'{"raw_evidence":null}')
            backup.inventory(s3=s3, bucket='synthetic-private-bucket', directory=directory,
                             paused_cutoff_utc=CUTOFF)
            backup.export(s3=s3, directory=directory)
            index = inc.build_baseline_index(directory, index_directory=target,
                                             verified_archive_sha256=inc.BASELINE_SHA256)
            self.assertEqual(index['baseline']['objects'], 1)
            self.assertEqual(inc.source_key(*inc.read_shard(target, '00')['entries'][0][:2]), key)
        for n, kwargs in enumerate(({'max_objects': 1}, {'max_pages': 1}, {'max_bytes': 1},
                                     {'max_records_per_shard': 1}, {'max_shard_bytes': 100})):
            target = self.parent / f'baseline-limit-{n}'
            with self.assertRaises(inc.CatalogError):
                self.build(target, **kwargs)
            self.assertFalse((target / 'index.json').exists())

    def test_limits_and_boundary_fail_closed(self):
        for kwargs in ({'max_objects_per_shard': 1}, {'max_records_per_shard': 1},
                       {'max_pages': 1}, {'max_spool_bytes': 1}, {'max_output_bytes': 1},
                       {'max_shard_bytes': 100}):
            result, output = self.run_plan(**kwargs)
            self.assertFalse(result['complete'])
            self.assertIsNotNone(result['error'])
            with self.assertRaises(inc.CatalogError):
                inc.read_plan_shard(output, '00')
        result, _ = self.run_plan(max_objects_per_shard=2, max_records_per_shard=2, max_pages=2)
        self.assertTrue(result['complete'])
        for kwargs in ({'max_pages': True}, {'max_records_per_shard': 0}, {'max_seconds': float('inf')}):
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
                index = inc.build_baseline_index(directory, index_directory=target,
                                                 verified_archive_sha256=inc.BASELINE_SHA256)
                self.assertEqual(index['baseline']['objects'], 2)
            else:
                with self.subTest(mode=mode), self.assertRaisesRegex(inc.CatalogError, 'relationship unresolved'):
                    inc.build_baseline_index(directory, index_directory=target,
                                             verified_archive_sha256=inc.BASELINE_SHA256)
                self.assertFalse((target / 'index.json').exists())

    def test_interrupted_multishard_publication_preserves_old_root(self):
        old = inc.load_index(self.index)
        old_bytes = (self.index / 'index.json').read_bytes()
        old_shard = inc.read_shard(self.index, '00', index=old)
        candidate = deepcopy(old)
        candidate['batches']['new'] = {'kind': 'increment', 'blob': 'incremental/new.7z',
                                     'sha256': 'b' * 64, 'receipts_member': 'receipts.json'}
        values = {}
        for shard in ('00', 'ff'):
            value = inc.read_shard(self.index, shard, index=old)
            value['entries'].append(['r', shard + '0' * 30, 'c' * 64, 'new'])
            values[shard] = value
        descriptor = inc.stage_shard_revision(self.index, values['00'], index=candidate,
                                              baseline_bytes=old['shards']['00']['baseline_bytes'])
        candidate['shards']['00'] = descriptor
        self.assertEqual((self.index / 'index.json').read_bytes(), old_bytes)
        self.assertEqual(inc.read_shard(self.index, '00'), old_shard)
        ff_digest = backup._digest(values['ff'])
        candidate['shards']['ff'].update(location=f'shards/ff/{ff_digest}.json', sha256=ff_digest, records=1)
        with self.assertRaises(inc.CatalogError):
            inc.publish_index(self.index, candidate)
        self.assertEqual((self.index / 'index.json').read_bytes(), old_bytes)
        candidate['shards']['ff'] = inc.stage_shard_revision(
            self.index, values['ff'], index=candidate, baseline_bytes=0)
        self.assertEqual(inc.stage_shard_revision(self.index, values['00'], index=candidate,
            baseline_bytes=old['shards']['00']['baseline_bytes']), descriptor)
        original_writer = backup._write
        def interrupted(root, name, value):
            if name == 'index.json':
                raise KeyboardInterrupt()
            return original_writer(root, name, value)
        with patch.object(backup, '_write', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            inc.publish_index(self.index, candidate)
        self.assertEqual((self.index / 'index.json').read_bytes(), old_bytes)
        self.assertEqual(inc.read_shard(self.index, '00', index=old), old_shard)
        inc.publish_index(self.index, candidate)
        self.assertEqual(len(inc.read_shard(self.index, '00')['entries']), 3)
        self.assertEqual(inc.read_shard(self.index, '00', index=old), old_shard)
        self.assertTrue((self.index / old['shards']['00']['location']).exists())

    def test_immutable_conflict_and_invalid_descriptor_fail_closed(self):
        index = inc.load_index(self.index)
        value = inc.read_shard(self.index, '00', index=index)
        path = self.shard_path('00')
        original = path.read_bytes()
        path.write_bytes(b'corrupt immutable data')
        with self.assertRaises(backup.BackupError):
            inc.stage_shard_revision(self.index, value, index=index,
                                     baseline_bytes=index['shards']['00']['baseline_bytes'])
        self.assertEqual(path.read_bytes(), b'corrupt immutable data')
        path.write_bytes(original)
        index['shards']['00']['location'] = '../outside.json'
        backup._write(self.index, 'index.json', index)
        with self.assertRaises(inc.CatalogError):
            inc.load_index(self.index)

    def commit_increment_for_publication_tests(self):
        candidate = inc.load_index(self.index)
        candidate['batches']['append'] = {'kind': 'increment', 'blob': 'incremental/append.7z',
                                         'sha256': 'b' * 64, 'receipts_member': 'receipts.json'}
        value = inc.read_shard(self.index, '00')
        item = self.pages()[backup.PREFIXES[0]][0]['objects'][0]
        item['etag'] = '"new-verified-revision"'
        value['entries'].append(['r', IDENTIFIER, inc.revision_fingerprint(item), 'append'])
        candidate['shards']['00'] = inc.stage_shard_revision(
            self.index, value, index=candidate, baseline_bytes=candidate['shards']['00']['baseline_bytes'])
        inc.publish_index(self.index, candidate)
        return candidate

    def test_append_only_publication_rejects_coverage_and_batch_changes(self):
        self.commit_increment_for_publication_tests()
        committed = inc.load_index(self.index)
        committed_bytes = (self.index / 'index.json').read_bytes()
        committed_shard = inc.read_shard(self.index, '00')
        for mode in ('replace-baseline', 'remove-increment', 'reassign-entry', 'change-batch',
                     'remove-batch', 'change-cutoff', 'false-baseline', 'redistribute-baseline-bytes'):
            with self.subTest(mode=mode):
                candidate = deepcopy(committed)
                value = deepcopy(committed_shard)
                changed = False
                if mode == 'replace-baseline':
                    value['entries'][0][1] = '00' + 'e' * 30
                    changed = True
                elif mode == 'remove-increment':
                    value['entries'].pop()
                    changed = True
                elif mode == 'reassign-entry':
                    candidate['batches']['other'] = {'kind': 'increment', 'blob': 'incremental/other.7z',
                        'sha256': 'c' * 64, 'receipts_member': 'receipts.json'}
                    value['entries'][-1][3] = 'other'
                    changed = True
                elif mode == 'change-batch':
                    candidate['batches']['append']['sha256'] = 'c' * 64
                elif mode == 'remove-batch':
                    candidate['batches'].pop('append')
                elif mode == 'change-cutoff':
                    candidate['baseline']['cutoff_utc'] = '2026-10-12T00:00:00+00:00'
                elif mode == 'false-baseline':
                    value['entries'].append(['r', '00' + 'e' * 30, 'a' * 64, 'baseline'])
                    candidate['baseline']['objects'] += 1
                    changed = True
                elif mode == 'redistribute-baseline-bytes':
                    candidate['shards']['00']['baseline_bytes'] -= 1
                    candidate['shards']['ff']['baseline_bytes'] += 1
                if changed:
                    candidate['shards']['00'] = inc.stage_shard_revision(
                        self.index, value, index=candidate,
                        baseline_bytes=candidate['shards']['00']['baseline_bytes'])
                with self.assertRaises(inc.CatalogError):
                    inc.publish_index(self.index, candidate)
                self.assertEqual((self.index / 'index.json').read_bytes(), committed_bytes)
                self.assertEqual(inc.read_shard(self.index, '00'), committed_shard)

    def test_append_only_publication_accepts_new_increment_and_keeps_old_entries(self):
        old = inc.load_index(self.index)
        old_entries = inc.read_shard(self.index, '00')['entries']
        candidate = self.commit_increment_for_publication_tests()
        new_entries = inc.read_shard(self.index, '00')['entries']
        self.assertEqual(len(new_entries), len(old_entries) + 1)
        self.assertEqual(new_entries[-1][:2], old_entries[0][:2])
        self.assertNotEqual(new_entries[-1][2], old_entries[0][2])
        self.assertTrue(all(entry in new_entries for entry in old_entries))
        self.assertEqual(candidate['baseline'], old['baseline'])
        self.assertEqual(candidate['batches']['baseline'], old['batches']['baseline'])
        self.assertEqual(inc.read_shard(self.index, '00', index=old)['entries'], old_entries)

    def test_replacement_publication_requires_valid_committed_root(self):
        candidate = inc.load_index(self.index)
        root_path = self.index / 'index.json'
        original = root_path.read_bytes()
        for mode in ('missing', 'corrupt'):
            root_path.unlink() if mode == 'missing' else root_path.write_bytes(b'corrupt root')
            with self.assertRaises(inc.CatalogError):
                inc.publish_index(self.index, candidate)
            self.assertEqual(root_path.exists(), mode == 'corrupt')
            if mode == 'corrupt':
                self.assertEqual(root_path.read_bytes(), b'corrupt root')
            root_path.write_bytes(original)
        self.assertEqual(inc.load_index(self.index), candidate)

    def test_iterator_interruption_and_diagnostics(self):
        def interrupted():
            yield self.pages()[backup.PREFIXES[0]][0]
            raise OSError('private synthetic detail')
        pages = self.pages()
        pages[backup.PREFIXES[0]] = interrupted()
        result, _ = self.run_plan(pages)
        self.assertFalse(result['complete'])
        self.assertEqual(result['error'], 'OSError')

    def test_growth_measurements_and_headroom(self):
        for years in (1, 5):
            measured = measure_growth(years)
            print('synthetic_scalability=' + json.dumps(measured, sort_keys=True))
            self.assertLess(measured['records_per_shard'] * 3, inc.MAX_RECORDS)
            self.assertLess(measured['serialized_shard_bytes'], inc.MAX_SHARD_BYTES)
            self.assertLess(measured['peak_traced_bytes'], 128 * 1024 ** 2)
            self.assertLess(measured['estimated_scan_spool_bytes'], 4 * 1024 ** 3)
        measured = measure_growth(5, inc.MAX_RECORDS)
        print('synthetic_capacity_boundary=' + json.dumps(measured, sort_keys=True))
        self.assertLess(measured['serialized_shard_bytes'], inc.MAX_SHARD_BYTES)
        self.assertLess(measured['peak_traced_bytes'], 256 * 1024 ** 2)
        worst = measure_growth(5, inc.MAX_RECORDS, batch_id='d' * 64, scenario='changed')
        print('synthetic_changed_capacity_boundary=' + json.dumps(worst, sort_keys=True))
        self.assertLess(worst['serialized_shard_bytes'], inc.MAX_SHARD_BYTES)
        self.assertLess(worst['result_bytes'], 4 * inc.MAX_SHARD_BYTES)
        self.assertLess(worst['peak_traced_bytes'], 512 * 1024 ** 2)
        item = observation('r', '00' + '0' * 30)
        value = {'schema_version': inc.SCHEMA, 'bucket': 'synthetic-private-bucket', 'shard': '00',
                 'entries': [['r', '00' + '0' * 30, inc.revision_fingerprint(item), 'baseline']] * (inc.MAX_RECORDS + 1)}
        with self.assertRaises(inc.CatalogError):
            inc._validate_entries(value, {'bucket': value['bucket'], 'batches': {'baseline': {}}},
                                  '00', inc.MAX_RECORDS)


if __name__ == '__main__':
    unittest.main()
