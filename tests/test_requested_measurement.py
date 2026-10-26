"""Prospective storage contracts with fake S3; no live performance attestation."""

from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from test_aws_shadow_forecast import Clock, S3, START
from tornredeye.evidence_paths import evidence_keys, validate_backup_key
from tornredeye.requested_measurement import RequestedMeasurement, planned_request_id
from tornredeye.shadow_forecast import _bytes
from tornredeye.stock_history import _utc


class Storage(S3):
    def list_objects_v2(self, **kwargs):
        page = super().list_objects_v2(**kwargs)
        for member in page["Contents"]:
            member["Size"] = len(self.objects[member["Key"]]["data"])
        return page


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.s3 = Storage(self.clock)

    def adapter(self, country="haw", item_id=1485, **kwargs):
        return RequestedMeasurement(self.s3, "private-evidence", epoch="requested-test",
                                    country=country, item_id=item_id,
                                    maximum_source_age_seconds=kwargs.pop("age", 300),
                                    clock=self.clock, **kwargs)

    def raw(self, minute, quantity, *, country="haw", item_id=1485, source=None,
            stored=None, identifier=None, failed=False):
        retrieved = START + timedelta(minutes=minute)
        source = int(retrieved.timestamp()) - 1 if source is None else source
        key = evidence_keys(retrieved, UUID(int=identifier if identifier is not None else int(minute * 1000)))[0]
        body = _bytes({"timestamp": source, "stocks": {country: {
            "update": source, "stocks": [{"id": item_id, "name": "item", "quantity": quantity, "cost": 1}]}}})
        before = self.clock.value
        self.clock.at(minute if stored is None else stored)
        self.s3.put_object(Bucket="private-evidence", Key=key, Body=body, IfNoneMatch="*",
                           Metadata={"source": "YATA", "retrieved-at": retrieved.isoformat(),
                            "sha256": hashlib.sha256(body).hexdigest(),
                            "acquisition-status": "collection_failed" if failed else "success",
                            "http-status": "502" if failed else "200", "lineage": "live-v1",
                            "source-url": "https://yata.yt/api/v1/travel/export/",
                            "collection-id": key.split("/")[-2]})
        self.clock.value = before
        return key

    def issue(self, minute=2.5, target=12.5, request="ten", **kwargs):
        self.clock.at(minute)
        return self.adapter(**kwargs).issue(START + timedelta(minutes=target), request_id=request)

    def read(self, request, kind="forecast", **kwargs):
        return json.loads(self.s3.objects[self.adapter(**kwargs).key(request, kind)]["data"])

    def test_actual_issuance_multiple_horizons_cold_start_and_no_mutable_state(self):
        self.raw(2, 0)
        a = self.issue()
        b = self.issue(target=62.5, request="sixty")
        self.assertEqual([a["horizon_seconds"], b["horizon_seconds"]], [600, 3600])
        self.assertEqual(a["forecast_status"], "no_resolved_training_labels")
        self.assertIsNone(a["empirical_probability"])
        self.assertEqual(a["persistence_probability"], 0)
        self.assertEqual(a["issued_at_utc"], "2026-10-10T00:02:30.000000Z")
        self.clock.at(3)
        report = self.adapter().report()
        self.assertEqual(sum(g["pending"] for g in report["horizons"].values()), 2)
        self.assertTrue(all(g["brier"]["empirical"] is None for g in report["horizons"].values()))
        self.assertTrue(all("forecast-state/" not in p["Key"] for p in self.s3.puts))
        self.assertEqual(len([k for k in self.s3.objects if "/requests/" in k]), 4)

    def test_duplicate_restart_returns_exact_bytes_and_conflict_rejects(self):
        self.raw(2, 0)
        original = self.issue()
        saved = deepcopy(self.s3.objects)
        again = self.issue(minute=4)
        self.assertEqual(original, again)
        self.assertEqual(saved, self.s3.objects)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            self.issue(minute=4, target=22.5)
        self.assertEqual(saved, self.s3.objects)
        with self.assertRaisesRegex(ValueError, "mixed freshness"):
            self.issue(minute=4, age=180)

    def test_lost_forecast_write_ack_recovers_without_reissuing(self):
        self.raw(2, 0)
        key = self.adapter().key("ten", "forecast")
        self.s3.fail = (key, "after")
        with self.assertRaises(TimeoutError):
            self.issue()
        original = self.s3.objects[key]["data"]
        recovered = self.issue(minute=3)
        self.assertEqual(_bytes(recovered), original)
        self.clock.at(4)
        self.assertTrue(self.adapter().report()["records"][0]["issuance_verified"])

    def test_missing_write_cannot_be_backfilled_after_target(self):
        self.raw(2, 0)
        key = self.adapter().key("ten", "forecast")
        self.s3.fail = (key, "before")
        with self.assertRaises(TimeoutError):
            self.issue()
        with self.assertRaisesRegex(ValueError, "follow actual issuance"):
            self.issue(minute=13)
        self.assertNotIn(key, self.s3.objects)

    def test_ack_crash_recovery_requires_pre_target_readback_proof(self):
        for position, expected in (("after", True), ("before", False)):
            with self.subTest(position=position):
                self.setUp()
                self.raw(2, 0)
                self.s3.fail = (self.adapter().key("ten", "ack"), position)
                with self.assertRaises(TimeoutError):
                    self.issue()
                self.issue(minute=13)
                self.clock.at(14)
                self.assertEqual(self.adapter().report()["records"][0]["issuance_verified"], expected)

    def test_late_ack_persistence_is_excluded_even_if_payload_claims_verified(self):
        self.raw(2, 0)
        put = self.s3.put_object
        def delay(**args):
            if args["Key"].endswith("ack.json"):
                self.clock.at(3.1)
            return put(**args)
        with patch.object(self.s3, "put_object", side_effect=delay):
            self.issue(target=3)
        self.clock.at(4)
        self.assertFalse(self.adapter().report()["records"][0]["issuance_verified"])

    def test_accounting_restart_training_and_matched_always_empty(self):
        self.raw(2, 0)
        self.issue()
        self.raw(12, 8)
        self.clock.at(13)
        outcomes = self.adapter().resolve()
        self.assertEqual(outcomes[0]["label"]["available"], 1)
        next_forecast = self.issue(minute=13.1, target=23.1, request="second")
        self.assertEqual(next_forecast["training_count"], 1)
        self.assertEqual(next_forecast["empirical_probability"], 1)
        self.raw(23, 0)
        self.clock.at(24)
        self.adapter().resolve()
        self.clock.at(25)
        report = self.adapter().report()
        group = next(g for g in report["horizons"].values() if g["paired_count"])
        self.assertEqual(group["brier"], {"empirical": 1, "persistence": 1, "always_empty": 0})
        self.assertEqual((group["positive"], group["zero"]), (0, 1))
        before = deepcopy(self.s3.objects)
        self.adapter().resolve()
        self.assertEqual(before, self.s3.objects)

    def test_same_second_boundary_is_explicit_and_never_imports_future_accounting(self):
        self.raw(2, 0)
        self.issue()
        self.raw(12, 8)
        self.clock.at(13)
        self.adapter().resolve()
        self.assertEqual(self.issue(minute=13, target=23, request="boundary")["training_count"], 0)
        self.clock.at(13 + 1/60)
        record = self.adapter().issue(START + timedelta(minutes=24), request_id="after-boundary")
        self.assertEqual(record["training_count"], 1)
        self.assertTrue(self.read("boundary")["training_inventory"]["boundary_exclusions"])

    def test_source_deduplication_across_requests_and_independent_items(self):
        self.raw(2, 0)
        self.issue()
        self.issue(target=13, request="another")
        self.raw(12, 8)
        self.clock.at(13.1)
        self.adapter().resolve()
        haw = self.issue(minute=14, target=24, request="haw-next")
        self.assertEqual(haw["training_count"], 1)
        self.raw(13.5, 3, country="jap", item_id=206)
        jap = self.issue(minute=14, target=24, request="jap-next", country="jap", item_id=206)
        self.assertEqual(jap["training_count"], 0)
        self.assertEqual(jap["origin"]["observation"]["quantity"], 3)
        self.assertEqual(jap["persistence_probability"], 1)

    def test_late_target_evidence_remains_unresolved_and_immutable(self):
        self.raw(2, 0)
        self.issue()
        self.raw(12, 8, stored=13)
        self.clock.at(14)
        result, = self.adapter().resolve()
        self.assertEqual(result["label"]["status"], "no_observation")
        self.assertNotIn("available", result["label"])
        saved = deepcopy(self.s3.objects)
        self.clock.at(15)
        self.assertEqual(self.adapter().resolve(), [])
        self.assertEqual(saved, self.s3.objects)
        self.assertEqual(self.issue(minute=15, target=25, request="next")["training_count"], 0)

    def test_unknown_origins_stale_targets_and_no_post_origin_update(self):
        self.issue(target=3)
        self.clock.at(4)
        self.adapter().resolve()
        group = next(iter(self.adapter().report()["horizons"].values()))
        self.assertEqual(group["forecast_status"], {"origin_ineligible": 1})
        self.assertIsNone(group["brier"]["always_empty"])
        self.setUp()
        self.raw(2, 8)
        self.issue(target=3)
        self.clock.at(4)
        outcome, = self.adapter().resolve()
        self.assertEqual(outcome["label"]["status"], "no_post_origin_source_update")
        self.issue(minute=5, target=15, request="stale")
        self.clock.at(16)
        result, = self.adapter().resolve()
        self.assertEqual(result["label"]["status"], "no_observation")

    def test_failed_attempt_carries_only_a_fresh_post_origin_source(self):
        self.raw(2, 0)
        self.issue()
        self.raw(12, 8)
        self.raw(12.3, 0, failed=True)
        self.clock.at(13)
        result, = self.adapter().resolve()
        self.assertEqual(result["label"]["available"], 1)
        self.assertTrue(result["label"]["observation"]["carried_over_unavailable_attempt"])

    def test_accounting_write_interruption_never_double_trains(self):
        for position in ("before", "after"):
            with self.subTest(position=position):
                self.setUp()
                self.raw(2, 0)
                self.issue()
                self.raw(12, 8)
                self.s3.fail = (self.adapter().key("ten", "outcome"), position)
                self.clock.at(13)
                with self.assertRaises(TimeoutError):
                    self.adapter().resolve()
                if position == "before":
                    self.assertEqual(self.issue(minute=13.1, target=23.1, request="before")["training_count"], 0)
                self.clock.at(14)
                self.adapter().resolve()
                self.assertEqual(self.issue(minute=15, target=25, request="after")["training_count"], 1)

    def test_truncated_inventory_budgets_integrity_and_pilot_isolation(self):
        self.raw(2, 0)
        with self.assertRaisesRegex(ValueError, "isolated"):
            RequestedMeasurement(self.s3, "private-evidence", epoch="epoch-1", country="haw", item_id=1485,
                                 maximum_source_age_seconds=300)
        with patch("tornredeye.requested_measurement.MAX_LEDGER_OBJECTS", 1):
            with self.assertRaisesRegex(ValueError, "room"):
                self.issue()
        self.issue()
        self.clock.at(3)
        with patch("tornredeye.requested_measurement.MAX_LEDGER_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "bound"):
                self.adapter().report()
        key = self.adapter().key("ten", "forecast")
        self.s3.objects[key]["data"] += b' '
        with self.assertRaisesRegex(ValueError, "digest"):
            self.adapter().report()
        for p in self.s3.puts:
            if "/requests/" in p["Key"]:
                self.assertEqual(p["IfNoneMatch"], "*")
                self.assertTrue(p["Key"].startswith("forecasts/live-v1/records/requested-test/requests/"))
                validate_backup_key(p["Key"])

    def test_incomplete_and_repeated_ledger_pages_abort_before_issuance(self):
        listing = self.s3.list_objects_v2
        def incomplete(**kwargs):
            page = listing(**kwargs)
            if "/requests/" in kwargs["Prefix"]:
                page["IsTruncated"] = True
                page["NextContinuationToken"] = "same"
            return page
        with patch.object(self.s3, "list_objects_v2", side_effect=incomplete):
            with self.assertRaisesRegex(ValueError, "pagination"):
                self.issue()
        self.assertFalse(any("/requests/" in k for k in self.s3.objects))

    def test_outcome_association_corruption_is_rejected_before_training(self):
        self.raw(2, 0)
        self.issue()
        self.raw(12, 8)
        self.clock.at(13)
        self.adapter().resolve()
        key = self.adapter().key("ten", "outcome")
        value = self.read("ten", "outcome")
        value["forecast_sha256"] = "0" * 64
        body = _bytes(value)
        self.s3.objects[key]["data"] = body
        self.s3.objects[key]["Metadata"]["sha256"] = hashlib.sha256(body).hexdigest()
        with self.assertRaisesRegex(ValueError, "association"):
            self.issue(minute=14, target=24, request="next")
        self.assertNotIn(self.adapter().key("next", "forecast"), self.s3.objects)

    def test_conflicts_and_stale_targets_are_unknown_not_zero(self):
        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting):
                self.setUp()
                self.raw(2, 0)
                self.issue()
                if conflicting:
                    source = int((START + timedelta(minutes=12)).timestamp()) - 1
                    self.raw(12, 8, source=source)
                    self.raw(12.2, 0, source=source)
                else:
                    self.raw(6, 8)
                self.clock.at(13)
                result, = self.adapter().resolve()
                self.assertEqual(result["label"]["status"], "conflicting_source_time" if conflicting else "stale")
                self.assertNotIn("available", result["label"])
                self.assertEqual(self.issue(minute=14, target=24, request="next")["training_count"], 0)

    def test_deadline_and_read_bounds_fail_without_partial_issuance(self):
        self.raw(2, 0)
        with self.assertRaisesRegex(RuntimeError, "execution budget"):
            self.issue(remaining_millis=lambda: 1)
        with self.assertRaisesRegex(ValueError, "read bound"):
            self.issue(maximum_evidence_bytes=2)
        self.assertFalse(any("/requests/" in k for k in self.s3.objects))

    def test_large_valid_raw_evidence_has_a_separate_configurable_read_bound(self):
        key = self.raw(2, 0)
        original = self.s3.objects.pop(key)
        body = original["data"] + b" " * (1024 * 1024)
        metadata = {**original["Metadata"], "sha256": hashlib.sha256(body).hexdigest()}
        self.clock.at(2)
        self.s3.put_object(Bucket="private-evidence", Key=key, Body=body,
                           IfNoneMatch="*", Metadata=metadata)
        with self.assertRaisesRegex(ValueError, "read bound"):
            self.issue(maximum_evidence_bytes=len(body) - 1)
        self.assertEqual(len(self.adapter(maximum_evidence_bytes=len(body)).get(key)[0]), len(body))
        forecast = self.issue()
        self.assertEqual(forecast["origin"]["status"], "eligible")
        with patch("tornredeye.requested_measurement.MAX_OBJECT_BYTES", 2):
            with self.assertRaisesRegex(ValueError, "read bound"):
                self.adapter().get(self.adapter().key("ten", "forecast"))

    def test_committed_research_cohorts_group_latency_differences_without_reassignment(self):
        self.raw(2, 0)
        self.issue(target=3.5, request="seed")
        self.raw(3.4, 8)
        self.clock.at(4)
        self.adapter().resolve()
        plan = [{"country": "haw", "item_id": 1485,
                 "target_utc": _utc(START + timedelta(minutes=target)),
                 "horizon_cohort": cohort}
                for target, cohort in ((14.1, "ten-minute"), (14.1, "ten-minute"),
                                       (64.1, "sixty-minute"))]
        forecasts = [self.issue(minute=4.1 + i / 100, target=target,
                                 request=planned_request_id(plan, i))
                     for i, target in enumerate((14.1, 14.1, 64.1))]
        self.assertNotEqual(forecasts[0]["horizon_seconds"], forecasts[1]["horizon_seconds"])
        self.raw(14, 0)
        self.raw(64, 8)
        self.clock.at(65)
        self.adapter().resolve()
        self.clock.at(66)
        before = deepcopy(self.s3.objects)
        report = self.adapter().report(request_plan=plan)
        short, long = (report["cohorts"]["cohort:" + label]
                       for label in ("ten-minute", "sixty-minute"))
        self.assertEqual((short["paired_count"], long["paired_count"]), (2, 1))
        self.assertEqual(short["brier"], {"empirical": 1, "persistence": 1, "always_empty": 0})
        self.assertEqual(long["brier"], {"empirical": 0, "persistence": 0, "always_empty": 1})
        self.assertEqual(report["cohorts"]["unplanned"]["recorded"], 1)
        self.assertTrue(all(r["issuance_verified"] for r in report["records"]))
        self.assertEqual({r["forecast"]["forecast_id"]: r["forecast"] for r in report["records"]
                          if r["horizon_cohort"] is not None},
                         {f["forecast_id"]: f for f in forecasts})
        changed = deepcopy(plan)
        changed[0]["horizon_cohort"] = "sixty-minute"
        with self.assertRaisesRegex(ValueError, "commitment mismatch"):
            self.adapter().report(request_plan=changed)
        self.assertEqual(before, self.s3.objects)

    def test_append_during_inventory_cannot_enter_the_frozen_training_cutoff(self):
        self.raw(2, 0)
        self.issue()
        self.raw(12, 8)
        self.clock.at(13)
        listing = self.s3.list_objects_v2
        appended = False
        def append(**kwargs):
            nonlocal appended
            if kwargs["Prefix"] == self.adapter().records_root and not appended:
                appended = True
                self.adapter().resolve()
            return listing(**kwargs)
        with patch.object(self.s3, "list_objects_v2", side_effect=append):
            result = self.adapter().issue(START + timedelta(minutes=23), request_id="during")
        self.assertEqual(result["training_count"], 0)
        self.assertTrue(result["training_inventory"]["boundary_exclusions"])
        self.assertEqual(self.issue(minute=14, target=24, request="after")["training_count"], 1)

    def test_both_items_have_independent_matched_scores_at_both_research_horizons(self):
        for country, item_id in (("haw", 1485), ("jap", 206)):
            with self.subTest(country=country):
                self.setUp()
                selection = {"country": country, "item_id": item_id}
                self.raw(2, 0, **selection)
                self.issue(target=3.5, request="seed", **selection)
                self.raw(3.4, 8, **selection)
                self.clock.at(4)
                self.adapter(**selection).resolve()
                a = self.issue(minute=4.1, target=14.1, request="ten", **selection)
                b = self.issue(minute=4.1, target=64.1, request="sixty", **selection)
                self.assertEqual(a["empirical_probability"], b["empirical_probability"])
                self.assertEqual(a["empirical_probability"], 1)
                self.raw(14, 0, **selection)
                self.raw(64, 8, **selection)
                self.clock.at(65)
                self.adapter(**selection).resolve()
                self.clock.at(66)
                report = self.adapter(**selection).report()
                short, long = report["horizons"]["600.0"], report["horizons"]["3600.0"]
                self.assertEqual((short["paired_count"], long["paired_count"]), (1, 1))
                self.assertEqual(short["brier"], {"empirical": 1, "persistence": 1, "always_empty": 0})
                self.assertEqual(long["brier"], {"empirical": 0, "persistence": 0, "always_empty": 1})


if __name__ == "__main__":
    unittest.main()
