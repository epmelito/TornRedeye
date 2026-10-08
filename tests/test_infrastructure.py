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

    def test_iam_and_expiration_match_actual_persistence_calls_and_leave_normalized_history(self):
        s3 = Mock()
        raw = b'{"timestamp":1,"stocks":{"jap":{"update":1,"stocks":[{"id":206,"name":"Xanax","quantity":0,"cost":800000}]}}}'
        result = normalize(raw, datetime(2026, 10, 8, tzinfo=timezone.utc))
        receipt = persist(result, s3=s3, bucket="test-evidence", collection_id=UUID(int=1))
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
        lifecycle = properties("EvidenceBucket")["LifecycleConfiguration"]["Rules"]
        enabled = [rule for rule in lifecycle if rule["Status"] == "Enabled"]
        matching_raw = [rule for rule in enabled if receipt.raw_key.startswith(rule["Prefix"])]
        self.assertEqual(len(matching_raw), 1)
        self.assertEqual(matching_raw[0]["ExpirationInDays"], 60)
        self.assertFalse(any(receipt.normalized_key.startswith(rule["Prefix"]) for rule in enabled))
        actions = {action for statement in statements for action in statement["Action"]}
        self.assertEqual(actions, {"s3:PutObject", "s3:GetObject", "logs:CreateLogStream", "logs:PutLogEvents"})
        self.assertTrue(all(statement["Effect"] == "Allow" for statement in statements))

    def test_lambda_uses_handler_configuration_bounded_execution_and_existing_log_group(self):
        function = properties("CollectorFunction")
        self.assertEqual(function["Handler"], "lambda_function.lambda_handler")
        self.assertEqual(function["Runtime"], "python3.13")
        self.assertGreater(function["Timeout"], 15)
        self.assertLess(function["Timeout"], 300)
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
        self.assertEqual(schedule["ScheduleExpression"], "rate(5 minutes)")
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


class PackageTests(unittest.TestCase):
    def test_package_contains_only_required_modules_and_loads_without_sdk_or_repo(self):
        self.assertEqual(properties("CollectorFunction")["CodeUri"], ".aws-sam/collector.zip")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "collector.zip"
            package_lambda.package(destination)
            with ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()), {
                    "lambda_function.py", "s3_persistence.py", "yata_collector.py",
                })
                self.assertIsNone(archive.testzip())
                for name in archive.namelist():
                    self.assertEqual(archive.read(name), (ROOT / name).read_bytes())
            code = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import lambda_function, s3_persistence, yata_collector; "
                "assert all(m.__file__.startswith(sys.argv[1]) "
                "for m in (lambda_function, s3_persistence, yata_collector))"
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
