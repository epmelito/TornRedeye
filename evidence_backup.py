"""Private, sequential evidence export and AWS-independent verification.

The caller supplies an S3 client. Only the explicitly gated CLI imports boto3.
Inventory pages and object receipts keep archive traversal bounded in memory.
"""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from uuid import uuid4


PREFIXES = ("raw/yata/jap/206/", "normalized/yata/jap/206/")
CHUNK = 64 * 1024
SCHEMA = 1


class BackupError(Exception):
    """A local contract or integrity failure, without source payload details."""


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _diagnostic(error):
    return str(error) if isinstance(error, BackupError) else type(error).__name__


def _path(root, relative):
    path = root / relative
    # generated paths only; reject existing symlinks/junctions at every level
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise BackupError("unsafe local path")
        if part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise BackupError("unsafe local reparse point")
    if not path.resolve().is_relative_to(root.resolve()):
        raise BackupError("path outside backup directory")
    return path


def _write(root, relative, value):
    path = _path(root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _path(root, relative + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_bytes({"value": value, "sha256": _digest(value)}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(root, relative, limit=2 * 1024 * 1024):
    path = _path(root, relative)
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise BackupError("local metadata exceeds bound")
    try:
        wrapper = json.loads(data)
        value = wrapper["value"]
        if wrapper["sha256"] != _digest(value):
            raise BackupError("metadata checksum mismatch")
        return value
    except (ValueError, KeyError, TypeError) as error:
        raise BackupError("invalid local metadata") from error


@contextmanager
def _lock(root):
    root = Path(root).absolute()
    _path(root, "writer.lock")
    if any((part / ".git").exists() for part in (root, *root.parents)):
        raise BackupError("backup directory must be outside Git working trees")
    root.mkdir(parents=True, exist_ok=True)
    lock = _path(root, "writer.lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise BackupError("backup locked; confirm no writer before removing lock") from error
    os.close(descriptor)
    try:
        yield root
    finally:
        lock.unlink()


def _manifest(root, bucket=None, cutoff=None):
    if _path(root, "manifest.json").exists():
        manifest = _read(root, "manifest.json")
        if (manifest.get("schema_version") != SCHEMA
                or manifest.get("prefixes") != list(PREFIXES)
                or not isinstance(manifest.get("bucket"), str)
                or len(manifest.get("inventory", [])) != 2):
            raise BackupError("unsupported manifest")
        for state in manifest["inventory"]:
            if (not isinstance(state, dict)
                    or any(type(state.get(field)) is not int or state[field] < 0
                           for field in ("pages", "objects", "bytes"))
                    or type(state.get("complete")) is not bool
                    or (state["complete"] and state["pages"] == 0)
                    or (state["pages"] and not state["complete"] and not state.get("next_token"))):
                raise BackupError("invalid inventory checkpoint")
        if bucket is not None and (manifest["bucket"] != bucket
                                   or manifest["paused_cutoff_utc"] != cutoff):
            raise BackupError("source or cutoff differs from existing backup")
        return manifest
    if not bucket or not isinstance(bucket, str):
        raise BackupError("manifest missing or bucket invalid")
    if cutoff is not None:
        try:
            timestamp = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
            if timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
                raise ValueError()
        except (ValueError, AttributeError):
            raise BackupError("cutoff must be an explicit UTC timestamp") from None
    manifest = {"schema_version": SCHEMA, "bucket": bucket,
                "prefixes": list(PREFIXES), "created_utc": _now(),
                "paused_cutoff_utc": cutoff,
                "inventory": [{"pages": 0, "objects": 0, "bytes": 0,
                               "next_token": None, "last_key": "",
                               "chain": "", "complete": False, "error": None}
                              for _ in PREFIXES]}
    _write(root, "manifest.json", manifest)
    return manifest


def _key_id(key):
    if not isinstance(key, str) or not any(
            key.startswith(prefix) for prefix in PREFIXES):
        raise BackupError("unexpected evidence key")
    try:
        encoded = key.encode("utf-8")
    except UnicodeError:
        raise BackupError("invalid evidence key") from None
    if len(encoded) > 1024:
        raise BackupError("evidence key exceeds S3 limit")
    # never interpret source keys as filesystem paths, including on Windows
    return hashlib.sha256(encoded).hexdigest()


def _page_name(index, number):
    return f"inventory/{index}/{number:08d}.json"


def _pages(root, manifest):
    for index, state in enumerate(manifest["inventory"]):
        chain, count, size, last, token = "", 0, 0, "", None
        for number in range(state["pages"]):
            page = _read(root, _page_name(index, number))
            if (not isinstance(page.get("objects"), list)
                    or len(page["objects"]) > 1000
                    or (number + 1 < state["pages"] and not page.get("next_token"))):
                raise BackupError("inventory page sequence inconsistent")
            if page["request_token"] != token:
                raise BackupError("inventory token chain inconsistent")
            for item in page["objects"]:
                _key_id(item["key"])
                if not item["key"].startswith(PREFIXES[index]) or item["key"] <= last:
                    raise BackupError("duplicate or unordered inventory key")
                if type(item["size"]) is not int or item["size"] < 0:
                    raise BackupError("invalid inventory size")
                last = item["key"]
                count += 1
                size += item["size"]
            token = page["next_token"]
            chain = hashlib.sha256((chain + _digest(page)).encode()).hexdigest()
            yield page
        if (chain != state["chain"] or count != state["objects"]
                or size != state["bytes"] or last != state["last_key"]
                or token != state["next_token"]
                or (state["complete"] and token is not None)):
            raise BackupError("inventory checkpoint inconsistent")


def inventory(*, s3, bucket, directory, paused_cutoff_utc=None,
              page_size=100, max_seconds=3600):
    """Checkpoint every bounded ListObjectsV2 page; resume at its saved token.

    paused_cutoff_utc attests that all writers remain paused/drained throughout
    inventory and export, including reruns. The tool cannot establish this fact.
    """
    if not 1 <= page_size <= 1000 or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise BackupError("invalid inventory limits")
    deadline = time.monotonic() + max_seconds
    with _lock(directory) as root:
        manifest = _manifest(root, bucket, paused_cutoff_utc)
        for _ in _pages(root, manifest):
            pass
        for index, prefix in enumerate(PREFIXES):
            state = manifest["inventory"][index]
            while not state["complete"]:
                if time.monotonic() >= deadline:
                    state["error"] = "runtime_limit"
                    _write(root, "manifest.json", manifest)
                    return manifest
                request = {"Bucket": bucket, "Prefix": prefix, "MaxKeys": page_size}
                if state["next_token"] is not None:
                    request["ContinuationToken"] = state["next_token"]
                try:
                    response = s3.list_objects_v2(**request)
                    contents = response.get("Contents", [])
                    if (type(response.get("IsTruncated")) is not bool
                            or len(contents) > page_size
                            or response.get("KeyCount") != len(contents)):
                        raise BackupError("invalid inventory page")
                    token = response.get("NextContinuationToken") if response["IsTruncated"] else None
                    if response["IsTruncated"] and (not isinstance(token, str) or not token):
                        raise BackupError("missing continuation token")
                    if token is not None:
                        if token == state["next_token"]:
                            raise BackupError("repeated continuation token")
                        if not contents:
                            raise BackupError("empty truncated inventory page")
                    items, last = [], state["last_key"]
                    for item in contents:
                        key = item["Key"]
                        _key_id(key)
                        if (not key.startswith(prefix) or key <= last
                                or type(item["Size"]) is not int or item["Size"] < 0
                                or not isinstance(item["ETag"], str)):
                            raise BackupError("invalid inventory object")
                        items.append({"key": key, "size": item["Size"],
                                      "etag": item["ETag"],
                                      "last_modified": item["LastModified"].isoformat()})
                        last = key
                    page = {"request_token": state["next_token"],
                            "next_token": token, "objects": items}
                    name = _page_name(index, state["pages"])
                    if _path(root, name).exists() and _read(root, name) != page:
                        raise BackupError("uncheckpointed inventory page conflict")
                    _write(root, name, page)
                    state.update(pages=state["pages"] + 1,
                                 objects=state["objects"] + len(items),
                                 bytes=state["bytes"] + sum(item["size"] for item in items),
                                 last_key=last, next_token=token,
                                 chain=hashlib.sha256((state["chain"] + _digest(page)).encode()).hexdigest(),
                                 complete=not response["IsTruncated"], error=None)
                except Exception as error:
                    state["error"] = _diagnostic(error)
                    _write(root, "manifest.json", manifest)
                    break
                _write(root, "manifest.json", manifest)
        return manifest


def _file_hash(path):
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _receipt(root, manifest, item):
    identifier = _key_id(item["key"])
    name = f"objects/{identifier}.json"
    if not _path(root, name).exists():
        return None
    receipt = _read(root, name)
    if (receipt.get("bucket") != manifest["bucket"] or receipt.get("inventory") != item
            or receipt.get("key") != item["key"]):
        raise BackupError("receipt identity conflict")
    return receipt


def _verified(root, item, receipt, extension="bin"):
    if receipt is None or receipt.get("status") not in ("verified", "prepared"):
        return "pending" if receipt is None else receipt.get("status", "invalid_status")
    metadata = receipt.get("metadata")
    if (not isinstance(metadata, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items())
            or not isinstance(receipt.get("content_type"), str)
            or receipt.get("size") != item["size"]
            or receipt.get("source_content_length") != item["size"]
            or receipt.get("etag") != item["etag"]
            or receipt.get("last_modified") != item["last_modified"]):
        return "metadata_inconsistent"
    path = _path(root, f"objects/{_key_id(item['key'])}.{extension}")
    if not path.is_file():
        return "missing"
    size, digest = _file_hash(path)
    if size != receipt["size"]:
        return "size_mismatch"
    if digest != receipt.get("sha256"):
        return "hash_mismatch"
    if item["key"].startswith(PREFIXES[0]) and "sha256" in metadata and metadata["sha256"] != digest:
        return "source_checksum_mismatch"
    return "verified"


def _relationship(root, manifest, item):
    """Inspect bounded normalized links without changing canonical bytes."""
    key = item["key"]
    if key.startswith(PREFIXES[0]):
        if not key.endswith(".bin"):
            return "unrecognized_pair_name"
        counterpart = PREFIXES[1] + key[len(PREFIXES[0]):-4] + ".json"
        return ("normalized_receipt_present" if _path(root, f"objects/{_key_id(counterpart)}.json").exists()
                else "no_normalized_local_receipt")
    if item["size"] > 1024 ** 2:
        return "normalized_link_unassessed_size_limit"
    try:
        document = json.loads(_path(root, f"objects/{_key_id(key)}.bin").read_bytes())
    except (ValueError, UnicodeError, RecursionError):
        return "normalized_link_unreadable"
    if not isinstance(document, dict):
        return "normalized_link_unreadable"
    link = document.get("raw_evidence")
    if link is None:
        return "no_raw_link"
    if not isinstance(link, dict):
        return "invalid_raw_link"
    raw_key = link.get("key")
    if (link.get("bucket") != manifest["bucket"] or not isinstance(raw_key, str)
            or not raw_key.startswith(PREFIXES[0])):
        return "raw_link_identity_mismatch"
    try:
        identifier = _key_id(raw_key)
    except BackupError:
        return "invalid_raw_link"
    name = f"objects/{identifier}.json"
    if not _path(root, name).exists():
        return "raw_counterpart_unavailable_locally"
    raw = _read(root, name)
    if raw.get("key") != raw_key or raw.get("bucket") != link["bucket"]:
        return "raw_link_identity_mismatch"
    if (raw.get("sha256") != link.get("sha256")
            or raw.get("size") != link.get("size_bytes")):
        return "raw_link_integrity_mismatch"
    if _verified(root, raw["inventory"], raw) != "verified":
        return "raw_counterpart_unverified"
    return "raw_link_verified"


def export(*, s3, directory, max_bytes=1024 ** 3, max_seconds=3600,
           bytes_per_second=8 * 1024 ** 2):
    """Export the saved inventory; no listing, transformation, or overwrite.

    Failed transfers retry on the next call. Verified files require no S3 read.
    The transfer budget includes bytes read on failed attempts.
    """
    if (min(max_bytes, max_seconds, bytes_per_second) <= 0
            or not math.isfinite(max_seconds)):
        raise BackupError("invalid transfer limits")
    started, transferred = time.monotonic(), 0
    with _lock(directory) as root:
        manifest = _manifest(root)
        # validate the complete checkpoint before making any source reads
        for _ in _pages(root, manifest):
            pass
        for page in _pages(root, manifest):
            for item in page["objects"]:
                identifier = _key_id(item["key"])
                name = f"objects/{identifier}.json"
                target = _path(root, f"objects/{identifier}.bin")
                receipt = _receipt(root, manifest, item)
                status = _verified(root, item, receipt)
                if receipt and receipt.get("status") == "prepared" and not target.exists():
                    temporary = _path(root, f"objects/{identifier}.part")
                    if temporary.exists():
                        if _verified(root, item, receipt, "part") != "verified":
                            continue
                        os.link(temporary, target)
                        temporary.unlink()
                        status = "verified"
                if status == "verified":
                    if receipt["status"] == "prepared":
                        receipt["status"] = "verified"
                        receipt["error"] = None
                        _write(root, name, receipt)
                    continue
                if target.exists():
                    # preserve conflicting files and receipts for diagnosis
                    continue
                if receipt and receipt["status"] == "verified":
                    continue
                if transferred >= max_bytes or time.monotonic() - started >= max_seconds:
                    return
                receipt = {"bucket": manifest["bucket"], "key": item["key"],
                           "inventory": item, "status": "downloading", "error": None}
                _write(root, name, receipt)
                temporary = _path(root, f"objects/{identifier}.part")
                if temporary.exists():
                    # only our named staging file is disposable; never final evidence
                    temporary.unlink()
                body = None
                try:
                    response = s3.get_object(Bucket=manifest["bucket"], Key=item["key"],
                                             IfMatch=item["etag"])
                    body = response["Body"]
                    receipt.update(content_type=response.get("ContentType"),
                                   metadata=response.get("Metadata", {}),
                                   etag=response.get("ETag"),
                                   last_modified=response["LastModified"].isoformat(),
                                   source_content_length=response.get("ContentLength"))
                    _write(root, name, receipt)
                    if (response["ContentLength"] != item["size"]
                            or response["ETag"] != item["etag"]
                            or response["LastModified"].isoformat() != item["last_modified"]):
                        raise BackupError("source changed since inventory")
                    metadata = response.get("Metadata", {})
                    if (not isinstance(metadata, dict)
                            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in metadata.items())
                            or not isinstance(response.get("ContentType"), str)):
                        raise BackupError("source metadata invalid")
                    digest, size = hashlib.sha256(), 0
                    with temporary.open("xb") as stream:
                        while True:
                            if (time.monotonic() - started >= max_seconds
                                    or (transferred >= max_bytes and size < item["size"])):
                                raise BackupError("transfer limit")
                            requested = (1 if size == item["size"] else
                                         min(CHUNK, max_bytes - transferred, bytes_per_second))
                            chunk = body.read(requested)
                            if not chunk:
                                break
                            if len(chunk) > requested:
                                raise BackupError("source exceeded read bound")
                            stream.write(chunk)
                            digest.update(chunk)
                            size += len(chunk)
                            transferred += len(chunk)
                            if size > item["size"]:
                                raise BackupError("source length mismatch")
                            delay = transferred / bytes_per_second - (time.monotonic() - started)
                            if delay > 0:
                                time.sleep(min(delay, max(0, max_seconds - (time.monotonic() - started))))
                        stream.flush()
                        os.fsync(stream.fileno())
                    checksum = digest.hexdigest()
                    if size != item["size"]:
                        raise BackupError("source length mismatch")
                    if item["key"].startswith(PREFIXES[0]) and metadata.get("sha256", checksum) != checksum:
                        raise BackupError("source SHA-256 mismatch")
                    if response.get("ChecksumSHA256") and response.get("ChecksumType") == "FULL_OBJECT":
                        if response["ChecksumSHA256"] != base64.b64encode(digest.digest()).decode():
                            raise BackupError("S3 SHA-256 mismatch")
                    receipt.update(status="prepared", size=size, sha256=checksum,
                                   content_type=response["ContentType"], metadata=metadata,
                                   etag=response["ETag"], last_modified=item["last_modified"],
                                   version_id=response.get("VersionId"), exported_utc=_now())
                    _write(root, name, receipt)
                    # hard link publishes a fully flushed file atomically without overwrite
                    os.link(temporary, target)
                    temporary.unlink()
                    receipt["status"] = "verified"
                    _write(root, name, receipt)
                except Exception as error:
                    if receipt["status"] != "prepared":
                        receipt["status"] = "failed"
                    receipt["error"] = _diagnostic(error)
                    _write(root, name, receipt)
                finally:
                    if body is not None:
                        body.close()


def verify(directory):
    """Stream local verification results; never imports an SDK or contacts AWS."""
    root = Path(directory).absolute()
    manifest = _manifest(root)
    for page in _pages(root, manifest):
        for item in page["objects"]:
            relationship = None
            try:
                receipt = _receipt(root, manifest, item)
                status = _verified(root, item, receipt)
                if receipt and receipt.get("status") == "prepared":
                    if status == "verified":
                        status = "incomplete_publication"
                    elif status == "missing" and _path(root, f"objects/{_key_id(item['key'])}.part").exists():
                        staging = _verified(root, item, receipt, "part")
                        status = "incomplete_publication" if staging == "verified" else "staging_" + staging
                if (status in ("pending", "downloading", "failed")
                        and _path(root, f"objects/{_key_id(item['key'])}.bin").exists()):
                    status = "conflicting_file"
                if status == "verified":
                    relationship = _relationship(root, manifest, item)
            except (BackupError, OSError, KeyError, TypeError, ValueError):
                status = "invalid_receipt_or_path"
            yield {"key": item["key"], "status": status, "relationship": relationship}
    yield {"inventory_complete": all(s["complete"] and not s["error"] for s in manifest["inventory"]),
           "consistent_cutoff_attested": manifest["paused_cutoff_utc"] is not None,
           "paused_cutoff_utc": manifest["paused_cutoff_utc"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inventory", "export", "verify"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("--bucket")
    parser.add_argument("--paused-cutoff-utc")
    parser.add_argument("--live-read-approved", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=3600)
    parser.add_argument("--max-bytes", type=int, default=1024 ** 3)
    parser.add_argument("--bytes-per-second", type=int, default=8 * 1024 ** 2)
    args = parser.parse_args(argv)
    try:
        if args.operation != "verify":
            if not args.live_read_approved:
                parser.error("live reads require separate authorization and --live-read-approved")
            if args.operation == "inventory" and not args.bucket:
                parser.error("inventory requires --bucket")
            import boto3
            from botocore.config import Config
            s3 = boto3.client("s3", region_name="eu-north-1",
                              config=Config(connect_timeout=10, read_timeout=30,
                                            retries={"mode": "standard", "total_max_attempts": 3}))
            if args.operation == "inventory":
                inventory(s3=s3, bucket=args.bucket, directory=args.directory,
                          paused_cutoff_utc=args.paused_cutoff_utc, max_seconds=args.max_seconds)
            else:
                export(s3=s3, directory=args.directory, max_bytes=args.max_bytes,
                       max_seconds=args.max_seconds, bytes_per_second=args.bytes_per_second)
        counts, relationships, coverage = {}, {}, None
        for result in verify(args.directory):
            if "status" in result:
                counts[result["status"]] = counts.get(result["status"], 0) + 1
                relationship = result["relationship"]
                if relationship:
                    relationships[relationship] = relationships.get(relationship, 0) + 1
            else:
                coverage = result
        print(json.dumps({"objects": counts, "relationships": relationships,
                          "coverage": coverage}, sort_keys=True))
        return int(any(status != "verified" for status in counts)
                   or any("mismatch" in status or status == "invalid_raw_link" for status in relationships)
                   or not coverage["inventory_complete"]
                   or not coverage["consistent_cutoff_attested"])
    except Exception as error:
        print("Backup operation failed: " + _diagnostic(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
