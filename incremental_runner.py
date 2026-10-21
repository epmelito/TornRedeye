"""Thin S3/Azure adapters and explicitly gated incremental backup executable."""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, urlencode, parse_qsl
from xml.etree import ElementTree as ET

import evidence_backup as evidence
import incremental_backup as coverage
import incremental_transfer as engine


class S3Source:
    def __init__(self, client, bucket, owner, *, max_requests=262144,
                 max_listing_bytes=4*1024**3, max_attempts=3, check_runtime=lambda: None):
        engine.require(type(max_requests) is int and max_requests >= 0
                       and type(max_listing_bytes) is int and max_listing_bytes >= 0
                       and type(max_attempts) is int and 1 <= max_attempts <= 3, 'invalid S3 adapter limits')
        self.client, self.bucket, self.owner = client, bucket, owner
        self.max_requests, self.max_listing_bytes = max_requests, max_listing_bytes
        self.max_attempts, self.check_runtime = max_attempts, check_runtime
        self.requests, self.listing_bytes = 0, 0

    def request(self, method, **kwargs):
        try:
            return getattr(self.client, method)(ExpectedBucketOwner=self.owner, **kwargs)
        except Exception as error:
            status = getattr(error, 'response', {}).get('ResponseMetadata', {}).get('HTTPStatusCode')
            if isinstance(error, OSError) or status in (429, 500, 502, 503, 504) or type(error).__name__ in (
                    'EndpointConnectionError', 'ConnectionClosedError', 'ReadTimeoutError', 'ConnectTimeoutError'):
                raise OSError('transient S3 request failure') from error
            raise engine.TransferError('S3 request failed' + (f' (HTTP {status})' if status else '')) from error

    def get_object(self, **kwargs):
        engine.require(kwargs['Bucket'] == self.bucket and 'IfMatch' in kwargs, 'invalid conditional source read')
        return self.request('get_object', **kwargs)

    def inventory(self, prefix):
        engine.require(prefix in evidence.PREFIXES, 'invalid source prefix')
        token, tokens = None, set()
        while True:
            request = dict(Bucket=self.bucket, Prefix=prefix, MaxKeys=1000)
            if token is not None:
                request['ContinuationToken'] = token
            for attempt in range(self.max_attempts):
                self.check_runtime()
                engine.require(self.requests < self.max_requests, 'S3 LIST request limit exceeded')
                self.requests += 1
                try:
                    response = self.request('list_objects_v2', **request)
                    break
                except OSError:
                    if attempt + 1 == self.max_attempts:
                        raise
            objects = response.get('Contents', [])
            engine.require(type(response.get('IsTruncated')) is bool and isinstance(objects, list)
                           and len(objects) <= 1000 and type(response.get('KeyCount', len(objects))) is int
                           and response.get('KeyCount', len(objects)) == len(objects),
                           'invalid S3 inventory response')
            following = response.get('NextContinuationToken') if response['IsTruncated'] else None
            engine.require(not response['IsTruncated'] or isinstance(following, str) and following
                           and following not in tokens and objects, 'invalid S3 continuation')
            items = [{'key': o['Key'], 'size': o['Size'], 'etag': o['ETag'],
                      'last_modified': o['LastModified'].isoformat()} for o in objects]
            page = {'request_token': token, 'next_token': following, 'objects': items}
            size = len(evidence._bytes(page))
            self.listing_bytes += size
            engine.require(size <= 4*1024**2 and self.listing_bytes <= self.max_listing_bytes,
                           'S3 listing metadata byte limit exceeded')
            self.check_runtime()
            yield page
            if following is None:
                return
            tokens.add(following)
            token = following


class AzureBody:
    def __init__(self, response, connection):
        self.response, self.connection = response, connection
    def read(self, size):
        engine.require(type(size) is int and 0 < size <= evidence.CHUNK, 'unbounded Azure response read')
        try:
            return self.response.read(size)
        except http.client.HTTPException as error:
            raise OSError('Azure response interrupted') from error
    def close(self):
        try:
            self.response.close()
        finally:
            self.connection.close()


class AzureBlobs:
    """Azure public-cloud REST; existing SAS supplied by the caller, never logged."""
    def __init__(self, account, container, sas, *, connect_timeout=10, read_timeout=30,
                 max_listing_bytes=8*1024**2, check_runtime=lambda: None,
                 connection_factory=http.client.HTTPSConnection):
        engine.require(re.fullmatch(r'[a-z0-9]{3,24}', account) and
                       re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])', container)
                       and '--' not in container, 'invalid Azure destination')
        engine.require(isinstance(sas, str) and 'sig=' in sas and all(ord(c) > 32 and ord(c) < 127 for c in sas),
                       'existing Azure SAS environment value required')
        engine.require(0 < connect_timeout <= 30 and 0 < read_timeout <= 60, 'invalid transport timeout')
        pairs = parse_qsl(sas.lstrip('?'), keep_blank_values=True, strict_parsing=True)
        keys = [key for key, _ in pairs]
        engine.require(len(keys) == len(set(keys)) and dict(pairs).get('sig')
                       and not set(keys) & {'comp', 'restype', 'prefix', 'marker', 'maxresults'},
                       'invalid Azure SAS query')
        engine.require(type(max_listing_bytes) is int and 0 < max_listing_bytes <= 8*1024**2,
                       'invalid Azure listing byte limit')
        self.host, self.container, self.sas = account+'.blob.core.windows.net', container, urlencode(pairs)
        self.connect_timeout, self.read_timeout = connect_timeout, read_timeout
        self.max_listing_bytes, self.check_runtime = max_listing_bytes, check_runtime
        self.connection_factory = connection_factory
        self.listing_bytes = 0

    def request(self, method, name='', query=None, stream=None, size=0):
        self.check_runtime()
        path = '/' + self.container + ('/'+quote(name, safe='/') if name else '')
        target = path + '?' + (urlencode(query)+'&' if query else '') + self.sas
        connection = self.connection_factory(self.host, timeout=self.connect_timeout)
        try:
            connection.connect()
            connection.sock.settimeout(self.read_timeout)
            headers = {'x-ms-version': '2019-12-12', 'Connection': 'close'}
            if stream is None:
                connection.request(method, target, headers=headers)
            else:
                engine.require(type(size) is int and 0 <= size <= coverage.MAX_ARCHIVE_BYTES, 'Azure upload size limit')
                connection.putrequest(method, target)
                for key, value in dict(headers, **{'Content-Length': str(size),
                        'x-ms-blob-type': 'BlockBlob', 'If-None-Match': '*'}).items():
                    connection.putheader(key, value)
                connection.endheaders()
                sent = 0
                while sent < size:
                    self.check_runtime()
                    chunk = stream.read(min(evidence.CHUNK, size-sent))
                    engine.require(isinstance(chunk, bytes) and 0 < len(chunk) <= min(evidence.CHUNK, size-sent),
                                   'truncated Azure upload stream')
                    connection.send(chunk)
                    sent += len(chunk)
            response = connection.getresponse()
            expected = 201 if method == 'PUT' else 200
            if response.status != expected:
                status, code = response.status, response.getheader('x-ms-error-code')
                response.close()
                if method == 'GET' and status == 404 and code == 'BlobNotFound':
                    raise engine.BlobMissing()
                if method == 'PUT' and status in (409, 412) and code in ('BlobAlreadyExists', 'ConditionNotMet'):
                    raise engine.BlobExists()
                if status in (408, 429, 500, 502, 503, 504):
                    raise OSError('transient Azure request failure')
                raise engine.TransferError(f'Azure request failed (HTTP {status})')
            return AzureBody(response, connection), response
        except BaseException as error:
            connection.close()
            if isinstance(error, http.client.HTTPException):
                raise OSError('Azure request interrupted') from error
            raise

    def open_blob(self, name):
        engine.require((name == coverage.ANCHOR_BLOB or name.startswith(coverage.BATCH_PREFIX)), 'Azure read scope violation')
        body, response = self.request('GET', name)
        try:
            size = int(response.getheader('Content-Length'))
            etag = response.getheader('ETag')
            engine.require(size >= 0 and etag and response.getheader('x-ms-blob-type') == 'BlockBlob',
                           'invalid Azure blob headers')
            return engine.BlobRead(body, size, etag)
        except BaseException:
            body.close()
            raise

    def create_block_blob(self, name, stream, size):
        engine.require((name == coverage.ANCHOR_BLOB or name.startswith(coverage.BATCH_PREFIX)) and '..' not in name.split('/'),
                       'Azure write scope violation')
        body, _ = self.request('PUT', name, stream=stream, size=size)
        body.close()

    def list_blobs(self, *, prefix, continuation_token, page_size):
        engine.require(prefix == coverage.BATCH_PREFIX and type(page_size) is int and 1 <= page_size <= 5000,
                       'invalid Azure listing scope')
        query = {'restype': 'container', 'comp': 'list', 'prefix': prefix, 'maxresults': page_size}
        if continuation_token is not None:
            query['marker'] = continuation_token
        body, _ = self.request('GET', query=query)
        data = bytearray()
        try:
            while True:
                self.check_runtime()
                chunk = body.read(min(evidence.CHUNK, self.max_listing_bytes-len(data)+1))
                if not chunk:
                    break
                data.extend(chunk)
                self.listing_bytes += len(chunk)
                engine.require(len(data) <= self.max_listing_bytes, 'Azure listing response byte limit exceeded')
        finally:
            body.close()
        engine.require(b'<!DOCTYPE' not in data.upper() and b'<!ENTITY' not in data.upper(), 'unsafe Azure XML')
        root = ET.fromstring(data)
        engine.require(root.tag == 'EnumerationResults' and root.find('Blobs') is not None
                       and root.find('NextMarker') is not None, 'invalid Azure listing response')
        entries = [{'name': b.findtext('Name'), 'size': int(b.findtext('Properties/Content-Length'))}
                   for b in root.find('Blobs')]
        engine.require(len(entries) <= page_size, 'oversized Azure listing page')
        return {'blobs': entries, 'next_token': root.findtext('NextMarker') or None}


def clients(args, limits):
    # import SDK and resolve existing credentials only behind the explicit CLI gate
    sas = os.environ.get('TORNREDEYE_AZURE_SAS')
    started = time.monotonic()
    def runtime():
        engine.require(time.monotonic()-started < limits.max_seconds, 'adapter runtime limit exceeded')
    azure = AzureBlobs(args.azure_account, args.azure_container, sas, check_runtime=runtime)
    try:
        import boto3
        from botocore.config import Config
    except ImportError as error:
        raise engine.TransferError('existing boto3 installation required for S3 adapter') from error
    client = boto3.client('s3', region_name=args.region,
                          config=Config(connect_timeout=10, read_timeout=30,
                                        retries={'mode': 'standard', 'total_max_attempts': 1}))
    return S3Source(client, args.bucket, args.owner, max_attempts=limits.max_attempts,
                    check_runtime=runtime), azure


def main(argv=None, *, client_factory=clients):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bucket', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--region', required=True)
    parser.add_argument('--azure-account', required=True)
    parser.add_argument('--azure-container', required=True)
    parser.add_argument('--anchor-sha256', required=True)
    parser.add_argument('--scratch-parent', type=Path, required=True)
    parser.add_argument('--max-seconds', type=float, default=3600)
    parser.add_argument('--live-access-approved', action='store_true')
    args = parser.parse_args(argv)
    try:
        engine.require(args.live_access_approved, 'live access requires separate authorization and --live-access-approved')
        coverage._hash(args.anchor_sha256)
        engine.require(re.fullmatch(r'[0-9]{12}', args.owner) and re.fullmatch(r'[a-z0-9-]+', args.region)
                       and re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', args.bucket), 'invalid AWS configuration')
        limits = engine.Limits(max_seconds=args.max_seconds)
        engine.Budget(limits)
        # reject unsafe scratch before creating clients or resolving credentials
        with evidence._lock(args.scratch_parent):
            s3, azure = client_factory(args, limits)
            result = engine.run_backup(s3=s3, azure=azure, bucket=args.bucket,
                anchor_sha256=args.anchor_sha256,
                inventories={prefix: s3.inventory(prefix) for prefix in evidence.PREFIXES},
                limits=limits, scratch_parent=args.scratch_parent)
            result['operations']['s3_list'] = s3.requests
            result['source_inventory'] = {'requests': s3.requests, 'serialized_bytes': s3.listing_bytes}
            result['azure_listing_response_bytes'] = azure.listing_bytes
        print(json.dumps(result, sort_keys=True))
        return 0 if result['complete'] else 1
    except Exception as error:
        print(json.dumps({'complete': False, 'error': evidence._diagnostic(error)}, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
