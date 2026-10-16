import copy
import json
from pathlib import Path
import uuid
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from devtools.demo import demo, stage
from common.sanity_common import digest, unpack_bundle, pack_json

from common.sanity_db import Database, Queue
from tests.support import PgTestCase


class PipelineTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_two_formats_grouping_dashboard_and_human_finality(self):
        receipt = demo(self.root / "complete")
        root = self.root / "complete"
        self.assertEqual(receipt["interface"], "OA_DASHBOARD")
        self.assertIs(receipt["jira_enabled"], False)
        self.assertNotIn("publication", receipt)
        with Database("oa") as db:
            payload = unpack_bundle(Queue(db, "vwp_to_oa").get(receipt["bundle_id"]))
        units = {u["review_unit_id"]: u for u in payload["review_units"]}
        self.assertEqual(units["u-numeric"]["record_count"], 4)
        self.assertEqual(units["u-summary"]["record_count"], 2)
        groups = [g for g in payload["finding_groups"] if g["review_unit_id"] == "u-numeric"]
        self.assertEqual(sorted(g["occurrence_count"] for g in groups), [1, 2])
        self.assertEqual({g["identity"]["object"] for g in groups}, {"top/A", "top/B"})
        rows = self.admin_query("SELECT payload FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE'",
                                (uuid.UUID(receipt["analysis_id"]),)).fetchall()
        self.assertEqual(6, len(rows))
        self.assertEqual(2, sum(row[0]["check_status"] == "PASS" for row in rows))
        self.assertFalse(list(root.rglob("*.sqlite*")))
        self.assertFalse((root / "oa-inbox").exists())
        c = receipt["coverage"]
        self.assertEqual((c["expected"], c["approved_na"], c["reviewed"], c["unresolved"]), (3, 1, 0, 2))
        self.assertEqual(c["review_status"], "INCOMPLETE")
        args = ["--bundle-id", receipt["bundle_id"]]
        self.assertEqual({"RECEIVED_BUNDLE", "REVIEW_UNIT", "REVIEW_EVENT", "REPORT"},
                         {row[0] for row in self.admin_query("SELECT DISTINCT record_type FROM {oa}").fetchall()})
        for unit_id in ("u-numeric", "u-summary"):
            stage("oa", "review", *args, "--unit-id", unit_id,
                  "--request-id", str(uuid.uuid4()), "--reviewer", "test-reviewer", "--conclusion", "FAIL", "--reason", "synthetic evidence checked")
        status = json.loads(stage("oa", "status", *args))
        self.assertEqual(status["coverage"]["review_status"], "COMPLETE")
        self.assertEqual(status["coverage"]["check_status"], "FAIL")
        self.assertEqual(status["context"], {"ai": {"status": "NOT_CONFIGURED"}})
        self.assertEqual(status["interface"], "OA_DASHBOARD")
        self.assertIs(status["jira_enabled"], False)
        self.assertNotIn("simulated_post_count", status)
        self.assertIn("u-excluded", (root / "report.md").read_text())
        report = (root / "report.md").read_text()
        self.assertIn("OA 대시보드 검토 보고서", report)
        self.assertNotIn("Jira", report)
        self.assertIn("임계값: 10", report)
        self.assertIn("JSON 위치: /results/1", report)
        self.assertIn("reports/numeric.csv", report)
        self.assertEqual(units["u-numeric"]["evaluation"]["limit"], 10)

    def test_incomplete_scenarios_keep_all_units(self):
        for scenario in ("missing", "malformed", "unsupported"):
            with self.subTest(scenario=scenario):
                receipt = demo(self.root / scenario, scenario)
                c = receipt["coverage"]
                self.assertEqual(c["expected"], 3)
                self.assertEqual(c["review_status"], "INCOMPLETE")
                self.assertEqual(c["missing"], int(scenario == "missing"))
                self.assertTrue(any(not u["analysis_ready"] for u in c["units"]))
                self.assertTrue(Path(receipt["report"]).is_file())

    def test_receive_cli_rejects_semantic_forgery_without_creating_state(self):
        source = self.root / "source"
        stage("vwp", "demo", "--out", str(source))
        export = self.root / "export"
        receipt = json.loads(stage("vwp_bridge", "analyze", "--input", str(source / "run.json"),
              "--out", str(export)))
        with Database("vwp") as db:
            original = unpack_bundle(Queue(db, "vwp_to_oa").get(receipt["message_id"]))
        for variant in ("wrong_report", "incomplete_identity"):
            with self.subTest(variant=variant):
                payload = copy.deepcopy(original)
                group = next(g for g in payload["finding_groups"]
                             if g["review_unit_id"] == "u-numeric")
                if variant == "wrong_report":
                    other = next(f for f in payload["source_files"]
                                 if f["path"] == "reports/summary.json")
                    group["examples"][0]["file_id"] = other["file_id"]
                else:
                    del group["identity"]["mode"]
                del payload["bundle_id"]
                payload["bundle_id"] = "b-" + digest(payload)
                # Valid compressed checksum and ID must not hide invalid semantics.
                with Database("vwp") as db, db.transaction():
                    Queue(db, "vwp_to_oa").enqueue({
                        "message_id": payload["bundle_id"], "message_type": "ANALYSIS_BUNDLE",
                        "bundle_id": payload["bundle_id"], "project": payload["run"]["project"],
                        "run_id": payload["run"]["run_id"], "payload": {"schema_version": 1},
                        **pack_json(payload)})
                result = subprocess.run([sys.executable, str(ROOT / "oa/main.py"),
                                         "receive", "--message-id", payload["bundle_id"]],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("code=INVALID_SCHEMA", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(0, self.admin_query("SELECT count(*) FROM {oa}").fetchone()[0])
                self.assertEqual("REJECTED", self.admin_query("SELECT status FROM {vwp_to_oa} WHERE message_id=%s",
                                  (payload["bundle_id"],)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
