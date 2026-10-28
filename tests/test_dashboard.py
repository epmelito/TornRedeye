"""Offline publisher, public contract, packaging and IAM regression checks."""

from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import traceback
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

import dashboard_publisher as publisher
from tools import package_dashboard
from tornredeye.evidence_paths import LINEAGE, evidence_keys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 10, 12, 30, tzinfo=timezone.utc)
ID = "12345678123412341234123456781234"
CONFIG = {"EVIDENCE_BUCKET": "private-evidence", "DASHBOARD_BUCKET": "private-dashboard",
          "COLLECTOR_FUNCTION": "private-collector", "PUBLISHER_FUNCTION": "private-publisher",
          "COLLECTOR_LOG_GROUP": "/private/logs", "MONITORING_NAMESPACE": "private-namespace",
          "MONITORING_TOPIC_NAME": "private-topic"}


def clients():
    result = {name: Mock() for name in ("cloudwatch", "logs", "s3")}
    result["cloudwatch"].get_metric_statistics.return_value = {"Datapoints": []}
    result["logs"].filter_log_events.return_value = {"events": []}
    return result


def observed_record(**changes):
    record = {"schema_version": 1, "collection_id": ID, "format": "stock-evidence", "lineage": LINEAGE, "acquisition": {"status": "success"}, "validation_status": "complete",
              "retrieved_at": "2026-10-10T12:20:00Z", "source_timestamp": int(NOW.timestamp()) - 3600,
              "export_timestamp": None, "raw_evidence": {"bucket": "secret", "key": "secret"},
              "detail": "private exception", "credentials": "secret"}
    record.update(changes)
    return {"Body": io.BytesIO(json.dumps(record).encode())}


def s3_error(code, status):
    error = Exception("private credentials bucket object key account 123456789012")
    error.response = {"Error": {"Code": code, "Message": str(error)},
                      "ResponseMetadata": {"HTTPStatusCode": status}}
    return error


class PublisherTests(unittest.TestCase):
    def test_neutral_logged_key_uses_legacy_status_without_exposing_all_stock(self):
        c = clients()
        key = evidence_keys(NOW, UUID(hex=ID))[1]
        c["logs"].filter_log_events.return_value = {"events": [{"timestamp": int(NOW.timestamp()*1000),
            "message": f"persisted collection_id={ID} acquisition_status=success normalized_key={key}"}]}
        c["s3"].get_object.return_value = observed_record(stock_export={"countries": {"private": "value"}})
        snapshot = publisher.build_snapshot(c, CONFIG, NOW)
        self.assertEqual(c["s3"].get_object.call_args.kwargs["Key"], key)
        self.assertEqual(snapshot["freshness"]["state"], "available")
        self.assertNotIn("stock_export", json.dumps(snapshot))

    def test_missing_is_not_zero_and_success_is_not_complete_coverage(self):
        c = clients()
        c["cloudwatch"].get_metric_statistics.return_value = {"Datapoints": [{"Timestamp": NOW.replace(minute=0) - timedelta(hours=1), "Sum": 0}]}
        s = publisher.build_snapshot(c, CONFIG, NOW)
        metric = s["metrics"]["persisted_events"]
        self.assertEqual(metric["samples"][-1], 0)
        self.assertIsNone(metric["samples"][0])
        self.assertEqual(metric["reported_slots"], 1)
        self.assertEqual(metric["latest_sample_utc"], "2026-10-10T11:00:00Z")
        self.assertIn("unknown", metric["coverage"])
        self.assertEqual(s["state"], "partial")
        self.assertEqual(len(metric["samples"]), 168)

    def test_individual_source_failures_publish_partial_without_private_details(self):
        c = clients()
        c["cloudwatch"].get_metric_statistics.side_effect = Exception("secret ARN and account")
        c["logs"].filter_log_events.side_effect = Exception("secret key")
        s = publisher.build_snapshot(c, CONFIG, NOW)
        public = json.dumps(s)
        self.assertEqual(s["state"], "partial")
        self.assertEqual(s["recent_gaps"], [])
        for private in ["secret", *CONFIG.values(), ID, "arn:", "collection_id", "raw_evidence"]:
            self.assertNotIn(private, public)

    def test_selected_evidence_only_timestamps_allowlisted(self):
        c = clients()
        c["logs"].filter_log_events.side_effect = [
            {"events": [{"message": f"persisted collection_id={ID} acquisition_status=success raw_key=secret normalized_key={evidence_keys(NOW, UUID(hex=ID))[1]}"}]}, {"events": []}]
        c["s3"].get_object.return_value = observed_record()
        s = publisher.build_snapshot(c, CONFIG, NOW)
        self.assertEqual(s["freshness"]["retrieved_at_utc"], "2026-10-10T12:20:00Z")
        self.assertIsNone(s["freshness"]["export_timestamp_utc"])
        self.assertEqual(c["s3"].get_object.call_args.kwargs["Key"], evidence_keys(NOW, UUID(hex=ID))[1])
        for text in (ID, "secret", "private exception", "credentials", "raw_key"):
            self.assertNotIn(text, json.dumps(s))
        self.assertFalse(c["s3"].list_objects_v2.called)
        self.assertFalse(c["s3"].put_object.called)

    def test_evidence_invalid_mismatched_or_oversized_is_unavailable(self):
        for response in (observed_record(collection_id="different"), observed_record(validation_status="malformed"),
                         observed_record(retrieved_at="future or secret"), {"Body": io.BytesIO(b"x" * (publisher.RECORD_BYTES + 1))}):
            with self.subTest(response=response):
                c = clients()
                c["logs"].filter_log_events.return_value = {"events": [{"message": f"persisted collection_id={ID} acquisition_status=success normalized_key={evidence_keys(NOW, UUID(hex=ID))[1]}"}]}
                c["s3"].get_object.return_value = response
                self.assertEqual(publisher.build_snapshot(c, CONFIG, NOW)["freshness"]["state"], "unavailable")
                self.assertTrue(response["Body"].closed)

    def test_query_windows_pages_events_reads_and_data_size_bounded(self):
        c = clients()
        events = [{"message": f"persisted collection_id={i:032x} acquisition_status=success normalized_key={evidence_keys(NOW, UUID(int=i))[1]}", "timestamp": i} for i in range(100)]
        c["logs"].filter_log_events.return_value = {"events": events, "nextToken": "repeat"}
        c["s3"].get_object.side_effect = Exception("unavailable")
        s = publisher.build_snapshot(c, CONFIG, NOW)
        self.assertEqual(c["cloudwatch"].get_metric_statistics.call_count, 17)
        self.assertEqual(c["logs"].filter_log_events.call_count, 4)
        self.assertEqual(c["s3"].get_object.call_count, 3)
        self.assertLess(len(json.dumps(s).encode()), publisher.MAX_BYTES)
        for call in c["cloudwatch"].get_metric_statistics.call_args_list:
            args = call.kwargs
            self.assertLessEqual(args["EndTime"] - args["StartTime"], timedelta(days=7))
            self.assertLessEqual((args["EndTime"] - args["StartTime"]).total_seconds() / args["Period"], 180)
        for call in c["logs"].filter_log_events.call_args_list:
            self.assertEqual(call.kwargs["endTime"] - call.kwargs["startTime"], 7200000)
            self.assertEqual(call.kwargs["limit"], 100)

    def test_budget_exhaustion_avoids_further_retrieval(self):
        c = clients()
        budget = Mock(side_effect=TimeoutError("private"))
        s = publisher.build_snapshot(c, CONFIG, NOW, budget)
        self.assertEqual(s["state"], "partial")
        for client in c.values():
            self.assertEqual(client.mock_calls, [])

    def test_gap_threshold_unknown_coverage_and_positive_boundary(self):
        series = {"start_utc": "2026-10-10T00:00:00Z", "samples": [0]*14 + [1] + [None]*15 + [1] + [0]*15}
        gaps = publisher.gap_candidates(series)
        self.assertEqual([g["minutes"] for g in gaps], [15, 15])
        self.assertEqual([g["unknown_minutes"] for g in gaps], [15, 0])
        self.assertTrue(all("unconfirmed" in g["coverage"] for g in gaps))

    def test_billed_reports_deduplicate_and_no_reports_does_not_mean_zero(self):
        c = clients()
        message = f"REPORT RequestId: 12345678-1234-1234-1234-123456789012 Duration: 999 ms Billed Duration: 1000 ms Memory Size: 128 MB Max Memory Used: 90 MB"
        c["logs"].filter_log_events.return_value = {"events": [{"message": message}, {"message": message}], "nextToken": "repeat"}
        s = publisher.build_snapshot(c, CONFIG, NOW)
        self.assertEqual(s["billed_compute"]["value"], .125)
        self.assertIn("partial", s["billed_compute"]["coverage"])
        self.assertIsNone(publisher.build_snapshot(clients(), CONFIG, NOW)["billed_compute"]["value"])

    def test_nonfinite_negative_boolean_and_future_values_rejected(self):
        for value in (True, -1, float("nan"), float("inf"), "secret"):
            self.assertIsNone(publisher.number(value))
        self.assertEqual(publisher.number(0), 0)
        self.assertIsNone(publisher.timestamp(NOW.timestamp() + 1, NOW))
        self.assertIsNone(publisher.timestamp("2026-10-10T12:00:00", NOW))

    def test_publication_atomic_conditional_and_failure_retains_previous(self):
        s = publisher.build_snapshot(clients(), CONFIG, NOW)
        client = Mock()
        old = {"published_at_utc": "2026-10-10T11:00:00Z"}
        client.get_object.return_value = {"Body": io.BytesIO(json.dumps(old).encode()), "ETag": '"old"'}
        client.put_object.side_effect = Exception("private failure")
        with self.assertRaises(Exception):
            publisher.publish(client, "bucket", s)
        self.assertEqual(client.put_object.call_args.kwargs["IfMatch"], '"old"')
        self.assertEqual(client.put_object.call_args.kwargs["Key"], "public/data.json")
        self.assertFalse(client.delete_object.called)

    def test_new_publication_and_out_of_order_publication(self):
        s = publisher.build_snapshot(clients(), CONFIG, NOW)
        client = Mock()
        for code, status in (("NoSuchKey", 404), ("404", 404), ("AccessDenied", 403), ("403", 403)):
            with self.subTest(code=code):
                client.reset_mock()
                client.get_object.side_effect = s3_error(code, status)
                self.assertTrue(publisher.publish(client, "bucket", s))
                written = client.put_object.call_args.kwargs
                self.assertEqual(written["IfNoneMatch"], "*")
                self.assertNotIn("IfMatch", written)
                self.assertEqual(json.loads(written["Body"]), s)
                client.put_object.assert_called_once()
        client.reset_mock(); client.get_object.side_effect = None
        client.get_object.return_value = {"Body": io.BytesIO(json.dumps({"published_at_utc": "2026-10-10T13:00:00Z"}).encode()), "ETag": "new"}
        self.assertFalse(publisher.publish(client, "bucket", s))
        client.put_object.assert_not_called()

    def test_denied_initial_read_never_hides_write_denial_or_conflict(self):
        snapshot = {"published_at_utc": publisher.stamp(NOW)}
        for read_code in ("AccessDenied", "403", "NoSuchKey"):
            for code, status in (("AccessDenied", 403), ("PreconditionFailed", 412),
                                 ("ConditionalRequestConflict", 409), ("InternalError", 500)):
                with self.subTest(read_code=read_code, write_code=code):
                    client = Mock()
                    client.get_object.side_effect = s3_error(read_code, 403 if read_code != "NoSuchKey" else 404)
                    error = s3_error(code, status)
                    client.put_object.side_effect = error
                    with self.assertRaises(Exception) as caught:
                        publisher.publish(client, "bucket", snapshot)
                    self.assertIs(caught.exception, error)
                    client.put_object.assert_called_once()
                    self.assertEqual(client.put_object.call_args.kwargs["IfNoneMatch"], "*")
                    self.assertNotIn("IfMatch", client.put_object.call_args.kwargs)
                    client.delete_object.assert_not_called()

    def test_existing_snapshot_update_remains_etag_conditional(self):
        snapshot = {"published_at_utc": publisher.stamp(NOW)}
        for code, status in ((None, None), ("PreconditionFailed", 412),
                             ("ConditionalRequestConflict", 409), ("AccessDenied", 403)):
            with self.subTest(code=code):
                client = Mock()
                client.get_object.return_value = {"Body": io.BytesIO(json.dumps(
                    {"published_at_utc": "2026-10-10T11:00:00Z"}).encode()), "ETag": '"old"'}
                if code is None:
                    self.assertTrue(publisher.publish(client, "bucket", snapshot))
                else:
                    error = s3_error(code, status)
                    client.put_object.side_effect = error
                    with self.assertRaises(Exception) as caught:
                        publisher.publish(client, "bucket", snapshot)
                    self.assertIs(caught.exception, error)
                self.assertEqual(client.put_object.call_args.kwargs["IfMatch"], '"old"')
                self.assertNotIn("IfNoneMatch", client.put_object.call_args.kwargs)
                client.put_object.assert_called_once()
                client.delete_object.assert_not_called()

    def test_unexpected_s3_read_errors_do_not_attempt_publication(self):
        for code, status in (("NoSuchBucket", 404), ("SignatureDoesNotMatch", 403),
                             ("SlowDown", 503), ("InternalError", 500)):
            with self.subTest(code=code):
                client = Mock()
                error = s3_error(code, status)
                client.get_object.side_effect = error
                with self.assertRaises(Exception) as caught:
                    publisher.publish(client, "bucket", {"published_at_utc": publisher.stamp(NOW)})
                self.assertIs(caught.exception, error)
                client.put_object.assert_not_called()

    def test_equal_or_newer_retrieval_cutoff_prevents_replacement(self):
        snapshot = {"as_of_utc": "2026-10-10T12:00:00Z", "published_at_utc": publisher.stamp(NOW)}
        for cutoff in ("2026-10-10T12:00:00Z", "2026-10-10T12:15:00Z"):
            with self.subTest(cutoff=cutoff):
                client = Mock()
                body = io.BytesIO(json.dumps({"as_of_utc": cutoff,
                    "published_at_utc": "2026-10-10T12:20:00Z"}).encode())
                client.get_object.return_value = {"Body": body, "ETag": '"newer"'}
                self.assertFalse(publisher.publish(client, "bucket", snapshot))
                self.assertTrue(body.closed)
                client.put_object.assert_not_called()

    def test_failed_read_or_oversize_never_blindly_overwrites(self):
        client = Mock(); client.get_object.side_effect = Exception("AccessDenied")
        with self.assertRaises(Exception):
            publisher.publish(client, "bucket", {"published_at_utc": publisher.stamp(NOW)})
        client.put_object.assert_not_called()
        with self.assertRaises(ValueError):
            publisher.publish(client, "bucket", {"oversize": "x" * publisher.MAX_BYTES})
        client.put_object.assert_not_called()

    def test_oversized_previous_object_fails_closed(self):
        client = Mock()
        previous = json.dumps({"published_at_utc": "2026-10-10T11:00:00Z"}).encode() + b" " * publisher.MAX_BYTES
        client.get_object.return_value = {"Body": io.BytesIO(previous), "ETag": "old"}
        with self.assertRaises(ValueError):
            publisher.publish(client, "bucket", {"published_at_utc": publisher.stamp(NOW)})
        client.put_object.assert_not_called()

    def test_handler_budget_partial_publication_and_sanitized_publication_failure(self):
        c = clients()
        missing = Exception(); missing.response = {"Error": {"Code": "NoSuchKey"}}
        c["s3"].get_object.side_effect = missing
        sdk = SimpleNamespace(client=lambda name, **kwargs: c[name])
        sdk_config = Mock()
        context = Mock(); context.get_remaining_time_in_millis.return_value = 39000
        with patch.dict("sys.modules", {"boto3": sdk, "botocore.config": SimpleNamespace(Config=sdk_config)}), patch.dict("os.environ", CONFIG, clear=True):
            result = publisher.lambda_handler({}, context)
            self.assertEqual(result, {"state": "partial", "updated": True})
            self.assertFalse(c["cloudwatch"].get_metric_statistics.called)
            self.assertFalse(c["logs"].filter_log_events.called)
            sdk_config.assert_called_once_with(connect_timeout=2, read_timeout=3, retries={"total_max_attempts": 2})
            c["s3"].put_object.side_effect = Exception("private exception account credentials")
            with self.assertRaisesRegex(RuntimeError, "^dashboard publication failed$"):
                publisher.lambda_handler({}, context)
            self.assertFalse(c["s3"].delete_object.called)

    def test_handler_publication_diagnostics_are_allowlisted_and_failure_stays_visible(self):
        c = clients()
        c["s3"].get_object.side_effect = s3_error("AccessDenied", 403)
        sdk = SimpleNamespace(client=lambda name, **kwargs: c[name])
        context = Mock(); context.get_remaining_time_in_millis.return_value = 39000
        private = "private credentials bucket object key account 123456789012"
        cases = [(s3_error("AccessDenied", 403), "AccessDenied"),
                 (s3_error("PreconditionFailed", 412), "PreconditionFailed"),
                 (s3_error("ConditionalRequestConflict", 409), "ConditionalRequestConflict"),
                 (s3_error("InternalError", 500), "InternalError"),
                 (s3_error(private, 500), "aws_error"),
                 (ValueError(private), "invalid_snapshot"),
                 (TimeoutError(private), "timeout"),
                 (OSError(private), "transport_error"),
                 (RuntimeError(private), "unexpected_error")]
        malformed = RuntimeError(private); malformed.response = {"Error": private}
        cases.append((malformed, "unexpected_error"))
        with patch.dict("sys.modules", {"boto3": sdk, "botocore.config": SimpleNamespace(Config=Mock())}), \
                patch.dict("os.environ", CONFIG, clear=True):
            for error, category in cases:
                with self.subTest(category=category):
                    c["s3"].put_object.side_effect = error
                    output = io.StringIO()
                    with redirect_stdout(output), self.assertRaises(RuntimeError) as caught:
                        publisher.lambda_handler({}, context)
                    self.assertEqual(str(caught.exception), "dashboard publication failed")
                    self.assertIn(f"dashboard publication failed ({category})", output.getvalue())
                    diagnostic = output.getvalue() + "".join(traceback.format_exception(caught.exception))
                    for secret in (private, *CONFIG.values(), publisher.SNAPSHOT_KEY):
                        self.assertNotIn(secret, diagnostic)
                    self.assertNotIn("dashboard publication completed", output.getvalue())
                    self.assertIsNone(caught.exception.__cause__)

    def test_metric_rejects_outside_window_datapoints(self):
        client = Mock()
        start = NOW - timedelta(hours=1)
        client.get_metric_statistics.return_value = {"Datapoints": [
            {"Timestamp": start - timedelta(seconds=1), "Sum": 99},
            {"Timestamp": NOW, "Sum": 99}]}
        result = publisher.read_metric(client, publisher.metric_specs(CONFIG)["persisted_events"], start, NOW, 3600)
        self.assertEqual(result["samples"], [None])
        self.assertIsNone(result["latest_sample_utc"])


class DashboardInfrastructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = json.loads((ROOT / "dashboard-template.json").read_text())
        cls.resources = cls.template["Resources"]

    def test_private_origin_https_oac_and_distribution_scoped_policy(self):
        r = self.resources
        bucket = r["DashboardBucket"]["Properties"]
        self.assertTrue(all(bucket["PublicAccessBlockConfiguration"].values()))
        self.assertNotIn("WebsiteConfiguration", bucket)
        self.assertNotIn("VersioningConfiguration", bucket)
        self.assertEqual(r["OriginAccess"]["Properties"]["OriginAccessControlConfig"]["SigningBehavior"], "always")
        distribution = r["Distribution"]["Properties"]["DistributionConfig"]
        self.assertEqual(distribution["DefaultCacheBehavior"]["ViewerProtocolPolicy"], "https-only")
        self.assertEqual(distribution["Origins"][0]["OriginPath"], "/public")
        allow = r["BucketPolicy"]["Properties"]["PolicyDocument"]["Statement"][0]
        self.assertEqual(allow["Principal"], {"Service": "cloudfront.amazonaws.com"})
        self.assertEqual(allow["Resource"], {"Fn::Sub": "${DashboardBucket.Arn}/public/*"})
        self.assertIn("distribution/${Distribution}", allow["Condition"]["StringEquals"]["AWS:SourceArn"]["Fn::Sub"])

    def test_publisher_permissions_exclude_control_listing_evidence_write_and_extra_services(self):
        statements = self.resources["PublisherRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        actions = {a for s in statements for a in s["Action"]}
        self.assertEqual(actions, {"cloudwatch:GetMetricStatistics", "logs:FilterLogEvents", "s3:GetObject", "s3:PutObject", "logs:CreateLogStream", "logs:PutLogEvents"})
        for s in statements:
            if "s3:PutObject" in s["Action"]:
                self.assertEqual(s["Resource"], {"Fn::Sub": "${DashboardBucket.Arn}/public/data.json"})
                self.assertIn("Condition", s)
        self.assertNotIn("control/", json.dumps(statements))
        reads = [s for s in statements if "s3:GetObject" in s["Action"]]
        self.assertEqual(len(reads), 2)
        self.assertIn("/evidence/live-v1/*/attempt.json", reads[0]["Resource"][0]["Fn::Sub"])
        types = {r["Type"] for r in self.resources.values()}
        self.assertNotIn("AWS::CloudWatch::Alarm", types)
        self.assertNotIn("AWS::Logs::MetricFilter", types)

    def test_hourly_disabled_schedule_bounded_runtime_no_retries_or_reserved_quota(self):
        function = self.resources["Publisher"]["Properties"]
        self.assertEqual(function["Timeout"], 90)
        self.assertEqual(function["MemorySize"], 128)
        self.assertNotIn("ReservedConcurrentExecutions", function)
        schedule = self.resources["HourlyPublication"]["Properties"]
        self.assertEqual(schedule["ScheduleExpression"], "rate(1 hour)")
        self.assertEqual(self.template["Parameters"]["ScheduleState"]["Default"], "DISABLED")
        self.assertEqual(self.resources["AsyncConfiguration"]["Properties"]["MaximumRetryAttempts"], 0)
        self.assertEqual(schedule["Targets"][0]["RetryPolicy"]["MaximumRetryAttempts"], 0)

    def test_package_only_publisher_valid_deterministic_and_exact_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publisher.zip"
            package_dashboard.package(path)
            with ZipFile(path) as archive:
                self.assertEqual(archive.namelist(), ["dashboard_publisher.py", "stock_publisher.py", "tornredeye/__init__.py", "tornredeye/evidence_paths.py"])
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.read("dashboard_publisher.py"), (ROOT / "dashboard_publisher.py").read_bytes())
            original = path.read_bytes(); package_dashboard.package(path)
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.resources["Publisher"]["Properties"]["CodeUri"], ".aws-sam/dashboard-publisher.zip")


if __name__ == "__main__":
    unittest.main()
