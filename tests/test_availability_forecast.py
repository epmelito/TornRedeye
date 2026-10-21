"""Deterministic synthetic histories; no private evidence or provider access."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from tornredeye import availability_forecast as forecast
from evidence_backup import PREFIXES
from tornredeye.evidence_paths import evidence_keys
from uuid import UUID


START = datetime(2026, 10, 10, tzinfo=timezone.utc)


def history(values, *, country="haw", item_id=1485):
    rows = []
    for index, value in enumerate(values):
        minute, quantity = value[:2]
        at = START + timedelta(minutes=minute)
        source = int((at - timedelta(seconds=1)).timestamp())
        if len(value) > 2:
            source = value[2]
        rows.append({"retrieved_at": at.isoformat(), "source_timestamp": source,
                     "quantity": quantity, "issues": [],
                     "raw_evidence": {"key": evidence_keys(at, UUID(int=index))[0]}})
    return {"country": country, "item_id": item_id, "observations": rows}


def evaluate(data, **kwargs):
    return forecast.evaluate(data, country=data["country"], item_id=data["item_id"], **kwargs)


class AvailabilityTests(unittest.TestCase):
    def test_expanding_mature_labels_and_common_hand_calculated_brier(self):
        report = evaluate(history([(0, 0), (60, 10), (120, 0), (180, 10), (240, 0)]))
        rows = report["forecasts"]
        self.assertEqual([r["training_count"] for r in rows], [0, 1, 2, 3, 4])
        self.assertEqual([r["training_positive_count"] for r in rows], [0, 1, 1, 2, 2])
        self.assertEqual([r["empirical_probability"] for r in rows], [None, 1, .5, 2/3, .5])
        self.assertEqual(rows[0]["forecast_status"], "no_resolved_training_labels")
        self.assertEqual(rows[1]["latest_training_target_utc"], rows[1]["origin_utc"])
        self.assertEqual(rows[-1]["label"]["status"], "beyond_evidence_cutoff")
        self.assertFalse(rows[0]["scored"])
        self.assertFalse(rows[-1]["scored"])
        comparison = report["summary"]["comparison"]
        self.assertEqual(comparison["count"], 3)
        self.assertAlmostEqual(comparison["brier"]["empirical"], (1 + .25 + 4/9)/3)
        self.assertEqual(comparison["brier"]["persistence"], 1)
        self.assertEqual(report["summary"]["labels_resolved_among_eligible_origins"], 4)

    def test_utc_grid_includes_missing_hours_without_inventing_origins(self):
        data = history([(0.5, 1), (180.5, 0)])
        rows = evaluate(data)["forecasts"]
        self.assertEqual([r["origin_utc"] for r in rows], [
            "2026-10-10T01:00:00.000000Z", "2026-10-10T02:00:00.000000Z",
            "2026-10-10T03:00:00.000000Z"])
        self.assertTrue(all(r["origin"]["status"] == "stale" for r in rows))

    def test_cached_receipt_does_not_refresh_source_age(self):
        stamp = int(START.timestamp()) - 1
        report = evaluate(history([(0, 1, stamp), (59, 1, stamp), (120, 0)]))
        self.assertEqual(report["forecasts"][0]["label"]["status"], "stale")
        self.assertEqual(report["forecasts"][1]["origin"]["status"], "stale")
        self.assertEqual(report["observation_counts"]["cached_attempts"], 1)

    def test_freshness_boundary_measured_at_target_not_receipt(self):
        for age, expected in ((300, "eligible"), (301, "stale")):
            with self.subTest(age=age):
                source = int((START + timedelta(hours=1)).timestamp()) - age
                data = history([(0, 0), (59, 10, source), (60, None, None)])
                label = evaluate(data)["forecasts"][0]["label"]
                self.assertEqual(label["status"], expected)
                self.assertEqual(label["observation"]["source_age_seconds"], age)

    def test_missing_attempt_carries_only_a_still_fresh_eligible_value(self):
        data = history([(0, 0), (59, 10), (60, None, None), (120, None, None)])
        rows = evaluate(data)["forecasts"]
        self.assertEqual(rows[0]["label"]["available"], 1)
        self.assertTrue(rows[0]["label"]["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(rows[1]["label"]["status"], "stale")
        self.assertIsNone(rows[1]["brier"])

    def test_future_and_unknown_source_or_invalid_quantity_stay_unresolved(self):
        future = int(START.timestamp()) + 1
        for source, quantity in ((0, 1), (None, 1), (future, 1), (True, 1),
                                 (int(START.timestamp())-1, None),
                                 (int(START.timestamp())-1, True),
                                 (int(START.timestamp())-1, -1),
                                 (int(START.timestamp())-1, 1.0)):
            with self.subTest(source=source, quantity=quantity):
                rows = evaluate(history([(0, quantity, source), (60, None, None)]))["forecasts"]
                self.assertEqual(rows[0]["origin"]["status"], "no_observation")
                self.assertIsNone(rows[0]["empirical_probability"])
                self.assertIsNone(rows[0]["persistence_probability"])

    def test_known_conflict_blocks_even_a_fresh_older_fallback(self):
        source = int((START + timedelta(minutes=59)).timestamp())
        data = history([(0, 1), (57, 1), (59.5, 0, source), (59.75, 3, source),
                        (60, None, None), (120, 1)])
        rows = evaluate(data)["forecasts"]
        self.assertEqual(rows[0]["label"]["status"], "conflicting_source_time")
        self.assertEqual(rows[1]["origin"]["status"], "conflicting_source_time")
        self.assertEqual(rows[2]["origin"]["status"], "eligible")

    def test_later_contradiction_is_audit_only_and_does_not_rewrite_training(self):
        source = int((START + timedelta(minutes=119.5)).timestamp())
        data = history([(0, 0), (60, 10), (119 + 50/60, 0, source),
                        (120, None, None), (120 + 10/60, 7, source), (180, 10), (240, 0)])
        # static history flags cannot disclose a contradiction before its retrieval
        data["observations"][2]["issues"] = ["conflicting_quantities_at_same_source_time"]
        data["observations"][4]["issues"] = ["conflicting_quantities_at_same_source_time"]
        prefix = deepcopy(data)
        prefix["observations"] = prefix["observations"][:4]
        early = evaluate(prefix)["forecasts"][1]
        report = evaluate(data)
        rows = report["forecasts"]
        self.assertEqual(rows[1]["label"], early["label"])
        self.assertEqual(rows[1]["brier"], early["brier"])
        self.assertEqual(rows[1]["empirical_probability"], early["empirical_probability"])
        self.assertTrue(rows[1]["scored"])
        self.assertIn("label", rows[1]["later_contradictions"])
        self.assertEqual(rows[3]["training_count"], 3)
        self.assertEqual(rows[3]["training_positive_count"], 2)
        self.assertEqual(report["summary"]["scored_later_contradicted_label_count"], 1)

    def test_post_target_receipt_with_earlier_source_time_is_not_used(self):
        data = history([(0, 0), (59, 3), (60 + 1/60, 0,
                        int((START + timedelta(minutes=59.5)).timestamp())), (120, 0)])
        first = evaluate(data)["forecasts"][0]
        self.assertEqual(first["label"]["available"], 1)
        self.assertEqual(first["label"]["observation"]["evidence_key"], data["observations"][1]["raw_evidence"]["key"])

    def test_exact_target_receipt_can_resolve_and_train_at_that_origin(self):
        rows = evaluate(history([(0, 0), (60, 8), (120, 0)]))["forecasts"]
        self.assertEqual(rows[0]["label"]["available"], 1)
        self.assertEqual(rows[1]["training_count"], 1)
        self.assertEqual(rows[1]["empirical_probability"], 1)

    def test_unresolved_labels_and_ineligible_origins_do_not_train_as_negatives(self):
        rows = evaluate(history([(0, 1), (60, None, None), (120, 0), (180, 8), (240, 0)]))["forecasts"]
        self.assertEqual(rows[0]["label"]["status"], "stale")
        self.assertEqual(rows[1]["origin"]["status"], "stale")
        self.assertEqual(rows[2]["training_count"], 0)
        self.assertEqual(rows[3]["training_count"], 1)
        self.assertEqual(rows[3]["empirical_probability"], 1)

    def test_regression_does_not_replace_a_fresh_more_recent_source(self):
        source = int((START + timedelta(minutes=56)).timestamp())
        data = history([(0, 0), (57, 9), (59, 0, source), (60, None, None)])
        label = evaluate(data)["forecasts"][0]["label"]
        self.assertEqual(label["available"], 1)
        self.assertEqual(label["observation"]["source_timestamp"], data["observations"][1]["source_timestamp"])

    def test_normalized_attempt_index_extends_known_cutoff_without_inventing_target(self):
        data = history([(0, 0), (59, 9)])
        attempts = [{"key": evidence_keys(START+timedelta(minutes=60), UUID(hex="f"*32))[1],
                     "retrieved_at": (START + timedelta(hours=1)).isoformat(),
                     "document_status": "collection_failed"}]
        report = evaluate(data, attempts=attempts)
        self.assertEqual(report["input_inventory"]["additional_unknown_attempts"], 1)
        self.assertEqual(report["forecasts"][0]["label"]["available"], 1)
        self.assertTrue(report["forecasts"][0]["label"]["observation"]["carried_over_unavailable_attempt"])
        attempts[0]["retrieved_at"] = (START + timedelta(minutes=120)).isoformat()
        attempts[0]["key"] = evidence_keys(START+timedelta(minutes=120), UUID(hex="f"*32))[1]
        self.assertEqual(evaluate(data, attempts=attempts)["forecasts"][0]["label"]["available"], 1)
        self.assertEqual(evaluate(data, attempts=attempts)["forecasts"][1]["label"]["status"], "stale")

    def test_matching_attempt_is_deduplicated_and_mismatch_rejected(self):
        data = history([(0, 0), (60, 3)])
        attempt = {"key": evidence_keys(START, UUID(int=0))[1], "retrieved_at": START.isoformat()}
        self.assertEqual(evaluate(data, attempts=[attempt])["input_inventory"]["additional_unknown_attempts"], 0)
        with self.assertRaises(ValueError):
            evaluate(data, attempts=[attempt, attempt])
        attempt["retrieved_at"] = (START + timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):
            evaluate(data, attempts=[attempt])

    def test_invalid_chronology_and_identity_fail_explicitly(self):
        data = history([(0, 0), (60, 3)])
        for change in (lambda r: r.pop("retrieved_at"),
                       lambda r: r.update(retrieved_at="2026-10-10T00:00:00"),
                       lambda r: r.pop("raw_evidence"),
                       lambda r: r.update(issues="unknown")):
            broken = deepcopy(data)
            change(broken["observations"][0])
            with self.assertRaises(ValueError):
                evaluate(broken)
        for field in ("retrieved_at", "raw_evidence"):
            broken = deepcopy(data)
            broken["observations"][1][field] = broken["observations"][0][field]
            with self.assertRaises(ValueError):
                evaluate(broken)
        data["warnings"] = ["Unplaced evidence prevents all transition comparisons."]
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_country_item_selection_and_cli_results_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"history.json"
            data = history([(0, 0), (60, 9), (120, 0)], country="jap", item_id=206)
            path.write_text(json.dumps(data), encoding="utf-8")
            outputs = []
            for _ in range(2):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(forecast.main([str(path), "--country", "jap", "--item-id", "206"]), 0)
                outputs.append(output.getvalue())
            self.assertEqual(outputs[0], outputs[1])
            result = json.loads(outputs[0])
            self.assertEqual((result["country"], result["item_id"]), ("jap", 206))
            self.assertEqual(len(result["inputs"][0]["sha256"]), 64)
            with redirect_stderr(StringIO()), redirect_stdout(StringIO()):
                self.assertEqual(forecast.main([str(path), "--country", "haw", "--item-id", "1485"]), 2)

    def test_existing_scoped_jsonl_history_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"history.jsonl"
            data = history([(0, 0), (60, 7), (120, 0)])
            rows = [{"country": "haw", "item_id": 1485, "retrieved_at_utc": r["retrieved_at"],
                     "evidence_key": r["raw_evidence"]["key"], "quantity": r["quantity"],
                     "source_timestamp": r["source_timestamp"], "issues": []} for r in data["observations"]]
            rows.append(dict(rows[0], country="can", item_id=206))
            path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(forecast.main([str(path), "--country", "haw", "--item-id", "1485"]), 0)
            self.assertEqual(json.loads(output.getvalue())["input_inventory"]["history_rows"], 3)

    def test_short_history_does_not_fabricate_origins_or_scores(self):
        report = evaluate(history([(0.5, 0), (1, 1)]))
        self.assertEqual(report["forecasts"], [])
        self.assertEqual(report["summary"]["comparison"], {
            "count": 0, "brier": {"empirical": None, "persistence": None}})

    def test_target_parameters_and_scope_types_are_explicit(self):
        for country, item_id in ((" ", 1485), (None, 1485), ("haw", True), ("haw", 0)):
            with self.subTest(country=country, item_id=item_id), self.assertRaises(ValueError):
                forecast.evaluate(history([(0, 1)]), country=country, item_id=item_id)
        data = history([(0, 1)], item_id=True)
        with self.assertRaises(ValueError):
            forecast.evaluate(data, country="haw", item_id=1)


if __name__ == "__main__":
    unittest.main()
