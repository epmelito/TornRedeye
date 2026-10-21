"""Synthetic processing clocks and snapshots, never actual live issuances."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from tornredeye import availability_forecast as baseline
from evidence_backup import PREFIXES
from tornredeye.evidence_paths import evidence_keys
from uuid import UUID
from tornredeye.shadow_forecast import OfflineShadowRun


START = datetime(2026, 10, 10, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.value = START

    def at(self, minutes):
        self.value = START + timedelta(minutes=minutes)

    def __call__(self):
        return self.value


def history(values):
    rows = []
    for index, value in enumerate(values):
        minute, quantity = value[:2]
        at = START + timedelta(minutes=minute)
        source = int(at.timestamp()) - 1 if len(value) == 2 else value[2]
        rows.append({"retrieved_at": at.isoformat(), "source_timestamp": source,
                     "quantity": quantity, "issues": [],
                     "evidence_key": evidence_keys(at, UUID(int=index))[0]})
    return {"country": "haw", "item_id": 1485, "observations": rows}


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.run = OfflineShadowRun(clock=self.clock)

    def observe(self, values, now=None):
        if now is not None:
            self.clock.at(now)
        return self.run.observe(history(values))

    def test_chronological_bootstrap_training_and_hand_calculated_scores(self):
        values = []
        forecasts = []
        for minute, quantity in [(0, 0), (60, 8), (120, 0), (180, 8)]:
            values.append((minute, quantity))
            self.observe(values, minute)
            self.run.resolve()
            forecasts.append(self.run.issue())
        self.assertEqual([f["training_count"] for f in forecasts], [0, 1, 2, 3])
        self.assertEqual([f["empirical_probability"] for f in forecasts], [None, 1, .5, 2/3])
        report = self.run.report()
        self.assertEqual(report["summary"]["paired_score_count"], 2)
        self.assertEqual(report["summary"]["brier"], {"empirical": .625, "persistence": 1})
        self.assertEqual(report["summary"]["outcome_status"], {"eligible": 3, "pending": 1})
        self.assertEqual(report["execution"], "offline_accounting_only")
        self.assertFalse(report["live_issuance_verified"])

    def test_frozen_replay_equivalence_for_hourly_immediate_snapshots(self):
        values = [(0, 0), (60, 8), (120, 0), (180, 8), (240, 0)]
        replay = baseline.evaluate(history(values), country="haw", item_id=1485)
        for index, (minute, _) in enumerate(values):
            self.observe(values[:index+1], minute)
            self.run.resolve()
            record = self.run.issue()
            expected = replay["forecasts"][index]
            for name in ("training_count", "training_positive_count", "empirical_probability",
                         "persistence_probability", "forecast_status"):
                self.assertEqual(record[name], expected[name])

    def test_unresolved_outcomes_never_become_zero_training_labels(self):
        self.observe([(0, 1)])
        self.run.issue()
        self.clock.at(60)
        outcome = self.run.resolve()[0]
        self.assertEqual(outcome["label"]["status"], "stale")
        self.assertNotIn("available", outcome["label"])
        self.observe([(0, 1), (60, 0)])
        record = self.run.issue()
        self.assertEqual(record["training_count"], 0)
        self.assertIsNone(self.run.report()["summary"]["brier"]["empirical"])

    def test_future_observations_rejected_before_issuance(self):
        with self.assertRaisesRegex(ValueError, "after capture"):
            self.observe([(0, 1), (60, 0)])
        self.assertEqual(self.run.issue()["origin"]["status"], "no_observation")

    def test_pending_until_target_and_resolved_labels_only_train_after_accounting(self):
        self.observe([(0, 0)])
        self.run.issue()
        self.observe([(0, 0), (59, 8)], 59)
        self.assertEqual(self.run.resolve(), [])
        self.clock.at(60)
        # the matured outcome has not yet been accounted for
        self.assertEqual(self.run.issue()["training_count"], 0)
        self.assertEqual(self.run.report()["summary"]["matured_unaccounted"], 1)
        self.run.resolve()
        self.clock.at(120)
        self.run.resolve()
        record = self.run.issue()
        self.assertEqual(record["training_count"], 1)
        self.assertEqual(record["training"][0]["resolved_at_utc"],
                         "2026-10-10T01:00:00.000000Z")

    def test_late_received_pre_target_evidence_cannot_resolve_target(self):
        self.observe([(0, 0)])
        self.run.issue()
        self.observe([(0, 0), (59, 8)], 61)
        outcome = self.run.resolve()[0]
        self.assertEqual(outcome["label"]["status"], "stale")
        self.assertEqual(self.run.resolve(), [])

    def test_post_target_receipt_with_earlier_source_is_not_a_target_label(self):
        self.observe([(0, 0)])
        self.run.issue()
        stamp = int((START + timedelta(minutes=59.5)).timestamp())
        self.observe([(0, 0), (61, 8, stamp)], 61)
        self.assertEqual(self.run.resolve()[0]["label"]["status"], "stale")

    def test_source_freshness_boundary_and_cached_receipt(self):
        for age, expected in [(300, "eligible"), (301, "stale")]:
            with self.subTest(age=age):
                clock = Clock()
                run = OfflineShadowRun(clock=clock)
                run.observe(history([(0, 0)]))
                run.issue()
                clock.at(59)
                source = int((START + timedelta(minutes=60)).timestamp()) - age
                run.observe(history([(0, 0), (58, 8, source), (59, 8, source)]))
                clock.at(60)
                outcome = run.resolve()[0]
                self.assertEqual(outcome["label"]["status"], expected)
                self.assertEqual(outcome["label"]["observation"]["source_age_seconds"], age)

    def test_known_conflict_blocks_and_later_contradiction_is_audit_only(self):
        source = int((START + timedelta(minutes=59)).timestamp()) - 1
        self.observe([(0, 0)])
        self.run.issue()
        self.observe([(0, 0), (59, 8, source)], 59)
        self.clock.at(60)
        first = self.run.resolve()[0]
        self.observe([(0, 0), (59, 8, source), (61, 0, source)], 61)
        later = self.run.report()
        self.assertEqual(later["records"][0]["outcome"], first)
        self.assertEqual(later["summary"]["later_contradicted_labels"], 1)
        # a known conflict still blocks the current origin, without older fallback
        self.assertEqual(self.run.issue()["origin"]["status"], "conflicting_source_time")

    def test_conflict_known_by_target_stays_unresolved(self):
        self.observe([(0, 0)])
        self.run.issue()
        stamp = int((START + timedelta(minutes=59)).timestamp()) - 1
        self.observe([(0, 0), (59, 8, stamp), (59.5, 0, stamp)], 59.5)
        self.clock.at(60)
        self.assertEqual(self.run.resolve()[0]["label"]["status"], "conflicting_source_time")

    def test_later_snapshot_order_disambiguates_equal_clock_readings(self):
        stamp = int(START.timestamp()) - 120
        self.observe([(-1, 8, stamp)])
        original = self.run.issue()
        self.observe([(-1, 8, stamp), (-.5, 0, stamp)])
        record = self.run.report()["records"][0]
        self.assertEqual(record["forecast"], original)
        self.assertEqual(len(record["later_contradictions"]["origin"]), 1)

    def test_later_contradiction_does_not_revise_paired_scores_or_training(self):
        self.observe([(0, 0)])
        self.run.issue()
        self.observe([(0, 0), (60, 8)], 60)
        self.run.resolve()
        self.run.issue()
        stamp = int((START + timedelta(minutes=119)).timestamp()) - 1
        self.observe([(0, 0), (60, 8), (119, 0, stamp)], 119)
        self.clock.at(120)
        self.run.resolve()
        before = self.run.report()
        self.assertEqual(before["summary"]["brier"], {"empirical": 1, "persistence": 1})
        self.observe([(0, 0), (60, 8), (119, 0, stamp), (121, 7, stamp)], 121)
        after = self.run.report()
        self.assertEqual(after["summary"]["brier"], before["summary"]["brier"])
        self.assertEqual(after["records"][1]["outcome"], before["records"][1]["outcome"])
        self.observe([(0, 0), (60, 8), (119, 0, stamp), (121, 7, stamp), (180, 8)], 180)
        record = self.run.issue()
        self.assertEqual(record["training_count"], 2)
        self.assertEqual(record["training_positive_count"], 1)

    def test_raw_arriving_after_unknown_attempt_is_new_information_only(self):
        self.observe([(0, 0)])
        self.run.issue()
        self.clock.at(59)
        attempt = {"key": evidence_keys(self.clock(), UUID(int=1))[1],
                   "retrieved_at": self.clock().isoformat(), "document_status": "observed"}
        self.run.observe(history([(0, 0)]), attempts=iter([attempt]))
        snapshot = self.run.report()["snapshots"][-1]
        self.assertEqual(snapshot["inventory"]["attempt_rows"], 1)
        data = history([(0, 0), (59, 8)])
        self.clock.at(61)
        self.run.observe(data, attempts=[attempt])
        self.assertEqual(self.run.resolve()[0]["label"]["status"], "stale")
        self.assertEqual(self.run.issue()["origin"]["observation"]["quantity"], 8)

    def test_unavailable_processing_clock_is_not_replaced_with_a_fabricated_time(self):
        for value in (START.replace(tzinfo=None), "2026-10-10", None):
            with self.subTest(value=value):
                run = OfflineShadowRun(clock=lambda: value)
                with self.assertRaisesRegex(ValueError, "aware datetime"):
                    run.issue()

    def test_failed_attempt_bounded_carry_and_no_invented_zero(self):
        self.observe([(0, 0)])
        self.run.issue()
        self.clock.at(60)
        attempts = [{"key": evidence_keys(self.clock(), UUID(hex="f"*32))[1],
                     "retrieved_at": self.clock().isoformat(),
                     "document_status": "collection_failed"}]
        self.run.observe(history([(0, 0), (59, 8)]), attempts=attempts)
        outcome = self.run.resolve()[0]
        self.assertEqual(outcome["label"]["available"], 1)
        self.assertTrue(outcome["label"]["observation"]["carried_over_unavailable_attempt"])

    def test_no_hindsight_time_parameter_duplicate_slot_or_silent_revision(self):
        self.observe([(0, 0)])
        record = self.run.issue()
        with self.assertRaises(TypeError):
            self.run.issue(issued_at=START-timedelta(hours=1))
        with self.assertRaisesRegex(ValueError, "already recorded"):
            self.run.issue()
        self.clock.at(.5)
        self.observe([(0, 0), (.5, 8)])
        with self.assertRaisesRegex(ValueError, "already recorded"):
            self.run.issue()
        self.assertEqual(self.run.report()["records"][0]["forecast"], record)
        self.clock.at(-1)
        with self.assertRaisesRegex(ValueError, "regressed"):
            self.run.issue()

    def test_input_and_returned_record_mutation_cannot_change_stored_evidence(self):
        data = history([(0, 8)])
        snapshot_id = self.run.observe(data)
        self.assertEqual(self.run.observe(deepcopy(data)), snapshot_id)
        record = self.run.issue()
        original = deepcopy(record)
        data["observations"][0]["quantity"] = 0
        record["origin"]["observation"]["quantity"] = 0
        report = self.run.report()
        self.assertEqual(report["records"][0]["forecast"], original)
        report["snapshots"][0]["rows"].clear()
        self.assertTrue(self.run.report()["snapshots"][0]["rows"])
        with self.assertRaisesRegex(ValueError, "removed or changed"):
            self.run.observe(data)

    def test_missing_previous_evidence_and_simultaneous_receipts_are_rejected(self):
        self.observe([(0, 8), (1, 0)], 1)
        with self.assertRaisesRegex(ValueError, "removed or changed"):
            self.observe([(0, 8)])
        with self.assertRaisesRegex(ValueError, "simultaneous"):
            self.run.observe(history([(0, 8), (0, 0)]))

    def test_utc_actual_decision_time_and_fixed_one_hour_target(self):
        self.clock.value = START.astimezone(timezone(timedelta(hours=2))) + timedelta(seconds=5)
        record = self.run.issue()
        self.assertEqual(record["issued_at_utc"], "2026-10-10T00:00:05.000000Z")
        self.assertEqual(record["target_utc"], "2026-10-10T01:00:05.000000Z")
        self.assertEqual(record["utc_hour_slot"], "2026-10-10T00:00:00.000000Z")
        self.assertFalse(record["live_issuance_verified"])

    def test_model_and_evidence_provenance_and_identity_are_deterministic(self):
        other = OfflineShadowRun(clock=self.clock)
        data = history([(0, 8)])
        self.run.observe(data)
        other.observe(data)
        a, b = self.run.issue(), other.issue()
        self.assertEqual(a, b)
        self.assertEqual(len(a["forecast_id"]), 64)
        self.assertEqual(len(a["snapshot_id"]), 64)
        self.assertEqual(a["maximum_source_age_seconds"], 300)
        self.assertEqual(a["origin"]["observation"]["evidence_key"], data["observations"][0]["evidence_key"])

    def test_unknown_initial_state_is_recorded_but_does_not_train(self):
        self.run.issue()
        self.observe([(60, 8)], 60)
        self.run.resolve()
        record = self.run.issue()
        self.assertEqual(record["training_count"], 0)
        self.assertEqual(self.run.report()["summary"]["eligible_origins"], 1)

    def test_unrecorded_slots_are_coverage_gaps_not_invented_forecasts(self):
        self.run.issue()
        self.clock.at(180)
        self.run.issue()
        summary = self.run.report()["summary"]
        self.assertEqual(summary["recorded_slots"], 2)
        self.assertEqual(summary["hour_slots_in_recorded_span"], 4)
        self.assertEqual(summary["unrecorded_hour_slots_in_span"], 2)
        self.assertEqual(summary["paired_score_count"], 0)


if __name__ == "__main__":
    unittest.main()
