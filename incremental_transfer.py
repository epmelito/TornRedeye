"""Injected-client incremental transfers; no SDK, credentials or live entry point.

Azure adapter: open_blob(name) -> BlobRead; create_block_blob(name, stream, size)
uses create-only BlockBlob writes; replace_root(name, stream, size, etag) uses
atomic If-Match. Each adapter request has bounded transport timeouts and no hidden
retries. Streams must read in chunks <= evidence_backup.CHUNK. Normalize provider
not-found, create-conflict and precondition failures to the exceptions below.
"""

import base64
from dataclasses import dataclass, fields
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import tempfile
import time
from zipfile import ZipFile, ZipInfo, ZIP_STORED

import evidence_backup as backup
import incremental_backup as index_api

ROOT_BLOB = 'index/index.json'
MANIFEST = 'receipts.json'
FORMAT = 1


class TransferError(backup.BackupError):
    """An explicit integrity, publication or resource failure."""


class BlobMissing(Exception):
    """The requested Azure blob does not exist; not an authorization failure."""


class BlobExists(Exception):
    """A create-only write found an existing immutable blob."""


class RootChanged(TransferError):
    """The conditional root publication lost to another writer."""


@dataclass
class BlobRead:
    body: object
    size: int
    etag: str


@dataclass(frozen=True)
class Limits:
    max_object_bytes: int = 16 * 1024 ** 2
    max_batch_objects: int = 4096
    max_batch_bytes: int = 64 * 1024 ** 2
    max_archive_bytes: int = 128 * 1024 ** 2
    max_manifest_bytes: int = 16 * 1024 ** 2
    max_scratch_bytes: int = 4 * 1024 ** 3
    max_transfer_bytes: int = 8 * 1024 ** 3
    max_s3_gets: int = 8192
    max_azure_reads: int = 4096
    max_azure_writes: int = 1024
    max_attempts: int = 3
    max_seconds: float = 3600


def require(condition, message):
    if not condition:
        raise TransferError(message)


class Budget:
    def __init__(self, limits):
        for field in fields(limits):
            value = getattr(limits, field.name)
            if field.name == 'max_seconds':
                require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                        'invalid runtime limit')
            else:
                require(type(value) is int and value >= 0, 'invalid resource limit')
        require(limits.max_attempts > 0 and limits.max_batch_objects > 0, 'invalid retry or batch limit')
        self.limits, self.started = limits, time.monotonic()
        self.counts = dict(s3_list=0, s3_gets=0, azure_reads=0, azure_writes=0, transfer_bytes=0)

    def runtime(self):
        require(time.monotonic() - self.started < self.limits.max_seconds, 'runtime limit exceeded')

    def operation(self, name):
        self.runtime()
        require(self.counts[name] < getattr(self.limits, 'max_' + name), name + ' limit exceeded')
        self.counts[name] += 1

    def bytes(self, count):
        self.runtime()
        require(self.counts['transfer_bytes'] + count <= self.limits.max_transfer_bytes,
                'transfer byte limit exceeded')
        self.counts['transfer_bytes'] += count

    def call(self, name, operation):
        for attempt in range(self.limits.max_attempts):
            self.operation(name)
            try:
                return operation()
            except OSError:
                if attempt + 1 == self.limits.max_attempts:
                    raise


class UploadStream:
    def __init__(self, stream, budget):
        self.stream, self.budget, self.consumed = stream, budget, 0

    def read(self, size):
        require(type(size) is int and 0 < size <= backup.CHUNK, 'unbounded adapter upload read')
        self.budget.runtime()
        remaining = self.budget.limits.max_transfer_bytes - self.budget.counts['transfer_bytes']
        data = self.stream.read(min(size, max(1, remaining)))
        self.budget.bytes(len(data))
        self.consumed += len(data)
        return data


def wrapped(value):
    return backup._bytes({'value': value, 'sha256': backup._digest(value)})


def decode(data):
    try:
        document = json.loads(data)
        require(document['sha256'] == backup._digest(document['value']), 'manifest checksum mismatch')
        return document['value']
    except (KeyError, TypeError, ValueError) as error:
        raise TransferError('invalid verification metadata') from error


def identity(item):
    kind, uuid = index_api.key_parts(item['key'])
    fingerprint = index_api.revision_fingerprint(item)
    require(item.get('revision', fingerprint) == fingerprint, 'candidate revision inconsistent')
    return f'{kind}/{uuid}/{fingerprint}'


class Runner:
    def __init__(self, s3, azure, cache, scratch, budget):
        self.s3, self.azure, self.cache = s3, azure, Path(cache).absolute()
        self.scratch, self.budget, self.limits = scratch, budget, budget.limits
        self.serial = 0
        self.allocated = {}
        self.current = index_api.load_index(self.cache)
        self.verified_batches = 0
        self.committed_batches = 0

    def path(self):
        self.serial += 1
        return backup._path(self.scratch, f'file-{self.serial:08d}')

    def disk(self, extra=0):
        total = sum(self.allocated.values())
        require(total + extra <= self.limits.max_scratch_bytes, 'scratch byte limit exceeded')

    def track(self, path):
        self.allocated[path] = path.stat().st_size
        self.disk()

    def remove(self, path):
        path.unlink(missing_ok=True)
        self.allocated.pop(path, None)

    def pump(self, body, path, expected, limit):
        require(type(expected) is int and 0 <= expected <= limit, 'object size limit exceeded')
        digest, size = hashlib.sha256(), 0
        try:
            with path.open('xb') as output:
                while True:
                    self.budget.runtime()
                    remaining = self.limits.max_transfer_bytes - self.budget.counts['transfer_bytes']
                    requested = min(backup.CHUNK, expected - size + 1, max(1, remaining))
                    chunk = body.read(requested)
                    require(isinstance(chunk, bytes) and len(chunk) <= requested, 'invalid bounded read')
                    self.budget.bytes(len(chunk))
                    if not chunk:
                        break
                    require(size + len(chunk) <= expected, 'response length mismatch')
                    self.disk(len(chunk))
                    output.write(chunk)
                    output.flush()
                    size += len(chunk)
                    self.allocated[path] = size
                    digest.update(chunk)
            require(size == expected, 'truncated response')
            return digest.hexdigest()
        finally:
            body.close()

    def read_blob(self, name, limit):
        response = self.budget.call('azure_reads', lambda: self.azure.open_blob(name))
        path = self.path()
        try:
            require(isinstance(response.etag, str) and bool(response.etag), 'missing Azure ETag')
            checksum = self.pump(response.body, path, response.size, limit)
            return path, checksum, response.etag
        except BaseException:
            response.body.close()
            self.remove(path)
            raise

    def json_blob(self, name, limit, optional=False):
        try:
            path, _, etag = self.read_blob(name, limit)
        except BlobMissing:
            if optional:
                return None, None
            raise
        try:
            return decode(path.read_bytes()), etag
        finally:
            self.remove(path)

    def create_verify(self, name, path, limit, retain=False):
        require(name != ROOT_BLOB and not name.startswith('baseline/'), 'immutable destination scope violation')
        expected_size, expected_hash = backup._file_hash(path)
        require(expected_size <= limit, 'destination size limit exceeded')
        for attempt in range(self.limits.max_attempts):
            self.budget.operation('azure_writes')
            try:
                with path.open('rb') as stream:
                    reader = UploadStream(stream, self.budget)
                    self.azure.create_block_blob(name, reader, expected_size)
                    require(reader.consumed == expected_size, 'adapter did not consume complete upload')
            except BlobExists:
                pass
            except OSError:
                try:
                    received, checksum, _ = self.read_blob(name, limit)
                except BlobMissing:
                    if attempt + 1 < self.limits.max_attempts:
                        continue
                    raise
                else:
                    try:
                        require(received.stat().st_size == expected_size and checksum == expected_hash,
                                'immutable destination conflict')
                        return received if retain else None
                    finally:
                        if not retain:
                            self.remove(received)
            received, checksum, _ = self.read_blob(name, limit)
            try:
                require(received.stat().st_size == expected_size and checksum == expected_hash,
                        'immutable destination conflict')
                return received if retain else None
            finally:
                if not retain:
                    self.remove(received)

    def json_create(self, name, value, limit):
        index_api._metadata_size(value, limit)
        path = self.path()
        data = wrapped(value)
        self.disk(len(data))
        path.write_bytes(data)
        self.track(path)
        try:
            self.create_verify(name, path, limit)
        finally:
            self.remove(path)

    def fetch(self, item):
        identity(item)
        require(item['size'] <= self.limits.max_object_bytes, 'source object size limit exceeded')
        response = self.budget.call('s3_gets', lambda: self.s3.get_object(
            Bucket=self.current['bucket'], Key=item['key'], IfMatch=item['etag']))
        body, path = response['Body'], self.path()
        try:
            observed = {'key': item['key'], 'size': response['ContentLength'], 'etag': response['ETag'],
                        'last_modified': response['LastModified'].isoformat()}
            require(index_api.revision_fingerprint(observed) == item['revision'], 'source revision changed')
            metadata, content_type = response.get('Metadata', {}), response.get('ContentType')
            require(isinstance(content_type, str) and isinstance(metadata, dict)
                    and all(isinstance(k, str) and isinstance(v, str) for k, v in metadata.items()),
                    'invalid source metadata')
            version = response.get('VersionId')
            require(version is None or isinstance(version, str), 'invalid source version')
            checksum = self.pump(body, path, item['size'], self.limits.max_object_bytes)
            if item['key'].startswith(backup.PREFIXES[0]) and 'sha256' in metadata:
                require(metadata['sha256'] == checksum, 'source SHA-256 mismatch')
            if response.get('ChecksumType') == 'FULL_OBJECT' and response.get('ChecksumSHA256'):
                require(response['ChecksumSHA256'] == base64.b64encode(bytes.fromhex(checksum)).decode(),
                        'S3 SHA-256 mismatch')
            receipt = {'status': 'source_verified', 'bucket': self.current['bucket'], 'key': item['key'],
                       'inventory': {k: item[k] for k in ('key', 'size', 'etag', 'last_modified')},
                       'sha256': checksum, 'size': item['size'], 'content_type': content_type,
                       'metadata': metadata, 'etag': observed['etag'],
                       'last_modified': observed['last_modified'], 'version_id': version,
                       'payload_member': 'objects/' + hashlib.sha256(identity(item).encode()).hexdigest() + '.bin'}
            return path, receipt
        except BaseException:
            body.close()
            self.remove(path)
            raise

    def pack(self, items, batch_id):
        sources, receipts, archive = [], {}, self.path()
        try:
            for item in items:
                path, receipt = self.fetch(item)
                sources.append(path)
                receipts[identity(item)] = receipt
            manifest = {'schema_version': FORMAT, 'batch': batch_id,
                        'bucket': self.current['bucket'], 'receipts': receipts}
            index_api._metadata_size(manifest, self.limits.max_manifest_bytes)
            manifest_bytes = wrapped(manifest)
            archive_bound = sum(p.stat().st_size for p in sources) + len(manifest_bytes) + len(items) * 512 + 512
            require(archive_bound <= self.limits.max_archive_bytes, 'archive size limit exceeded')
            self.disk(archive_bound)
            with ZipFile(archive, 'w', compression=ZIP_STORED) as output:
                for item, path in zip(items, sources):
                    name = receipts[identity(item)]['payload_member']
                    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system, info.external_attr = 3, 0o100644 << 16
                    with output.open(info, 'w') as member, path.open('rb') as stream:
                        while chunk := stream.read(backup.CHUNK):
                            self.budget.runtime()
                            member.write(chunk)
                info = ZipInfo(MANIFEST, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system, info.external_attr = 3, 0o100644 << 16
                output.writestr(info, manifest_bytes)
            self.track(archive)
            require(archive.stat().st_size <= self.limits.max_archive_bytes, 'archive size limit exceeded')
            self.disk()
            return archive, manifest
        except BaseException:
            self.remove(archive)
            raise
        finally:
            for path in sources:
                self.remove(path)

    def inspect_archive(self, path, expected, items=None):
        size, checksum = backup._file_hash(path)
        require(size == expected['size'] and checksum == expected['sha256'], 'Azure archive checksum mismatch')
        with ZipFile(path) as archive:
            infos = archive.infolist()
            require(len(infos) <= self.limits.max_batch_objects + 1
                    and len({info.filename for info in infos}) == len(infos), 'invalid batch member count')
            require(all(info.compress_type == ZIP_STORED and not info.is_dir()
                        and (info.external_attr >> 16) & 0o170000 == 0o100000 for info in infos),
                    'unsupported batch member')
            require(archive.getinfo(MANIFEST).file_size <= self.limits.max_manifest_bytes,
                    'receipt manifest size limit exceeded')
            manifest = decode(archive.read(MANIFEST))
            require(manifest['schema_version'] == FORMAT and manifest['batch'] == expected['batch']
                    and manifest['bucket'] == self.current['bucket']
                    and backup._digest(manifest) == expected['manifest_sha256'], 'batch manifest conflict')
            receipts = manifest['receipts']
            require(isinstance(receipts, dict) and 0 < len(receipts) <= self.limits.max_batch_objects,
                    'invalid receipt count')
            if items is not None:
                require(set(receipts) == {identity(item) for item in items}, 'batch source mapping conflict')
            members, payload_bytes = {MANIFEST}, 0
            for reference, receipt in receipts.items():
                item = receipt['inventory']
                require(reference == identity(item) and receipt['bucket'] == manifest['bucket']
                        and receipt['key'] == item['key'] and receipt['status'] == 'source_verified'
                        and receipt['size'] == item['size'] and receipt['etag'] == item['etag']
                        and index_api._utc(receipt['last_modified']) == index_api._utc(item['last_modified'])
                        and (receipt['version_id'] is None or isinstance(receipt['version_id'], str))
                        and isinstance(receipt['content_type'], str)
                        and isinstance(receipt['metadata'], dict)
                        and all(isinstance(k, str) and isinstance(v, str) for k, v in receipt['metadata'].items()),
                        'invalid source receipt')
                name = 'objects/' + hashlib.sha256(reference.encode()).hexdigest() + '.bin'
                require(receipt['payload_member'] == name and name not in members, 'invalid payload mapping')
                members.add(name)
                require(archive.getinfo(name).file_size == receipt['size'] <= self.limits.max_object_bytes,
                        'payload size mismatch')
                payload_bytes += receipt['size']
                require(payload_bytes <= self.limits.max_batch_bytes, 'batch payload size limit exceeded')
                digest, count = hashlib.sha256(), 0
                with archive.open(name) as body:
                    while chunk := body.read(backup.CHUNK):
                        self.budget.runtime()
                        count += len(chunk)
                        digest.update(chunk)
                require(count == receipt['size'] and digest.hexdigest() == receipt['sha256'],
                        'payload checksum mismatch')
                if receipt['key'].startswith(backup.PREFIXES[0]) and 'sha256' in receipt['metadata']:
                    require(receipt['metadata']['sha256'] == receipt['sha256'], 'raw metadata checksum mismatch')
            require({info.filename for info in infos} == members, 'unexpected archive member')
            return manifest

    def verify_existing_batch(self, batch_id, descriptor):
        proof, _ = self.json_blob(descriptor['verification_blob'], self.limits.max_manifest_bytes)
        require(backup._digest(proof) == descriptor['verification_sha256']
                and proof['status'] == 'destination_verified' and proof['batch'] == batch_id
                and proof['blob'] == descriptor['blob'] and proof['sha256'] == descriptor['sha256']
                and descriptor['receipts_member'] == MANIFEST, 'verification receipt conflict')
        path, _, _ = self.read_blob(descriptor['blob'], self.limits.max_archive_bytes)
        try:
            return self.inspect_archive(path, proof)
        finally:
            self.remove(path)

    def snapshot(self):
        remote, self.etag = self.json_blob(ROOT_BLOB, index_api.MAX_ROOT_BYTES)
        index_api._validate_index(remote)
        target = self.scratch / 'snapshot'
        target.mkdir()
        backup._write(target, 'index.json', self.current)
        self.track(target / 'index.json')
        added_batches = set()
        for shard in index_api.SHARDS:
            old = index_api.read_shard(self.cache, shard, index=self.current)
            local = backup._path(target, self.current['shards'][shard]['location'])
            local.parent.mkdir(parents=True, exist_ok=True)
            self.disk((self.cache / self.current['shards'][shard]['location']).stat().st_size)
            os.link(self.cache / self.current['shards'][shard]['location'], local)
            self.track(local)
            incoming, _, _ = self.read_blob('index/' + remote['shards'][shard]['location'], index_api.MAX_SHARD_BYTES)
            try:
                value = decode(incoming.read_bytes())
                if remote['shards'][shard] == self.current['shards'][shard]:
                    require(value == old, 'remote shard content conflict')
                else:
                    self.disk(len(wrapped(value)))
                    descriptor = index_api.stage_shard_revision(target, value, index=remote,
                        baseline_bytes=remote['shards'][shard]['baseline_bytes'])
                    self.track(target / descriptor['location'])
                    require(descriptor == remote['shards'][shard], 'remote shard descriptor conflict')
                old_set = {tuple(entry) for entry in old['entries']}
                for entry in value['entries']:
                    if tuple(entry) not in old_set:
                        require(entry[3] != 'baseline', 'remote fabricated baseline coverage')
                        added_batches.add(entry[3])
            finally:
                self.remove(incoming)
        authority = self.scratch / 'authority'
        authority.mkdir()
        for batch in sorted(added_batches):
            manifest = self.verify_existing_batch(batch, remote['batches'][batch])
            for ref, receipt in manifest['receipts'].items():
                kind, uuid = index_api.key_parts(receipt['key'])
                entry = [kind, uuid, index_api.revision_fingerprint(receipt['inventory']), batch]
                self.disk(len(backup._bytes(entry)) + 1)
                index_api._append(authority, uuid[:2], entry)
                self.track(authority / (uuid[:2] + '.jsonl'))
        for shard in index_api.SHARDS:
            old = {tuple(e) for e in index_api.read_shard(self.cache, shard, index=self.current)['entries']}
            new = {tuple(e) for e in index_api.read_shard(target, shard, index=remote)['entries']}
            proof = {tuple(e) for e in index_api._spooled(authority, shard, index_api.MAX_RECORDS,
                                                         index_api.MAX_SHARD_BYTES)}
            require(new - old <= proof, 'remote coverage lacks independently verified evidence')
        self.disk(len(wrapped(remote)))
        index_api.publish_index(target, remote)
        self.track(target / 'index.json')
        self.sync_cache(target, remote)
        self.current, self.snapshot_dir = remote, target
        self.json_create('index/roots/' + backup._digest(remote) + '.json', remote, index_api.MAX_ROOT_BYTES)

    def sync_cache(self, source, candidate):
        for shard in index_api.SHARDS:
            if self.current['shards'][shard] != candidate['shards'][shard]:
                value = index_api.read_shard(source, shard, index=candidate)
                index_api.stage_shard_revision(self.cache, value, index=candidate,
                                              baseline_bytes=candidate['shards'][shard]['baseline_bytes'])
        index_api.publish_index(self.cache, candidate)

    def candidates(self, plan_directory):
        for shard in index_api.SHARDS:
            plan = index_api.read_plan_shard(plan_directory, shard)
            require(plan['bucket'] == self.current['bucket'], 'plan source bucket conflict')
            covered = {tuple(e[:3]) for e in index_api.read_shard(
                self.snapshot_dir, shard, index=self.current)['entries']}
            last = None
            for item in sorted(plan['new'] + plan['changed'], key=lambda item: item['key']):
                ref = identity(item)
                kind, uuid = index_api.key_parts(item['key'])
                require(uuid[:2] == shard and item['key'] != last, 'duplicate or misplaced candidate')
                last = item['key']
                if (kind, uuid, ref.split('/')[-1]) not in covered:
                    require(item['size'] <= self.limits.max_object_bytes, 'source object size limit exceeded')
                    yield item

    def conditional_root(self, candidate):
        data = wrapped(candidate)
        self.budget.operation('azure_writes')
        try:
            reader = UploadStream(BytesIO(data), self.budget)
            self.azure.replace_root(ROOT_BLOB, reader, len(data), self.etag)
            require(reader.consumed == len(data), 'incomplete root upload')
        except RootChanged:
            raise
        except OSError:
            pass
        persisted, etag = self.json_blob(ROOT_BLOB, index_api.MAX_ROOT_BYTES)
        require(persisted == candidate, 'conditional root outcome uncertain or concurrent root update')
        self.etag = etag

    def process(self, items):
        batch_id = backup._digest({'bucket': self.current['bucket'], 'objects': sorted(identity(i) for i in items)})
        require(len(self.current['batches']) < index_api.MAX_BATCHES, 'batch reference capacity exceeded')
        blob = 'incremental/batches/' + batch_id + '.zip'
        prepared_name, verified_name = blob + '.prepared.json', blob + '.verified.json'
        prepared, _ = self.json_blob(prepared_name, self.limits.max_manifest_bytes, optional=True)
        archive = None
        if prepared is not None:
            require(prepared['batch'] == batch_id and prepared['blob'] == blob, 'prepared intent conflict')
            try:
                archive, _, _ = self.read_blob(blob, self.limits.max_archive_bytes)
            except BlobMissing:
                pass
        if archive is None:
            archive, manifest = self.pack(items, batch_id)
            size, checksum = backup._file_hash(archive)
            expected = {'schema_version': FORMAT, 'batch': batch_id, 'blob': blob,
                        'size': size, 'sha256': checksum, 'manifest_sha256': backup._digest(manifest)}
            if prepared is not None:
                require(prepared == expected, 'source or prepared batch conflict')
            else:
                self.json_create(prepared_name, expected, self.limits.max_manifest_bytes)
                prepared = expected
            persisted = self.create_verify(blob, archive, self.limits.max_archive_bytes, retain=True)
            self.remove(archive)
            archive = persisted
        try:
            manifest = self.inspect_archive(archive, prepared, items)
        finally:
            self.remove(archive)
        proof = dict(prepared, status='destination_verified')
        self.json_create(verified_name, proof, self.limits.max_manifest_bytes)
        self.verified_batches += 1
        candidate = decode(wrapped(self.current))
        candidate['batches'][batch_id] = {'kind': 'increment', 'blob': blob, 'sha256': proof['sha256'],
            'receipts_member': MANIFEST, 'verification_blob': verified_name,
            'verification_sha256': backup._digest(proof)}
        by_shard = {}
        for reference, receipt in manifest['receipts'].items():
            kind, uuid = index_api.key_parts(receipt['key'])
            by_shard.setdefault(uuid[:2], []).append([kind, uuid, reference.split('/')[-1], batch_id])
        for shard, entries in by_shard.items():
            value = index_api.read_shard(self.snapshot_dir, shard, index=self.current)
            value['entries'].extend(entries)
            self.disk(len(wrapped(value)))
            candidate['shards'][shard] = index_api.stage_shard_revision(self.snapshot_dir, value, index=candidate,
                baseline_bytes=self.current['shards'][shard]['baseline_bytes'])
            self.track(self.snapshot_dir / candidate['shards'][shard]['location'])
        old_current = self.current
        self.disk(len(wrapped(candidate)))
        index_api.publish_index(self.snapshot_dir, candidate)
        self.track(self.snapshot_dir / 'index.json')
        for shard in by_shard:
            path = self.snapshot_dir / candidate['shards'][shard]['location']
            self.create_verify('index/' + candidate['shards'][shard]['location'], path, index_api.MAX_SHARD_BYTES)
        self.json_create('index/roots/' + backup._digest(candidate) + '.json', candidate, index_api.MAX_ROOT_BYTES)
        self.conditional_root(candidate)
        self.committed_batches += 1
        self.current = old_current
        self.sync_cache(self.snapshot_dir, candidate)
        self.current = candidate


def transfer_plan(*, s3, azure, index_directory, plan_directory, limits=Limits()):
    """Transfer a complete Slice 1 plan using injected clients and ephemeral scratch.

    Baseline bootstrap is not provided. The private local index is a trusted
    committed cache; remote updates must be append-only and their added coverage
    independently verified before adoption. Successful batches commit separately,
    retaining progress on later failure. No payload remains locally on return.
    """
    budget = Budget(limits)
    result = {'complete': False, 'consistent_cutoff': False, 'verified_batches': 0,
              'committed_batches': 0, 'error': None}
    runner = None
    try:
        # same filesystem permits immutable shard hardlinks; scratch is ephemeral
        with tempfile.TemporaryDirectory(prefix='transfer-', dir=Path(index_directory).absolute().parent) as temporary:
            root = Path(temporary)
            runner = Runner(s3, azure, index_directory, root, budget)
            runner.snapshot()
            items, size = [], 0
            for item in runner.candidates(plan_directory):
                if items and (len(items) == limits.max_batch_objects or size + item['size'] > limits.max_batch_bytes):
                    runner.process(items)
                    items, size = [], 0
                require(item['size'] <= limits.max_batch_bytes, 'batch payload size limit exceeded')
                items.append(item)
                size += item['size']
            if items:
                runner.process(items)
            result['complete'] = True
    except Exception as error:
        result['error'] = str(error) if isinstance(error, backup.BackupError) else type(error).__name__
    if runner is not None:
        result.update(verified_batches=runner.verified_batches, committed_batches=runner.committed_batches)
    result['operations'] = dict(budget.counts)
    return result
