"""Offline deployment boundaries; these checks do not attest AWS plan acceptance."""

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def template(name):
    return json.loads((ROOT / name).read_text())


class CloudFrontReadinessTests(unittest.TestCase):
    def test_candidate_is_opt_in_and_keeps_rollback_policies(self):
        t = template("dashboard-template.json")
        r = t["Resources"]
        self.assertEqual(t["Parameters"]["FreePlanCandidate"]["Default"], "DISABLED")
        self.assertEqual(t["Parameters"]["WebAclArn"]["Default"], "")
        d = r["Distribution"]["Properties"]["DistributionConfig"]
        self.assertEqual(d["Enabled"], {"Fn::If": ["DeliveryEnabled", True, False]})
        self.assertEqual(d["DefaultRootObject"], "index.html")
        self.assertEqual(d["PriceClass"], {"Fn::If": ["UseFreePlanCandidate", "PriceClass_All", "PriceClass_100"]})
        b = d["DefaultCacheBehavior"]
        self.assertEqual(b["CachePolicyId"], {"Fn::If": ["UseFreePlanCandidate",
            "658327ea-f89d-4fab-a63d-7e88639e58f6", {"Ref": "CachePolicy"}]})
        self.assertEqual(b["ResponseHeadersPolicyId"], {"Fn::If": ["UseFreePlanCandidate",
            "67f7725c-6f97-4210-82d7-5512b31e9d03", {"Ref": "SecurityHeaders"}]})
        self.assertEqual(b["FunctionAssociations"]["Fn::If"][0], "UseFreePlanCandidate")
        self.assertEqual(b["FunctionAssociations"]["Fn::If"][2], {"Ref": "AWS::NoValue"})
        for name in ("CachePolicy", "SecurityHeaders"):
            self.assertNotIn("Condition", r[name])
        cache = r["CachePolicy"]["Properties"]["CachePolicyConfig"]
        self.assertEqual([cache[k] for k in ("MinTTL", "DefaultTTL", "MaxTTL")], [0, 300, 3600])
        self.assertEqual(t["Rules"]["CandidateRequiresWebAcl"]["Assertions"][0]["Assert"],
                         {"Fn::Not": [{"Fn::Equals": [{"Ref": "WebAclArn"}, ""]}]})
        function = r["ViewerSecurityHeaders"]
        self.assertEqual(function["Condition"], "UseFreePlanCandidate")
        self.assertTrue(function["Properties"]["AutoPublish"])
        self.assertEqual(function["Properties"]["FunctionConfig"]["Runtime"], "cloudfront-js-2.0")
        self.assertNotIn("AWS::PricingPlanManager::Subscription", {v["Type"] for v in r.values()})

    def test_dedicated_waf_has_count_first_ip_rule_and_no_paid_extras(self):
        t = template("cloudfront-waf-template.json")
        self.assertEqual(t["Parameters"]["ProvisionWebAcl"]["Default"], "DISABLED")
        self.assertEqual(t["Parameters"]["RateAction"]["Default"], "COUNT")
        self.assertEqual(t["Parameters"]["RequestsPerFiveMinutes"]["Default"], 3000)
        self.assertEqual(set(t["Resources"]), {"WebAcl"})
        acl = t["Resources"]["WebAcl"]
        self.assertEqual(acl["Condition"], "CreateWebAcl")
        self.assertNotIn("DeletionPolicy", acl)
        p = acl["Properties"]
        self.assertEqual(p["Scope"], "CLOUDFRONT")
        self.assertEqual(p["DefaultAction"], {"Allow": {}})
        self.assertEqual(len(p["Rules"]), 1)
        rule = p["Rules"][0]
        self.assertEqual(rule["Name"], "SourceIpRate")
        self.assertEqual(rule["Priority"], 1)
        self.assertEqual(rule["Action"], {"Fn::If": ["EnforceRate", {"Block": {}}, {"Count": {}}]})
        self.assertEqual(rule["Statement"], {"RateBasedStatement": {
            "AggregateKeyType": "IP", "EvaluationWindowSec": 300,
            "Limit": {"Ref": "RequestsPerFiveMinutes"}}})
        self.assertFalse(rule["VisibilityConfig"]["SampledRequestsEnabled"])
        self.assertNotIn("CustomResponseBodies", p)
        self.assertNotIn("AssociationConfig", p)
        self.assertEqual(t["Rules"]["VirginiaOnly"]["Assertions"][0]["Assert"],
                         {"Fn::Equals": [{"Ref": "AWS::Region"}, "us-east-1"]})

    def test_waf_excludes_free_tier_incompatible_byte_match_and_path_controls(self):
        t = template("cloudfront-waf-template.json")
        self.assertNotIn("PathAction", t["Parameters"])
        self.assertNotIn("EnforcePaths", t["Conditions"])
        for incompatible in ("ByteMatchStatement", "UnexpectedPublicPath"):
            self.assertNotIn(incompatible, json.dumps(t))


if __name__ == "__main__":
    unittest.main()
