"""Public-reference initial parser tests; original synthetic messages only."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from sanity_common import sha256_file
from sanity_sample_reports import generate_library, sample_body
from site_integration.public_parsers import FAMILIES, PROFILE, RULE_VERSION, create_public_registry

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_parser_bridge", ROOT / "vwp-bridge/main.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Evidence:
    def __init__(self, run):
        self.run, self.records = run, []

    def record(self, unit, file, index, fields, raw, location, status, reasons):
        self.records.append({"fields": fields, "raw": raw, "location": location,
                             "status": status, "reasons": reasons})


class PublicParserTests(unittest.TestCase):
    def analyze(self, family, body, rule, sample=True):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "reports/check.rpt"
            path.parent.mkdir()
            path.write_text(body)
            info = FAMILIES[family]
            run = {"run_id": "public-test", "project": "Atlas", "design": "CPU", "stage": "POST",
                   "tool": info["tool"], "tool_version": PROFILE}
            unit = {"review_unit_id": "u-test", "item_id": info["item"], "report": "reports/check.rpt",
                    "parser": info["parser"], "rule_version": RULE_VERSION,
                    "condition": {"mode": "FUNC_SS", "rule": rule, **({"sample_profile": PROFILE} if sample else {})},
                    "evaluation": {"operator": "le", "limit": 0, "unit": "violations"},
                    "identity_fields": ["object", "check_point", "mode"], "execution_status": "COMPLETED",
                    "applicable": True, "exclusion": None}
            evidence = Evidence(run)
            result = bridge.analyze_unit(unit, {"file_id": "f-test", "path": unit["report"],
                                               "size_bytes": path.stat().st_size, "sha256": sha256_file(path)},
                                         root, evidence, create_public_registry())
            return result, evidence.records

    def cases(self):
        return (("GCA", "CLK_0003"), ("VCLP", "ISO_CONTROL_CONN"), ("SPYGLASS", "W415"))

    def test_each_native_subset_extracts_a_violation_and_original_multiline_location(self):
        for family, rule in self.cases():
            with self.subTest(family=family):
                result, records = self.analyze(family, sample_body(family, rule, "CPU", 1, "finding"), rule)
                self.assertEqual("COMPLETE", result["parse_status"])
                self.assertEqual("FAIL", result["check_status"])
                self.assertEqual(rule, records[0]["fields"]["code"])
                self.assertEqual("1", records[0]["fields"]["observed"])
                self.assertGreater(records[0]["location"]["line_end"], records[0]["location"]["line_start"])

    def test_explicit_synthetic_zero_proof_creates_a_pass_evidence_record(self):
        for family, rule in self.cases():
            result, records = self.analyze(family, sample_body(family, rule, "CPU", 1, "clean"), rule)
            self.assertEqual("COMPLETE", result["parse_status"])
            self.assertEqual("PASS", result["check_status"])
            self.assertEqual(1, result["record_count"])
            self.assertTrue(records[0]["fields"]["sample_zero_proof"])

    def test_truncated_and_unknown_sections_block_completion_for_all_three_formats(self):
        for family, rule in self.cases():
            for case in ("truncated", "unparsed"):
                result, records = self.analyze(family, sample_body(family, rule, "CPU", 1, case), rule)
                self.assertEqual("PARTIAL", result["parse_status"])
                self.assertTrue(result["blockers"])
                if case == "unparsed":
                    self.assertGreater(result["unknown_count"], 0)
                    self.assertTrue(any("UNSUPPORTED SYNTHETIC SECTION" in record["raw"] for record in records))

    def test_native_excerpt_without_sample_markers_is_extracted_but_not_qualified_complete(self):
        for family, rule in self.cases():
            body = sample_body(family, rule, "CPU", 1, "finding")
            body = "\n".join(line for line in body.splitlines() if not line.startswith("# SANITY_")) + "\n"
            result, records = self.analyze(family, body, rule, sample=False)
            self.assertEqual(1, result["record_count"])
            self.assertEqual(rule, records[0]["fields"]["code"])
            self.assertEqual("PARTIAL", result["parse_status"])
            self.assertIn("UNVERIFIED_REPORT_COMPLETION", result["blockers"])

    def test_vclp_summary_limit_and_gca_incomplete_detail_counts_are_detected(self):
        for family, rule in self.cases()[:2]:
            body = sample_body(family, rule, "CPU", 1, "finding")
            body = body.replace(f"{rule} 1", f"{rule} 100", 1)
            result, _ = self.analyze(family, body, rule)
            self.assertEqual("PARTIAL", result["parse_status"])
            self.assertIn("SUMMARY_DETAIL_MISMATCH", result["blockers"])

    def test_waived_gca_findings_are_not_pass_and_unqualified_spyglass_turbo_is_unknown(self):
        body = sample_body("GCA", "CLK_0003", "CPU", 1, "finding").replace("CLK_0003 1 0", "CLK_0003 1 1")
        result, _ = self.analyze("GCA", body, "CLK_0003")
        self.assertEqual("FAIL", result["check_status"])
        self.assertIn("WAIVER_REVIEW_REQUIRED", result["blockers"])
        body = sample_body("SPYGLASS", "W415", "CPU", 1, "finding").replace("MORESIMPLE REPORT:", "MORESIMPLE_TURBO REPORT:")
        result, _ = self.analyze("SPYGLASS", body, "W415")
        self.assertEqual("PARTIAL", result["parse_status"])
        self.assertIn("SPYGLASS_TURBO_UNSUPPORTED", result["blockers"])

    def test_rule_mismatch_and_unauthorized_sample_envelope_never_complete(self):
        for family, rule in self.cases():
            body = sample_body(family, rule, "CPU", 1, "finding")
            result, _ = self.analyze(family, body, "WRONG_RULE")
            self.assertIn("RULE_MISMATCH", result["blockers"])
            result, _ = self.analyze(family, body, rule, sample=False)
            self.assertEqual("PARTIAL", result["parse_status"])
            self.assertIn("INVALID_SAMPLE_ENVELOPE", result["blockers"])

    def test_empty_report_never_passes(self):
        for family, rule in self.cases():
            result, _ = self.analyze(family, "", rule)
            self.assertEqual("UNKNOWN", result["check_status"])

    def test_zero_proof_after_the_footer_is_retained_as_unknown_and_never_completes(self):
        for family, rule in self.cases():
            body = sample_body(family, rule, "CPU", 1, "clean")
            body = body.replace(f"# SANITY_ZERO rule={rule} violations=0\n", "")
            body += f"# SANITY_ZERO rule={rule} violations=0\n"
            result, records = self.analyze(family, body, rule)
            self.assertEqual("PARTIAL", result["parse_status"])
            self.assertEqual("UNKNOWN", result["check_status"])
            self.assertFalse(any(record["fields"].get("sample_zero_proof") for record in records))

    def test_native_design_from_another_block_blocks_completion(self):
        for family, rule in self.cases()[:2]:
            body = sample_body(family, rule, "GPU", 1, "finding")
            result, _ = self.analyze(family, body, rule)
            self.assertEqual("PARTIAL", result["parse_status"])
            self.assertIn("DESIGN_MISMATCH", result["blockers"])

    def test_small_library_keeps_report_directories_for_missing_only_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            library = generate_library(Path(temp) / "data", count=27)
            self.assertEqual(27, len(library["runs"]))
            self.assertTrue(all((run["directory"] / "reports").is_dir() for run in library["runs"].values()))

    def test_library_has_1000_expected_reports_10_users_3_projects_and_9_blocks(self):
        with tempfile.TemporaryDirectory() as temp:
            library = generate_library(Path(temp) / "data")
            self.assertEqual((1000, 10, 3, 9), tuple(len(library[key]) for key in ("items", "users", "projects", "blocks")))
            self.assertEqual(27, len(library["runs"]))
            self.assertEqual(950, sum(Path(item["report_path"]).exists() for item in library["items"]))
            for project in library["projects"]:
                self.assertEqual(3, sum(block["project_id"] == project["id"] for block in library["blocks"]))
            for block in library["blocks"]:
                self.assertEqual(set(FAMILIES), {item["family"] for item in library["items"] if item["block_id"] == block["id"]})


if __name__ == "__main__":
    unittest.main()
