"""Regression checks for coverage and item identity, using synthetic reports only."""
import importlib.util
import json
from pathlib import Path
import uuid
from unittest.mock import patch
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sanity_common import ReviewError, atomic_json, atomic_text, digest, unpack_bundle, sha256_file

from sanity_db import Database, Queue, json_value
from tests_support import PgTestCase

spec = importlib.util.spec_from_file_location("vwp_bridge_main", Path(__file__).with_name("main.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.run_id = 'run-1'
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "reports/numeric.csv"
        self.unit = {"review_unit_id": "u-1", "item_id": "numeric", "report": "reports/numeric.csv",
                     "parser": "numeric_csv_v1", "rule_version": "1", "condition": {"mode": "M1"},
                     "identity_fields": ["object", "check_point", "mode"],
                     "evaluation": {"operator": "le", "limit": 10, "unit": "ns"},
                     "execution_status": "COMPLETED", "applicable": True, "exclusion": None}

    def run_analysis(self, body=None, unit=None, extra_units=None, output="output"):
        if body is not None:
            atomic_text(self.report, body)
        files = []
        if self.report.exists():
            files.append({"file_id": "f-1", "path": "reports/numeric.csv",
                          "size_bytes": self.report.stat().st_size, "sha256": sha256_file(self.report)})
        inventory = {"confirmed": True, "source": "test-plan", "version": "1",
                     "units": [unit or self.unit] + (extra_units or [])}
        inventory["sha256"] = digest(inventory)
        manifest = {"schema_version": 1, "ready": True,
                    "run": {"run_id": self.run_id, "project": "p", "design": "d", "stage": "s",
                            "tool": "test", "tool_version": "1"},
                    "inventory": inventory, "files": files, "unexpected_files": []}
        atomic_json(self.root / "run.json", manifest)
        return bridge.analyze(self.root / "run.json", self.root / output)

    @staticmethod
    def numeric(rows, footer=True):
        return ",".join(bridge.HEADER) + "\n" + "\n".join(rows) + "\n" + (
            "# END rows=" + str(len(rows)) + "\n" if footer else "")

    def test_all_records_evaluated_and_identity_groups(self):
        rows = ["A,P1,M1,C-1,WARNING,12,ns", "A,P1,M1,C-1,WARNING,13,ns",
                "B,P1,M1,C-1,WARNING,12,ns", "C,P1,M1,C-1,WARNING,9,ns"]
        payload, path = self.run_analysis(self.numeric(rows))
        self.assertEqual([1, 2], sorted(g["occurrence_count"] for g in payload["finding_groups"]))
        self.assertEqual({"PASS": 1, "FAIL": 3, "UNKNOWN": 0}, payload["review_units"][0]["status_counts"])
        with Database("vwp") as db:
            self.assertEqual(payload, unpack_bundle(Queue(db, "vwp_to_oa").get(path["message_id"])))
            rows = db.execute("SELECT payload FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE'",
                              (uuid.UUID(path["analysis_id"]),)).fetchall()
            self.assertEqual(4, len(rows))
            self.assertEqual(1, sum(r["payload"]["finding_group_id"] is None and r["payload"]["check_status"] == "PASS" for r in rows))

    def test_missing_identity_isolated_but_numerically_evaluated(self):
        payload, _ = self.run_analysis(self.numeric([",P1,M1,C-1,WARNING,12,ns"] * 2))
        self.assertEqual(2, len(payload["finding_groups"]))
        self.assertTrue(all(g["identity_status"] == "UNDECIDED" for g in payload["finding_groups"]))
        self.assertEqual("COMPLETE", payload["review_units"][0]["parse_status"])

    def test_missing_expected_report_remains(self):
        payload, _ = self.run_analysis()
        unit = payload["review_units"][0]
        self.assertEqual("MISSING", unit["parse_status"])
        self.assertEqual(["u-1"], payload["inventory"]["expected_unit_ids"])

    def test_footer_required_even_when_rows_pass(self):
        payload, _ = self.run_analysis(self.numeric(["A,P1,M1,C-1,WARNING,1,ns"], footer=False))
        self.assertIn("MISSING_END_MARKER", payload["review_units"][0]["blockers"])
        self.assertEqual("UNKNOWN", payload["review_units"][0]["check_status"])

    def test_unit_mode_nonfinite_and_columns_block_evaluation(self):
        rows = ["A,P1,M1,C-1,WARNING,1,ps", "A,P1,M2,C-1,WARNING,1,ns",
                "A,P1,M1,C-1,WARNING,NaN,ns", "A,P1,M1,C-1,WARNING,1,ns,extra"]
        payload, _ = self.run_analysis(self.numeric(rows))
        unit = payload["review_units"][0]
        self.assertEqual(4, unit["unknown_count"])
        for reason in ("UNIT_MISMATCH", "MODE_MISMATCH", "INVALID_MEASUREMENT", "MALFORMED_ROW"):
            self.assertIn(reason, unit["blockers"])

    def test_unknown_parser_preserves_raw(self):
        unit = {**self.unit, "parser": "unknown"}
        payload, _ = self.run_analysis("opaque report\n", unit=unit)
        self.assertEqual("UNSUPPORTED", payload["review_units"][0]["parse_status"])
        self.assertEqual("opaque report", payload["finding_groups"][0]["examples"][0]["raw"])

    def test_summary_total_mismatch_and_bad_status(self):
        unit = {**self.unit, "parser": "summary_json_v1"}
        report = {"format_version": 1, "complete": True, "total": 2,
                  "results": [{"object": "A", "check_point": "P1", "mode": "M1", "code": "C",
                               "severity": "ERROR", "status": "MAYBE"}]}
        payload, _ = self.run_analysis(json.dumps(report), unit=unit)
        self.assertIn("TOTAL_MISMATCH", payload["review_units"][0]["blockers"])
        self.assertEqual(1, payload["review_units"][0]["unknown_count"])

    def test_summary_missing_required_fields_block_completion(self):
        unit = {**self.unit, "parser": "summary_json_v1"}
        report = {"format_version": 1, "complete": True, "total": 1,
                  "results": [{"mode": "M1", "code": "C", "severity": "INFO", "status": "PASS"}]}
        payload, _ = self.run_analysis(json.dumps(report), unit=unit)
        self.assertIn("MISSING_OBJECT", payload["review_units"][0]["blockers"])
        self.assertIn("MISSING_CHECK_POINT", payload["review_units"][0]["blockers"])
        self.assertEqual("UNKNOWN", payload["review_units"][0]["check_status"])

    def test_identical_analysis_is_deterministic_and_samples_bounded(self):
        body = self.numeric(["A,P1,M1,C-1,WARNING,12,ns"] * 8)
        first, path1 = self.run_analysis(body)
        second, path2 = self.run_analysis(body, output="other")
        self.assertEqual(first["bundle_id"], second["bundle_id"])
        self.assertEqual(path1, path2)
        self.assertEqual(1, self.admin_query("SELECT count(*) FROM {vwp_to_oa}").fetchone()[0])
        self.assertEqual(3, len(first["finding_groups"][0]["examples"]))
        self.assertEqual(5, first["finding_groups"][0]["examples_omitted"])

    def test_sealed_evidence_replay_is_read_only(self):
        body = self.numeric(["A,P1,M1,C-1,WARNING,12,ns", "B,P1,M1,C-1,WARNING,1,ns"])
        first, _ = self.run_analysis(body)
        before = self.admin_query("SELECT * FROM {vwp} ORDER BY record_id").fetchall()
        second, _ = self.run_analysis(body)
        self.assertEqual(first, second)
        self.assertEqual(before, self.admin_query("SELECT * FROM {vwp} ORDER BY record_id").fetchall())

    def test_modified_evidence_tables_rejected_and_preserved(self):
        body = self.numeric(["A,P1,M1,C-1,WARNING,12,ns", "B,P1,M1,C-1,WARNING,1,ns"])
        changes = [
            ("UPDATE {vwp} SET state=%s WHERE analysis_id=%s AND record_type='RUN'", json_value({"bundle_id": "wrong"})),
            ("UPDATE {vwp} SET payload=%s WHERE analysis_id=%s AND record_type='EVIDENCE' AND payload->>'check_status'='PASS'", json_value({})),
            ("UPDATE {vwp} SET payload=%s WHERE analysis_id=%s AND record_type='GROUP'", json_value({})),
            ("DELETE FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE' AND payload->>'check_status'='PASS'", None),
        ]
        for index, (query, value) in enumerate(changes):
            with self.subTest(index=index):
                self.run_id = "tampered-" + str(index)
                _, receipt = self.run_analysis(body)
                aid = uuid.UUID(receipt["analysis_id"])
                self.admin_query(query, (aid,) if value is None else (value, aid))
                before = self.admin_query("SELECT * FROM {vwp} WHERE analysis_id=%s ORDER BY record_id", (aid,)).fetchall()
                with self.assertRaises(ReviewError) as err:
                    self.run_analysis(body)
                self.assertEqual("EVIDENCE_CONFLICT", err.exception.code)
                self.assertEqual(before, self.admin_query("SELECT * FROM {vwp} WHERE analysis_id=%s ORDER BY record_id", (aid,)).fetchall())

    def test_interrupted_analysis_resumes_without_duplicate_records(self):
        body = self.numeric(["A,P1,M1,C-1,WARNING,12,ns"] * 300)
        with patch.object(bridge.Evidence, "seal", side_effect=RuntimeError("crash before enqueue")):
            with self.assertRaises(RuntimeError):
                self.run_analysis(body)
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {vwp_to_oa}").fetchone()[0])
        self.assertEqual("BUILDING", self.admin_query("SELECT status FROM {vwp} WHERE record_type='RUN'").fetchone()[0])
        value, _ = self.run_analysis(body)
        self.assertEqual(300, value["review_units"][0]["record_count"])
        self.assertEqual(300, self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='EVIDENCE'").fetchone()[0])
        self.assertEqual("SEALED", self.admin_query("SELECT status FROM {vwp} WHERE record_type='RUN'").fetchone()[0])

    def test_extra_sealed_evidence_is_rejected_and_preserved(self):
        body = self.numeric(["A,P1,M1,C-1,WARNING,12,ns"])
        self.run_analysis(body)
        self.admin_query("INSERT INTO {vwp}(record_id,record_type,analysis_id,entity_key,review_unit_id,parent_id,"
                         "payload,status,raw_bytes,raw_sha256) SELECT %s,record_type,analysis_id,'r-extra',"
                         "review_unit_id,parent_id,payload,status,raw_bytes,raw_sha256 FROM {vwp} "
                         "WHERE record_type='EVIDENCE' LIMIT 1", (uuid.uuid4(),))
        before = self.admin_query("SELECT * FROM {vwp} ORDER BY record_id").fetchall()
        with self.assertRaises(ReviewError) as err:
            self.run_analysis(body)
        self.assertEqual("EVIDENCE_CONFLICT", err.exception.code)
        self.assertEqual(before, self.admin_query("SELECT * FROM {vwp} ORDER BY record_id").fetchall())

    def test_empty_unknown_raw_record_can_be_preserved(self):
        value, _ = self.run_analysis("\n", unit={**self.unit, "parser": "unknown"})
        self.assertEqual(1, value["review_units"][0]["unknown_count"])
        self.assertEqual(b"", self.admin_query("SELECT raw_bytes FROM {vwp} WHERE record_type='EVIDENCE'").fetchone()[0])

    def test_invalid_typed_identity_records_are_isolated(self):
        unit = {**self.unit, "parser": "summary_json_v1"}
        for value in (123, True, [], {"path": "A"}):
            with self.subTest(value=value):
                record = {"object": value, "check_point": "P1", "mode": "M1", "code": "C",
                          "severity": "ERROR", "status": "FAIL"}
                report = {"format_version": 1, "complete": True, "total": 2, "results": [record, record]}
                payload, _ = self.run_analysis(json.dumps(report), unit=unit)
                self.assertEqual(2, len(payload["finding_groups"]))
                self.assertTrue(all(g["identity_status"] == "UNDECIDED" and g["occurrence_count"] == 1
                                    for g in payload["finding_groups"]))

    def test_custom_identity_scalar_types_do_not_change_numeric_judgment(self):
        body = self.numeric(["A,P1,M1,C-1,WARNING,12,ns"] * 2)
        for value, grouped in ((3, True), ("corner-A", True), (True, False),
                               (None, False), ([], False), ({"name": "A"}, False)):
            with self.subTest(value=value):
                unit = {**self.unit, "identity_fields": ["object", "custom"],
                        "condition": {"mode": "M1", "custom": value}}
                payload, _ = self.run_analysis(body, unit=unit)
                self.assertEqual("COMPLETE", payload["review_units"][0]["parse_status"])
                self.assertEqual(2, payload["review_units"][0]["evaluated_count"])
                self.assertEqual(1 if grouped else 2, len(payload["finding_groups"]))
                self.assertTrue(all(g["identity_status"] == ("SAME_FINDING" if grouped else "UNDECIDED")
                                    for g in payload["finding_groups"]))

    def test_oversized_footer_count_is_partial_result(self):
        body = self.numeric(["A,P1,M1,C-1,WARNING,1,ns"], footer=False)
        payload, _ = self.run_analysis(body + "# END rows=" + "9" * 5000 + "\n")
        unit = payload["review_units"][0]
        self.assertEqual("PARTIAL", unit["parse_status"])
        self.assertIn("TOTAL_MISMATCH", unit["blockers"])
        self.assertEqual(1, unit["unknown_count"])

    def test_integrity_mismatch_rejected(self):
        self.run_analysis(self.numeric(["A,P1,M1,C-1,WARNING,1,ns"]))
        atomic_text(self.report, "tampered")
        with self.assertRaises(ReviewError) as context:
            bridge.analyze(self.root / "run.json", self.root / "bad")
        self.assertEqual("FILE_INTEGRITY", context.exception.code)

    def test_empty_results_and_invalid_exclusion_never_pass(self):
        unit = {**self.unit, "parser": "summary_json_v1"}
        payload, _ = self.run_analysis('{"format_version":1,"complete":true,"total":0,"results":[]}', unit=unit)
        self.assertIn("EMPTY_REPORT", payload["review_units"][0]["blockers"])
        payload, _ = self.run_analysis(unit={**self.unit, "applicable": False})
        self.assertIn("INVALID_EXCLUSION", payload["review_units"][0]["blockers"])

    def test_exact_decimal_boundary_le_and_ge(self):
        rows = ["A,P1,M1,C-1,WARNING,10.0000000000000001,ns",
                "B,P1,M1,C-1,WARNING,9.9999999999999999,ns",
                "C,P1,M1,C-1,WARNING,10.0000000000000000,ns"]
        for operator in ("le", "ge"):
            unit = {**self.unit, "evaluation": {"operator": operator, "limit": 10, "unit": "ns"}}
            payload, _ = self.run_analysis(self.numeric(rows), unit=unit, output=operator)
            self.assertEqual({"PASS": 2, "FAIL": 1, "UNKNOWN": 0}, payload["review_units"][0]["status_counts"])
            group = payload["finding_groups"][0]
            self.assertEqual("A" if operator == "le" else "B", group["identity"]["object"])
            self.assertEqual(rows[0 if operator == "le" else 1].split(",")[5], group["examples"][0]["observed"])

    def test_exact_string_threshold_and_float_threshold_rejection(self):
        rows = ["A,P1,M1,C-1,WARNING,10.0000000000000000,ns",
                "B,P1,M1,C-1,WARNING,10.0000000000000001,ns",
                "C,P1,M1,C-1,WARNING,10.0000000000000002,ns"]
        for operator in ("le", "ge"):
            unit = {**self.unit, "evaluation": {"operator": operator, "limit": "10.0000000000000001", "unit": "ns"}}
            payload, _ = self.run_analysis(self.numeric(rows), unit=unit, output="exact-" + operator)
            self.assertEqual({"PASS": 2, "FAIL": 1, "UNKNOWN": 0}, payload["review_units"][0]["status_counts"])
            self.assertEqual("C" if operator == "le" else "A", payload["finding_groups"][0]["identity"]["object"])
        unit = {**self.unit, "evaluation": {"operator": "le", "limit": 10.1, "unit": "ns"}}
        payload, _ = self.run_analysis(self.numeric(rows), unit=unit, output="float")
        self.assertIn("INVALID_EVALUATION_RULE", payload["review_units"][0]["blockers"])
        self.assertEqual({"PASS": 0, "FAIL": 0, "UNKNOWN": 3}, payload["review_units"][0]["status_counts"])

    def test_unlisted_snapshot_file_and_symlink_rejected(self):
        self.run_analysis(self.numeric(["A,P1,M1,C-1,WARNING,1,ns"]))
        extra = self.root / "reports/unlisted.txt"
        atomic_text(extra, "unlisted")
        with self.assertRaises(ReviewError) as context:
            bridge.analyze(self.root / "run.json", self.root / "bad")
        self.assertEqual("FILE_INTEGRITY", context.exception.code)
        extra.unlink()
        extra.symlink_to(self.report)
        with self.assertRaises(ReviewError) as context:
            bridge.analyze(self.root / "run.json", self.root / "bad")
        self.assertEqual("UNSAFE_PATH", context.exception.code)

    def test_manifest_boolean_schema_and_bad_provenance_rejected(self):
        self.run_analysis(self.numeric(["A,P1,M1,C-1,WARNING,1,ns"]))
        path = self.root / "run.json"
        original = json.loads(path.read_text())
        for change in ("schema", "confirmed", "source", "version"):
            manifest = json.loads(json.dumps(original))
            if change == "schema":
                manifest["schema_version"] = True
            else:
                manifest["inventory"][change] = "yes" if change == "confirmed" else ""
                manifest["inventory"]["sha256"] = digest({k: v for k, v in manifest["inventory"].items() if k != "sha256"})
            atomic_json(path, manifest)
            with self.assertRaises(ReviewError):
                bridge.analyze(path, self.root / "bad")


if __name__ == "__main__":
    unittest.main()
