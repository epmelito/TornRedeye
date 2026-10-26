"""Empty-anchor trust and bounded complete metadata reconciliation."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import evidence_backup as backup
import incremental_backup as inc
from test_incremental_transfer import RAW

class CatalogTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.anchor=inc.empty_anchor("synthetic-private-bucket")
        self.pin=backup._digest(self.anchor)
        inc.reconstruct_coverage(self.anchor,[],trusted_anchor_sha256=self.pin,output_directory=self.root/"coverage")

    def test_empty_anchor_cannot_claim_historical_coverage(self):
        self.assertEqual(inc.load_coverage(self.root/"coverage")["batches"],{})
        with self.assertRaises(inc.CatalogError): inc.validate_anchor(self.anchor,"0"*64)
        changed=dict(self.anchor,prefixes=["raw/yata/jap/206/"])
        with self.assertRaises(inc.CatalogError):inc.validate_anchor(changed,backup._digest(changed))

    def test_mutable_and_unknown_keys_are_rejected(self):
        for key in ("forecast-state/live-v1/epoch-1/head.json", "raw/yata/jap/206/x.bin",backup.PREFIXES[0]+"unknown"):
            with self.assertRaises(inc.CatalogError):inc.key_parts(key)
        self.assertEqual(inc.source_key(*inc.key_parts(RAW)),RAW)

    def test_requested_measurement_keys_preserve_strict_backup_scope(self):
        root = backup.PREFIXES[1] + "requested-test/requests/" + "a" * 64 + "/"
        for kind in ("forecast", "ack", "outcome"):
            key = root + kind + ".json"
            self.assertEqual(inc.source_key(*inc.key_parts(key)), key)
        for key in (root + "head.json", root + "checkpoint.json",
                    root.replace("a" * 64, "short") + "forecast.json",
                    root.replace("requested-test", "epoch-1") + "forecast.json",
                    root.replace("/requests/", "/other/") + "forecast.json"):
            with self.subTest(key=key), self.assertRaises(inc.CatalogError):
                inc.key_parts(key)

    def test_incomplete_pages_and_unknown_objects_prevent_global_completion(self):
        pages={p:[{"request_token":None,"next_token":None,"objects":[]}] for p in backup.PREFIXES}
        for index, change in enumerate((lambda p:p.pop(backup.PREFIXES[1]), lambda p:p[backup.PREFIXES[0]][0].update(next_token="lost"), lambda p:p[backup.PREFIXES[0]][0].update(request_token="wrong"))):
            broken=deepcopy(pages);change(broken)
            result=inc.plan_reconciliation(self.root/"coverage",broken,output_directory=self.root/str(index))
            self.assertFalse(result["complete"])
            with self.assertRaises(inc.CatalogError):inc.read_plan_partition(self.root/str(index),"00")
