"""Actual packaging plus offline conditional metadata planning; no S3 clients."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import package_stock_frontend as package
from tools import plan_public_metadata as planner


class PublicMetadataPlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        build = root / "frontend/dist"
        (build / "stock-assets").mkdir(parents=True)
        (build / "stock.html").write_text("stock entry")
        (build / "stock-assets/app-12345678.js").write_text("public script")
        (build / "stock-assets/app-12345678.css").write_text("public style")
        fonts = root / "frontend/src/fonts"
        fonts.mkdir(parents=True)
        (fonts / "OFL.txt").write_text("license")
        (root / "dashboard").mkdir()
        for name in package.OPERATIONS:
            (root / "dashboard" / name).write_text("operations " + name)
        with patch.object(package, "ROOT", root):
            self.directory = package.package(root / "assembled")
        self.inventory = {"bucket": "synthetic-hosting", "prefix": "public/", "complete": True,
                          "objects": [self.obj("data.json"), self.obj("stock.json", "public,max-age=15"),
                                      self.obj("index.html"), self.obj("stock-assets/old-87654321.js")]}
        self.retained = {"stock-assets/old-87654321.js": {
            "CacheControl": "public,max-age=31536000,immutable", "ContentType": "text/javascript; charset=utf-8"}}

    def make_plan(self, inventory=None):
        return planner.plan(self.directory, self.inventory if inventory is None else inventory, [self.retained])

    @staticmethod
    def obj(name, cache=None):
        return {"Key": "public/" + name, "ETag": '"opaque-2"', "Size": 123,
                "CacheControl": cache, "ContentType": "application/json"}

    def test_complete_deterministic_plan_covers_json_retained_and_assembled(self):
        original = deepcopy(self.inventory)
        result = self.make_plan()
        self.inventory["objects"].reverse()
        self.assertEqual(result, self.make_plan())
        rows = {r["key"]: r for r in result["operations"]}
        self.assertEqual(rows["public/data.json"]["action"], "repair")
        self.assertEqual(rows["public/data.json"]["put_object"]["CacheControl"], "public,max-age=300")
        self.assertEqual(rows["public/stock.json"]["action"], "verify")
        retained = rows["public/stock-assets/old-87654321.js"]
        self.assertEqual(retained["action"], "repair")
        self.assertEqual(retained["metadata"], {"CacheControl": "public,max-age=31536000,immutable",
                                               "ContentType": "text/javascript; charset=utf-8"})
        existing = rows["public/index.html"]
        self.assertEqual(existing["read_before_write"]["IfMatch"], '"opaque-2"')
        self.assertEqual(existing["put_object"]["IfMatch"], '"opaque-2"')
        self.assertTrue(existing["preserve_existing_properties"])
        added = rows["public/stock-assets/app-12345678.js"]
        self.assertEqual(added["put_object"]["IfNoneMatch"], "*")
        self.assertEqual(added["local_source"]["size"], len(b"public script"))
        self.assertEqual(len(added["local_source"]["sha256"]), 64)
        self.assertEqual(rows["public/stock.html"]["metadata"]["CacheControl"], "public,max-age=0,must-revalidate")
        self.assertEqual(result["delete_operations"], [])
        self.assertTrue(result["dry_run"])
        self.assertTrue(result["requires_publishers_paused"])
        self.assertEqual(sorted(self.inventory["objects"], key=lambda r: r["Key"]),
                         sorted(original["objects"], key=lambda r: r["Key"]))

    def test_unknown_private_and_manifest_keys_are_never_silently_omitted(self):
        for key in ("raw/private.bin", "public/evidence/live-v1/attempt.json", "public/stock-site-files.json",
                    "public/stock-site-upload-metadata.json", "public/new.html", "public/../secret",
                    "public/stock-assets/a/secret.json", "public/stock-assets/plain.js", "public/stock-assets/../private-12345678.js",
                    "public/stock-assets/unknown-12345678.js"):
            with self.subTest(key=key):
                inventory = deepcopy(self.inventory)
                obj = self.obj("irrelevant"); obj["Key"] = key
                inventory["objects"].append(obj)
                with self.assertRaises(ValueError):
                    self.make_plan(inventory)

    def test_incomplete_duplicate_missing_json_or_missing_preconditions_fail(self):
        for change in (lambda x: x.update(complete=False), lambda x: x.update(prefix=""),
                       lambda x: x["objects"].pop(0), lambda x: x["objects"].append(x["objects"][0]),
                       lambda x: x["objects"][0].pop("ETag"), lambda x: x["objects"][0].update(Size=True)):
            inventory = deepcopy(self.inventory); change(inventory)
            with self.assertRaises(ValueError):
                self.make_plan(inventory)

    def test_altered_manifest_values_or_missing_source_fail_before_plan(self):
        path = self.directory / "stock-site-upload-metadata.json"
        original = json.loads(path.read_text())
        for field, value in (("CacheControl", "public,max-age=86400"), ("ContentType", "text/plain")):
            changed = deepcopy(original); changed["stock.html"][field] = value
            path.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                self.make_plan()
        path.write_text(json.dumps(original))
        (self.directory / "stock.html").unlink()
        with self.assertRaises(ValueError):
            self.make_plan()

    def test_static_manifests_cannot_upload_snapshot_or_unknown_keys(self):
        files_path = self.directory / "stock-site-files.json"
        meta_path = self.directory / "stock-site-upload-metadata.json"
        files = json.loads(files_path.read_text()); meta = json.loads(meta_path.read_text())
        for name in ("stock.json", "stock-site-files.json", "private.json"):
            files_path.write_text(json.dumps(files + [name]))
            meta_path.write_text(json.dumps({**meta, name: {"CacheControl": "public,max-age=15", "ContentType": "application/json"}}))
            with self.assertRaises(ValueError):
                self.make_plan()

    def test_duplicate_json_keys_fail_closed(self):
        path = self.directory / "stock-site-upload-metadata.json"
        path.write_text('{"stock.html": {}, "stock.html": {}}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.make_plan()

    def test_retained_assets_require_release_allowlist_provenance(self):
        with self.assertRaisesRegex(ValueError, "retained release allowlists"):
            planner.plan(self.directory, self.inventory)
        changed = deepcopy(self.retained)
        changed["stock-assets/old-87654321.js"]["CacheControl"] = "public,max-age=86400"
        with self.assertRaises(ValueError):
            planner.plan(self.directory, self.inventory, [changed])


if __name__ == "__main__":
    unittest.main()
