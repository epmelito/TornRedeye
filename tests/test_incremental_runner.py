"""Provider contracts and executable tested only with in-memory SDK/HTTP fakes."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit, parse_qs, unquote
from xml.etree import ElementTree as ET

import evidence_backup as evidence
import incremental_backup as coverage
import incremental_runner as runner
import incremental_transfer as engine
from test_evidence_backup import FakeS3, BoundedBody, CUTOFF, NOW
from tornredeye.evidence_paths import evidence_keys
from uuid import UUID

BUCKET = 'synthetic-private-bucket'
OWNER = '123456789012'
BASE = evidence_keys(NOW, UUID(int=1))[0]
RAW, NORM = evidence_keys(NOW, UUID(int=2))


class Response(BoundedBody):
    def __init__(self, status=200, data=b'', **headers):
        super().__init__(data)
        self.status, self.headers = status, headers
    def getheader(self, name):
        return self.headers.get(name)


class Service:
    def __init__(self):
        self.data, self.connections = {}, []
        self.fail = lambda method, name: None
        self.after_put = lambda name: None
        self.transform = lambda name, data: data
        self.page_size = 5000
    def connect(self, host, timeout):
        connection = Connection(self, host, timeout)
        self.connections.append(connection)
        return connection


class Connection:
    def __init__(self, service, host, timeout):
        self.service, self.host, self.timeout = service, host, timeout
        self.sock, self.closed, self.chunks = self, False, []
        self.headers = {}
    def connect(self):
        pass
    def settimeout(self, value):
        self.read_timeout = value
    def close(self):
        self.closed = True
    def request(self, method, target, headers):
        self.putrequest(method, target)
        self.headers = headers
    def putrequest(self, method, target):
        self.method, self.target = method, target
    def putheader(self, key, value):
        self.headers[key] = value
    def endheaders(self):
        pass
    def send(self, chunk):
        assert 0 < len(chunk) <= evidence.CHUNK
        self.chunks.append(chunk)
    def getresponse(self):
        url = urlsplit(self.target)
        name = unquote(url.path).split('/', 2)[2] if url.path.count('/') > 1 else ''
        query = parse_qs(url.query)
        self.service.fail(self.method, name)
        if self.method == 'PUT':
            if name in self.service.data:
                return Response(412, **{'x-ms-error-code': 'ConditionNotMet'})
            data = b''.join(self.chunks)
            assert len(data) == int(self.headers['Content-Length'])
            assert self.headers['If-None-Match'] == '*'
            assert self.headers['x-ms-blob-type'] == 'BlockBlob'
            self.service.data[name] = data
            self.service.after_put(name)
            return Response(201)
        if query.get('comp') == ['list']:
            root = ET.Element('EnumerationResults')
            blobs = ET.SubElement(root, 'Blobs')
            names = sorted(n for n in self.service.data if n.startswith(query['prefix'][0]))
            start = int(query.get('marker', ['0'])[0])
            end = min(len(names), start+min(int(query['maxresults'][0]), self.service.page_size))
            for n in names[start:end]:
                blob = ET.SubElement(blobs, 'Blob')
                ET.SubElement(blob, 'Name').text = n
                props = ET.SubElement(blob, 'Properties')
                ET.SubElement(props, 'Content-Length').text = str(len(self.service.data[n]))
            ET.SubElement(root, 'NextMarker').text = str(end) if end < len(names) else ''
            return Response(data=ET.tostring(root))
        if name not in self.service.data:
            return Response(404, **{'x-ms-error-code': 'BlobNotFound'})
        original = self.service.data[name]
        return Response(data=self.service.transform(name, original), **{
            'Content-Length': str(len(original)), 'ETag': 'opaque-azure-etag', 'x-ms-blob-type': 'BlockBlob'})


class SDK:
    def __init__(self):
        self.fake, self.requests = FakeS3(), []
    def list_objects_v2(self, **request):
        self.requests.append(request)
        return self.fake.list_objects_v2(**request)
    def get_object(self, **request):
        self.requests.append(request)
        request.pop('ExpectedBucketOwner')
        return self.fake.get_object(**request)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.service = Service()
        self.azure = runner.AzureBlobs('syntheticaccount', 'synthetic-container', 'sig=synthetic',
                                        connection_factory=self.service.connect)

    def test_azure_streams_exact_empty_binary_and_existing_conflict(self):
        for size in (0, 2*evidence.CHUNK+5):
            name = coverage.BATCH_PREFIX+str(size)+'.zip'
            data = bytes(range(256))*(size//256)+b'x'*(size%256)
            self.azure.create_block_blob(name, BoundedBody(data), len(data))
            blob = self.azure.open_blob(name)
            self.assertEqual(blob.size, size)
            read = bytearray()
            while chunk := blob.body.read(evidence.CHUNK):
                read.extend(chunk)
            blob.body.close()
            self.assertEqual(read, data)
            with self.assertRaises(engine.BlobExists):
                self.azure.create_block_blob(name, BoundedBody(b'other'), 5)
            self.assertEqual(self.service.data[name], data)
        self.assertTrue(all(c.closed for c in self.service.connections))
        self.assertTrue(all(c.timeout == 10 and c.read_timeout == 30 for c in self.service.connections))
        with self.assertRaises(engine.TransferError):
            self.azure.create_block_blob('baseline/archive', BoundedBody(b''), 0)

    def test_azure_paginated_xml_and_size_bound(self):
        for n in range(3):
            self.service.data[f'{coverage.BATCH_PREFIX}{n}.zip'] = b'xx'
        self.service.page_size = 2
        first = self.azure.list_blobs(prefix=coverage.BATCH_PREFIX, continuation_token=None, page_size=5000)
        last = self.azure.list_blobs(prefix=coverage.BATCH_PREFIX, continuation_token=first['next_token'], page_size=5000)
        self.assertEqual([b['name'] for b in first['blobs']+last['blobs']], sorted(self.service.data))
        self.assertIsNone(last['next_token'])
        self.assertEqual(self.service.connections[-1].target.split('?')[1].split('&')[:2],
                         ['restype=container', 'comp=list'])
        self.azure.max_listing_bytes = 1
        with self.assertRaises(engine.TransferError):
            self.azure.list_blobs(prefix=coverage.BATCH_PREFIX, continuation_token=None, page_size=5000)
        self.assertTrue(self.service.connections[-1].closed)

    def test_azure_maps_only_correct_statuses_and_rejects_bad_headers_xml(self):
        with self.assertRaises(engine.BlobMissing):
            self.azure.open_blob(coverage.BATCH_PREFIX+"missing")
        for status, code, expected in ((404, 'ContainerNotFound', engine.TransferError),
                (403, 'AuthenticationFailed', engine.TransferError), (503, 'ServerBusy', OSError),
                (301, None, engine.TransferError), (409, 'LeaseAlreadyPresent', engine.TransferError)):
            fake = Mock()
            fake.getresponse.return_value = Response(status, **{'x-ms-error-code': code})
            self.azure.connection_factory = lambda *a, **kw: fake
            with self.assertRaises(expected):
                self.azure.open_blob(coverage.BATCH_PREFIX+"example")
            fake.close.assert_called()
        for data in (b'<invalid/>', b'<!DOCTYPE x><EnumerationResults/>',
                     b'<EnumerationResults><Blobs/></EnumerationResults>'):
            fake.getresponse.return_value = Response(data=data)
            with self.assertRaises(engine.TransferError):
                self.azure.list_blobs(prefix=coverage.BATCH_PREFIX, continuation_token=None, page_size=5000)
        fake.getresponse.return_value = Response(data=b'payload', **{'Content-Length': '7'})
        with self.assertRaises(engine.TransferError):
            self.azure.open_blob(coverage.BATCH_PREFIX+"example")

    def test_azure_rejects_sas_operation_override_and_truncated_upload(self):
        for token in ('sig=synthetic&prefix=unapproved', 'sig=synthetic&sig=other', 'sig=synthetic\n'):
            with self.assertRaises(engine.TransferError):
                runner.AzureBlobs('syntheticaccount', 'synthetic-container', token)
        with self.assertRaises(engine.TransferError):
            self.azure.create_block_blob(coverage.BATCH_PREFIX+'example.zip', BoundedBody(b'x'), 2)
        self.assertTrue(self.service.connections[-1].closed)
        self.assertFalse(self.service.data)

    def test_s3_page_tokens_owner_and_unmodified_conditional_response(self):
        client = Mock()
        obj = {'Key': RAW, 'Size': 1, 'ETag': 'opaque', 'LastModified': NOW}
        client.list_objects_v2.side_effect = [{'Contents': [obj], 'IsTruncated': True, 'NextContinuationToken': 'next'},
                                              {'IsTruncated': False}]
        source = runner.S3Source(client, BUCKET, OWNER)
        pages = list(source.inventory(evidence.PREFIXES[0]))
        self.assertEqual(pages[1]['request_token'], 'next')
        self.assertIsNone(pages[1]['next_token'])
        self.assertEqual(client.list_objects_v2.call_args.kwargs['ExpectedBucketOwner'], OWNER)
        self.assertEqual(client.list_objects_v2.call_args.kwargs['ContinuationToken'], 'next')
        value = {'Body': BoundedBody(b'x'), 'Metadata': {'original': 'value'}, 'VersionId': 'v1'}
        client.get_object.return_value = value
        self.assertIs(source.get_object(Bucket=BUCKET, Key=RAW, IfMatch='opaque'), value)
        self.assertEqual(client.get_object.call_args.kwargs['IfMatch'], 'opaque')
        self.assertEqual(client.get_object.call_args.kwargs['ExpectedBucketOwner'], OWNER)
        value['Body'].close()

    def test_s3_repeated_token_partial_failure_and_listing_byte_limits(self):
        client = Mock()
        obj = {'Key': RAW, 'Size': 1, 'ETag': 'opaque', 'LastModified': NOW}
        client.list_objects_v2.return_value = {'Contents': [obj], 'IsTruncated': True,
                                               'NextContinuationToken': 'same'}
        with self.assertRaises(engine.TransferError):
            list(runner.S3Source(client, BUCKET, OWNER).inventory(evidence.PREFIXES[0]))
        client.list_objects_v2.side_effect = [{'Contents': [obj], 'IsTruncated': True,
                                              'NextContinuationToken': 'next'}, OSError('sensitive detail')]
        source = runner.S3Source(client, BUCKET, OWNER, max_attempts=1)
        pages = source.inventory(evidence.PREFIXES[0])
        self.assertEqual(next(pages)['next_token'], 'next')
        with self.assertRaises(OSError):
            next(pages)
        client.list_objects_v2.side_effect = None
        client.list_objects_v2.return_value = {'Contents': [obj], 'IsTruncated': False}
        with self.assertRaisesRegex(engine.TransferError, 'metadata byte limit'):
            list(runner.S3Source(client, BUCKET, OWNER, max_listing_bytes=1).inventory(evidence.PREFIXES[0]))
        with self.assertRaises(engine.TransferError):
            runner.S3Source(client, BUCKET, OWNER, max_attempts=0)

    def test_s3_partial_invalid_pages_retries_and_bounds(self):
        client = Mock()
        for response in ({'IsTruncated': True}, {'IsTruncated': 'false'},
                         {'IsTruncated': False, 'Contents': [], 'KeyCount': 1}):
            client.list_objects_v2.return_value = response
            with self.assertRaises(engine.TransferError):
                list(runner.S3Source(client, BUCKET, OWNER).inventory(evidence.PREFIXES[0]))
        error = RuntimeError('sensitive provider message')
        error.response = {'ResponseMetadata': {'HTTPStatusCode': 503}}
        client.list_objects_v2.side_effect = error
        source = runner.S3Source(client, BUCKET, OWNER, max_requests=2)
        with self.assertRaisesRegex(engine.TransferError, 'LIST request limit'):
            list(source.inventory(evidence.PREFIXES[0]))
        self.assertEqual(source.requests, 2)
        source = runner.S3Source(client, BUCKET, OWNER)
        with self.assertRaises(OSError):
            list(source.inventory(evidence.PREFIXES[0]))
        self.assertEqual(source.requests, 3)
        client.get_object.side_effect = error
        error.response['ResponseMetadata']['HTTPStatusCode'] = 412
        with self.assertRaisesRegex(engine.TransferError, 'HTTP 412'):
            source.get_object(Bucket=BUCKET, Key=RAW, IfMatch='opaque')


class EntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temp = tempfile.TemporaryDirectory()
        cls.fixture = temp
        path = Path(temp.name)
        cls.baseline = coverage.empty_anchor(BUCKET)
    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.parent = Path(temp.name)
        self.sdk = SDK(); self.sdk.fake.add(BASE, b'baseline')
        self.source = runner.S3Source(self.sdk, BUCKET, OWNER)
        self.service = Service()
        self.service.data[coverage.ANCHOR_BLOB] = engine.wrapped(self.baseline)
        self.azure = runner.AzureBlobs('syntheticaccount', 'synthetic-container', 'sig=synthetic',
                                      connection_factory=self.service.connect)
        self.factory = Mock(return_value=(self.source, self.azure))
        self.args = ['--bucket', BUCKET, '--owner', OWNER, '--region', 'eu-north-1',
            '--azure-account', 'syntheticaccount', '--azure-container', 'synthetic-container',
            '--anchor-sha256', evidence._digest(self.baseline), '--scratch-parent', str(self.parent)]
    def invoke(self, gated=True):
        output = StringIO()
        with redirect_stdout(output):
            status = runner.main(self.args+(['--live-access-approved'] if gated else []), client_factory=self.factory)
        return status, json.loads(output.getvalue())

    def test_entry_gate_bad_config_and_missing_baseline_prevent_source_access(self):
        status, result = self.invoke(False)
        self.assertEqual(status, 1); self.assertFalse(result['complete'])
        self.factory.assert_not_called()
        self.args[3] = 'wrong-owner'
        self.assertEqual(self.invoke()[0], 1)
        self.factory.assert_not_called()
        self.args[3] = OWNER
        del self.service.data[coverage.ANCHOR_BLOB]
        status, result = self.invoke()
        self.assertEqual(status, 0, result)
        self.assertIn(coverage.ANCHOR_BLOB,self.service.data)

    def test_entry_real_adapters_exact_transfer_verified_restart_and_lost_ack(self):
        self.sdk.fake.add(RAW, b'\x00\xfforiginal', {'source': 'synthetic'})
        self.sdk.fake.add(NORM, b'')
        self.service.after_put = lambda name: (_ for _ in ()).throw(OSError('lost ack')) if name.endswith('.zip') else None
        status, result = self.invoke()
        self.assertEqual(status, 0, result)
        self.assertEqual(result['committed_batches'], 1)
        self.assertEqual(result['source_inventory']['requests'], len(evidence.PREFIXES))
        self.assertEqual(result['operations']['s3_list'], len(evidence.PREFIXES))
        before = dict(self.service.data)
        self.sdk.fake.get_calls.clear()
        self.service.page_size = 1
        status, result = self.invoke()
        self.assertEqual(status, 0, result)
        self.assertFalse(self.sdk.fake.get_calls)
        self.assertEqual(self.service.data, before)
        self.assertTrue(all(c.closed for c in self.service.connections))
        self.assertFalse(list(self.parent.glob('backup-*')))
        self.assertFalse((self.parent/'writer.lock').exists())

    def test_entry_partial_inventory_and_corrupt_destination_report_nonzero(self):
        self.sdk.fake.add(RAW, b'original')
        self.sdk.fake.page_transform = lambda p: dict(p, IsTruncated=True)
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertFalse(result['sweep']['complete'])
        self.assertFalse(self.sdk.fake.get_calls)
        self.sdk.fake.page_transform = lambda p: p
        self.service.transform = lambda name, data: b'x'+data[1:] if name.endswith('.zip') else data
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertFalse(any(n.endswith('.verified.json') for n in self.service.data))
        self.assertEqual(result['pending_intents'], 1)
        self.assertNotIn('sig=', result['error'])

    def test_factory_uses_existing_sdk_with_bounded_timeouts_and_no_sdk_retries(self):
        import sys
        from types import ModuleType
        boto = ModuleType('boto3'); boto.client = Mock(return_value=self.sdk)
        botocore = ModuleType('botocore'); botocore.__path__ = []
        config = ModuleType('botocore.config'); config.Config = Mock(side_effect=lambda **kw: kw)
        args = SimpleNamespace(azure_account='syntheticaccount', azure_container='synthetic-container',
                               region='eu-north-1', bucket=BUCKET, owner=OWNER)
        with patch.dict(sys.modules, {'boto3': boto, 'botocore': botocore, 'botocore.config': config}), \
                patch.dict(runner.os.environ, {'TORNREDEYE_AZURE_SAS': 'sig=synthetic'}):
            source, azure = runner.clients(args, engine.Limits())
        self.assertEqual(source.owner, OWNER)
        self.assertEqual(azure.host, 'syntheticaccount.blob.core.windows.net')
        settings = boto.client.call_args.kwargs['config']
        self.assertEqual(settings['connect_timeout'], 10)
        self.assertEqual(settings['read_timeout'], 30)
        self.assertEqual(settings['retries']['total_max_attempts'], 1)
        self.assertFalse(self.service.connections)

    def test_entry_rejects_untrusted_catalog_and_shared_scratch_writer(self):
        self.service.data[coverage.ANCHOR_BLOB] = engine.wrapped(dict(self.baseline, bucket='other'))
        status, result = self.invoke()
        self.assertEqual(status, 1)
        self.assertEqual(self.source.requests, 0)
        with evidence._lock(self.parent):
            self.assertEqual(self.invoke()[0], 1)
        self.factory.assert_called_once()


if __name__ == '__main__':
    unittest.main()
