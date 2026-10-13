"""Run with: python3 -m unittest discover -s oa-bridge -p 'test_*.py'."""
import argparse
import importlib.util
import io
import copy
import uuid
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("oa_main", Path(__file__).with_name("main.py"))
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)
from sanity_common import ReviewError, digest, pack_json

from sanity_db import Database, Queue
from sanity_oa_store import OAStore
from tests_support import PgTestCase


def payload(bundle_id="b-test"):
    return {
        "schema_version": 1, "bundle_id": bundle_id,
        "run": {"run_id": "run-test", "project": "demo", "design": "chip",
                "stage": "sanity", "tool": "synthetic", "tool_version": bundle_id},
        "inventory": {"confirmed": True, "source": "plan.json", "version": "1",
                      "sha256": "0" * 64, "expected_unit_ids": ["u-numeric"]},
        "review_units": [{"review_unit_id": "u-numeric", "item_id": "numeric_limits",
                          "report": "reports/numeric.csv", "execution_status": "COMPLETED",
                          "applicable": True, "exclusion": None, "parse_status": "COMPLETE",
                          "check_status": "FAIL", "blockers": [], "record_count": 1,
                          "evaluated_count": 1, "unknown_count": 0,
                          "status_counts": {"PASS": 0, "FAIL": 1, "UNKNOWN": 0},
                          "identity_fields": ["object", "check_point", "mode"],
                          "rule_version": "1"}],
        "finding_groups": [{"finding_group_id": "g-test", "review_unit_id": "u-numeric",
                            "item_id": "numeric_limits", "message_code": "CHECK-101",
                            "severity": "WARNING", "identity": {"object": "top/A",
                            "check_point": "P1", "mode": "M1"},
                            "identity_status": "SAME_FINDING", "rule_version": "1",
                            "occurrence_count": 1, "status_counts": {"PASS": 0, "FAIL": 1,
                            "UNKNOWN": 0}, "examples": [{"record_id": "r-1", "file_id": "f-1",
                            "line_start": 2, "line_end": 2, "raw": "synthetic failure",
                            "observed": 12, "expected": 10, "unit": "ns"}],
                            "examples_omitted": 0}],
        "global_blockers": [], "source_files": [{"file_id": "f-1",
                          "path": "reports/numeric.csv", "size_bytes": 50,
                          "sha256": "0" * 64}], "producer": {"version": "prototype-1"}}


class OATests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.bundle_id = None
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def args(self, **kwargs):
        return argparse.Namespace(**dict({"bundle_id": self.bundle_id}, **kwargs))

    def receive(self, value=None, fixture=None):
        value = copy.deepcopy(value or payload())
        value.pop("bundle_id", None)
        value["bundle_id"] = "b-" + digest(value)
        self.bundle_id = value["bundle_id"]
        with Database("vwp") as db, db.transaction():
            Queue(db, "vwp_to_oa").enqueue({
                "message_id": self.bundle_id, "message_type": "ANALYSIS_BUNDLE",
                "project": value["run"]["project"], "run_id": value["run"]["run_id"],
                "bundle_id": self.bundle_id, "payload": {"schema_version": 1,
                    "inventory_sha256": value["inventory"]["sha256"],
                    "producer_version": value["producer"]["version"]}, **pack_json(value)})
        return oa.receive(self.args(message_id=self.bundle_id, jira_context=fixture))

    def review(self, conclusion="FAIL", request_id=None):
        return oa.review(self.args(unit_id="u-numeric", reviewer="kim", reason="checked",
                                   conclusion=conclusion, request_id=request_id or str(uuid.uuid4())))

    def snapshot(self):
        return self.admin_query("SELECT * FROM {oa} ORDER BY record_id").fetchall()

    def receive_pass(self):
        value = payload()
        value["review_units"][0]["check_status"] = "PASS"
        value["review_units"][0]["status_counts"] = {"PASS": 1, "FAIL": 0, "UNKNOWN": 0}
        value["finding_groups"] = []
        self.receive(value)

    def test_a_b_a_review_retains_each_event_and_report_revision(self):
        self.receive_pass()
        revisions = []
        snapshots = []
        for conclusion in ("FAIL", "PASS", "FAIL"):
            revisions.append(self.review(conclusion)["review_revision"])
            snapshots.append(oa.report(self.args(out=None)))
        self.assertEqual(revisions, [1, 2, 3])
        self.assertEqual(snapshots[0], snapshots[2])
        self.assertNotEqual(snapshots[0], snapshots[1])
        self.assertEqual(oa.status(self.args())["review_revision"], 3)
        events = self.admin_query("SELECT review_revision,payload FROM {oa} "
                                  "WHERE record_type='REVIEW_EVENT' ORDER BY review_revision").fetchall()
        self.assertEqual([row[0] for row in events], [0, 1, 2, 3])
        self.assertEqual([row[1]["decisions"][0]["conclusion"] for row in events[1:]],
                         ["FAIL", "PASS", "FAIL"])
        reports = self.admin_query("SELECT review_revision,payload_bytes FROM {oa} "
                                   "WHERE record_type='REPORT' ORDER BY review_revision").fetchall()
        self.assertEqual([row[0] for row in reports], [0, 1, 2, 3])
        self.assertEqual([bytes(row[1]).decode("utf-8") for row in reports[1:]], snapshots)

    def test_review_failure_rolls_back_event_report_and_revision(self):
        self.receive_pass()
        before = self.snapshot()
        with patch.object(OAStore, "save_report", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.review()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(0, oa.status(self.args())["review_revision"])

    def test_review_request_id_replay_and_conflict(self):
        self.receive_pass()
        request_id = str(uuid.uuid4())
        self.assertEqual("REVIEWED", self.review("FAIL", request_id)["status"])
        self.assertEqual("ALREADY_REVIEWED", self.review("FAIL", request_id)["status"])
        with self.assertRaises(ReviewError) as err:
            self.review("PASS", request_id)
        self.assertEqual("REQUEST_CONFLICT", err.exception.code)

    def test_stored_inventory_or_report_damage_is_not_silently_repaired(self):
        self.receive()
        self.admin_query("DELETE FROM {oa} WHERE record_type='REVIEW_UNIT'")
        before = self.snapshot()
        with self.assertRaises(ReviewError) as err:
            oa.status(self.args())
        self.assertEqual("STATE_CORRUPT", err.exception.code)
        self.assertEqual(before, self.snapshot())

    def test_retired_publication_modes_reject_without_state_change(self):
        self.receive()
        before = self.snapshot()
        for dry_run in (False, True):
            for simulate in (False, True):
                with self.subTest(dry_run=dry_run, simulate=simulate):
                    with self.assertRaises(ReviewError) as err:
                        oa.publish(self.args(dry_run=dry_run, simulate=simulate))
                    self.assertEqual("JIRA_DISABLED", err.exception.code)
                    with Database("oa") as db, db.transaction():
                        with self.assertRaises(ReviewError) as err:
                            OAStore(db).publish(self.bundle_id, dry_run, simulate)
                        self.assertEqual("JIRA_DISABLED", err.exception.code)
                    self.assertEqual(before, self.snapshot())

    def test_legacy_jira_context_rejects_before_claiming_message(self):
        with patch.object(oa, "Database", side_effect=AssertionError("must not access OA state")):
            with self.assertRaises(ReviewError) as err:
                self.receive(fixture=self.root / "retired-jira.json")
        self.assertEqual("JIRA_DISABLED", err.exception.code)
        self.assertEqual([], self.snapshot())
        self.assertEqual(("READY", 0), self.admin_query(
            "SELECT status,attempt_count FROM {vwp_to_oa} WHERE message_id=%s", (self.bundle_id,)).fetchone())
        self.assertEqual("RECEIVED", self.receive()["status"])

    def test_receive_is_idempotent_without_publication_records(self):
        self.assertEqual(self.receive()["status"], "RECEIVED")
        before = self.snapshot()
        self.assertEqual(self.receive()["status"], "ALREADY_RECEIVED")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({"RECEIVED_BUNDLE", "REVIEW_UNIT", "REVIEW_EVENT", "REPORT"},
                         {row[0] for row in self.admin_query("SELECT DISTINCT record_type FROM {oa}").fetchall()})
        self.assertEqual(0, oa.status(self.args())["review_revision"])

    def test_dashboard_reads_preserve_state_and_review_completion(self):
        self.receive()
        before = self.snapshot()
        first = oa.report(self.args(out=None))
        self.assertEqual(first, oa.report(self.args(out=None)))
        result = oa.status(self.args())
        self.assertEqual(result["coverage"]["review_status"], "INCOMPLETE")
        self.assertEqual(result["decisions"], {})
        self.assertEqual(self.snapshot(), before)

    def test_review_complete_is_not_sanity_pass(self):
        self.receive()
        with self.assertRaises(ReviewError) as err:
            self.review("PASS")
        self.assertEqual(err.exception.code, "FAIL_CANNOT_PASS")
        request_id = str(uuid.uuid4())
        result = self.review(request_id=request_id)
        self.assertEqual(result["coverage"]["review_status"], "COMPLETE")
        self.assertEqual(result["coverage"]["check_status"], "FAIL")
        self.assertEqual(self.review(request_id=request_id)["status"], "ALREADY_REVIEWED")
        self.receive()
        self.assertEqual(oa.status(self.args())["coverage"]["review_status"], "COMPLETE")

    def test_new_bundle_does_not_inherit_approval(self):
        self.receive()
        self.review()
        self.receive(payload("b-next"))
        args = self.args()
        result = oa.status(args)
        self.assertEqual(result["decisions"], {})
        self.assertEqual(result["coverage"]["review_status"], "INCOMPLETE")

    def test_human_review_cannot_waive_blocker(self):
        value = payload()
        value["global_blockers"] = ["UNEXPECTED_REPORT"]
        self.receive(value)
        with self.assertRaises(ReviewError) as err:
            self.review()
        self.assertEqual(err.exception.code, "REVIEW_BLOCKED")

    def test_review_rejects_missing_source_even_without_transmitted_blocker(self):
        value = payload()
        unit = value["review_units"][0]
        unit["check_status"] = "PASS"
        unit["status_counts"] = {"PASS": 1, "FAIL": 0, "UNKNOWN": 0}
        value["finding_groups"] = []
        value["source_files"] = []
        self.receive(value)
        with self.assertRaises(ReviewError) as err:
            self.review("PASS")
        self.assertEqual(err.exception.code, "REVIEW_BLOCKED")

    def test_context_and_report_use_dashboard_without_implicit_ai_review(self):
        result = self.receive()
        self.assertEqual(result["context"], {"interface": "dashboard-v1", "ai": {"status": "NOT_CONFIGURED"}})
        self.assertEqual(result["coverage"]["review_status"], "INCOMPLETE")
        status = oa.status(self.args())
        self.assertEqual(status["context"], {"ai": {"status": "NOT_CONFIGURED"}})
        self.assertEqual(status["interface"], "OA_DASHBOARD")
        self.assertIs(status["jira_enabled"], False)
        self.assertNotIn("simulated_post_count", status)
        rendered = oa.report(self.args(out=None))
        self.assertIn("OA 대시보드 검토 보고서", rendered)
        self.assertNotIn("Jira", rendered)
        self.assertIn("NOT_CONFIGURED", rendered)
        self.assertIn("synthetic failure", rendered)

    def test_receive_cli_has_no_jira_context_option(self):
        errors = io.StringIO()
        with redirect_stderr(errors), self.assertRaises(SystemExit) as err:
            oa.main(["receive", "--jira-context", str(self.root / "retired-jira.json")])
        self.assertEqual(2, err.exception.code)
        self.assertIn("unrecognized arguments", errors.getvalue())
        self.assertEqual([], self.snapshot())

    def test_review_change_appends_one_report_without_overwriting_prior_revision(self):
        self.receive()
        initial = self.admin_query("SELECT * FROM {oa} WHERE record_type='REPORT' AND review_revision=0").fetchone()
        request_id = str(uuid.uuid4())
        self.review(request_id=request_id)
        reports = self.admin_query("SELECT review_revision FROM {oa} WHERE record_type='REPORT' ORDER BY review_revision").fetchall()
        self.assertEqual([(0,), (1,)], reports)
        self.assertEqual(initial, self.admin_query(
            "SELECT * FROM {oa} WHERE record_type='REPORT' AND review_revision=0").fetchone())
        before = self.snapshot()
        self.assertEqual("ALREADY_REVIEWED", self.review(request_id=request_id)["status"])
        self.assertEqual(before, self.snapshot())
        self.assertIn("**COMPLETE**", oa.report(self.args(out=None)))

    def test_receive_report_failure_rolls_back_records_then_recovers(self):
        with patch.object(OAStore, "save_report", side_effect=RuntimeError("report crash")):
            with self.assertRaises(RuntimeError):
                self.receive()
        self.assertEqual([], self.snapshot())
        self.assertEqual("PROCESSING", self.admin_query("SELECT status FROM {vwp_to_oa}").fetchone()[0])
        self.admin_query("UPDATE {vwp_to_oa} SET lease_expires_at=clock_timestamp()-interval '1 second'")
        self.assertEqual("RECEIVED", self.receive()["status"])
        self.assertEqual(0, oa.status(self.args())["review_revision"])

    def test_same_id_different_payload_is_rejected(self):
        self.receive()
        with self.assertRaises(ReviewError) as err:
            with Database("vwp") as db, db.transaction():
                message = Queue(db, "vwp_to_oa").get(self.bundle_id)
                Queue(db, "vwp_to_oa").enqueue({
                    "message_id": self.bundle_id, "message_type": "ANALYSIS_BUNDLE",
                    "project": message["project"], "run_id": message["run_id"], "bundle_id": self.bundle_id,
                    "payload": {"schema_version": 2}, "payload_gzip": message["payload_gzip"],
                    "payload_sha256": message["payload_sha256"], "uncompressed_bytes": message["uncompressed_bytes"]})
        self.assertEqual(err.exception.code, "MESSAGE_CONFLICT")

    def test_brief_cli_bounds_status_and_retired_publish_error(self):
        self.receive()
        output = io.StringIO()
        with redirect_stdout(output):
            code = oa.main(["status", "--bundle-id", self.bundle_id,
                            "--brief"])
        self.assertEqual(code, 0)
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.assertIn("expected=1", output.getvalue())
        self.assertIn("review=INCOMPLETE", output.getvalue())
        before = self.snapshot()
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = oa.main(["publish", "--bundle-id", self.bundle_id,
                            "--dry-run", "--simulate", "--brief"])
        self.assertEqual(code, 2)
        self.assertEqual(errors.getvalue().strip(), "code=JIRA_DISABLED")
        self.assertEqual(before, self.snapshot())
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = oa.main(["review", "--bundle-id", self.bundle_id,
                            "--unit-id", "u-numeric", "--reviewer", "kim", "--conclusion",
                            "PASS", "--reason", "checked", "--request-id", str(uuid.uuid4()), "--brief"])
        self.assertEqual(code, 2)
        self.assertEqual(errors.getvalue().strip(), "code=FAIL_CANNOT_PASS")


if __name__ == "__main__":
    unittest.main()
