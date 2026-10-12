"""Injected-client incremental transfers; no SDK, credentials or live entry point.

Azure adapter: open_blob(name) -> BlobRead; create_block_blob(name, stream, size)
uses create-only BlockBlob writes; list_blobs(prefix, continuation_token, page_size)
returns blobs (name, size) and next_token. A single publisher must be serialized. Each adapter request has bounded transport
timeouts and no hidden
retries. Streams must read in chunks <= evidence_backup.CHUNK. Normalize provider
not-found, create-conflict and create-only conflicts to the exceptions below.
"""

import base64
from dataclasses import dataclass, fields
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
from zipfile import ZipFile, ZipInfo, ZIP_STORED

import evidence_backup as backup
import incremental_backup as index_api

MANIFEST = 'receipts.json'
FORMAT = 1


class TransferError(backup.BackupError):
    """An explicit integrity, publication or resource failure."""


class BlobMissing(Exception):
    """The requested Azure blob does not exist; not an authorization failure."""


class BlobExists(Exception):
    """A create-only write found an existing immutable blob."""


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
    max_azure_reads: int = 8192
    max_azure_lists: int = 64
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
        self.counts = dict(s3_list=0, s3_gets=0, azure_reads=0, azure_lists=0, azure_writes=0, transfer_bytes=0)

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
    def __init__(self, s3, azure, bucket, scratch, budget):
        self.s3, self.azure, self.bucket = s3, azure, bucket
        self.scratch, self.budget, self.limits = scratch, budget, budget.limits
        self.serial = 0
        self.allocated = {}
        self.verified_batches = 0
        self.committed_batches = 0
        self.pending_intents = set()

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

    def create_verify(self, name, path, limit, retain=False, expected=None):
        require(name.startswith('incremental/batches/'), 'immutable destination scope violation')
        expected_size, expected_hash = expected if expected is not None else backup._file_hash(path)
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
            Bucket=self.bucket, Key=item['key'], IfMatch=item['etag']))
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
            receipt = {'status': 'source_verified', 'bucket': self.bucket, 'key': item['key'],
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
                        'bucket': self.bucket, 'receipts': receipts}
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

    def inspect_archive(self, path, expected, items=None, observed=None):
        size, checksum = observed if observed is not None else backup._file_hash(path)
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
                    and manifest['bucket'] == self.bucket
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
            if 'coverage' in expected:
                rows = [[*reference.split('/'), receipt['sha256'], receipt['size']]
                        for reference, receipt in sorted(receipts.items())]
                index_api.validate_completion(expected, self.bucket)
                require(sorted(expected['coverage']) == rows, 'completion receipt mapping conflict')
            return manifest

    def _complete(self, prepared, persisted, items=None, observed=None):
        manifest = self.inspect_archive(persisted, prepared, items, observed=observed)
        rows = []
        for reference, receipt in sorted(manifest['receipts'].items()):
            kind, identifier, revision = reference.split('/')
            rows.append([kind, identifier, revision, receipt['sha256'], receipt['size']])
        value = dict(prepared, schema_version=2, status='destination_verified',
                     bucket=self.bucket, coverage=rows)
        index_api.validate_completion(value, self.bucket)
        self.json_create(prepared['blob'] + '.verified.json', value, self.limits.max_manifest_bytes)
        self.verified_batches += 1
        self.committed_batches += 1
        self.pending_intents.discard(prepared['batch'])
        return value

    def enumerate_batches(self):
        """Bounded complete enumeration; no historical payload reads on normal runs."""
        blobs, token, tokens, last = {}, None, set(), ''
        while True:
            page = self.budget.call('azure_lists', lambda: self.azure.list_blobs(
                prefix='incremental/batches/', continuation_token=token, page_size=5000))
            entries, following = page['blobs'], page['next_token']
            require(isinstance(entries, list) and len(entries) <= 5000, 'invalid Azure listing page')
            require(following is None or isinstance(following, str) and following
                    and following not in tokens and entries, 'invalid Azure continuation')
            for entry in entries:
                name, size = entry['name'], entry['size']
                require(isinstance(name, str) and name > last, 'duplicate or unordered Azure listing')
                last = name
                prefix = 'incremental/batches/'
                require(name.startswith(prefix), 'unexpected Azure listing prefix')
                suffix = name[len(prefix):]
                batch, ending = suffix[:64], suffix[64:]
                require(index_api.batch_blob(batch) == prefix + batch + '.zip'
                        and ending in ('.zip', '.zip.prepared.json', '.zip.verified.json'),
                        'unexpected incremental blob')
                require(type(size) is int and 0 <= size <= (self.limits.max_archive_bytes
                        if ending == '.zip' else self.limits.max_manifest_bytes), 'invalid listed blob size')
                if batch not in blobs:
                    require(len(blobs) < index_api.MAX_BATCHES, 'batch reference capacity exceeded')
                    blobs[batch] = {}
                blobs[batch][ending] = size
            if following is None:
                self.pending_intents = {batch for batch, files in blobs.items()
                                        if '.zip.prepared.json' in files and '.zip.verified.json' not in files}
                return blobs
            tokens.add(following)
            token = following

    def completed_batches(self, blobs):
        for batch, files in sorted(blobs.items()):
            self.budget.runtime()
            blob = index_api.batch_blob(batch)
            if '.zip.verified.json' in files:
                proof, _ = self.json_blob(blob + '.verified.json', self.limits.max_manifest_bytes)
                index_api.validate_completion(proof, self.bucket)
                require(proof['batch'] == batch and files.get('.zip') == proof['size'],
                        'completed batch evidence missing or size mismatch')
                yield proof
            elif '.zip' in files:
                require('.zip.prepared.json' in files, 'uncommitted archive lacks trusted intent')
                prepared, _ = self.json_blob(blob + '.prepared.json', self.limits.max_manifest_bytes)
                require(prepared['batch'] == batch and prepared['blob'] == blob
                        and prepared['schema_version'] == FORMAT and files['.zip'] == prepared['size'],
                        'prepared intent conflict')
                path, checksum, _ = self.read_blob(blob, self.limits.max_archive_bytes)
                try:
                    yield self._complete(prepared, path, observed=(path.stat().st_size, checksum))
                finally:
                    self.remove(path)
            # A prepared-only batch establishes no coverage. It may be retried
            # when its source candidates are still present; never skip them.

    def process(self, items):
        batch = backup._digest({'bucket': self.bucket, 'objects': sorted(identity(i) for i in items)})
        blob = index_api.batch_blob(batch)
        prepared, _ = self.json_blob(blob + '.prepared.json', self.limits.max_manifest_bytes, optional=True)
        archive, manifest = self.pack(items, batch)
        try:
            size, checksum = backup._file_hash(archive)
            expected = {'schema_version': FORMAT, 'batch': batch, 'blob': blob, 'size': size,
                        'sha256': checksum, 'manifest_sha256': backup._digest(manifest)}
            require(prepared is None or prepared == expected, 'source or prepared batch conflict')
            if prepared is None:
                self.json_create(blob + '.prepared.json', expected, self.limits.max_manifest_bytes)
            self.pending_intents.add(batch)
            persisted = self.create_verify(blob, archive, self.limits.max_archive_bytes,
                                           retain=True, expected=(size, checksum))
            try:
                # create_verify already independently hashed persisted bytes.
                self._complete(expected, persisted, items, observed=(size, checksum))
            finally:
                self.remove(persisted)
        finally:
            self.remove(archive)


def run_backup(*, s3, azure, bucket, baseline_catalog_sha256, inventories,
               limits=Limits(), scratch_parent=None):
    """One serialized offline-testable sweep; injected adapters own live access.

    Pin the baseline catalog digest independently after verified archive bootstrap.
    Completed immutable manifests are trusted writer receipts, not cryptographic
    proof against a privileged writer. Scratch coverage is disposable. Full S3
    listing inputs must include both terminal prefixes; no source LIST is issued
    here. Real adapter listing attempts/bytes require their own bounded accounting.
    """
    budget = Budget(limits)
    result = {'complete': False, 'consistent_cutoff': False, 'verified_batches': 0,
              'committed_batches': 0, 'pending_intents': 0, 'error': None}
    runner = None
    try:
        with tempfile.TemporaryDirectory(prefix='backup-', dir=scratch_parent) as temporary:
            root = Path(temporary)
            with backup._lock(root) as private:
                runner = Runner(s3, azure, bucket, private, budget)
                try:
                    baseline, _ = runner.json_blob(index_api.BASELINE_CATALOG_BLOB, limits.max_manifest_bytes)
                except BlobMissing as error:
                    raise TransferError('verified baseline catalog unavailable; bootstrap required') from error
                index_api.validate_baseline(baseline, baseline_catalog_sha256)
                require(baseline['bucket'] == bucket, 'baseline bucket conflict')
                blobs = runner.enumerate_batches()
                coverage = root / 'coverage'
                index_api.reconstruct_coverage(baseline, runner.completed_batches(blobs),
                    trusted_catalog_sha256=baseline_catalog_sha256, output_directory=coverage,
                    max_spool_bytes=limits.max_scratch_bytes // 3, check_runtime=budget.runtime)
                budget.runtime()
                plan = root / 'plan'
                remaining = limits.max_seconds - (time.monotonic() - budget.started)
                sweep = index_api.plan_reconciliation(coverage, inventories, output_directory=plan,
                    max_spool_bytes=limits.max_scratch_bytes // 3,
                    max_output_bytes=limits.max_scratch_bytes // 3, max_seconds=remaining)
                result['sweep'] = sweep
                require(sweep['complete'], 'incomplete source sweep: ' + str(sweep['error']))
                for path in root.rglob('*'):
                    if path.is_file():
                        runner.track(path)
                batch_items, size = [], 0
                new_batches = 0
                for partition in index_api.PARTITIONS:
                    budget.runtime()
                    part = index_api.read_plan_partition(plan, partition)
                    for item in sorted(part['new'] + part['changed'], key=lambda row: row['key']):
                        require(item['size'] <= limits.max_object_bytes and item['size'] <= limits.max_batch_bytes,
                                'source object size limit exceeded')
                        if batch_items and (len(batch_items) == limits.max_batch_objects
                                            or size + item['size'] > limits.max_batch_bytes):
                            require(len(blobs) + new_batches < index_api.MAX_BATCHES, 'batch capacity exceeded')
                            runner.process(batch_items)
                            new_batches += 1
                            batch_items, size = [], 0
                        batch_items.append(item)
                        size += item['size']
                if batch_items:
                    require(len(blobs) + new_batches < index_api.MAX_BATCHES, 'batch capacity exceeded')
                    runner.process(batch_items)
                require(not runner.pending_intents,
                        'unresolved prepared intents: ' + str(len(runner.pending_intents)))
                result['complete'] = True
    except Exception as error:
        result['error'] = str(error) if isinstance(error, backup.BackupError) else type(error).__name__
    if runner is not None:
        result.update(verified_batches=runner.verified_batches, committed_batches=runner.committed_batches,
                      pending_intents=len(runner.pending_intents))
    result['operations'] = dict(budget.counts)
    return result
