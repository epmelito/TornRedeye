"""Acquisition, validation and availability have independent meanings."""
from dataclasses import replace
import json
import unittest
from test_s3_persistence import NOW, RAW
from yata_collector import normalize
from tornredeye.normalization import normalize_collection, normalized_target_fields

class IntegrationTests(unittest.TestCase):
    def test_unknown_country_time_and_observed_zero_are_not_conflated(self):
        result = normalize(b'{"timestamp":1,"stocks":{"haw":{"update":0,"stocks":[{"id":1485,"name":"Fin","quantity":0,"cost":0}]}}}', NOW)
        doc = json.loads(normalize_collection(result, raw_evidence_id="s3://private/raw"))
        fields = normalized_target_fields(doc, NOW, "haw", 1485)
        self.assertEqual(doc["acquisition"]["status"], "success")
        self.assertEqual(doc["validation_status"], "complete")
        self.assertEqual(fields["quantity"], 0)
        self.assertIn("source_time_unknown", fields["issues"])
        self.assertIsNone(normalized_target_fields(doc, NOW, "can", 206)["quantity"])

    def test_parseable_partial_transport_cannot_become_stock(self):
        result = replace(normalize(RAW, NOW), status="collection_failed", detail="partial response")
        doc = json.loads(normalize_collection(result, raw_evidence_id="s3://private/raw"))
        self.assertEqual(doc["countries"], {})
        self.assertEqual(doc["validation_status"], "collection_failed")
        self.assertEqual(doc["acquisition"]["detail"], "partial response")
