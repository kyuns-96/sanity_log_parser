"""Pure company-parser contract tests; all reports are synthetic temporary files."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.sanity_common import sha256_file
from common.sanity_parsers import ParseFinished, ParsedRecord, ParserError, ParserRegistry, ReportMetadata, ReportParser
from vwp_bridge.integration.parser_example import SyntheticTextParser

spec = importlib.util.spec_from_file_location("parser_bridge", Path(__file__).resolve().parents[1] / "vwp_bridge/main.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
spec_cli = importlib.util.spec_from_file_location("corporate_parser_cli", Path(__file__).resolve().parents[1] / "vwp_bridge/corporate_analyze.py")
corporate_cli = importlib.util.module_from_spec(spec_cli)
spec_cli.loader.exec_module(corporate_cli)


class RecordingEvidence:
    def __init__(self, run):
        self.run, self.records = run, []

    def record(self, unit, file, index, fields, raw, location, status, reasons):
        self.records.append({"fields": fields, "raw": raw, "location": location,
                             "status": status, "reasons": reasons})


class FunctionParser(ReportParser):
    def __init__(self, function):
        self.function = function

    def parse(self, source, context):
        return self.function(source, context)


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "reports").mkdir()
        self.path = self.root / "reports/check.txt"
        self.run = {"run_id": "r-test", "project": "p", "design": "d", "stage": "s",
                    "tool": "synthetic", "tool_version": "1"}
        self.unit = {"review_unit_id": "u-test", "item_id": "synthetic_item",
                     "report": "reports/check.txt", "parser": "synthetic_text_v1",
                     "rule_version": "1", "condition": {"mode": "M1"},
                     "identity_fields": ["object", "check_point", "mode"],
                     "execution_status": "COMPLETED", "applicable": True, "exclusion": None}

    def registry(self, parser=None, **changes):
        registry = ParserRegistry()
        options = {"implementation_version": "1", "evaluation": "reported",
                   "item_ids": ("synthetic_item",), "tool": "synthetic",
                   "tool_versions": ("1",), "rule_versions": ("1",)}
        registry.register("synthetic_text_v1", parser or SyntheticTextParser(), **{**options, **changes})
        return registry

    def analyze(self, body, parser=None, unit=None, registry=None, mode="reported"):
        self.path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
        source = {"file_id": "f-test", "path": "reports/check.txt",
                  "sha256": sha256_file(self.path), "size_bytes": self.path.stat().st_size}
        evidence = RecordingEvidence(self.run)
        result = bridge.analyze_unit(unit or self.unit, source, self.root, evidence,
                                     registry if registry is not None else self.registry(parser, evaluation=mode))
        return result, evidence.records

    def fields(self, **values):
        return {"object": "A", "check_point": "P1", "mode": "M1", "code": "SYNTHETIC-1",
                "severity": "INFO", "status": "PASS", **values}


    def test_nonblank_metadata_and_invalid_encoding_cannot_be_ignored(self):
        def parse(source, context):
            for line in source.lines():
                yield ReportMetadata(line.span, "BLANK")
            yield ParseFinished(0, True)
        with self.assertRaises(ParserError):
            self.analyze("content\n", FunctionParser(parse))
        result, records = self.analyze(b"\xff\n", FunctionParser(parse))
        self.assertIn("INVALID_ENCODING", result["blockers"])
        self.assertEqual("hex:ff0a", records[0]["raw"])


    def test_json_pointer_resolves_original_values_and_preserves_all_records(self):
        def parse(source, context):
            lines = list(source.lines())
            span = source.span(lines[0], lines[-1])
            for index in range(2):
                yield ParsedRecord(self.fields(), span, json_pointer="/results/" + str(index))
            yield ParseFinished(2, True)
        body = json.dumps({"results": [{"source": "a"}, {"source": "b"}]}, indent=2)
        result, records = self.analyze(body, FunctionParser(parse))
        self.assertEqual("COMPLETE", result["parse_status"])
        self.assertEqual('{"source":"b"}', records[1]["raw"])
        self.assertEqual("/results/1", records[1]["location"]["json_pointer"])


if __name__ == "__main__":
    unittest.main()
