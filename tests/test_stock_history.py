"""Synthetic local backup receipts; no cloud access or historical payloads."""

from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import evidence_backup as backup
import stock_history as history


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "objects").mkdir()

    def add(self, minute, quantity, *, source=None, status="200", modify=None, raw=None,
            country="haw", item_id=1485, name="Shark Fin"):
        retrieval = NOW + timedelta(minutes=minute)
        key = backup.PREFIXES[0] + f"{len(list((self.root / 'objects').glob('*.json'))):032x}.bin"
        item = {"id": item_id, "name": name, "quantity": quantity, "cost": 3000}
        payload = {"timestamp": int(retrieval.timestamp()) - 30, "stocks": {
            country: {"update": int((retrieval - timedelta(minutes=10)).timestamp()) if source is None else source,
                    "stocks": [item]},
        }}
        if modify:
            modify(payload)
        raw = backup._bytes(payload) if raw is None else raw
        digest = hashlib.sha256(raw).hexdigest()
        metadata = {"source": "YATA", "source-url": "https://yata.yt/api/v1/travel/export/",
                    "retrieved-at": retrieval.isoformat(), "status": "missing",
                    "collection-id": key.rsplit("/", 1)[1][:-4], "sha256": digest}
        if status is not None:
            metadata["http-status"] = status
        inventory = {"key": key, "size": len(raw), "etag": '"synthetic"',
                     "last_modified": retrieval.isoformat()}
        receipt = {"bucket": "synthetic-private-bucket", "key": key, "inventory": inventory,
                   "status": "verified", "size": len(raw), "sha256": digest,
                   "source_content_length": len(raw), "content_type": "application/octet-stream",
                   "etag": inventory["etag"], "last_modified": inventory["last_modified"],
                   "metadata": metadata}
        member = self.root / "objects" / (backup._key_id(key) + ".json")
        member.with_suffix(".bin").write_bytes(raw)
        self.write_receipt(member, receipt)
        return member, receipt

    def write_receipt(self, member, receipt):
        member.write_bytes(backup._bytes({"value": receipt, "sha256": backup._digest(receipt)}))

    def rows(self):
        return history.analyze(self.root, country="haw", item_id=1485)["observations"]

    def test_another_country_and_item_are_selected_by_cli_arguments(self):
        def add_other_stocks(payload):
            record = payload["stocks"]["jap"]
            record["stocks"].append({"id": 1485, "name": "Other item", "quantity": 88, "cost": 10})
            payload["stocks"]["haw"] = {"update": record["update"], "stocks": [
                {"id": 206, "name": "Other country", "quantity": 99, "cost": 20},
            ]}
        self.add(0, 0, country="jap", item_id=206, name="Xanax", modify=add_other_stocks)
        self.add(1, 7, country="jap", item_id=206, name="Xanax", modify=add_other_stocks)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(history.main([
                str(self.root), "--country", "jap", "--item-id", "206",
            ]), 0)
        report = json.loads(output.getvalue())
        self.assertEqual((report["country"], report["item_id"]), ("jap", 206))
        self.assertEqual([r["quantity"] for r in report["observations"]], [0, 7])
        self.assertTrue(all(r["name"] == "Xanax" for r in report["observations"]))
        comparison = report["observations"][1]["comparison"]
        self.assertEqual(comparison["quantity_change"], 7)
        self.assertTrue(comparison["zero_to_positive"])

    def test_target_arguments_are_required_and_validated(self):
        for args in ([], ["--country", "haw"], ["--item-id", "1485"],
                     ["--country", " ", "--item-id", "1485"],
                     ["--country", "haw", "--item-id", "0"],
                     ["--country", "haw", "--item-id", "not-an-id"]):
            with self.subTest(args=args), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    history.main([str(self.root), *args])
                self.assertEqual(raised.exception.code, 2)
        for country, item_id in ((None, 1485), (" ", 1485), ("haw", True), ("haw", -1)):
            with self.subTest(country=country, item_id=item_id), self.assertRaises(ValueError):
                history.analyze(self.root, country=country, item_id=item_id)

    def test_chronology_zero_and_interval_changes(self):
        for minute, quantity in ((4, 4), (1, 10), (3, 0), (0, 0), (2, 7)):
            self.add(minute, quantity)
        rows = self.rows()
        self.assertEqual([r["quantity"] for r in rows], [0, 10, 7, 0, 4])
        self.assertIsNone(rows[0]["comparison"])
        self.assertEqual([r["comparison"]["kind"] for r in rows[1:]],
                         ["increase", "decrease", "decrease", "increase"])
        self.assertEqual([r["comparison"]["zero_to_positive"] for r in rows[1:]],
                         [True, False, False, True])
        self.assertEqual(rows[1]["comparison"]["quantity_change"], 10)
        self.assertEqual(rows[1]["comparison"]["retrieval_interval_utc"],
                         [rows[0]["retrieved_at"], rows[1]["retrieved_at"]])
        self.assertEqual(rows[1]["comparison"]["elapsed_source_seconds"], 60)
        self.assertEqual(rows[1]["elapsed_retrieval_seconds"], 60)
        self.assertEqual(rows[0]["source_age_seconds"], 600)
        self.assertEqual(rows[0]["export_age_seconds"], 30)
        self.assertEqual(rows[0]["raw_evidence"]["bucket"], "synthetic-private-bucket")
        self.assertEqual(rows[0]["name"], "Shark Fin")
        self.assertEqual(rows[0]["cost"], 3000)

    def test_cached_retrievals_remain_visible_without_new_source_changes(self):
        source = int(NOW.timestamp()) - 600
        self.add(0, 0, source=source)
        self.add(20, 0, source=source)
        self.add(30, 5, source=source + 60)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[1]["repeated_source_observation"])
        self.assertIsNone(rows[1]["comparison"])
        self.assertEqual(rows[1]["source_age_seconds"], 1800)
        self.assertEqual(rows[2]["comparison"]["source_update_interval"], [source, source + 60])
        self.assertLess(source + 60, history._time(rows[1]["retrieved_at"]).timestamp())
        self.assertIn("do not bound actual stock events", history.analyze(self.root, country="haw", item_id=1485)["interpretation"])

    def test_missing_http_status_is_not_defaulted_and_breaks_comparison(self):
        self.add(0, 0)
        self.add(1, 99, status=None)
        self.add(2, 5)
        with patch.object(history, "normalize_export", wraps=history.normalize_export) as normalize:
            rows = self.rows()
        self.assertEqual(normalize.call_count, 2)
        self.assertTrue(all(c.kwargs["http_status"] == 200 for c in normalize.call_args_list))
        self.assertEqual([r["quantity"] for r in rows], [0, None, 5])
        self.assertIn("missing_or_invalid_http_status", rows[1]["issues"])
        self.assertIsNone(rows[2]["comparison"])

    def test_invalid_and_http_failure_observations_are_barriers(self):
        self.add(0, 0)
        self.add(1, 9, status="503")
        self.add(2, 8, status="true")
        self.add(3, 7, raw=b"invalid JSON")
        self.add(4, 6, modify=lambda p: p["stocks"].pop("haw"))
        self.add(5, 5, modify=lambda p: p["stocks"]["haw"]["stocks"][0].pop("quantity"))
        self.add(6, 4)
        rows = self.rows()
        self.assertEqual([r["quantity"] for r in rows], [0, None, None, None, None, None, 4])
        self.assertTrue(all(r["comparison"] is None for r in rows))
        self.assertEqual(rows[1]["normalization_status"], "collection_failed")
        self.assertTrue(rows[3]["diagnostics"])

    def test_failed_collection_cannot_be_recovered_from_a_valid_looking_body(self):
        member, receipt = self.add(0, 5)
        receipt["metadata"]["status"] = "collection_failed"
        self.write_receipt(member, receipt)
        row = self.rows()[0]
        self.assertIsNone(row["quantity"])
        self.assertIn("failed_or_unknown_collection_context", row["issues"])

    def test_duplicate_target_does_not_become_a_transition(self):
        self.add(0, 0)
        self.add(1, 10, modify=lambda p: p["stocks"]["haw"]["stocks"].append(
            {"id": 1485, "name": "Shark Fin", "quantity": 20, "cost": 3000}))
        self.add(2, 5)
        rows = self.rows()
        self.assertIsNone(rows[1]["quantity"])
        self.assertIn("duplicate_item_id", [d["code"] for d in rows[1]["diagnostics"]])
        self.assertIsNone(rows[2]["comparison"])

    def test_invalid_target_fields_and_export_times_block_comparisons(self):
        self.add(0, 0)
        self.add(1, 1, modify=lambda p: p["stocks"]["haw"]["stocks"][0].pop("cost"))
        self.add(2, 2)
        self.add(3, 3, modify=lambda p: p.pop("timestamp"))
        self.add(4, 4)
        self.add(5, 5, modify=lambda p: p.update(timestamp=int(NOW.timestamp()) + 99999))
        self.add(6, 6)
        rows = self.rows()
        self.assertEqual([r["quantity"] for r in rows], list(range(7)))
        self.assertTrue(all(r["comparison"] is None for r in rows))
        self.assertIn("invalid_target_fields", rows[1]["issues"])
        self.assertIn("export_time_unknown", rows[3]["issues"])
        self.assertIn("export_time_in_future", rows[5]["issues"])

    def test_unrelated_partial_records_do_not_discard_valid_target(self):
        self.add(0, 0)
        self.add(1, 10, modify=lambda p: p["stocks"].update(bad=None))
        rows = self.rows()
        self.assertEqual(rows[1]["normalization_status"], "partial")
        self.assertEqual(rows[1]["comparison"]["kind"], "increase")
        self.assertTrue(rows[1]["diagnostics"])

    def test_unknown_future_and_regressing_source_times_break_comparisons(self):
        self.add(0, 0)
        self.add(1, 1, source=0)
        self.add(2, 2)
        self.add(3, 3, source=int(NOW.timestamp()) + 99999)
        self.add(4, 4)
        self.add(5, 5, source=int(NOW.timestamp()) - 1000)
        self.add(6, 6)
        rows = self.rows()
        self.assertTrue(all(r["comparison"] is None for r in rows))
        self.assertIn("source_time_unknown", rows[1]["issues"])
        self.assertIn("source_time_in_future", rows[3]["issues"])
        self.assertIn("source_time_regressed", rows[5]["issues"])

    def test_conflicting_source_timestamp_never_looks_like_a_restock(self):
        source = int(NOW.timestamp()) - 600
        self.add(0, 0, source=source)
        self.add(1, 8, source=source)
        self.add(2, 10)
        rows = self.rows()
        self.assertTrue(all(r["comparison"] is None for r in rows))
        self.assertIn("conflicting_quantities_at_same_source_time", rows[0]["issues"])

    def test_simultaneous_retrievals_are_not_arbitrarily_ordered(self):
        self.add(0, 0)
        self.add(0, 5, source=int(NOW.timestamp()) - 500)
        self.add(1, 10)
        rows = self.rows()
        self.assertTrue(all(r["comparison"] is None for r in rows))
        self.assertIn("simultaneous_retrievals_order_unknown", rows[0]["issues"])

    def test_long_gap_reports_elapsed_time_without_claiming_coverage(self):
        self.add(0, 5)
        self.add(600, 2)
        row = self.rows()[1]
        self.assertEqual(row["elapsed_retrieval_seconds"], 36000)
        self.assertEqual(row["comparison"]["kind"], "decrease")
        self.assertIn("coverage between observations is unknown", history.analyze(self.root, country="haw", item_id=1485)["scope"])

    def test_unplaced_context_disables_comparisons_without_losing_other_rows(self):
        self.add(0, 0)
        member, receipt = self.add(1, 99)
        del receipt["metadata"]["retrieved-at"]
        self.write_receipt(member, receipt)
        self.add(2, 5)
        report = history.analyze(self.root, country="haw", item_id=1485)
        self.assertTrue(report["warnings"])
        self.assertEqual([r["quantity"] for r in report["observations"]], [0, 5, None])
        self.assertTrue(all(r["comparison"] is None for r in report["observations"]))

    def test_corrupt_payload_receipt_and_orphan_stay_visible(self):
        member, _ = self.add(0, 1)
        member.with_suffix(".bin").write_bytes(b"changed")
        other, _ = self.add(1, 2)
        other.write_bytes(b"{}")
        (self.root / "objects" / ("a" * 64 + ".bin")).write_bytes(b"orphan")
        report = history.analyze(self.root, country="haw", item_id=1485)
        self.assertEqual(len(report["observations"]), 3)
        self.assertTrue(report["warnings"])
        self.assertTrue(all(r["quantity"] is None for r in report["observations"]))

    def test_deterministic_read_only_report_and_cli(self):
        self.add(0, 0)
        self.add(1, 5)
        def snapshot():
            return {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in (self.root / "objects").iterdir()}
        before = snapshot()
        with patch.object(backup, "_write", side_effect=AssertionError("no writes")):
            first = history.analyze(self.root, country="haw", item_id=1485)
            self.assertEqual(first, history.analyze(self.root, country="haw", item_id=1485))
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(history.main([str(self.root), "--country", "haw", "--item-id", "1485"]), 0)
        self.assertEqual(json.loads(output.getvalue()), first)
        process = subprocess.run([sys.executable, "-B", "stock_history.py", str(self.root), "--country", "haw", "--item-id", "1485"],
                                 cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout), first)
        self.assertEqual(before, snapshot())

    def test_missing_input_fails_with_no_report(self):
        output, errors = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(history.main([str(self.root), "--country", "haw", "--item-id", "1485"]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Cannot read local", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
