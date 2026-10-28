"""Stock snapshot and event publication contracts; fake storage only."""

from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

import dashboard_publisher
import stock_publisher as stock
from tornredeye.evidence_paths import evidence_keys
from tornredeye.normalization import normalize_collection
from yata_collector import normalize

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
CONFIG = {"EVIDENCE_BUCKET": "private-evidence", "DASHBOARD_BUCKET": "hosting"}


def document(at=NOW, quantity=8):
    return {"format": "stock-evidence", "schema_version": 1, "lineage": "live-v1",
            "collection_id": str(UUID(int=1)), "retrieved_at": stock._stamp(at),
            "export_timestamp": int(at.timestamp()) - 1, "validation_status": "complete",
            "acquisition": {"status": "success", "detail": "PRIVATE_METADATA"},
            "raw_evidence": {"key": "PRIVATE_KEY"},
            "countries": {"arg": {"source_timestamp": int(at.timestamp()) - 10,
                "items": [{"item_id": 101, "name": "Example item", "quantity": quantity, "cost": 100}]},
                "can": {"source_timestamp": int(at.timestamp()) - 90,
                "items": [{"item_id": 202, "name": "Another item", "quantity": 0, "cost": 0}]}}}


def error(code):
    result = RuntimeError("PRIVATE_METADATA")
    result.response = {"Error": {"Code": code}}
    return result


class Storage:
    def __init__(self, doc=None):
        doc = doc or document()
        self.key = evidence_keys(stock._time(doc["retrieved_at"]), UUID(int=1))[1]
        body = json.dumps(doc).encode()
        self.objects = {(CONFIG["EVIDENCE_BUCKET"], self.key): body}
        self.metadata = {"sha256": hashlib.sha256(body).hexdigest()}
        self.calls, self.streams = [], []
        self.fail = None

    def get_object(self, **args):
        self.calls.append(("get", args))
        key = args["Bucket"], args["Key"]
        if key not in self.objects:
            raise error("NoSuchKey")
        stream = io.BytesIO(self.objects[key]); self.streams.append(stream)
        return {"Body": stream, "Metadata": self.metadata, "ETag": hashlib.sha256(self.objects[key]).hexdigest()}

    def put_object(self, **args):
        self.calls.append(("put", args))
        if self.fail:
            raise self.fail
        key = args["Bucket"], args["Key"]
        old = self.objects.get(key)
        if (args.get("IfNoneMatch") == "*" and old is not None
                or "IfMatch" in args and (old is None or args["IfMatch"] != hashlib.sha256(old).hexdigest())):
            raise error("PreconditionFailed")
        self.objects[key] = args["Body"]

    def event(self):
        return {"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put",
                 "s3": {"bucket": {"name": CONFIG["EVIDENCE_BUCKET"]}, "object": {"key": self.key}}}]}


class SnapshotTests(unittest.TestCase):
    def test_real_normalized_collection_contract(self):
        raw = json.dumps({"timestamp": int(NOW.timestamp()), "stocks": {
            "arg": {"update": int(NOW.timestamp())-5, "stocks": [{"id": 101, "name": "Example", "quantity": 0, "cost": 100}]},
            "can": {"update": 0, "stocks": [{"id": 202, "name": "Another", "quantity": None, "cost": 0}]}}}).encode()
        doc = json.loads(normalize_collection(normalize(raw, NOW), raw_evidence_id="PRIVATE_KEY"))
        doc.update(lineage="live-v1", collection_id=str(UUID(int=1)))
        s3 = Storage(doc)
        self.assertTrue(stock.publish(s3, CONFIG, s3.key, NOW))
        public = json.loads(s3.objects[CONFIG["DASHBOARD_BUCKET"], stock.KEY])
        self.assertEqual(public["state"], "partial")
        self.assertEqual(public["countries"][0]["items"][0]["quantity"], 0)
        self.assertIsNone(public["countries"][1]["items"][0]["quantity"])
        self.assertIsNone(public["countries"][1]["source_timestamp_utc"])
        self.assertNotIn("PRIVATE", json.dumps(public))

    def test_allowlist_all_countries_zero_prices_and_source_times(self):
        s = stock.build_snapshot(document(), NOW)
        self.assertEqual([c["code"] for c in s["countries"]], ["arg", "can"])
        self.assertEqual(s["countries"][1]["items"][0]["quantity"], 0)
        self.assertEqual(s["countries"][1]["items"][0]["price"], 0)
        self.assertNotEqual(*[c["source_timestamp_utc"] for c in s["countries"]])
        for private in ("PRIVATE", "collection_id", "raw_evidence", "sha256", "acquisition", "source_url"):
            self.assertNotIn(private, json.dumps(s))

    def test_partial_and_absent_items_are_unknown_without_losing_identity(self):
        old = stock.build_snapshot(document(), NOW)
        doc = document(NOW + timedelta(minutes=1)); del doc["countries"]["can"]
        doc["countries"]["arg"]["items"][0]["quantity"] = None
        s = stock.build_snapshot(doc, NOW + timedelta(minutes=1), old)
        self.assertEqual(s["state"], "partial")
        self.assertFalse(s["countries"][1]["reported"])
        row = s["countries"][1]["items"][0]
        self.assertEqual(row["name"], "Another item")
        self.assertIsNone(row["quantity"]); self.assertIsNone(row["price"])
        self.assertFalse(row["reported"])
        self.assertEqual(row["comparison"]["state"], "unavailable")

    def test_changes_cached_conflicts_regressions_and_unknown_times(self):
        old = stock.build_snapshot(document(), NOW)
        at = NOW + timedelta(minutes=1)
        doc = document(at, quantity=0)
        change = stock.build_snapshot(doc, at, old)["countries"][0]["items"][0]["comparison"]
        self.assertEqual(change["quantity_change"], -8)
        self.assertEqual(change["price_change"], 0)
        self.assertEqual(change["source_interval_utc"], [old["countries"][0]["source_timestamp_utc"], stock._stamp(at-timedelta(seconds=10))])
        for shift, qty, expected in ((0, 8, "cached"), (0, 0, "source_conflict"), (-10, 0, "source_regressed")):
            doc["countries"]["arg"]["source_timestamp"] = int(NOW.timestamp()) - 10 + shift
            doc["countries"]["arg"]["items"][0]["quantity"] = qty
            self.assertEqual(stock.build_snapshot(doc, at, old)["countries"][0]["items"][0]["comparison"]["state"], expected)
        for value in (0, None, int(at.timestamp()) + 1):
            doc["countries"]["arg"]["source_timestamp"] = value
            self.assertIsNone(stock.build_snapshot(doc, at, old)["countries"][0]["source_timestamp_utc"])

    def test_conflict_survives_cached_reports_and_blocks_next_comparison(self):
        old = stock.build_snapshot(document(), NOW)
        at = NOW + timedelta(minutes=1)
        doc = document(at, 0)
        doc["countries"]["arg"]["source_timestamp"] = int(NOW.timestamp()) - 10
        conflict = stock.build_snapshot(doc, at, old)
        cached = stock.build_snapshot(doc, at, conflict)
        self.assertEqual(cached["countries"][0]["items"][0]["comparison"]["state"], "source_conflict")
        next_report = stock.build_snapshot(document(at, 5), at, cached)
        self.assertEqual(next_report["countries"][0]["items"][0]["comparison"]["state"], "unavailable")

    def test_matching_caches_retain_original_change_and_evidence_intervals(self):
        first = stock.build_snapshot(document(), NOW)
        at = NOW + timedelta(minutes=1)
        doc = document(at, 0)
        doc["countries"]["arg"]["items"][0]["cost"] = 120
        changed = stock.build_snapshot(doc, at, first)
        original = deepcopy(changed["countries"][0]["items"][0]["comparison"])
        for minute in (2, 3, 4):
            at = NOW + timedelta(minutes=minute)
            doc["retrieved_at"] = stock._stamp(at)
            changed = stock.build_snapshot(doc, at, changed)
            comparison = changed["countries"][0]["items"][0]["comparison"]
            self.assertEqual(comparison, {**original, "state": "cached"})
            self.assertEqual(changed["countries"][0]["items"][0]["quantity"], 0)
        # advancing source evidence starts a fresh comparison, not a cumulative delta
        doc = document(at + timedelta(minutes=1), 3)
        doc["countries"]["arg"]["items"][0]["cost"] = 120
        next_report = stock.build_snapshot(doc, at + timedelta(minutes=1), changed)
        comparison = next_report["countries"][0]["items"][0]["comparison"]
        self.assertEqual(comparison["state"], "compared")
        self.assertEqual(comparison["quantity_change"], 3)
        self.assertEqual(comparison["retrieval_interval_utc"][0], changed["retrieved_at_utc"])

    def test_retained_changes_do_not_cross_missing_conflicting_or_failed_reports(self):
        first = stock.build_snapshot(document(), NOW)
        at = NOW + timedelta(minutes=1)
        doc = document(at, 0)
        changed = stock.build_snapshot(doc, at, first)
        for problem, expected in (("quantity", "cached"), ("cost", "cached"),
                                  ("item", "unavailable"), ("country", "unavailable"),
                                  ("source", "unavailable"), ("conflict", "source_conflict"),
                                  ("regression", "source_regressed"), ("failure", "unavailable")):
            with self.subTest(problem=problem):
                cached_doc = deepcopy(doc)
                cached_doc["retrieved_at"] = stock._stamp(at + timedelta(minutes=1))
                country = cached_doc["countries"]["arg"]
                if problem in ("quantity", "cost"): country["items"][0][problem] = None
                if problem == "item": country["items"] = []
                if problem == "country": del cached_doc["countries"]["arg"]
                if problem == "source": country["source_timestamp"] = 0
                if problem == "conflict": country["items"][0]["cost"] = 110
                if problem == "regression": country["source_timestamp"] -= 1
                if problem == "failure": cached_doc["acquisition"]["status"] = "collection_failed"
                report = stock.build_snapshot(cached_doc, at + timedelta(minutes=1), changed)
                comparison = report["countries"][0]["items"][0]["comparison"]
                self.assertEqual(comparison["state"], expected)
                self.assertTrue(all(v is None for k, v in comparison.items() if k != "state"))
                again = stock.build_snapshot(cached_doc, at + timedelta(minutes=1), report)
                self.assertIsNone(again["countries"][0]["items"][0]["comparison"]["quantity_change"])
                if problem in ("conflict", "regression"):
                    self.assertEqual(again["countries"][0]["items"][0]["comparison"]["state"], expected)
                    advanced = stock.build_snapshot(document(at + timedelta(minutes=2)), at + timedelta(minutes=2), again)
                    self.assertEqual(advanced["countries"][0]["items"][0]["comparison"]["state"], "unavailable")

    def test_price_only_changes_and_unknown_fields_are_retained_without_invention(self):
        for quantity, price in ((8, 120), (None, 120), (0, None)):
            with self.subTest(quantity=quantity, price=price):
                initial = document(quantity=None if quantity is None else 8)
                initial["countries"]["arg"]["items"][0]["cost"] = None if price is None else 100
                first = stock.build_snapshot(initial, NOW)
                at = NOW + timedelta(minutes=1)
                doc = document(at, quantity)
                doc["countries"]["arg"]["items"][0]["cost"] = price
                changed = stock.build_snapshot(doc, at, first)
                doc["retrieved_at"] = stock._stamp(at + timedelta(minutes=1))
                cached = stock.build_snapshot(doc, at + timedelta(minutes=1), changed)
                self.assertEqual(cached["countries"][0]["items"][0]["comparison"],
                                 {**changed["countries"][0]["items"][0]["comparison"], "state": "cached"})

        doc = document(NOW + timedelta(minutes=1))
        unchanged = stock.build_snapshot(doc, NOW + timedelta(minutes=1), stock.build_snapshot(document(), NOW))
        cached = stock.build_snapshot(doc, NOW + timedelta(minutes=1), unchanged)
        self.assertIsNone(cached["countries"][0]["items"][0]["comparison"]["source_interval_utc"])

    def test_failure_or_malformed_export_never_carries_quantities(self):
        old = stock.build_snapshot(document(), NOW)
        for status, acquisition, expected in (("collection_failed", "collection_failed", "collection_failed"), ("malformed", "success", "unavailable")):
            doc = document(NOW+timedelta(minutes=1)); doc.update(validation_status=status)
            doc["acquisition"]["status"] = acquisition
            s = stock.build_snapshot(doc, NOW+timedelta(minutes=1), old)
            self.assertEqual(s["state"], expected)
            self.assertTrue(all(i["quantity"] is None for c in s["countries"] for i in c["items"]))

    def test_malformed_contract_duplicate_identity_and_numeric_types(self):
        for key, value in (("lineage", "old"), ("schema_version", True), ("countries", [])):
            doc = document(); doc[key] = value
            with self.assertRaises(ValueError): stock.build_snapshot(doc, NOW)
        doc = document(); doc["countries"]["arg"]["items"] *= 2
        with self.assertRaises(ValueError): stock.build_snapshot(doc, NOW)
        for value in (True, -1, 2**60, float("nan")):
            doc = document(quantity=value)
            self.assertIsNone(stock.build_snapshot(doc, NOW)["countries"][0]["items"][0]["quantity"])


class PublicationTests(unittest.TestCase):
    def test_cached_change_retention_reuses_only_previous_public_read(self):
        first = stock.build_snapshot(document(), NOW)
        at = NOW + timedelta(minutes=1)
        doc = document(at, 0)
        changed = stock.build_snapshot(doc, at, first)
        doc["retrieved_at"] = stock._stamp(at + timedelta(minutes=1))
        s3 = Storage(doc)
        s3.objects[CONFIG["DASHBOARD_BUCKET"], stock.KEY] = json.dumps(changed).encode()
        self.assertTrue(stock.publish(s3, CONFIG, s3.key, at + timedelta(minutes=1)))
        self.assertEqual([call[0] for call in s3.calls], ["get", "get", "put"])
        public = json.loads(s3.objects[CONFIG["DASHBOARD_BUCKET"], stock.KEY])
        self.assertEqual(public["countries"][0]["items"][0]["comparison"],
                         {**changed["countries"][0]["items"][0]["comparison"], "state": "cached"})

    def test_exact_event_three_calls_conditional_write_and_duplicate_skip(self):
        s3 = Storage()
        self.assertEqual(stock.process_event(s3, CONFIG, s3.event(), NOW), {"updated": 1})
        self.assertEqual([x[0] for x in s3.calls], ["get", "get", "put"])
        args = s3.calls[-1][1]
        self.assertEqual(args["CacheControl"], "public,max-age=15")
        self.assertEqual(args["IfNoneMatch"], "*")
        before = deepcopy(s3.objects)
        self.assertFalse(stock.publish(s3, CONFIG, s3.key, NOW))
        self.assertEqual(before, s3.objects)
        self.assertTrue(all(x.closed for x in s3.streams))

    def test_newer_snapshot_rejects_old_event_and_put_failure_retains_previous(self):
        s3 = Storage(); old = stock.build_snapshot(document(NOW-timedelta(minutes=1)), NOW)
        s3.objects[CONFIG["DASHBOARD_BUCKET"], stock.KEY] = json.dumps(old).encode()
        before = deepcopy(s3.objects); s3.fail = error("PreconditionFailed")
        with self.assertRaises(RuntimeError): stock.publish(s3, CONFIG, s3.key, NOW)
        self.assertEqual(s3.calls[-1][1]["IfMatch"], hashlib.sha256(before[CONFIG["DASHBOARD_BUCKET"], stock.KEY]).hexdigest())
        self.assertEqual(before, s3.objects)
        old["retrieved_at_utc"] = stock._stamp(NOW+timedelta(minutes=1))
        s3.objects[CONFIG["DASHBOARD_BUCKET"], stock.KEY] = json.dumps(old).encode()
        self.assertFalse(stock.publish(s3, CONFIG, s3.key, NOW))

    def test_evidence_integrity_identity_size_budget_and_event_bounds(self):
        for problem in ("digest", "identity", "size", "budget"):
            s3 = Storage()
            if problem == "digest": s3.metadata["sha256"] = "0"*64
            if problem == "identity":
                doc = document(); doc["collection_id"] = str(UUID(int=2)); s3 = Storage(doc)
            if problem == "size": s3.objects[CONFIG["EVIDENCE_BUCKET"], s3.key] = b"x"*(stock.EVIDENCE_BYTES+1)
            def budget():
                if problem == "budget": raise TimeoutError()
            with self.subTest(problem=problem), self.assertRaises((ValueError, TimeoutError)):
                stock.publish(s3, CONFIG, s3.key, NOW, budget)
            self.assertFalse(any(x[0] == "put" for x in s3.calls))
        for change in ("bucket", "raw", "batch"):
            s3 = Storage(); event = s3.event()
            if change == "bucket": event["Records"][0]["s3"]["bucket"]["name"] = "other"
            if change == "raw": event["Records"][0]["s3"]["object"]["key"] = s3.key.replace("attempt.json", "raw.bin")
            if change == "batch": event["Records"] *= 11
            with self.assertRaises(ValueError): stock.process_event(s3, CONFIG, event, NOW)
            self.assertEqual(s3.calls, [])

    def test_handler_uses_only_s3_and_sanitizes_failures(self):
        s3 = Storage(); client = Mock(return_value=s3)
        context = SimpleNamespace(get_remaining_time_in_millis=lambda: 90000)
        with patch.dict("sys.modules", {"boto3": SimpleNamespace(client=client), "botocore.config": SimpleNamespace(Config=Mock())}), patch.dict("os.environ", CONFIG):
            self.assertEqual(dashboard_publisher.lambda_handler(s3.event(), context), {"updated": 1})
            self.assertEqual(client.call_args.args, ("s3",))
            s3.metadata["sha256"] = "PRIVATE_METADATA"
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaisesRegex(RuntimeError, "^stock publication failed$"):
                dashboard_publisher.lambda_handler(s3.event(), context)
            self.assertNotIn("PRIVATE", output.getvalue())

    def test_ambiguous_public_read_denial_only_allows_create_only(self):
        s3 = Storage(); get = s3.get_object
        def read(**args):
            if args["Key"] == stock.KEY: raise error("AccessDenied")
            return get(**args)
        with patch.object(s3, "get_object", side_effect=read):
            self.assertTrue(stock.publish(s3, CONFIG, s3.key, NOW))
            self.assertEqual(s3.calls[-1][1]["IfNoneMatch"], "*")
            before = deepcopy(s3.objects)
            with self.assertRaises(RuntimeError): stock.publish(s3, CONFIG, s3.key, NOW)
            self.assertEqual(before, s3.objects)

    def test_invalid_previous_snapshot_and_output_size_fail_closed(self):
        for body in (b"x"*(stock.MAX_BYTES+1), b'{}', b'not json'):
            s3 = Storage(); s3.objects[CONFIG["DASHBOARD_BUCKET"], stock.KEY] = body
            before = deepcopy(s3.objects)
            with self.assertRaises(ValueError): stock.publish(s3, CONFIG, s3.key, NOW)
            self.assertEqual(before, s3.objects)
        s3 = Storage()
        with patch.object(stock, "MAX_BYTES", 8), self.assertRaises(ValueError):
            stock.publish(s3, CONFIG, s3.key, NOW)
        self.assertFalse(any(x[0] == "put" for x in s3.calls))


def preview_fixture():
    """Synthetic 227-item/11-country preview, independent of private evidence."""
    doc = document(); doc["countries"] = {}
    for index, code in enumerate(("arg", "can", "cay", "chi", "haw", "jap", "mex", "sou", "swi", "uae", "uni")):
        doc["countries"][code] = {"source_timestamp": int(NOW.timestamp())-20-index*8,
            "items": [{"item_id": 1000+index*30+j, "name": f"Example shop item {j+1}",
                       "quantity": 0 if j%4 == 0 else 100+j*35, "cost": 0 if j == 1 else 1000+j*1500}
                      for j in range(21 if index < 7 else 20)]}
    old = stock.build_snapshot(doc, NOW)
    doc["retrieved_at"] = stock._stamp(NOW+timedelta(minutes=1))
    doc["export_timestamp"] += 60
    for country in doc["countries"].values(): country["source_timestamp"] += 60
    doc["countries"]["arg"]["items"][0]["quantity"] = 125
    doc["countries"]["can"]["items"][2]["quantity"] = None
    doc["validation_status"] = "partial"
    return stock.build_snapshot(doc, NOW+timedelta(minutes=1), old)


if __name__ == "__main__":
    unittest.main()
