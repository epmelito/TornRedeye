"""Synthetic requested-time replay and availability boundaries; no cloud access."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_availability_forecast import START, history
from tornredeye import availability_forecast as frozen
from tornredeye import requested_availability as requested
from tornredeye.evidence_paths import counterpart, evidence_keys
from uuid import UUID


def utc(minute):
    return (START + timedelta(minutes=minute)).isoformat()


def pairs(*values):
    return [{"origin_utc": utc(origin), "target_utc": utc(target)} for origin, target in values]


def evaluate(data, requests, **kwargs):
    return requested.evaluate(data, requests, country=data["country"], item_id=data["item_id"],
                              maximum_source_age_seconds=kwargs.pop("age", 300), **kwargs)


class RequestedTests(unittest.TestCase):
    def test_arbitrary_requested_times_mature_labels_and_hand_calculated_paired_scores(self):
        data = history([(0, 0), (10, 1), (20, 0), (60, 1), (70, 0), (120, 0)])
        report = evaluate(data, pairs((60, 120), (10, 20), (0, 10), (60, 70), (20, 60)))
        rows = report["forecasts"]
        self.assertEqual([r["training_count"] for r in rows], [0, 0, 1, 2, 2])
        self.assertEqual([r["empirical_probability"] for r in rows], [None, None, 1, .5, .5])
        self.assertEqual([r["forecast_status"] for r in rows],
                         ["no_resolved_training_labels"] * 2 + ["replay_estimate"] * 3)
        self.assertLess(rows[3]["latest_training_target_utc"], rows[3]["origin_utc"])
        summary = report["summary"]
        self.assertEqual((summary["paired_scored"], summary["excluded_from_paired"]), (3, 2))
        self.assertEqual(summary["cold_start_abstentions"], 2)
        self.assertEqual((summary["resolved_positive"], summary["resolved_zero"]), (2, 3))
        self.assertEqual((summary["paired_positive"], summary["paired_zero"]), (1, 2))
        self.assertAlmostEqual(summary["brier"]["empirical"], (0 + .25 + .25)/3)
        self.assertEqual(summary["brier"]["persistence"], 1)
        self.assertEqual(set(report["by_offset_seconds"]), {"600.0", "2400.0", "3600.0"})
        self.assertFalse(report["live_issuance_verified"])
        self.assertEqual(report["evaluation_kind"], "retrospective_retrieval_time_proxy")

    def test_country_item_selection_and_10_and_60_minute_requests_share_behavior(self):
        for country, item_id in (("haw", 1485), ("jap", 206)):
            with self.subTest(country=country):
                data = history([(0, 0), (10, 1), (60, 0), (70, 1), (120, 0)],
                               country=country, item_id=item_id)
                report = evaluate(data, pairs((0, 10), (0, 60), (60, 70), (60, 120)))
                self.assertEqual((report["country"], report["item_id"]), (country, item_id))
                self.assertEqual([r["training_count"] for r in report["forecasts"]], [0, 0, 1, 1])
                self.assertEqual(report["summary"]["paired_scored"], 2)

    def test_cached_targets_do_not_duplicate_training_labels_or_renew_freshness(self):
        source = int((START + timedelta(minutes=10)).timestamp()) - 1
        data = history([(0, 0), (10, 1, source), (11, 1, source),
                        (12, 1, source), (20, 0), (30, 1)])
        report = evaluate(data, pairs((0, 10), (0, 11), (11, 12), (20, 30)))
        rows = report["forecasts"]
        self.assertEqual([r["training_count"] for r in rows], [0, 0, 1, 1])
        self.assertEqual(rows[2]["label"]["status"], "no_post_origin_source_update")
        self.assertEqual(len(report["training_labels"]), 1)
        self.assertEqual(report["observation_counts"]["cached_attempts"], 2)
        self.assertEqual(rows[1]["label"]["observation"]["source_age_seconds"], 61)

    def test_freshness_is_explicit_and_does_not_modify_frozen_selection(self):
        source = int((START + timedelta(minutes=10)).timestamp()) - 240
        data = history([(0, 0), (10, 1, source)])
        self.assertEqual(evaluate(data, pairs((0, 10)), age=300)["forecasts"][0]["label"]["status"], "eligible")
        self.assertEqual(evaluate(data, pairs((0, 10)), age=180)["forecasts"][0]["label"]["status"], "stale")
        self.assertEqual(frozen.MAX_SOURCE_AGE, 300)
        for age in (180, 300, 600):
            with self.subTest(age=age):
                report = evaluate(data, pairs((0, 10)), age=age)
                self.assertEqual(report["maximum_source_age_seconds"], age)
        for age in (180, 300):
            edge = int((START + timedelta(minutes=10)).timestamp()) - age
            data = history([(0, 0), (10, 1, edge)])
            self.assertEqual(evaluate(data, pairs((0, 10)), age=age)["forecasts"][0]["label"]["status"], "eligible")
            data["observations"][1]["source_timestamp"] -= 1
            self.assertEqual(evaluate(data, pairs((0, 10)), age=age)["forecasts"][0]["label"]["status"], "stale")
        old_source = int((START + timedelta(minutes=10)).timestamp()) - 400
        data = history([(0, 0), (10, 1, old_source)])
        self.assertEqual(evaluate(data, pairs((0, 10)), age=600)["forecasts"][0]["label"]["status"], "eligible")

    def test_overlapping_requests_train_in_target_maturity_order_not_origin_order(self):
        data = history([(0, 0), (1, 0), (10, 1), (20, 0), (30, 1), (60, 1)])
        rows = evaluate(data, pairs((0, 60), (1, 10), (10, 20), (20, 30)))["forecasts"]
        self.assertEqual([r["training_count"] for r in rows], [0, 0, 0, 1])
        self.assertIsNone(rows[2]["empirical_probability"])
        self.assertEqual(rows[3]["empirical_probability"], 1)
        self.assertLess(rows[3]["latest_training_target_utc"], rows[3]["origin_utc"])

    def test_equal_time_target_is_not_training_even_when_its_report_is_already_available(self):
        data = history([(0, 0), (10, 1), (20, 0)])
        requests = pairs((0, 10), (10, 20))
        requests.append({"origin_utc": (START + timedelta(minutes=10, microseconds=1)).isoformat(),
                         "target_utc": utc(20)})
        mapping = {r["raw_evidence"]["key"]: r["retrieved_at"] for r in data["observations"]}
        for available in (None, mapping):
            with self.subTest(available=available):
                report = evaluate(data, requests, available_at=available)
                rows = report["forecasts"]
                self.assertEqual([r["training_count"] for r in rows], [0, 0, 1])
                self.assertEqual(rows[1]["origin"]["status"], "eligible")
                self.assertIsNone(rows[1]["empirical_probability"])
                self.assertEqual(rows[2]["empirical_probability"], 1)
                self.assertIn("strictly before", report["training_maturity"])

    def test_downstream_delays_do_not_change_retrieval_times_or_repair_target_labels(self):
        data = history([(0, 0), (10, 1), (20, 0), (30, 1)])
        available = {r["raw_evidence"]["key"]: r["retrieved_at"] for r in data["observations"]}
        late = data["observations"][1]["raw_evidence"]["key"]
        available[late] = utc(11)
        report = evaluate(data, pairs((0, 10), (10, 20), (11, 20), (20, 30)), available_at=available)
        rows = report["forecasts"]
        self.assertEqual(rows[0]["label"]["status"], "stale")
        self.assertEqual(rows[1]["origin"]["status"], "stale")
        selected = rows[2]["origin"]["observation"]
        self.assertEqual(selected["evidence_key"], late)
        self.assertEqual(selected["retrieved_at"], "2026-10-10T00:10:00.000000Z")
        self.assertEqual(selected["available_at_utc"], "2026-10-10T00:11:00.000000Z")
        self.assertEqual(rows[3]["training_count"], 0)
        self.assertEqual(report["evaluation_kind"], "retrospective_supplied_availability_times")
        self.assertFalse(report["live_issuance_verified"])

    def test_post_target_retrieval_is_never_used_even_with_an_earlier_source_time(self):
        source = int((START + timedelta(minutes=10)).timestamp()) - 1
        data = history([(0, 0), (11, 1, source), (20, 0)])
        row, = evaluate(data, pairs((0, 10)))["forecasts"]
        self.assertEqual(row["label"]["status"], "stale")
        self.assertNotIn("available", row["label"])

    def test_future_conflict_flags_are_reconstructed_only_when_available(self):
        source = int((START + timedelta(minutes=10)).timestamp()) - 1
        data = history([(0, 0), (10, 1, source), (11, 0, source), (20, 0), (30, 1)])
        for r in data["observations"][1:3]:
            r["issues"] = ["conflicting_quantities_at_same_source_time"]
        rows = evaluate(data, pairs((0, 10), (0, 11), (20, 30)))["forecasts"]
        self.assertEqual(rows[0]["label"]["available"], 1)
        self.assertEqual(rows[1]["label"]["status"], "conflicting_source_time")
        self.assertEqual(rows[2]["training_count"], 1)
        self.assertEqual(rows[2]["training_positive_count"], 1)
        self.assertEqual(len(rows[0]["later_contradictions"]["label"]), 1)

    def test_conflict_discovery_uses_downstream_availability_not_old_retrieval_time(self):
        source = int((START + timedelta(minutes=10)).timestamp()) - 1
        data = history([(0, 0), (10, 1, source), (11, 0, source), (20, 1)])
        available = {r["raw_evidence"]["key"]: r["retrieved_at"] for r in data["observations"]}
        available[data["observations"][2]["raw_evidence"]["key"]] = utc(30)
        report = evaluate(data, pairs((0, 12), (20, 30)), available_at=available)
        self.assertEqual(report["forecasts"][0]["label"]["available"], 1)
        self.assertEqual(report["contradiction_discoveries"][0]["discovered_at"],
                         "2026-10-10T00:30:00.000000Z")
        self.assertEqual(report["forecasts"][1]["label"]["status"], "stale")
        self.assertTrue(report["forecasts"][1]["label"]["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(report["forecasts"][1]["training_count"], 1)

    def test_simultaneous_delivery_includes_all_reports_and_retains_original_retrievals(self):
        source = int((START + timedelta(minutes=10)).timestamp()) - 1
        data = history([(0, 0), (10, 1, source), (11, 0, source), (20, 1)])
        available = {r["raw_evidence"]["key"]: r["retrieved_at"] for r in data["observations"]}
        for r in data["observations"][1:3]:
            available[r["raw_evidence"]["key"]] = utc(12)
        report = evaluate(data, pairs((0, 11), (0, 12)), available_at=available)
        self.assertEqual(report["forecasts"][0]["label"]["status"], "stale")
        self.assertEqual(report["forecasts"][1]["label"]["status"], "conflicting_source_time")

    def test_invalid_latest_attempt_carries_fresh_stock_explicitly_without_becoming_zero(self):
        for issues, source, quantity in ((["collection_failed"], None, None),
                                         (["target_missing_or_invalid"], None, None),
                                         ([], None, None),
                                         (["invalid_target_fields"], int(START.timestamp()) + 601, 0)):
            with self.subTest(issues=issues, source=source):
                data = history([(0, 0), (10, 1), (11, quantity, source), (20, 0)])
                data["observations"][2]["issues"] = issues
                report = evaluate(data, pairs((0, 11), (11, 20)))
                label = report["forecasts"][0]["label"]
                self.assertEqual(label["status"], "eligible")
                self.assertEqual(label["available"], 1)
                self.assertTrue(label["observation"]["carried_over_unavailable_attempt"])
                self.assertEqual(label["observation"]["source_age_seconds"], 61)
                self.assertEqual(label["observation"]["evidence_key"], data["observations"][1]["raw_evidence"]["key"])
                self.assertEqual(report["forecasts"][1]["origin"]["status"], "eligible")
                self.assertEqual(report["forecasts"][1]["training_count"], 0)
                self.assertEqual(report["summary"]["paired_scored"], 0)
                self.assertIsNone(report["summary"]["brier"]["empirical"])

    def test_failed_attempt_carry_expires_at_the_configured_source_age(self):
        data = history([(0, 0), (10, 1), (11, None, None), (20, 0)])
        data["observations"][2]["issues"] = ["collection_failed"]
        rows = evaluate(data, pairs((0, 11), (0, 14 + 59/60), (0, 15)))["forecasts"]
        self.assertEqual([r["label"]["status"] for r in rows], ["eligible", "eligible", "stale"])
        self.assertEqual([r["label"]["observation"]["source_age_seconds"] for r in rows], [61, 300, 301])
        self.assertTrue(all(r["label"]["observation"]["carried_over_unavailable_attempt"] for r in rows))
        self.assertNotIn("available", rows[-1]["label"])

    def test_carry_preserves_an_observed_zero_and_its_original_identity(self):
        data = history([(0, 1), (10, 0), (11, None, None), (20, 1)])
        label = evaluate(data, pairs((0, 11)))["forecasts"][0]["label"]
        self.assertEqual(label["available"], 0)
        self.assertEqual(label["observation"]["quantity"], 0)
        self.assertTrue(label["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(label["observation"]["evidence_key"], data["observations"][1]["raw_evidence"]["key"])
        self.assertEqual(label["observation"]["source_age_seconds"], 61)

    def test_failed_attempt_cannot_hide_a_known_source_conflict(self):
        source = int((START + timedelta(minutes=10)).timestamp()) - 1
        data = history([(0, 0), (10, 1, source), (11, 0, source), (12, None, None), (20, 1)])
        data["observations"][3]["issues"] = ["collection_failed"]
        rows = evaluate(data, pairs((0, 12), (12, 20)))["forecasts"]
        self.assertEqual(rows[0]["label"]["status"], "conflicting_source_time")
        self.assertNotIn("available", rows[0]["label"])
        self.assertEqual(rows[1]["origin"]["status"], "conflicting_source_time")
        self.assertIsNone(rows[1]["persistence_probability"])

    def test_extra_failure_attempt_index_is_retained_without_manufacturing_stock(self):
        data = history([(0, 0), (10, 1), (20, 0)])
        raw, normalized = evidence_keys(START + timedelta(minutes=11), UUID(int=99))
        attempts = [{"key": normalized, "retrieved_at": utc(11), "document_status": "collection_failed"}]
        report = evaluate(data, pairs((0, 11)), attempts=attempts)
        self.assertEqual(report["input_inventory"]["additional_unknown_attempts"], 1)
        label = report["forecasts"][0]["label"]
        self.assertEqual(label["available"], 1)
        self.assertTrue(label["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(counterpart(normalized), raw)

    def test_matched_attempt_stays_unknown_until_its_raw_evidence_is_available(self):
        data = history([(0, 0), (9, 1), (10, 0), (20, 0)])
        raw = data["observations"][2]["raw_evidence"]["key"]
        normalized = counterpart(raw)
        attempts = [{"key": normalized, "retrieved_at": utc(10)}]
        available = {r["raw_evidence"]["key"]: r["retrieved_at"] for r in data["observations"]}
        available[normalized] = utc(10)
        available[raw] = utc(11)
        report = evaluate(data, pairs((0, 10), (0, 11)), attempts=attempts, available_at=available)
        before, after = report["forecasts"]
        self.assertEqual(before["label"]["available"], 1)
        self.assertTrue(before["label"]["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(before["label"]["observation"]["source_age_seconds"], 61)
        self.assertNotEqual(before["label"]["observation"]["evidence_key"], raw)
        self.assertEqual(after["label"]["available"], 0)
        self.assertEqual(after["label"]["observation"]["evidence_key"], raw)
        available[raw] = utc(10)
        report = evaluate(data, pairs((0, 10)), attempts=attempts, available_at=available)
        self.assertEqual(report["forecasts"][0]["label"]["available"], 0)
        available[normalized] = utc(12)
        report = evaluate(data, pairs((0, 12)), attempts=attempts, available_at=available)
        self.assertEqual(report["forecasts"][0]["label"]["available"], 0)

    def test_evidence_cutoff_and_no_post_origin_update_are_explicit(self):
        source = int(START.timestamp()) - 1
        data = history([(0, 1, source), (1, 1, source)])
        rows = evaluate(data, pairs((0, .5), (0, 2), (2, 3)))["forecasts"]
        self.assertEqual([r["label"]["status"] for r in rows],
                         ["no_post_origin_source_update", "beyond_evidence_cutoff", "beyond_evidence_cutoff"])
        self.assertEqual(rows[2]["origin"]["status"], "beyond_evidence_cutoff")

    def test_requests_and_availability_mapping_are_strictly_validated(self):
        data = history([(0, 0), (10, 1)])
        for requests in ([], {}, [None], pairs((0, 0)), pairs((1, 0)), pairs((0, 10), (0, 10)),
                         [{"origin_utc": "2026-10-10", "target_utc": utc(10)}]):
            with self.subTest(requests=requests), self.assertRaises(ValueError):
                evaluate(data, requests)
        for age in (True, -1, "300", float("nan"), float("inf")):
            with self.subTest(age=age), self.assertRaises(ValueError):
                evaluate(data, pairs((0, 10)), age=age)
        mapping = {r["raw_evidence"]["key"]: r["retrieved_at"] for r in data["observations"]}
        for available in ({}, {**mapping, "unknown": utc(0)},
                          {**mapping, next(iter(mapping)): utc(-1)}, []):
            with self.subTest(available=available), self.assertRaises(ValueError):
                evaluate(data, pairs((0, 10)), available_at=available)
        shifted = [{"origin_utc": "2026-10-10T02:00:00+02:00",
                    "target_utc": "2026-10-10T02:10:00+02:00"}]
        self.assertEqual(evaluate(data, shifted), evaluate(data, pairs((0, 10))))

    def test_existing_scope_identity_and_unplaceable_evidence_failures_are_preserved(self):
        data = history([(0, 0), (10, 1)])
        for mutate in (lambda d: d.update(country="other"),
                       lambda d: d.update(warnings=["unplaced evidence"]),
                       lambda d: d["observations"].append(deepcopy(d["observations"][0]))):
            invalid = deepcopy(data)
            mutate(invalid)
            with self.assertRaises(ValueError):
                requested.evaluate(invalid, pairs((0, 10)), country="haw", item_id=1485,
                                   maximum_source_age_seconds=300)

    def test_cli_is_read_only_deterministic_and_records_input_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = history([(0, 0), (10, 1), (20, 0)])
            (root / "history.json").write_text(json.dumps(data), encoding="utf-8")
            (root / "requests.json").write_text(json.dumps(pairs((0, 10), (10, 20))), encoding="utf-8")
            before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()}
            args = [str(root / "history.json"), str(root / "requests.json"),
                    "--country", "haw", "--item-id", "1485", "--maximum-source-age-seconds", "300"]
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(requested.main(args), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(set(report["inputs"]), {"history", "requests"})
            self.assertEqual(len(report["inputs"]["history"]["sha256"]), 64)
            process = subprocess.run([sys.executable, "-B", "-m", "tornredeye.requested_availability", *args],
                                     cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(json.loads(process.stdout), report)
            self.assertEqual(before, {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.iterdir()})
            (root / "requests.json").write_text("{}", encoding="utf-8")
            with redirect_stdout(StringIO()) as failed, redirect_stderr(StringIO()):
                self.assertEqual(requested.main(args), 2)
            self.assertEqual(failed.getvalue(), "")

    def test_cli_errors_identify_stage_and_reason_without_echoing_private_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = "PRIVATE_EVIDENCE_OR_CREDENTIAL"
            history_file, requests_file = root / "history.json", root / "requests.json"
            args = [str(history_file), str(requests_file), "--country", "haw", "--item-id", "1485",
                    "--maximum-source-age-seconds", "300"]

            def failed(expected, extra=()):
                before = {p.name: p.read_bytes() for p in root.iterdir()}
                with redirect_stdout(StringIO()) as output, redirect_stderr(StringIO()) as error:
                    self.assertEqual(requested.main([*args, *extra]), 2)
                self.assertEqual(output.getvalue(), "")
                self.assertIn(expected, error.getvalue())
                self.assertNotIn(secret, error.getvalue())
                self.assertNotIn(str(root), error.getvalue())
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.iterdir()})

            failed("(history): input file not found")
            history_file.write_text('{"private":"' + secret + '",', encoding="utf-8")
            failed("(history): invalid JSON at line 1, column")
            history_file.write_text(json.dumps(history([(0, 0), (10, 1)])), encoding="utf-8")
            requests_file.write_text(json.dumps(pairs((0, 10), (0, 10))), encoding="utf-8")
            failed("(evaluation): duplicate origin/target pair")
            requests_file.write_text(json.dumps([{"origin_utc": secret, "target_utc": utc(10)}]), encoding="utf-8")
            failed("(evaluation): invalid input structure or timestamp")
            requests_file.write_text(json.dumps(pairs((0, 10))), encoding="utf-8")
            attempts_file = root / "attempts.jsonl"
            attempts_file.write_text('{}\n{"private":"' + secret + '",', encoding="utf-8")
            failed("(attempts line 2): invalid JSON at line 1, column", ("--attempts", str(attempts_file)))
            with patch.object(requested, "_read", side_effect=PermissionError(secret)):
                failed("(history): input file access denied")


if __name__ == "__main__":
    unittest.main()
