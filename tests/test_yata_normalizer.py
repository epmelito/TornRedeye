"""Synthetic offline all-stock contracts; no API or storage clients."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import unittest

from yata_collector import normalize
from yata_normalizer import normalize_export


NOW = datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
PAYLOAD = {
    "timestamp": int(NOW.timestamp()) - 60,
    "stocks": {
        "jap": {"update": int(NOW.timestamp()) - 300, "stocks": [
            {"id": 206, "name": "Xanax", "quantity": 0, "cost": 800000},
            {"id": 205, "name": "Other item", "quantity": 10, "cost": 0},
        ]},
        "can": {"update": 0, "stocks": []},
        "new-country": {"update": int(NOW.timestamp()) + 10, "stocks": [
            {"id": 206, "name": " Unchanged name \u2603 ", "quantity": 4, "cost": 8},
        ]},
    },
}


def encode(payload):
    return json.dumps(payload).encode()


def run(payload=PAYLOAD, **kwargs):
    return json.loads(normalize_export(
        encode(payload), kwargs.pop("retrieved_at", NOW),
        raw_evidence_id="synthetic/raw/export.bin",
        http_status=kwargs.pop("http_status", 200), **kwargs,
    ))


class NormalizeExportTests(unittest.TestCase):
    def test_all_countries_items_and_identity_are_preserved(self):
        result = run()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["diagnostics"], [])
        self.assertEqual(set(result["countries"]), set(PAYLOAD["stocks"]))
        for country, record in PAYLOAD["stocks"].items():
            normalized = result["countries"][country]
            self.assertEqual(normalized["source_timestamp"], record["update"])
            self.assertEqual(normalized["items"], [
                {"item_id": item["id"], **{k: item[k] for k in ("name", "quantity", "cost")}}
                for item in sorted(record["stocks"], key=lambda item: item["id"])
            ])
        self.assertEqual(result["export_timestamp"], PAYLOAD["timestamp"])
        self.assertEqual(result["raw_evidence"], {
            "identity": "synthetic/raw/export.bin",
            "sha256": hashlib.sha256(encode(PAYLOAD)).hexdigest(),
            "size_bytes": len(encode(PAYLOAD)),
        })

    def test_deterministic_compact_bytes_and_no_input_mutation(self):
        raw = encode(PAYLOAD)
        first = normalize_export(raw, NOW, raw_evidence_id="raw-1", http_status=200)
        self.assertEqual(first, normalize_export(raw, NOW, raw_evidence_id="raw-1", http_status=200))
        self.assertEqual(first, json.dumps(json.loads(first), sort_keys=True,
                                         separators=(",", ":"), allow_nan=False).encode())
        self.assertEqual(raw, encode(PAYLOAD))
        changed = normalize_export(raw + b"\n", NOW, raw_evidence_id="raw-2", http_status=200)
        self.assertNotEqual(json.loads(first)["raw_evidence"], json.loads(changed)["raw_evidence"])
        self.assertEqual(json.loads(first)["countries"], json.loads(changed)["countries"])

    def test_country_and_item_ordering_is_canonical(self):
        payload = deepcopy(PAYLOAD)
        payload["stocks"] = dict(reversed(list(payload["stocks"].items())))
        payload["stocks"]["jap"]["stocks"].reverse()
        self.assertEqual(run(payload)["countries"], run()["countries"])
        self.assertEqual(list(run()["countries"]), sorted(PAYLOAD["stocks"]))

    def test_cached_unknown_and_future_times_are_not_rewritten(self):
        first = run()
        later = run(retrieved_at=NOW + timedelta(hours=2))
        self.assertEqual(first["countries"], later["countries"])
        self.assertEqual(first["export_timestamp"], later["export_timestamp"])
        self.assertNotEqual(first["retrieved_at"], later["retrieved_at"])
        self.assertEqual(first["countries"]["can"]["source_timestamp"], 0)
        self.assertGreater(first["countries"]["new-country"]["source_timestamp"], NOW.timestamp())
        self.assertEqual(first["raw_evidence"], later["raw_evidence"])

    def test_retrieval_time_converts_to_utc(self):
        self.assertEqual(run(retrieved_at=NOW.astimezone(timezone(timedelta(hours=2)))), run())
        self.assertEqual(run()["retrieved_at"], "2026-10-18T12:00:00.000000Z")

    def test_invalid_caller_context_raises(self):
        for overrides in ({"retrieved_at": NOW.replace(tzinfo=None)},
                          {"retrieved_at": None}, {"raw_response": "{}"},
                          {"raw_evidence_id": " "}, {"raw_evidence_id": None},
                          {"http_status": True}, {"http_status": 600}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                normalize_export(**dict(raw_response=encode(PAYLOAD), retrieved_at=NOW,
                                        raw_evidence_id="raw-1", http_status=200) | overrides)

    def test_bad_fields_are_null_not_zero_and_other_items_survive(self):
        for field, invalid_values in {
            "quantity": [None, False, "0", -1, 1.5],
            "cost": [None, True, "0", -1, 1.5],
            "name": [None, False, "", "   ", 2],
        }.items():
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    payload = deepcopy(PAYLOAD)
                    payload["stocks"]["jap"]["stocks"][0][field] = value
                    result = run(payload)
                    self.assertEqual(result["status"], "partial")
                    self.assertIsNone(result["countries"]["jap"]["items"][1][field])
                    self.assertEqual(len(result["countries"]["jap"]["items"]), 2)
                    self.assertEqual(result["countries"]["new-country"], run()["countries"]["new-country"])
                    self.assertEqual(result["diagnostics"], [{
                        "path": ["stocks", "jap", "stocks", 0, field], "code": "invalid",
                    }])

    def test_missing_fields_have_distinct_diagnostics(self):
        for field in ("name", "quantity", "cost"):
            with self.subTest(field=field):
                payload = deepcopy(PAYLOAD)
                del payload["stocks"]["jap"]["stocks"][0][field]
                result = run(payload)
                self.assertIsNone(result["countries"]["jap"]["items"][1][field])
                self.assertEqual(result["diagnostics"][0]["code"], "missing")
        self.assertEqual(run()["countries"]["jap"]["items"][1]["quantity"], 0)

    def test_missing_timestamp_and_item_list_retain_independent_evidence(self):
        payload = deepcopy(PAYLOAD)
        del payload["timestamp"]
        del payload["stocks"]["jap"]["update"]
        del payload["stocks"]["can"]["stocks"]
        result = run(payload)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["export_timestamp"])
        self.assertIsNone(result["countries"]["jap"]["source_timestamp"])
        self.assertEqual(result["countries"]["jap"]["items"], run()["countries"]["jap"]["items"])
        self.assertEqual(result["countries"]["can"]["items"], [])
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["missing"] * 3)

    def test_invalid_timestamps_retain_stock_and_other_timestamps(self):
        for field in ("timestamp", "update"):
            for value in (None, True, "0", -1, 1.5):
                with self.subTest(field=field, value=value):
                    payload = deepcopy(PAYLOAD)
                    parent = payload if field == "timestamp" else payload["stocks"]["jap"]
                    parent[field] = value
                    result = run(payload)
                    self.assertEqual(result["status"], "partial")
                    self.assertEqual(result["countries"]["jap"]["items"], run()["countries"]["jap"]["items"])
                    self.assertEqual(result["export_timestamp"], None if field == "timestamp" else PAYLOAD["timestamp"])
                    self.assertEqual(result["countries"]["jap"]["source_timestamp"],
                                     None if field == "update" else PAYLOAD["stocks"]["jap"]["update"])
        self.assertEqual(run(dict(PAYLOAD, timestamp=0))["export_timestamp"], 0)

    def test_malformed_records_do_not_discard_valid_neighbors(self):
        payload = deepcopy(PAYLOAD)
        payload["stocks"].update(bad=None, **{" ": {}})
        payload["stocks"]["can"] = {"update": 0, "stocks": {}}
        items = payload["stocks"]["jap"]["stocks"]
        items.extend([None, {}, {"id": False}, {"id": "206"}, {"id": 0}])
        result = run(payload)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["countries"]["jap"], run()["countries"]["jap"])
        self.assertEqual(result["countries"]["can"]["items"], [])
        self.assertNotIn("bad", result["countries"])
        self.assertEqual(len(result["diagnostics"]), 8)

    def test_duplicate_ids_are_ambiguous_only_within_one_country(self):
        payload = deepcopy(PAYLOAD)
        payload["stocks"]["jap"]["stocks"].append({"id": 206, "quantity": 9})
        result = run(payload)
        self.assertEqual([i["item_id"] for i in result["countries"]["jap"]["items"]], [205])
        self.assertEqual(result["countries"]["new-country"], run()["countries"]["new-country"])
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["duplicate_item_id"] * 2)

    def test_duplicate_json_keys_do_not_silently_select_a_value(self):
        raw = b'{"timestamp":1,"timestamp":2,"stocks":{"jap":{"update":0,"stocks":[{"id":206,"quantity":0,"quantity":5,"cost":0,"name":"X"}]},"can":{},"can":{}}}'
        result = json.loads(normalize_export(raw, NOW, raw_evidence_id="raw-1", http_status=200))
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["export_timestamp"])
        self.assertNotIn("can", result["countries"])
        self.assertIsNone(result["countries"]["jap"]["items"][0]["quantity"])
        self.assertEqual([d["code"] for d in result["diagnostics"]], ["duplicate"] * 3)

    def test_unreadable_exports_keep_raw_identity_without_observations(self):
        for raw in (b"", b"not json", b"\xff", b"null", b"[]", b'{"timestamp":NaN}',
                    b'{"stocks":{', b'{"stocks":[]}'):
            with self.subTest(raw=raw):
                result = json.loads(normalize_export(raw, NOW, raw_evidence_id="raw-1", http_status=200))
                self.assertEqual(result["status"], "malformed")
                self.assertEqual(result["countries"], {})
                self.assertTrue(result["diagnostics"])
                self.assertEqual(result["raw_evidence"]["sha256"], hashlib.sha256(raw).hexdigest())

    def test_empty_exports_are_not_zero_stock(self):
        result = run({"timestamp": 1, "stocks": {}})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["countries"], {})
        self.assertEqual(run()["countries"]["can"]["items"], [])

    def test_omitted_http_status_cannot_produce_stock(self):
        with self.assertRaisesRegex(TypeError, "http_status"):
            normalize_export(encode(PAYLOAD), NOW, raw_evidence_id="raw-1")

    def test_explicit_valid_http_statuses_preserve_existing_behavior(self):
        for status in (100, 199, 200, 201, 204, 299, 300, 404, 503, 599):
            with self.subTest(status=status):
                result = run(http_status=status)
                self.assertEqual(result["http_status"], status)
                if 200 <= status < 300:
                    self.assertEqual(result["status"], "complete")
                    self.assertEqual(result["countries"], run()["countries"])
                else:
                    self.assertEqual(result["status"], "collection_failed")
                    self.assertEqual(result["countries"], {})

    def test_http_failure_never_becomes_stock(self):
        result = run(http_status=503)
        self.assertEqual(result["status"], "collection_failed")
        self.assertEqual(result["countries"], {})
        self.assertIsNone(result["export_timestamp"])
        self.assertEqual(result["diagnostics"], [{"path": [], "code": "http_failure"}])

    def test_legacy_japan_interpretation_is_unchanged(self):
        legacy = normalize(encode(PAYLOAD), NOW)
        japan = run()["countries"]["jap"]
        self.assertEqual(legacy.status, "observed")
        self.assertEqual(japan["source_timestamp"], legacy.source_timestamp)
        self.assertEqual(japan["items"][1], dict(item_id=legacy.observation.item_id,
                         name=legacy.observation.name, quantity=legacy.observation.quantity,
                         cost=legacy.observation.cost))
        payload = deepcopy(PAYLOAD)
        payload["stocks"]["jap"]["stocks"].append(None)
        self.assertEqual(normalize(encode(payload), NOW).status, "malformed")
        self.assertEqual(run(payload)["status"], "partial")


if __name__ == "__main__":
    unittest.main()
