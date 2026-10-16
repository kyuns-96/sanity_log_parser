"""Real PostgreSQL integration of the company interface, with synthetic reports only."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.sanity_common import atomic_json, atomic_text, coverage, digest, unpack_bundle, sha256_file
from common.sanity_db import Database, Queue
from devtools.demo import stage
from common.sanity_parsers import ParserError, ParserRegistry, ReportParser
from vwp_bridge.integration.parser_example import SyntheticTextParser
from tests.support import PgTestCase

spec = importlib.util.spec_from_file_location("corporate_parser_bridge", Path(__file__).with_name("main.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class CorporateBridgeTests(PgTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report = self.root / "reports/check.txt"
        atomic_text(self.report, "SYNTHETIC REPORT v1\nA|P1|M1|C1|WARNING|FAIL\nB|P1|M1|C1|INFO|PASS\nEND records=2\n")
        unit = {"review_unit_id": "u-company", "item_id": "synthetic_item", "report": "reports/check.txt",
                "parser": "synthetic_text_v1", "rule_version": "1", "condition": {"mode": "M1"},
                "identity_fields": ["object", "check_point", "mode"],
                "execution_status": "COMPLETED", "applicable": True, "exclusion": None}
        inventory = {"confirmed": True, "source": "synthetic-plan", "version": "1", "units": [unit]}
        inventory["sha256"] = digest(inventory)
        self.manifest = {"schema_version": 1, "ready": True,
                         "run": {"run_id": "synthetic-run", "project": "p", "design": "d", "stage": "s",
                                 "tool": "synthetic", "tool_version": "1"},
                         "inventory": inventory, "unexpected_files": []}

    def input(self):
        self.manifest["files"] = [{"file_id": "f-company", "path": "reports/check.txt",
                                   "size_bytes": self.report.stat().st_size, "sha256": sha256_file(self.report)}]
        atomic_json(self.root / "run.json", self.manifest)
        return self.root / "run.json"

    def registry(self, parser=None, version="1"):
        registry = ParserRegistry()
        registry.register("synthetic_text_v1", parser or SyntheticTextParser(), implementation_version=version,
                          evaluation="reported", item_ids=("synthetic_item",), tool="synthetic",
                          tool_versions=("1",), rule_versions=("1",))
        return registry

    def test_full_evidence_sealing_transport_and_deterministic_replay(self):
        payload, receipt = bridge.analyze(self.input(), parser_registry=self.registry())
        state = coverage(payload)
        self.assertTrue(state["units"][0]["analysis_ready"])
        self.assertEqual("INCOMPLETE", state["review_status"])  # Still needs a human decision.
        rows = self.admin_query("SELECT payload,raw_bytes FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE'",
                                (uuid.UUID(receipt["analysis_id"]),)).fetchall()
        self.assertEqual(2, len(rows))
        self.assertEqual({"PASS", "FAIL"}, {row[0]["check_status"] for row in rows})
        with Database("oa") as db:
            self.assertEqual(payload, unpack_bundle(Queue(db, "vwp_to_oa").get(receipt["message_id"])))
        received = json.loads(stage("oa", "receive", "--message-id", receipt["message_id"]))
        self.assertEqual("RECEIVED", received["status"])
        self.assertEqual(state, received["coverage"])
        before = self.admin_query("SELECT * FROM {vwp} ORDER BY record_id").fetchall()
        replay, replay_receipt = bridge.analyze(self.input(), parser_registry=self.registry())
        self.assertEqual(payload, replay)
        self.assertEqual(receipt, replay_receipt)
        self.assertEqual(before, self.admin_query("SELECT * FROM {vwp} ORDER BY record_id").fetchall())

    def test_registration_and_implementation_revision_create_distinct_immutable_analyses(self):
        unsupported, old = bridge.analyze(self.input())
        first, receipt1 = bridge.analyze(self.input(), parser_registry=self.registry())
        second, receipt2 = bridge.analyze(self.input(), parser_registry=self.registry(version="2"))
        self.assertEqual("UNSUPPORTED", unsupported["review_units"][0]["parse_status"])
        self.assertEqual(3, len({old["analysis_id"], receipt1["analysis_id"], receipt2["analysis_id"]}))
        self.assertEqual(3, len({unsupported["bundle_id"], first["bundle_id"], second["bundle_id"]}))
        self.assertEqual(3, self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RUN' AND status='SEALED'").fetchone()[0])
        self.assertEqual("1", first["producer"]["parsers"][0]["implementation_version"])
        self.assertEqual("2", second["producer"]["parsers"][0]["implementation_version"])

    def test_default_stub_cannot_seal_or_enqueue(self):
        class UnconfiguredParser(ReportParser):
            def parse(self, source, context):
                raise ParserError("PARSER_NOT_CONFIGURED")
        with self.assertRaises(ParserError) as error:
            bridge.analyze(self.input(), parser_registry=self.registry(UnconfiguredParser()))
        self.assertEqual("PARSER_NOT_CONFIGURED", error.exception.code)
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {vwp_to_oa}").fetchone()[0])
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RUN' AND status='SEALED'").fetchone()[0])

    def test_failure_after_a_record_cannot_publish_partial_success(self):
        class BrokenParser(ReportParser):
            def parse(self, source, context):
                yield from SyntheticTextParser().parse(source, context)
                raise RuntimeError("synthetic-private-error")
        with self.assertRaises(ParserError) as error:
            bridge.analyze(self.input(), parser_registry=self.registry(BrokenParser()))
        self.assertEqual("PARSER_FAILED", error.exception.code)
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {vwp_to_oa}").fetchone()[0])
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {vwp} WHERE record_type='RUN' AND status='SEALED'").fetchone()[0])

    def test_format_errors_are_valid_incomplete_bundles_with_original_unknown_evidence(self):
        atomic_text(self.report, "SYNTHETIC REPORT v1\nCLEAN block|all_checks|M1\nunrecognized section\n")
        payload, receipt = bridge.analyze(self.input(), parser_registry=self.registry())
        self.assertEqual("PARTIAL", payload["review_units"][0]["parse_status"])
        self.assertFalse(coverage(payload)["units"][0]["analysis_ready"])
        unknown = self.admin_query("SELECT raw_bytes FROM {vwp} WHERE analysis_id=%s AND record_type='EVIDENCE' "
                                   "AND payload->>'check_status'='UNKNOWN'", (uuid.UUID(receipt["analysis_id"]),)).fetchone()
        self.assertEqual(b"unrecognized section", bytes(unknown[0]))

    def test_scope_mismatch_fails_before_creating_any_database_state(self):
        self.manifest["run"]["tool_version"] = "2"
        with self.assertRaises(ParserError) as error:
            bridge.analyze(self.input(), parser_registry=self.registry())
        self.assertEqual("PARSER_SCOPE_MISMATCH", error.exception.code)
        self.assertEqual(0, self.admin_query("SELECT count(*) FROM {vwp}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
