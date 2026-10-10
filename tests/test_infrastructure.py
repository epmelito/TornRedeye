"""Offline checks of packaging, source contracts, and infrastructure safeguards."""

import fnmatch
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import UUID
from datetime import datetime, timezone
from zipfile import ZipFile

from polling_guard import CONTROL_KEY, LEASE_SECONDS
from s3_persistence import persist
from yata_collector import normalize
from tools import package_lambda


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = json.loads((ROOT / "template.json").read_text(encoding="utf-8"))
RESOURCES = TEMPLATE["Resources"]


def properties(name):
    return RESOURCES[name]["Properties"]


class InfrastructureTests(unittest.TestCase):
    def test_bucket_is_private_encrypted_and_retained_on_stack_removal_or_replacement(self):
        bucket = RESOURCES["EvidenceBucket"]
        self.assertEqual(bucket["DeletionPolicy"], "Retain")
        self.assertEqual(bucket["UpdateReplacePolicy"], "Retain")
        config = bucket["Properties"]
        self.assertEqual(config["PublicAccessBlockConfiguration"], {
            "BlockPublicAcls": True, "BlockPublicPolicy": True,
            "IgnorePublicAcls": True, "RestrictPublicBuckets": True,
        })
        encryption = config["BucketEncryption"]["ServerSideEncryptionConfiguration"]
        self.assertEqual(encryption[0]["ServerSideEncryptionByDefault"]["SSEAlgorithm"], "AES256")
        self.assertEqual(config["OwnershipControls"]["Rules"][0]["ObjectOwnership"], "BucketOwnerEnforced")

    def test_iam_matches_actual_persistence_calls_and_evidence_has_no_expiration(self):
        s3 = Mock()
        raw = b'{"timestamp":1,"stocks":{"jap":{"update":1,"stocks":[{"id":206,"name":"Xanax","quantity":0,"cost":800000}]}}}'
        result = normalize(raw, datetime(2026, 10, 8, tzinfo=timezone.utc))
        persist(result, s3=s3, bucket="test-evidence", collection_id=UUID(int=1))
        statements = properties("CollectorRole")["Policies"][0]["PolicyDocument"]["Statement"]
        writes = next(statement for statement in statements if statement["Action"] == ["s3:PutObject"])
        reads = next(statement for statement in statements if statement["Action"] == ["s3:GetObject"])
        self.assertEqual(writes["Condition"], {"StringEquals": {"s3:if-none-match": "*"}})
        for call in s3.put_object.call_args_list:
            self.assertEqual(call.kwargs["IfNoneMatch"], "*")
            for statement in (writes, reads):
                patterns = [resource["Fn::Sub"].replace("${EvidenceBucket.Arn}/", "") for resource in statement["Resource"]]
                self.assertTrue(any(fnmatch.fnmatchcase(call.kwargs["Key"], pattern) for pattern in patterns))
                for unrelated_key in ("unrelated.json", "raw/other/data.bin", "normalized/other/data.json"):
                    self.assertFalse(any(fnmatch.fnmatchcase(unrelated_key, pattern) for pattern in patterns))
        self.assertNotIn("LifecycleConfiguration", properties("EvidenceBucket"))
        actions = {action for statement in statements for action in statement["Action"]}
        self.assertEqual(actions, {"s3:PutObject", "s3:GetObject", "logs:CreateLogStream", "logs:PutLogEvents"})
        self.assertTrue(all(statement["Effect"] == "Allow" for statement in statements))

    def test_control_permissions_are_conditional_and_scoped_to_exact_key(self):
        statements = properties("CollectorRole")["Policies"][0]["PolicyDocument"]["Statement"]
        control = [s for s in statements if s["Sid"] in ("ReadPollingControl", "UpdatePollingControl")]
        self.assertEqual(len(control), 2)
        for statement in control:
            self.assertEqual(statement["Resource"], {"Fn::Sub": "${EvidenceBucket.Arn}/" + CONTROL_KEY})
        read = next(s for s in control if s["Sid"] == "ReadPollingControl")
        write = next(s for s in control if s["Sid"] == "UpdatePollingControl")
        self.assertEqual(read["Action"], ["s3:GetObject"])
        self.assertEqual(write["Action"], ["s3:PutObject"])
        self.assertEqual(write["Condition"], {"Null": {"s3:if-match": "false"}})

    def test_lambda_uses_handler_configuration_bounded_execution_and_existing_log_group(self):
        function = properties("CollectorFunction")
        self.assertEqual(function["Handler"], "lambda_function.lambda_handler")
        self.assertEqual(function["Runtime"], "python3.13")
        self.assertGreater(function["Timeout"], 15)
        self.assertLess(function["Timeout"], 300)
        self.assertGreater(LEASE_SECONDS, function["Timeout"])
        self.assertNotIn("ReservedConcurrentExecutions", function)
        self.assertEqual(function["Environment"]["Variables"], {
            "DESTINATION_BUCKET": {"Ref": "EvidenceBucket"}, "YATA_TIMEOUT_SECONDS": "15",
        })
        self.assertNotIn("AWS_REGION", function["Environment"]["Variables"])
        self.assertEqual(function["Role"], {"Fn::GetAtt": ["CollectorRole", "Arn"]})
        self.assertEqual(function["LoggingConfig"], {"LogGroup": {"Ref": "CollectorLogGroup"}})
        self.assertEqual(properties("CollectorLogGroup")["RetentionInDays"], 14)
        statements = properties("CollectorRole")["Policies"][0]["PolicyDocument"]["Statement"]
        logs = next(statement for statement in statements if "logs:PutLogEvents" in statement["Action"])
        self.assertIn("${CollectorLogGroup}:log-stream:*", logs["Resource"]["Fn::Sub"])
        region_assertion = TEMPLATE["Rules"]["StockholmOnly"]["Assertions"][0]["Assert"]
        self.assertEqual(region_assertion, {"Fn::Equals": [{"Ref": "AWS::Region"}, "eu-north-1"]})

    def test_schedule_stays_disabled_until_enabled_and_delivery_is_bounded(self):
        parameter = TEMPLATE["Parameters"]["ScheduleState"]
        self.assertEqual(parameter["Default"], "DISABLED")
        self.assertEqual(set(parameter["AllowedValues"]), {"DISABLED", "ENABLED"})
        schedule = properties("CollectionSchedule")
        self.assertEqual(schedule["State"], {"Ref": "ScheduleState"})
        self.assertEqual(schedule["ScheduleExpression"], "rate(1 minute)")
        self.assertEqual(schedule["FlexibleTimeWindow"], {"Mode": "OFF"})
        self.assertEqual(schedule["GroupName"], {"Ref": "CollectionScheduleGroup"})
        self.assertEqual(schedule["Target"]["Arn"], {"Fn::GetAtt": ["CollectorFunction", "Arn"]})
        self.assertEqual(schedule["Target"]["RetryPolicy"], {
            "MaximumEventAgeInSeconds": 60, "MaximumRetryAttempts": 1,
        })
        asynchronous = properties("CollectorAsyncConfiguration")
        self.assertEqual(asynchronous["FunctionName"], {"Ref": "CollectorFunction"})
        self.assertEqual(asynchronous["Qualifier"], "$LATEST")
        self.assertEqual(asynchronous["MaximumEventAgeInSeconds"], 60)
        self.assertEqual(asynchronous["MaximumRetryAttempts"], 0)
        self.assertIn("CollectorAsyncConfiguration", RESOURCES["CollectionSchedule"]["DependsOn"])

    def test_roles_grant_only_collector_access_and_scope_scheduler_trust_to_its_group(self):
        trust = properties("SchedulerRole")["AssumeRolePolicyDocument"]["Statement"]
        self.assertEqual(len(trust), 1)
        self.assertEqual(trust[0]["Principal"], {"Service": "scheduler.amazonaws.com"})
        self.assertEqual(trust[0]["Condition"]["StringEquals"], {
            "aws:SourceAccount": {"Ref": "AWS::AccountId"},
            "aws:SourceArn": {"Fn::GetAtt": ["CollectionScheduleGroup", "Arn"]},
        })
        invocation = properties("SchedulerRole")["Policies"][0]["PolicyDocument"]["Statement"]
        self.assertEqual(invocation, [{
            "Effect": "Allow", "Action": ["lambda:InvokeFunction"],
            "Resource": {"Fn::GetAtt": ["CollectorFunction", "Arn"]},
        }])
        self.assertEqual(properties("CollectorRole")["AssumeRolePolicyDocument"]["Statement"][0]["Principal"], {"Service": "lambda.amazonaws.com"})


class MonitoringTests(unittest.TestCase):
    def test_metrics_reuse_logs_without_dimensions_or_s3_request_monitoring(self):
        expected = {
            "PersistedObservationFilter": ('"persisted" "status=observed"', "PersistedObservations"),
            "PollingHaltFilter": ('"collection skipped" "reason=halted:"', "PollingHalts"),
        }
        filters = {name for name, resource in RESOURCES.items()
                   if resource["Type"] == "AWS::Logs::MetricFilter"}
        self.assertEqual(filters, set(expected))
        for name, (pattern, metric) in expected.items():
            config = properties(name)
            self.assertEqual(config["LogGroupName"], {"Ref": "CollectorLogGroup"})
            self.assertEqual(config["FilterPattern"], pattern)
            self.assertEqual(config["MetricTransformations"], [{
                "MetricNamespace": {"Fn::Sub": "TornRedeye/${AWS::StackName}"},
                "MetricName": metric, "MetricValue": "1", "DefaultValue": 0, "Unit": "Count",
            }])
        self.assertNotIn("MetricsConfigurations", properties("EvidenceBucket"))

    def test_heartbeat_window_missing_data_and_recovery_contract(self):
        alarm = properties("MissingObservationsAlarm")
        self.assertEqual(alarm["ComparisonOperator"], "LessThanThreshold")
        self.assertEqual(alarm["Threshold"], 1)
        self.assertEqual(alarm["EvaluationPeriods"], 15)
        self.assertEqual(alarm["DatapointsToAlarm"], 15)
        self.assertEqual(alarm["TreatMissingData"], "breaching")
        metric, expression = alarm["Metrics"]
        self.assertEqual(metric["Id"], "observations")
        self.assertEqual(metric["MetricStat"]["Period"], 60)
        self.assertEqual(metric["MetricStat"]["Stat"], "Sum")
        self.assertEqual(metric["MetricStat"]["Metric"]["MetricName"], "PersistedObservations")
        self.assertFalse(metric["ReturnData"])
        self.assertEqual(expression, {
            "Id": "heartbeat", "Expression": "FILL(observations, 0)", "ReturnData": True,
        })
        # verify the window contract; AWS ingestion/evaluation timing needs a live check
        def breaches(window):
            return sum((value or 0) < alarm["Threshold"] for value in window[-15:]) >= alarm["DatapointsToAlarm"]
        self.assertFalse(breaches([1] + [None] * 14))
        self.assertTrue(breaches([None] * 15))
        self.assertTrue(breaches([0] * 15))
        self.assertFalse(breaches([None] * 15 + [1]))

    def test_failure_and_halt_alarms_and_schedule_aware_notification_actions(self):
        errors = properties("CollectionErrorsAlarm")
        self.assertEqual(errors["Namespace"], "AWS/Lambda")
        self.assertEqual(errors["MetricName"], "Errors")
        self.assertEqual(errors["Dimensions"], [{"Name": "FunctionName", "Value": {"Ref": "CollectorFunction"}}])
        self.assertEqual(properties("PollingHaltAlarm")["MetricName"], "PollingHalts")
        for name, periods, breaching_periods in (
            ("CollectionErrorsAlarm", 5, 3), ("PollingHaltAlarm", 1, 1),
        ):
            config = properties(name)
            self.assertEqual(config["Period"], 60)
            self.assertEqual(config["Statistic"], "Sum")
            self.assertEqual(config["EvaluationPeriods"], periods)
            self.assertEqual(config["DatapointsToAlarm"], breaching_periods)
            self.assertEqual(config["Threshold"], 1)
            self.assertEqual(config["ComparisonOperator"], "GreaterThanOrEqualToThreshold")
            self.assertEqual(config["TreatMissingData"], "notBreaching")
        for name in ("MissingObservationsAlarm", "CollectionErrorsAlarm", "PollingHaltAlarm"):
            config = properties(name)
            self.assertIn("MonitoringTopicPolicy", RESOURCES[name]["DependsOn"])
            self.assertEqual(config["ActionsEnabled"], {"Fn::If": ["MonitoringActionsEnabled", True, False]})
            for action in ("AlarmActions", "OKActions"):
                self.assertEqual(config[action], [{"Ref": "MonitoringTopic"}])
        self.assertEqual(TEMPLATE["Conditions"]["MonitoringActionsEnabled"], {
            "Fn::Equals": [{"Ref": "ScheduleState"}, "ENABLED"],
        })

    def test_error_alarm_requires_three_breaching_minutes_in_five(self):
        alarm = properties("CollectionErrorsAlarm")

        # check the configured threshold; service timing is verified after deployment
        def breaches(window):
            return sum((value or 0) >= alarm["Threshold"]
                       for value in window[-alarm["EvaluationPeriods"]:]) >= alarm["DatapointsToAlarm"]

        self.assertFalse(breaches([None] * 5))
        self.assertFalse(breaches([0, 0, 0, 1, 1]))
        self.assertFalse(breaches([0, 0, 0, 0, 3]))
        self.assertTrue(breaches([1, 0, 1, 0, 1]))
        self.assertFalse(breaches([1, 0, 1, 0, 1, 0]))

    def test_private_configurable_destination_and_scoped_publish_permission(self):
        self.assertEqual(TEMPLATE["Parameters"]["OperatorEmail"]["Default"], "")
        self.assertTrue(TEMPLATE["Parameters"]["OperatorEmail"]["NoEcho"])
        self.assertEqual(RESOURCES["OperatorSubscription"]["Condition"], "HasOperatorEmail")
        self.assertEqual(properties("OperatorSubscription"), {
            "Protocol": "email", "Endpoint": {"Ref": "OperatorEmail"},
            "TopicArn": {"Ref": "MonitoringTopic"},
        })
        policy = properties("MonitoringTopicPolicy")["PolicyDocument"]["Statement"]
        self.assertEqual(len(policy), 1)
        statement = policy[0]
        self.assertEqual(statement["Principal"], {"Service": "cloudwatch.amazonaws.com"})
        self.assertEqual(statement["Action"], "sns:Publish")
        self.assertEqual(statement["Resource"], {"Ref": "MonitoringTopic"})
        self.assertEqual(statement["Condition"]["StringEquals"], {
            "aws:SourceAccount": {"Ref": "AWS::AccountId"},
        })
        permitted = statement["Condition"]["ArnEquals"]["aws:SourceArn"]
        names = [properties(name)["AlarmName"]["Fn::Sub"] for name in
                 ("MissingObservationsAlarm", "CollectionErrorsAlarm", "PollingHaltAlarm")]
        self.assertEqual([arn["Fn::Sub"].split(":alarm:")[1] for arn in permitted], names)
        self.assertTrue(all(arn["Fn::Sub"].startswith(
            "arn:${AWS::Partition}:cloudwatch:${AWS::Region}:${AWS::AccountId}:alarm:") for arn in permitted))


class PackageTests(unittest.TestCase):
    def test_package_contains_only_required_modules_and_loads_without_sdk_or_repo(self):
        self.assertEqual(properties("CollectorFunction")["CodeUri"], ".aws-sam/collector.zip")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "collector.zip"
            package_lambda.package(destination)
            with ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "lambda_function.py", "s3_persistence.py", "yata_collector.py", "polling_guard.py",
                })
                self.assertIsNone(archive.testzip())
                for name in archive.namelist():
                    self.assertEqual(archive.read(name), (ROOT / name).read_bytes())
            code = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import lambda_function, s3_persistence, yata_collector, polling_guard; "
                "assert all(m.__file__.startswith(sys.argv[1]) "
                "for m in (lambda_function, s3_persistence, yata_collector, polling_guard))"
            )
            checked = subprocess.run(
                [sys.executable, "-I", "-B", "-c", code, str(destination)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stderr)
            original = destination.read_bytes()
            package_lambda.package(destination)
            self.assertEqual(destination.read_bytes(), original)

    def test_missing_source_does_not_replace_last_valid_package(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "collector.zip"
            package_lambda.package(destination)
            original = destination.read_bytes()
            with patch.object(package_lambda, "MODULES", ("missing_module.py",)):
                with self.assertRaises(FileNotFoundError):
                    package_lambda.package(destination)
            self.assertEqual(destination.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
