"""Pure company-parser contract tests; all reports are synthetic temporary files."""
from dataclasses import replace
from contextlib import redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sanity_common import sha256_file
from sanity_parsers import (LINE_LIMIT, ParseContext, ParseFinished, ParsedRecord, ParserError,
                            ParserRegistry, ReportMetadata, ReportParser, SourceSpan, UnparsedRegion)
from site_integration.parser_example import SyntheticTextParser

spec = importlib.util.spec_from_file_location("parser_bridge", Path(__file__).resolve().parents[1] / "vwp-bridge/main.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
spec_cli = importlib.util.spec_from_file_location("corporate_parser_cli", Path(__file__).resolve().parents[1] / "vwp-bridge/corporate_analyze.py")
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

    def test_interface_is_abstract_and_unconfigured_adapter_cannot_succeed(self):
        class UnconfiguredParser(ReportParser):
            def parse(self, source, context):
                raise ParserError("PARSER_NOT_CONFIGURED")
        with self.assertRaises(TypeError):
            ReportParser()
        self.assertIsNone(ParserRegistry().resolve(self.unit["parser"], self.run, self.unit))
        with self.assertRaises(ParserError) as error:
            self.analyze("private-but-synthetic", UnconfiguredParser())
        self.assertEqual("PARSER_NOT_CONFIGURED", error.exception.code)

    def test_explicit_registration_processes_every_pass_fail_and_raw_location(self):
        result, records = self.analyze("SYNTHETIC REPORT v1\nA|P1|M1|C1|WARNING|FAIL\nB|P1|M1|C1|INFO|PASS\nEND records=2\n")
        self.assertEqual("COMPLETE", result["parse_status"])
        self.assertEqual("FAIL", result["check_status"])
        self.assertEqual({"PASS": 1, "FAIL": 1, "UNKNOWN": 0}, result["status_counts"])
        self.assertEqual("A|P1|M1|C1|WARNING|FAIL", records[0]["raw"])
        self.assertEqual({"line_start": 2, "line_end": 2}, records[0]["location"])

    def test_explicit_zero_violation_proof_is_one_pass_record(self):
        result, records = self.analyze("SYNTHETIC REPORT v1\nCLEAN block|all_checks|M1\nEND records=1\n")
        self.assertEqual("PASS", result["check_status"])
        self.assertEqual("COMPLETE", result["parse_status"])
        self.assertEqual(1, result["record_count"])
        self.assertEqual("CLEAN block|all_checks|M1", records[0]["raw"])

    def test_empty_file_and_zero_record_report_are_incomplete(self):
        for body in ("", "SYNTHETIC REPORT v1\nEND records=0\n"):
            result, _ = self.analyze(body)
            self.assertEqual("UNKNOWN", result["check_status"])
            self.assertIn("EMPTY_REPORT", result["blockers"])

    def test_truncation_bad_header_count_unknown_and_trailing_sections_are_retained(self):
        bodies = {
            "MISSING_END_MARKER": "SYNTHETIC REPORT v1\nCLEAN block|all_checks|M1\n",
            "INVALID_HEADER": "different format\nCLEAN block|all_checks|M1\nEND records=1\n",
            "TOTAL_MISMATCH": "SYNTHETIC REPORT v1\nCLEAN block|all_checks|M1\nEND records=2\n",
            "MALFORMED_ROW": "SYNTHETIC REPORT v1\nunknown section\nEND records=0\n",
            "TRAILING_CONTENT": "SYNTHETIC REPORT v1\nCLEAN block|all_checks|M1\nEND records=1\nextra content\n"}
        for reason, body in bodies.items():
            with self.subTest(reason=reason):
                result, _ = self.analyze(body)
                self.assertEqual("PARTIAL", result["parse_status"])
                self.assertEqual("UNKNOWN", result["check_status"])
                self.assertIn(reason, result["blockers"])

    def test_unknown_parser_has_no_company_or_builtin_fallback(self):
        result, records = self.analyze("opaque\n", unit={**self.unit, "parser": "unregistered_v1"})
        self.assertEqual("UNSUPPORTED", result["parse_status"])
        self.assertEqual("opaque", records[0]["raw"])

    def test_missing_file_remains_missing_without_calling_adapter(self):
        evidence = RecordingEvidence(self.run)
        result = bridge.analyze_unit(self.unit, None, self.root, evidence,
                                     self.registry(FunctionParser(lambda source, context: self.fail("Missing report must not call parser"))))
        self.assertEqual("MISSING", result["parse_status"])
        self.assertEqual([], evidence.records)

    def test_registry_rejects_duplicates_builtin_override_wildcards_and_bad_types(self):
        registry = self.registry()
        for parser_id, options in (("synthetic_text_v1", {}), ("numeric_csv_v1", {}),
                                   ("summary_json_v1", {}), ("other_v1", {"item_ids": ("*",)}),
                                   ("other_v1", {"tool_versions": ()}),
                                   ("other_v1", {"rule_versions": ["1"]}),
                                   ("module:class", {}), ("other_v1", {"implementation_version": ""})):
            with self.subTest(parser_id=parser_id, options=options):
                with self.assertRaises(ParserError) as error:
                    registry.register(parser_id, SyntheticTextParser(),
                                      **{"implementation_version": "1", "evaluation": "reported",
                                         "item_ids": ("synthetic_item",), "tool": "synthetic",
                                         "tool_versions": ("1",), "rule_versions": ("1",), **options})
                self.assertEqual("PARSER_REGISTRATION_ERROR", error.exception.code)

    def test_registry_rejects_wrong_item_tool_version_and_rule(self):
        registry = self.registry()
        for run, unit in ((self.run, {**self.unit, "item_id": "other"}),
                          ({**self.run, "tool": "other"}, self.unit),
                          ({**self.run, "tool_version": "2"}, self.unit),
                          (self.run, {**self.unit, "rule_version": "2"}),
                          (self.run, {**self.unit, "evaluation": {}}),
                          (self.run, {**self.unit, "evaluation": {"operator": "le", "limit": 1}})):
            with self.assertRaises(ParserError) as error:
                registry.resolve(self.unit["parser"], run, unit)
            self.assertEqual("PARSER_SCOPE_MISMATCH", error.exception.code)

    def test_parser_version_is_present_in_contract_and_unused_adapters_are_excluded(self):
        first = self.registry().contract(self.run, [self.unit])
        second = self.registry(implementation_version="2").contract(self.run, [self.unit])
        self.assertNotEqual(first, second)
        self.assertEqual("1", first[0]["implementation_version"])
        self.assertEqual([], self.registry().contract(self.run, [{**self.unit, "parser": "unregistered"}]))

    def test_context_cannot_mutate_source_run_conditions_or_identity_rules(self):
        context = ParseContext(self.run, self.unit)
        self.unit["condition"]["mode"] = "changed"
        self.assertEqual("M1", context.unit["condition"]["mode"])
        self.assertEqual(("object", "check_point", "mode"), context.unit["identity_fields"])
        with self.assertRaises(TypeError):
            context.unit["condition"]["mode"] = "other"
        self.assertNotIn("M1", repr(context))

    def test_early_return_does_not_hide_unread_input(self):
        def parse(source, context):
            line = next(iter(source.lines()))
            yield ParsedRecord(self.fields(), line.span)
            yield ParseFinished(1, True)
        result, records = self.analyze("first\nsecond\nthird\n", FunctionParser(parse))
        self.assertEqual("PARTIAL", result["parse_status"])
        self.assertIn("PARSER_DID_NOT_READ_EOF", result["blockers"])
        self.assertEqual(["first", "second", "third"], [record["raw"] for record in records])
        self.assertEqual({"PASS": 1, "FAIL": 0, "UNKNOWN": 2}, result["status_counts"])

    def test_reading_every_line_without_accounting_for_them_is_partial(self):
        def parse(source, context):
            lines = list(source.lines())
            yield ParsedRecord(self.fields(), lines[1].span)
            yield ParseFinished(1, True)
        result, records = self.analyze("skipped header\nrecord\nskipped tail\n", FunctionParser(parse))
        self.assertIn("UNPARSED_CONTENT", result["blockers"])
        self.assertNotIn("PARSER_DID_NOT_READ_EOF", result["blockers"])
        self.assertEqual(["skipped header", "record", "skipped tail"], [record["raw"] for record in records])

    def test_missing_completion_is_partial_even_when_pass_record_covers_file(self):
        def parse(source, context):
            for line in source.lines():
                yield ParsedRecord(self.fields(), line.span)
        result, _ = self.analyze("record\n", FunctionParser(parse))
        self.assertEqual("UNKNOWN", result["check_status"])
        self.assertIn("PARSER_MISSING_COMPLETION", result["blockers"])

    def test_extra_events_and_invalid_completion_types_are_rejected(self):
        for completion in (ParseFinished(True, True), ParseFinished(1, "yes"),
                           ParseFinished(1, True, ("raw private report text",)), None):
            def parse(source, context):
                for line in source.lines():
                    yield ParsedRecord(self.fields(), line.span)
                yield completion or ParseFinished(1, True)
                if completion is None:
                    yield ParseFinished(1, True)
            with self.subTest(completion=completion):
                with self.assertRaises(ParserError) as error:
                    self.analyze("record\n", FunctionParser(parse))
                self.assertEqual("PARSER_INVALID_RESULT", error.exception.code)

    def test_invented_modified_and_other_source_spans_are_rejected(self):
        for transform in (lambda span: replace(span, line_start=2),
                          lambda span: SourceSpan(1, 1, 0, 7, (object(), 1, 1, 0, 7))):
            def parse(source, context):
                lines = list(source.lines())
                yield ParsedRecord(self.fields(), transform(lines[0].span))
                yield ParseFinished(1, True)
            with self.assertRaises(ParserError) as error:
                self.analyze("record\n", FunctionParser(parse))
            self.assertEqual("PARSER_INVALID_RESULT", error.exception.code)

    def test_out_of_order_records_are_rejected(self):
        def parse(source, context):
            lines = list(source.lines())
            yield ParsedRecord(self.fields(), lines[1].span)
            yield ParsedRecord(self.fields(), lines[0].span)
            yield ParseFinished(2, True)
        with self.assertRaises(ParserError) as error:
            self.analyze("first\nsecond\n", FunctionParser(parse))
        self.assertEqual("PARSER_INVALID_RESULT", error.exception.code)

    def test_multiline_record_preserves_original_text_and_line_range(self):
        def parse(source, context):
            lines = list(source.lines())
            yield ParsedRecord(self.fields(), source.span(lines[0], lines[1]))
            yield ParseFinished(1, True)
        result, records = self.analyze("start\r\n  continuation\r\n", FunctionParser(parse))
        self.assertEqual("PASS", result["check_status"])
        self.assertEqual("start\r\n  continuation", records[0]["raw"])
        self.assertEqual({"line_start": 1, "line_end": 2}, records[0]["location"])

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

    def test_numeric_parser_uses_common_exact_decimal_rules_without_report_status(self):
        def parse(source, context):
            for line in source.lines():
                yield ParsedRecord(self.fields(observed=line.text, unit="ns", status="PASS"), line.span)
            yield ParseFinished(2, True)
        unit = {**self.unit, "evaluation": {"operator": "le", "limit": "10", "unit": "ns"}}
        result, records = self.analyze("10\n10.0000000000000001\n", FunctionParser(parse), unit=unit, mode="numeric")
        self.assertEqual({"PASS": 1, "FAIL": 1, "UNKNOWN": 0}, result["status_counts"])
        self.assertEqual("10.0000000000000001", records[1]["fields"]["observed"])
        self.assertEqual("10", records[1]["fields"]["expected"])

    def test_reported_missing_fields_and_wrong_mode_remain_unknown(self):
        result, _ = self.analyze("SYNTHETIC REPORT v1\n|P1|M2|C1|INFO|PASS\nEND records=1\n")
        self.assertEqual(1, result["unknown_count"])
        self.assertIn("MODE_MISMATCH", result["blockers"])
        self.assertIn("MISSING_OBJECT", result["blockers"])

    def test_failure_strings_never_escape_or_trigger_a_retry(self):
        calls = []
        def before(source, context):
            calls.append("called")
            raise RuntimeError("SYNTHETIC_SECRET_report_or_credentials")
        def during(source, context):
            calls.append("called")
            for line in source.lines():
                yield ParsedRecord(self.fields(), line.span)
            raise RuntimeError("SYNTHETIC_SECRET_report_or_credentials")
        for function in (before, during):
            with self.assertRaises(ParserError) as error:
                self.analyze("record\n", FunctionParser(function))
            self.assertEqual("PARSER_FAILED", error.exception.code)
            self.assertNotIn("SYNTHETIC_SECRET", str(error.exception))
            self.assertIsNone(error.exception.__cause__)
        self.assertEqual(["called", "called"], calls)

    def test_company_cli_prints_fixed_registration_error_without_private_details(self):
        for failure, code in ((RuntimeError("SYNTHETIC_SECRET"), "PARSER_FAILED"),
                              (ParserError("PARSER_REGISTRATION_ERROR"), "PARSER_REGISTRATION_ERROR")):
            errors = io.StringIO()
            company_module = ModuleType("site_integration.corporate_parsers")
            company_module.create_parser_registry = Mock(side_effect=failure)
            with patch.dict(sys.modules, {company_module.__name__: company_module}), redirect_stderr(errors):
                self.assertEqual(2, corporate_cli.corporate_main(["analyze", "--input", "unused"]))
            self.assertEqual("code=" + code + "\n", errors.getvalue())

    def test_invalid_field_container_nonfinite_and_unknown_event_are_rejected(self):
        for fields in ([], self.fields(custom=float("nan")), self.fields(custom=object())):
            def parse(source, context):
                for line in source.lines():
                    yield ParsedRecord(fields, line.span)
                yield ParseFinished(1, True)
            with self.assertRaises(ParserError) as error:
                self.analyze("record\n", FunctionParser(parse))
            self.assertEqual("PARSER_INVALID_RESULT", error.exception.code)
        with self.assertRaises(ParserError):
            self.analyze("record\n", FunctionParser(lambda source, context: iter([{}])))

    def test_line_limit_stops_analysis_instead_of_passing_a_prefix(self):
        with self.assertRaises(ParserError) as error:
            self.analyze("x" * (LINE_LIMIT + 1), SyntheticTextParser())
        self.assertEqual("PARSER_SOURCE_LIMIT", error.exception.code)

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

    def test_invalid_json_pointer_is_a_contract_error(self):
        for pointer in ("/missing", "/results/01", "/results/2", "/results/~2", "/results/" + "9" * 100):
            def parse(source, context):
                lines = list(source.lines())
                yield ParsedRecord(self.fields(), source.span(lines[0], lines[-1]), json_pointer=pointer)
                yield ParseFinished(1, True)
            with self.subTest(pointer=pointer):
                with self.assertRaises(ParserError) as error:
                    self.analyze('{"results":[{}]}', FunctionParser(parse))
                self.assertEqual("PARSER_INVALID_RESULT", error.exception.code)


if __name__ == "__main__":
    unittest.main()
