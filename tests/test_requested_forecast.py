"""Deterministic prospective operations; no cloud or attested live issuance."""

from copy import deepcopy
from datetime import timedelta, timezone
import unittest
from uuid import UUID

from test_availability_forecast import START, history
from test_shadow_forecast import Clock
from tornredeye.evidence_paths import evidence_keys
from tornredeye.requested_forecast import RequestedForecastRun
from tornredeye.shadow_forecast import OfflineShadowRun


def at(minute):
    return START + timedelta(minutes=minute)


class RequestedForecastTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.run = RequestedForecastRun(country="haw", item_id=1485,
                                        maximum_source_age_seconds=300, clock=self.clock)

    def observe(self, values, minute):
        self.clock.at(minute)
        return self.run.observe(history(values))

    def test_country_item_reuse_multiple_targets_and_cold_start_comparator(self):
        for country, item in (("haw", 1485), ("jap", 206)):
            with self.subTest(country=country):
                run = RequestedForecastRun(country=country, item_id=item, maximum_source_age_seconds=300,
                                           clock=self.clock)
                run.observe(history([(0, 8)], country=country, item_id=item))
                a = run.issue(at(10), request_id="ten")
                b = run.issue(at(60).isoformat(), request_id="sixty")
                self.assertEqual((a["country"], a["item_id"]), (country, item))
                self.assertNotEqual(a["forecast_id"], b["forecast_id"])
                self.assertEqual((a["horizon_seconds"], b["horizon_seconds"]), (600, 3600))
                self.assertNotIn("utc_hour_slot", a)
                self.assertEqual(a["forecast_status"], "no_resolved_training_labels")
                self.assertIsNone(a["empirical_probability"])
                self.assertEqual(a["persistence_probability"], 1)
                self.assertEqual(a["execution"], "offline_accounting_only")
                self.assertFalse(a["live_issuance_verified"])

    def test_unknown_origin_is_distinct_from_cold_start_and_never_trains(self):
        a = self.run.issue(at(10), request_id="unknown")
        self.assertEqual(a["origin"]["status"], "no_observation")
        self.assertEqual(a["forecast_status"], "origin_ineligible")
        self.assertIsNone(a["persistence_probability"])
        self.observe([(10, 8)], 10)
        self.run.resolve()
        b = self.run.issue(at(20), request_id="known")
        self.assertEqual(b["training_count"], 0)
        self.assertEqual(b["forecast_status"], "no_resolved_training_labels")

    def test_actual_clock_issuance_utc_and_target_validation(self):
        self.clock.value = at(2).astimezone(timezone(timedelta(hours=2))) + timedelta(seconds=7)
        record = self.run.issue(at(12), request_id="actual")
        self.assertEqual(record["issued_at_utc"], "2026-10-10T00:02:07.000000Z")
        self.assertEqual(record["target_utc"], "2026-10-10T00:12:00.000000Z")
        self.assertEqual(record["horizon_seconds"], 593)
        for target in (at(2), self.clock.value, at(12).replace(tzinfo=None), None, "2026-10-10"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.run.issue(target, request_id="invalid")
        with self.assertRaises(TypeError):
            self.run.issue(at(12), request_id="backdated", issued_at=at(0))
        self.clock.at(1)
        with self.assertRaisesRegex(ValueError, "regressed"):
            self.run.issue(at(12), request_id="regression")

    def test_constructor_and_request_identifiers_validate_before_record_creation(self):
        for age in (True, -1, "300", float("nan"), float("inf")):
            with self.subTest(age=age), self.assertRaises(ValueError):
                RequestedForecastRun(country="haw", item_id=1485, maximum_source_age_seconds=age)
        for country, item in (("", 1485), ("haw", True), ("haw", 0)):
            with self.subTest(country=country, item=item), self.assertRaises(ValueError):
                RequestedForecastRun(country=country, item_id=item, maximum_source_age_seconds=300)
        for identifier in (None, "", " ", 3):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.run.issue(at(10), request_id=identifier)
        self.assertEqual(self.run.report()["records"], [])

    def test_duplicate_and_conflicting_request_ids_cannot_reissue_or_rewrite(self):
        self.observe([(0, 8)], 0)
        original = self.run.issue(at(10), request_id="same-request")
        self.observe([(0, 8), (1, 0)], 1)
        for target in (at(10), at(20)):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "already recorded"):
                self.run.issue(target, request_id="same-request")
        self.assertEqual(self.run.report()["records"][0]["forecast"], original)
        other = self.run.issue(at(10), request_id="another-request")
        self.assertNotEqual(other["forecast_id"], original["forecast_id"])

    def test_accounting_must_complete_before_training_even_at_equal_clock_time(self):
        self.observe([(0, 0)], 0)
        first = self.run.issue(at(10), request_id="first")
        self.observe([(0, 0), (10, 8)], 10)
        before = self.run.issue(at(20), request_id="before-accounting")
        self.assertEqual(before["training_count"], 0)
        self.assertEqual(self.run.report()["summary"]["matured_unaccounted"], 1)
        outcome, = self.run.resolve()
        after = self.run.issue(at(20), request_id="after-accounting")
        self.assertEqual(after["training_count"], 1)
        self.assertEqual(after["empirical_probability"], 1)
        self.assertEqual(after["training"][0]["forecast_id"], first["forecast_id"])
        self.assertEqual(after["training"][0]["resolved_at_utc"], after["issued_at_utc"])
        self.assertLess(outcome["operation_sequence"], after["operation_sequence"])
        self.assertGreater(outcome["operation_sequence"], before["operation_sequence"])
        self.observe([(0, 0), (10, 8), (20, 0)], 20)
        self.run.resolve()
        final = self.run.issue(at(25), request_id="next")
        self.assertEqual(final["training_count"], 2)
        self.assertEqual(final["empirical_probability"], .5)
        summary = self.run.report()["summary"]
        self.assertEqual(summary["paired_scored"], 1)
        self.assertEqual(summary["brier"], {"empirical": 1, "persistence": 1})

    def test_source_deduplication_prevents_repeated_requests_or_caches_inflating_training(self):
        self.observe([(0, 0)], 0)
        self.run.issue(at(10), request_id="a")
        self.run.issue(at(11), request_id="b")
        self.run.issue(at(20), request_id="later-target")
        stamp = int(at(10).timestamp()) - 1
        self.observe([(0, 0), (10, 8, stamp)], 10)
        self.run.resolve()
        self.observe([(0, 0), (10, 8, stamp), (11, 8, stamp)], 11)
        self.run.resolve()
        record = self.run.issue(at(15), request_id="trained")
        self.assertEqual(record["training_count"], 1)
        self.assertEqual(record["training"][0]["source_timestamp"], stamp)
        self.assertEqual(record["origin"]["observation"]["source_age_seconds"], 61)
        self.assertEqual(self.run.report()["summary"]["label_status"]["pending"], 2)

    def test_outcomes_resolve_chronologically_without_backfilling_late_evidence(self):
        self.observe([(0, 0)], 0)
        later = self.run.issue(at(20), request_id="later")
        earlier = self.run.issue(at(10), request_id="earlier")
        self.assertEqual(self.run.resolve(), [])
        self.observe([(0, 0), (9, 8)], 11)
        self.clock.at(21)
        outcomes = self.run.resolve()
        self.assertEqual([o["forecast_id"] for o in outcomes], [earlier["forecast_id"], later["forecast_id"]])
        self.assertEqual([o["label"]["status"] for o in outcomes], ["stale", "stale"])
        self.assertEqual(self.run.resolve(), [])
        self.observe([(0, 0), (9, 8), (19, 8)], 22)
        self.assertEqual(self.run.resolve(), [])
        self.assertEqual(self.run.issue(at(30), request_id="no-training")["training_count"], 0)
        self.assertEqual([r["outcome"] for r in self.run.report()["records"][:2]], list(reversed(outcomes)))

    def test_capture_time_is_not_original_retrieval_time_and_old_history_cannot_seed_training(self):
        self.observe([(0, 8), (9, 8)], 12)
        record = self.run.issue(at(20), request_id="captured-now")
        self.assertEqual(record["origin"]["captured_at_utc"], "2026-10-10T00:12:00.000000Z")
        self.assertEqual(record["origin"]["observation"]["retrieved_at"], "2026-10-10T00:09:00.000000Z")
        self.assertEqual(record["training_count"], 0)
        self.assertTrue(record["snapshot_id"])
        with self.assertRaisesRegex(ValueError, "after capture"):
            self.run.observe(history([(0, 8), (9, 8), (13, 8)]))

    def test_missing_stale_failed_and_unknown_evidence_never_invents_zero(self):
        self.observe([(0, 0)], 0)
        self.run.issue(at(10), request_id="origin-zero")
        self.observe([(0, 0), (9, 8)], 9)
        self.clock.at(10)
        attempt = {"key": evidence_keys(at(10), UUID(int=99))[1], "retrieved_at": at(10).isoformat(),
                   "document_status": "collection_failed"}
        self.run.observe(history([(0, 0), (9, 8)]), attempts=[attempt])
        outcome, = self.run.resolve()
        self.assertEqual(outcome["label"]["available"], 1)
        self.assertTrue(outcome["label"]["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(outcome["label"]["observation"]["source_age_seconds"], 61)
        self.clock.at(15)
        record = self.run.issue(at(20), request_id="stale")
        self.assertEqual(record["origin"]["status"], "stale")
        self.assertIsNone(record["empirical_probability"])
        self.assertIsNone(record["persistence_probability"])

    def test_no_post_origin_update_is_an_unresolved_label_not_an_exception_or_zero(self):
        self.observe([(0, 8)], 0)
        self.run.issue(at(1), request_id="short")
        self.clock.at(1)
        outcome, = self.run.resolve()
        self.assertEqual(outcome["label"]["status"], "no_post_origin_source_update")
        self.assertNotIn("available", outcome["label"])
        self.assertEqual(self.run.issue(at(2), request_id="next")["training_count"], 0)

    def test_delayed_raw_replacement_of_an_unknown_attempt_cannot_repair_a_target(self):
        self.observe([(0, 0)], 0)
        self.run.issue(at(10), request_id="a")
        attempt = {"key": evidence_keys(at(9), UUID(int=1))[1], "retrieved_at": at(9).isoformat(),
                   "document_status": "unavailable_target_evidence"}
        self.clock.at(9)
        self.run.observe(history([(0, 0)]), attempts=[attempt])
        self.clock.at(11)
        self.run.observe(history([(0, 0), (9, 8)]), attempts=[attempt])
        outcome, = self.run.resolve()
        self.assertEqual(outcome["label"]["status"], "stale")
        self.assertNotIn("available", outcome["label"])
        record = self.run.issue(at(20), request_id="new-information")
        self.assertEqual(record["origin"]["observation"]["quantity"], 8)
        self.assertEqual(record["origin"]["captured_at_utc"], "2026-10-10T00:11:00.000000Z")
        self.assertEqual(record["training_count"], 0)

    def test_invalid_target_fields_carry_only_a_still_fresh_valid_report(self):
        self.observe([(0, 0)], 0)
        self.run.issue(at(10), request_id="a")
        data = history([(0, 0), (9, 8), (9.5, 0)])
        data["observations"][-1]["issues"] = ["invalid_target_fields"]
        self.clock.at(9.5)
        self.run.observe(data)
        self.clock.at(10)
        outcome, = self.run.resolve()
        self.assertEqual(outcome["label"]["available"], 1)
        self.assertTrue(outcome["label"]["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(outcome["label"]["observation"]["source_age_seconds"], 61)

    def test_conflict_known_by_target_remains_unresolved_without_older_fallback(self):
        self.observe([(0, 0)], 0)
        self.run.issue(at(10), request_id="a")
        source = int(at(9).timestamp()) - 1
        self.observe([(0, 0), (9, 8, source), (9.5, 0, source)], 9.5)
        self.clock.at(10)
        outcome, = self.run.resolve()
        self.assertEqual(outcome["label"]["status"], "conflicting_source_time")
        self.assertNotIn("available", outcome["label"])
        self.assertEqual(self.run.issue(at(20), request_id="blocked")["training_count"], 0)

    def test_invalid_processing_clocks_are_rejected_without_recording_a_forecast(self):
        for value in (START.replace(tzinfo=None), "2026-10-10", None):
            with self.subTest(value=value):
                run = RequestedForecastRun(country="haw", item_id=1485,
                                           maximum_source_age_seconds=300, clock=lambda: value)
                with self.assertRaisesRegex(ValueError, "aware datetime"):
                    run.issue(at(10), request_id="invalid-clock")

    def test_forecast_identity_and_records_are_deterministic_for_the_same_operations(self):
        other = RequestedForecastRun(country="haw", item_id=1485, maximum_source_age_seconds=300,
                                     clock=self.clock)
        for run in (self.run, other):
            run.observe(history([(0, 8)]))
        a = self.run.issue(at(10), request_id="a")
        b = other.issue(at(10), request_id="a")
        self.assertEqual(a, b)
        self.assertEqual(len(a["forecast_id"]), 64)
        self.assertEqual(len(a["snapshot_id"]), 64)

    def test_freshness_boundary_and_cache_age_do_not_depend_on_capture_time(self):
        for maximum, age, expected in ((180, 180, "eligible"), (180, 181, "stale"),
                                       (300, 300, "eligible"), (300, 301, "stale"), (600, 400, "eligible")):
            with self.subTest(maximum=maximum, age=age):
                clock = Clock()
                run = RequestedForecastRun(country="haw", item_id=1485,
                                           maximum_source_age_seconds=maximum, clock=clock)
                run.observe(history([(0, 0)]))
                run.issue(at(10), request_id="a")
                source = int(at(10).timestamp()) - age
                clock.at(9)
                run.observe(history([(0, 0), (8, 8, source), (9, 8, source)]))
                clock.at(10)
                label = run.resolve()[0]["label"]
                self.assertEqual(label["status"], expected)
                self.assertEqual(label["observation"]["source_age_seconds"], age)

    def test_known_conflict_blocks_and_later_contradictions_do_not_revise_records(self):
        self.observe([(0, 0)], 0)
        self.run.issue(at(10), request_id="a")
        source = int(at(9).timestamp()) - 1
        self.observe([(0, 0), (9, 8, source)], 9)
        self.clock.at(10)
        outcome, = self.run.resolve()
        before = self.run.issue(at(20), request_id="before-conflict")
        data = history([(0, 0), (9, 8, source), (11, 0, source)])
        for row in data["observations"][1:]:
            row["issues"] = ["conflicting_quantities_at_same_source_time"]
        self.clock.at(11)
        self.run.observe(data)
        blocked = self.run.issue(at(20), request_id="known-conflict")
        self.assertEqual(blocked["origin"]["status"], "conflicting_source_time")
        report = self.run.report()
        self.assertEqual(report["records"][0]["outcome"], outcome)
        self.assertEqual(report["records"][1]["forecast"], before)
        self.assertEqual(report["summary"]["later_contradicted_labels"], 1)

    def test_mutations_of_inputs_outputs_and_reports_cannot_rewrite_stored_decisions(self):
        data = history([(0, 8)])
        self.run.observe(data)
        a = self.run.issue(at(10), request_id="a")
        original = deepcopy(a)
        data["observations"][0]["quantity"] = 0
        a["origin"]["observation"]["quantity"] = 0
        report = self.run.report()
        self.assertEqual(report["records"][0]["forecast"], original)
        report["snapshots"][0]["rows"].clear()
        self.assertTrue(self.run.report()["snapshots"][0]["rows"])
        with self.assertRaisesRegex(ValueError, "removed or changed"):
            self.run.observe(data)

    def test_new_core_does_not_change_frozen_hourly_behavior(self):
        frozen = OfflineShadowRun(clock=self.clock)
        frozen.observe(history([(0, 8)]))
        original = frozen.issue()
        self.run.observe(history([(0, 8)]))
        self.run.issue(at(2), request_id="one")
        self.run.issue(at(3), request_id="two")
        self.assertEqual(original["horizon_seconds"], 3600)
        self.assertIn("utc_hour_slot", original)
        with self.assertRaisesRegex(ValueError, "already recorded"):
            frozen.issue()
        self.assertEqual(frozen.report()["records"][0]["forecast"], original)


if __name__ == "__main__":
    unittest.main()
