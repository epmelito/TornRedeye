"""The static React stock build must coexist with the existing operations routes."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from tools import package_stock_frontend as stock


class StockPackagingTests(unittest.TestCase):
    def test_stock_overlay_keeps_operations_and_excludes_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            build = root / "frontend" / "dist"
            (build / "stock-assets").mkdir(parents=True)
            (build / "stock.html").write_text('<script type="module" src="./stock-assets/app.js"></script>')
            (build / "stock-assets" / "app.js").write_text('fetch("stock.json")')
            (build / "stock-assets" / "app.css").write_text("body{color:white}")
            (build / "stock.json").write_text("PRIVATE_EVIDENCE")
            fonts = root / "frontend" / "src" / "fonts"
            fonts.mkdir(parents=True)
            (fonts / "OFL.txt").write_text("Font license")
            dashboard = root / "dashboard"
            dashboard.mkdir()
            for name in stock.OPERATIONS:
                (dashboard / name).write_bytes(("operations: " + name).encode())
            (dashboard / "stock.html").write_text("legacy stock page")
            with patch.object(stock, "ROOT", root):
                result = stock.package(root / "output")
            for name in stock.OPERATIONS:
                self.assertEqual((result / name).read_bytes(), (dashboard / name).read_bytes())
            self.assertNotEqual((result / "stock.html").read_text(), "legacy stock page")
            self.assertFalse((result / "stock.json").exists())
            self.assertFalse((result / "stock.js").exists())
            self.assertIn("./stock-assets/", (result / "stock.html").read_text())
            self.assertTrue((result / "stock-assets" / "FONT-LICENSE.txt").is_file())
            files = json.loads((result / "stock-site-files.json").read_text())
            metadata = json.loads((result / "stock-site-upload-metadata.json").read_text())
            self.assertEqual(set(metadata), set(files))
            for name in stock.OPERATIONS + ("stock-assets/FONT-LICENSE.txt",):
                self.assertEqual(metadata[name]["CacheControl"], "public,max-age=300")
            self.assertEqual(metadata["stock.html"]["CacheControl"], "public,max-age=0,must-revalidate")
            self.assertEqual(metadata["stock-assets/app.js"]["CacheControl"], "public,max-age=31536000,immutable")
            self.assertEqual(metadata["stock-assets/app.css"]["ContentType"], "text/css; charset=utf-8")
            self.assertNotIn("stock.json", metadata)
            self.assertNotIn("stock-site-upload-metadata.json", files)

    def test_missing_build_fails_without_inventing_a_page(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(stock, "ROOT", Path(temp)):
                with self.assertRaises(ValueError):
                    stock.package(Path(temp) / "output")
            self.assertFalse((Path(temp) / "output").exists())
