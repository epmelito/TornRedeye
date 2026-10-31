"""Build a local-only S3 upload/repair plan; never create a client or execute writes."""

import argparse
import hashlib
import json
from pathlib import Path
import re

from tools.package_stock_frontend import OPERATIONS

HASHED_ASSET = re.compile(r"stock-assets/[A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9_-]{8,}\.(?:js|css|svg|ttf|woff2)\Z")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".ttf": "font/ttf",
         ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8", ".json": "application/json"}
JSON_FILES = {"data.json": "public,max-age=300", "stock.json": "public,max-age=15"}
LICENSE = "stock-assets/FONT-LICENSE.txt"


def metadata(name):
    if name in JSON_FILES:
        cache = JSON_FILES[name]
    elif name in OPERATIONS or name == LICENSE:
        cache = "public,max-age=300"
    elif name == "stock.html":
        cache = "public,max-age=0,must-revalidate"
    elif HASHED_ASSET.fullmatch(name) and ".." not in name:
        cache = "public,max-age=31536000,immutable"
    else:
        raise ValueError(f"unknown public object: {name}")
    return {"CacheControl": cache, "ContentType": TYPES[Path(name).suffix]}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique)


def plan(directory, inventory, retained_manifests=()):
    directory = Path(directory).resolve()
    files = read_json(directory / "stock-site-files.json")
    upload = read_json(directory / "stock-site-upload-metadata.json")
    if (not isinstance(files, list) or not all(isinstance(n, str) for n in files)
            or len(set(files)) != len(files) or not isinstance(upload, dict) or set(upload) != set(files)):
        raise ValueError("upload manifests must list the same unique paths")
    if not set((*OPERATIONS, "stock.html", LICENSE)).issubset(files):
        raise ValueError("assembled operations/stock assets are incomplete")
    local = {}
    for name in files:
        expected = metadata(name)
        if name in JSON_FILES or upload[name] != expected:
            raise ValueError("static manifest metadata mismatch or forbidden JSON upload")
        source = directory / name
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(directory):
            raise ValueError("local source must be a regular file inside the assembled directory")
        body = source.read_bytes()
        local[name] = {"path": name, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    retained = set()
    for manifest in retained_manifests:
        if not isinstance(manifest, dict):
            raise ValueError("retained release metadata must be an explicit reviewed manifest")
        for name, value in manifest.items():
            if name in JSON_FILES or value != metadata(name):
                raise ValueError("invalid retained release metadata")
            retained.add(name)
    if (not isinstance(inventory, dict) or inventory.get("complete") is not True
            or inventory.get("prefix") != "public/" or not isinstance(inventory.get("objects"), list)
            or not isinstance(inventory.get("bucket"), str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", inventory["bucket"])):
        raise ValueError("require complete public/ inventory and explicit hosting bucket")
    existing = {}
    for obj in inventory["objects"]:
        if not isinstance(obj, dict) or not isinstance(obj.get("Key"), str) or not obj["Key"].startswith("public/"):
            raise ValueError("inventory contains a non-public key")
        name = obj["Key"][len("public/"):]
        metadata(name)
        if name not in set((*OPERATIONS, "stock.html", LICENSE, *JSON_FILES)) | set(local) | retained:
            raise ValueError("object is not in current or retained release allowlists")
        if name in existing:
            raise ValueError("duplicate inventory object")
        if (not isinstance(obj.get("ETag"), str) or not re.fullmatch(r'"[A-Za-z0-9-]+"', obj["ETag"])
                or type(obj.get("Size")) is not int or obj["Size"] < 0):
            raise ValueError("inventory requires an opaque quoted ETag and nonnegative size")
        existing[name] = obj
    if not set(JSON_FILES).issubset(existing):
        raise ValueError("both public JSON objects must exist; never fabricate a missing snapshot")
    operations = []
    for name in sorted(set(local) | set(existing)):
        prior = existing.get(name)
        expected = upload[name] if name in upload else metadata(name)
        matches = prior and all(prior.get(k) == v for k, v in expected.items())
        action = "upload" if name in local else "verify" if matches else "repair"
        item = {"key": "public/" + name, "action": action, "metadata": expected,
                "prior": {"ETag": prior["ETag"], "Size": prior["Size"]} if prior else None}
        if name in local:
            item["local_source"] = local[name]
        if action != "verify":
            item["put_object"] = {"Bucket": inventory["bucket"], "Key": item["key"], **expected,
                                  **({"IfMatch": prior["ETag"]} if prior else {"IfNoneMatch": "*"})}
            if prior:
                item["read_before_write"] = {"Bucket": inventory["bucket"], "Key": item["key"], "IfMatch": prior["ETag"]}
            item["preserve_existing_properties"] = bool(prior)
        operations.append(item)
    return {"schema_version": 1, "dry_run": True, "bucket": inventory["bucket"], "prefix": "public/",
            "requires_publishers_paused": True, "delete_operations": [], "operations": operations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--retained-manifest", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = plan(args.directory, read_json(args.inventory), [read_json(p) for p in args.retained_manifest])
    # output is operator evidence outside the assembled content, never an upload
    if (args.output.resolve().is_relative_to(args.directory.resolve())
            or args.output.resolve() in {p.resolve() for p in [args.inventory, *args.retained_manifest]}):
        raise ValueError("write the private plan outside the assembled content")
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
