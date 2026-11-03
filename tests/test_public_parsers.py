"""Public-reference initial parser tests; original synthetic messages only."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from common.sanity_common import sha256_file
from devtools.sanity_sample_reports import sample_body
from vwp_bridge.integration.public_parsers import FAMILIES, PROFILE, RULE_VERSION, create_public_registry

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_parser_bridge", ROOT / "vwp_bridge/main.py")
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


    def test_truncated_and_unknown_sections_block_completion_for_all_three_formats(self):
        for family, rule in self.cases():
            for case in ("truncated", "unparsed"):
                result, records = self.analyze(family, sample_body(family, rule, "CPU", 1, case), rule)
                self.assertEqual("PARTIAL", result["parse_status"])
                self.assertTrue(result["blockers"])
                if case == "unparsed":
                    self.assertGreater(result["unknown_count"], 0)
                    self.assertTrue(any("UNSUPPORTED SYNTHETIC SECTION" in record["raw"] for record in records))


if __name__ == "__main__":
    unittest.main()
