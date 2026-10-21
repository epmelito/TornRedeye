"""Deterministic storage and temporal-contract tests; never call AWS."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from aws_shadow_forecast import Consumer, LEASE, POINTER, ROOT
from evidence_backup import PREFIXES
from tools import package_shadow


START = datetime(2026, 10, 10, tzinfo=timezone.utc)


class Clock:
    value = START + timedelta(minutes=2.5)

    def at(self, minute):
        self.value = START + timedelta(minutes=minute)

    def __call__(self):
        return self.value


class S3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}


class S3:
    def __init__(self, clock):
        self.clock, self.objects = clock, {}
        self.puts, self.gets, self.lists = [], [], []
        self.fail = None
        self.request = 0

    def put_object(self, **args):
        self.puts.append(args)
        key = args["Key"]
        if args.get("IfNoneMatch") == "*" and key in self.objects:
            raise S3Error("PreconditionFailed")
        if "IfMatch" in args and self.objects.get(key, {}).get("ETag") != args["IfMatch"]:
            raise S3Error("PreconditionFailed")
        if self.fail == (key, "before"):
            self.fail = None
            raise TimeoutError("write not sent")
        body = args["Body"]
        self.objects[key] = {"data": body, "Metadata": args.get("Metadata", {}),
                             "LastModified": self.clock().replace(microsecond=0),
                             "ETag": '"' + hashlib.sha256(body).hexdigest() + '"'}
        if self.fail == (key, "after"):
            self.fail = None
            raise TimeoutError("acknowledgement lost")
        return {"ResponseMetadata": {"HTTPStatusCode": 200, "RequestId": "put"}}

    def get_object(self, **args):
        self.gets.append(args)
        key = args["Key"]
        if key not in self.objects:
            raise S3Error("NoSuchKey")
        value = deepcopy(self.objects[key])
        if args.get("IfMatch", value["ETag"]) != value["ETag"]:
            raise S3Error("PreconditionFailed")
        self.request += 1
        value.update(Body=BytesIO(value.pop("data")), ResponseMetadata={
            "HTTPStatusCode": 200, "RequestId": str(self.request)})
        return value

    def list_objects_v2(self, **args):
        self.lists.append(args)
        keys = sorted(k for k in self.objects if k.startswith(args["Prefix"]))
        offset = int(args.get("ContinuationToken", "0"))
        selected = keys[offset:offset+2]
        result = {"Contents": [{"Key": k, "LastModified": self.objects[k]["LastModified"],
                                "ETag": self.objects[k]["ETag"]} for k in selected],
                  "IsTruncated": offset + 2 < len(keys)}
        if result["IsTruncated"]:
            result["NextContinuationToken"] = str(offset + 2)
        return result


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.s3 = S3(self.clock)

    def consumer(self, **kwargs):
        return Consumer(self.s3, "private-evidence", clock=self.clock, **kwargs)

    def raw(self, minute, quantity, *, source=None, stored=None, identifier=None):
        retrieved = START + timedelta(minutes=minute)
        source = int(retrieved.timestamp()) - 1 if source is None else source
        key = PREFIXES[0] + f"{identifier if identifier is not None else int(minute*1000):032x}.bin"
        body = json.dumps({"timestamp": source, "stocks": {"haw": {
            "update": source, "stocks": [{"id": 1485, "name": "Shark Fin",
                                         "quantity": quantity, "cost": 100}]}}}).encode()
        previous = self.clock.value
        self.clock.at(minute if stored is None else stored)
        self.s3.put_object(Bucket="private-evidence", Key=key, Body=body, IfNoneMatch="*",
                           Metadata={"source": "YATA", "retrieved-at": retrieved.isoformat(),
                                     "sha256": hashlib.sha256(body).hexdigest(),
                                     "http-status": "200", "status": "observed"})
        self.clock.value = previous
        return key

    def read(self, key):
        return json.loads(self.s3.objects[key]["data"])

    def invoke(self, minute):
        self.clock.at(minute)
        return self.consumer().run()

    def test_bootstrap_restart_training_paired_scores_and_duplicate_slot(self):
        forecasts = []
        for minute, quantity in [(2.5, 0), (62.6, 8), (122.7, 0), (182.8, 8)]:
            self.raw(minute-.3, quantity)
            result = self.invoke(minute)
            forecasts.append(self.read(result["forecast_key"]))
            self.assertTrue(result["acknowledgement"]["live_issuance_verified"])
        self.assertEqual([r["training_count"] for r in forecasts], [0, 1, 2, 3])
        self.assertEqual([r["empirical_probability"] for r in forecasts], [None, 1, .5, 2/3])
        self.assertEqual(result["summary"]["paired_count"], 2)
        self.assertEqual(result["summary"]["brier"], {"empirical": .625, "persistence": 1})
        saved = deepcopy(self.s3.objects)
        self.invoke(183)
        self.assertEqual(saved, self.s3.objects)
        for args in self.s3.puts:
            if args["Key"].startswith(ROOT):
                self.assertTrue(args.get("IfNoneMatch") == "*" or
                                (args["Key"] in (POINTER, LEASE) and "IfMatch" in args))
        self.assertTrue(any("ContinuationToken" in c for c in self.s3.lists))

    def test_delayed_processing_uses_original_persistence_not_processing_time(self):
        self.raw(2, 0)
        first = self.invoke(2.5)
        self.raw(62, 8)
        self.raw(61.9, 0, stored=65, identifier=99)
        result = self.invoke(182.5)
        outcome = self.read(first["forecast_key"].replace("forecast.json", "outcome.json"))
        self.assertEqual(outcome["label"]["available"], 1)
        self.assertEqual(outcome["resolved_at_utc"], "2026-10-10T03:02:30.000000Z")
        self.assertEqual(result["summary"]["unrecorded_slots"], 2)
        self.assertEqual(self.read(result["forecast_key"])["training_count"], 1)

    def test_late_persistence_cannot_repair_missing_outcome(self):
        self.raw(2, 0)
        first = self.invoke(2.5)
        self.raw(62, 8, stored=63)
        result = self.invoke(64)
        outcome = self.read(first["forecast_key"].replace("forecast.json", "outcome.json"))
        self.assertEqual(outcome["label"]["status"], "no_observation")
        self.assertNotIn("available", outcome["label"])
        self.assertEqual(self.read(result["forecast_key"])["training_count"], 0)

    def test_known_conflicts_block_and_post_target_conflicts_do_not_revise(self):
        self.raw(2, 0)
        first = self.invoke(2.5)
        stamp = int((START + timedelta(minutes=61)).timestamp())
        self.raw(61.5, 8, source=stamp)
        self.raw(62, 0, source=stamp)
        self.invoke(63)
        key = first["forecast_key"].replace("forecast.json", "outcome.json")
        self.assertEqual(self.read(key)["label"]["status"], "conflicting_source_time")
        before = self.s3.objects[key]["data"]
        self.raw(63.5, 8, source=stamp)
        self.invoke(123)
        self.assertEqual(self.s3.objects[key]["data"], before)

    def test_out_of_order_source_regression_cached_freshness_and_unknown_attempt(self):
        stamp = int((START + timedelta(minutes=1)).timestamp())
        self.raw(1.5, 8, source=stamp, identifier=99)
        self.raw(2, 0, source=stamp-1, identifier=1)
        self.raw(2.1, 8, source=stamp, identifier=55)
        key = PREFIXES[1] + 'f'*32 + '.json'
        self.clock.at(2.2)
        self.s3.put_object(Bucket="private-evidence", Key=key, IfNoneMatch="*", Body=json.dumps({
            "collection_id": 'f'*32, "retrieved_at": (START+timedelta(minutes=2.2)).isoformat(),
            "status": "collection_failed"}).encode())
        result = self.invoke(2.5)
        forecast = self.read(result["forecast_key"])
        self.assertEqual(forecast["origin"]["observation"]["quantity"], 8)
        self.assertTrue(forecast["origin"]["observation"]["carried_over_unavailable_attempt"])
        self.assertEqual(forecast["origin"]["observation"]["source_age_seconds"], 90)
        # a repeated receipt cannot renew the source timestamp
        self.clock.at(7)
        snapshot, points = self.consumer().evidence(self.clock())
        from availability_forecast import _select
        self.assertEqual(_select(points, [p["at"] for p in points], self.clock())["status"], "stale")

    def test_ambiguous_forecast_write_recovers_exact_record_after_target(self):
        self.raw(2, 8)
        key = self.consumer().slot_key("2026-10-10T00:00:00.000000Z", "forecast")
        self.s3.fail = (key, "after")
        with self.assertRaises(TimeoutError):
            self.invoke(2.5)
        original = self.s3.objects[key]["data"]
        self.raw(62, 0)
        self.invoke(63)
        self.assertEqual(self.s3.objects[key]["data"], original)
        self.assertTrue(self.read(key.replace("forecast.json", "ack.json"))["live_issuance_verified"])

    def test_missing_forecast_write_is_failed_slot_and_is_never_backfilled(self):
        key = self.consumer().slot_key("2026-10-10T00:00:00.000000Z", "forecast")
        self.s3.fail = (key, "before")
        with self.assertRaises(TimeoutError):
            self.invoke(2.5)
        result = self.invoke(122.5)
        self.assertNotIn(key, self.s3.objects)
        self.assertEqual(result["summary"]["failed_slots"], 1)
        self.assertEqual(result["summary"]["unrecorded_slots"], 1)

    def test_checkpoint_head_and_outcome_write_crashes_do_not_double_train(self):
        for kind in ("checkpoint", "outcome", "head"):
            with self.subTest(kind=kind):
                self.setUp()
                self.raw(2, 0)
                first = self.invoke(2.5)
                self.raw(62, 8)
                key = (POINTER if kind == "head" else
                       first["forecast_key"].replace("forecast.json", "outcome.json") if kind == "outcome" else
                       self.consumer().slot_key("2026-10-10T01:00:00.000000Z", "checkpoint"))
                self.s3.fail = (key, "after")
                with self.assertRaises(TimeoutError):
                    self.invoke(63)
                result = self.invoke(68.1)
                self.assertEqual(self.read(result["forecast_key"])["training_count"], 1)
                self.assertEqual(result["summary"]["recorded_slots"], 1)

    def test_boundary_second_and_late_forecast_write_are_unverified(self):
        self.raw(2.5, 8)
        result = self.invoke(2.5)
        forecast = self.read(result["forecast_key"])
        self.assertEqual(forecast["origin"]["status"], "no_observation")
        self.assertEqual(len(forecast["inputs"]["persistence_boundary_exclusions"]), 1)
        receipt = deepcopy(result["acknowledgement"]["forecast"])
        receipt["persisted_before_utc"] = forecast["target_utc"]
        with self.assertRaisesRegex(ValueError, "acknowledgement mismatch"):
            self.consumer().attest(forecast, receipt)

    def test_timeout_integrity_failure_and_clock_regression_fail_visibly(self):
        self.raw(2, 8)
        with self.assertRaisesRegex(RuntimeError, "execution budget"):
            self.consumer(remaining_millis=lambda: 9999).run()
        self.assertNotIn(POINTER, self.s3.objects)
        self.invoke(62.5)
        with self.assertRaisesRegex(ValueError, "precedes lease"):
            self.invoke(2.5)
        key = next(k for k in self.s3.objects if k.startswith(PREFIXES[0]))
        self.s3.objects[key]["Metadata"]["sha256"] = "bad"
        self.clock.at(2.5)
        with self.assertRaisesRegex(ValueError, "invalid canonical raw"):
            self.consumer().evidence(self.clock())

    def test_package_and_permissions_keep_consumer_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            package_shadow.package(Path(temporary)/"consumer.zip")
            with ZipFile(Path(temporary)/"consumer.zip") as archive:
                self.assertEqual(set(archive.namelist()), set(package_shadow.MODULES))
                self.assertNotIn("lambda_function.py", archive.namelist())
        template = json.loads(Path("shadow-template.json").read_text())
        resources = template["Resources"]
        self.assertEqual(template["Parameters"]["ScheduleState"]["Default"], "DISABLED")
        self.assertNotIn("ReservedConcurrentExecutions", resources["ConsumerFunction"]["Properties"])
        self.assertEqual(resources["HourlyRule"]["Properties"]["ScheduleExpression"], "cron(2 * * * ? *)")
        statements = resources["ConsumerRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        for statement in statements:
            if statement["Action"] == ["s3:PutObject"]:
                self.assertTrue(all(ROOT in r["Fn::Sub"] for r in (statement["Resource"] if isinstance(statement["Resource"], list) else [statement["Resource"]])))
                self.assertIn("Condition", statement)
        self.assertFalse(any(r["Type"] == "AWS::S3::Bucket" for r in resources.values()))

    def test_lease_serializes_duplicates_and_expires_without_release(self):
        first = self.invoke(2.5)
        original = self.s3.objects[first["forecast_key"]]["data"]
        self.assertEqual(self.invoke(3), {"status": "skipped", "reason": "lease_busy"})
        again = self.invoke(8)
        self.assertEqual(again["forecast_key"], first["forecast_key"])
        self.assertEqual(self.s3.objects[first["forecast_key"]]["data"], original)

    def test_lost_lease_acknowledgement_never_creates_unreserved_forecast(self):
        self.s3.fail = (LEASE, "after")
        with self.assertRaises(TimeoutError):
            self.invoke(2.5)
        self.assertEqual(self.invoke(3)["reason"], "lease_busy")
        self.assertFalse(any(k.endswith('/forecast.json') for k in self.s3.objects))
        self.assertTrue(self.invoke(8)["acknowledgement"]["live_issuance_verified"])

    def test_eligible_outcome_must_be_resolved_before_subsequent_training(self):
        self.raw(2, 0)
        first = self.invoke(2.5)
        self.raw(62, 8)
        second = self.invoke(62.4)
        self.assertEqual(self.read(second["forecast_key"])["training_count"], 0)
        self.assertNotIn(first["forecast_key"].replace('forecast.json', 'outcome.json'), self.s3.objects)
        third = self.invoke(122.5)
        self.assertEqual(self.read(third["forecast_key"])["training_count"], 1)
        checkpoint = self.read(self.read(POINTER)["key"])
        self.assertEqual(len(checkpoint["resolutions"]), 2)
        self.assertTrue(all(r["acknowledged_at_utc"] <= self.read(third["forecast_key"])["issued_at_utc"]
                            for r in checkpoint["resolutions"]))

    def test_freshness_boundary_is_source_age_and_missing_is_never_zero(self):
        for age, status in ((300, 'eligible'), (301, 'stale')):
            with self.subTest(age=age):
                self.setUp()
                self.raw(2, 0)
                first = self.invoke(2.5)
                source = int((START+timedelta(minutes=62.5)).timestamp()) - age
                self.raw(62, 8, source=source)
                self.invoke(63)
                outcome = self.read(first["forecast_key"].replace('forecast.json', 'outcome.json'))
                self.assertEqual(outcome['label']['status'], status)
                self.assertEqual('available' in outcome['label'], status == 'eligible')

    def test_late_durable_forecast_is_excluded_from_training_and_scoring(self):
        self.raw(2, 0)
        key = self.consumer().slot_key('2026-10-10T00:00:00.000000Z', 'forecast')
        put = self.s3.put_object

        def delayed_put(**args):
            if args['Key'] == key:
                # exercise a late storage response using an old decision body
                self.clock.at(64)
            return put(**args)

        with patch.object(self.s3, 'put_object', side_effect=delayed_put):
            with self.assertRaisesRegex(RuntimeError, 'deadline'):
                self.invoke(2.5)
        self.raw(62, 8)
        result = self.invoke(68)
        ack = self.read(key.replace('forecast.json', 'ack.json'))
        self.assertFalse(ack['live_issuance_verified'])
        self.assertEqual(result['summary']['training_count'], 0)
        self.assertEqual(result['summary']['paired_count'], 0)
        self.assertEqual(result['summary']['issuance_status'], {'late_or_ambiguous_write': 1})

    def test_partial_inventory_and_cross_slot_scan_never_issue(self):
        self.raw(2, 0)
        with patch.object(self.s3, 'list_objects_v2', side_effect=TimeoutError('list failed')):
            with self.assertRaises(TimeoutError):
                self.invoke(2.5)
        self.assertFalse(any(k.endswith('/forecast.json') for k in self.s3.objects))
        self.clock.at(59.9)
        consumer = self.consumer()
        scan = consumer.evidence

        def crossing(cutoff):
            result = scan(cutoff)
            self.clock.at(60.1)
            return result

        with patch.object(consumer, 'evidence', side_effect=crossing):
            with self.assertRaisesRegex(RuntimeError, 'crossed UTC slot'):
                consumer.run()
        self.assertFalse(any(k.endswith('/forecast.json') for k in self.s3.objects))


if __name__ == "__main__":
    unittest.main()
