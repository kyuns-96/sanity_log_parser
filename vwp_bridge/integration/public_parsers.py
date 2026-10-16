"""Initial, deliberately narrow extractors informed by public vendor documentation.

These are not qualified company parsers. Native excerpts are extracted but stay
incomplete without a verified completion contract. Only the synthetic preview
profile accepts the SANITY fixture markers (our markers, not vendor syntax).
See PUBLIC_REPORT_FORMATS.md for sources, supported subsets and site work.
"""
from __future__ import annotations

import re

from common.sanity_parsers import ParseFinished, ParsedRecord, ParserRegistry, ReportMetadata, ReportParser, UnparsedRegion

PROFILE = "public-reference-v1"
RULE_VERSION = "sample-zero-violations-v1"
FAMILIES = {
    "GCA": {"tool": "PrimeTime GCA", "item": "gca_constraints", "parser": "gca_public_text_v1"},
    "VCLP": {"tool": "VC LP", "item": "vclp_low_power", "parser": "vclp_public_text_v1"},
    "SPYGLASS": {"tool": "SpyGlass", "item": "spyglass_lint", "parser": "spyglass_moresimple_v1"},
}


class _PublicParser(ReportParser):
    family = ""

    def fields(self, context, rule, severity, observed, **extra):
        # Numerical PASS/FAIL comes from the configured evaluator, never severity.
        return {"code": rule, "check_point": rule, "severity": severity,
                "observed": str(observed), "unit": "violations",
                "mode": context.unit["condition"].get("mode"), **extra}

    def parse(self, source, context):
        state = {"header": False, "records": 0, "fixture": False, "finished": False,
                 "declared": None, "blockers": [], "rule": None, "severity": None,
                 "expected": None, "details": 0, "zero": False, "design": None,
                 "scenario": None, "stage": None}
        pending = []

        def flush():
            if not pending:
                return []
            lines, fields = pending[:], state.pop("pending_fields")
            pending.clear()
            fields["message"] = "\n".join(line.text for line in lines)
            state["records"] += 1
            return [ParsedRecord(fields, source.span(lines[0], lines[-1]))]

        for line in source.lines():
            text = line.text.strip()
            if not line.encoding_valid:
                yield from flush()
                yield UnparsedRegion(line.span, "INVALID_ENCODING")
                continue
            begin = re.fullmatch(r"# SANITY_SAMPLE family=(GCA|VCLP|SPYGLASS) profile=public-reference-v1", text)
            end = re.fullmatch(r"# SANITY_END records=(0|[1-9][0-9]{0,8})", text)
            zero = re.fullmatch(r"# SANITY_ZERO rule=([A-Za-z0-9_]+) violations=0", text)
            if begin or end or zero:
                yield from flush()
                authorized = context.unit["condition"].get("sample_profile") == PROFILE
                if begin and authorized and begin[1] == self.family and not state["fixture"] and line.span.line_start == 1:
                    state["fixture"] = True
                    yield ReportMetadata(line.span, "HEADER")
                elif end and authorized and state["fixture"] and not state["finished"]:
                    state["finished"], state["declared"] = True, int(end[1])
                    yield ReportMetadata(line.span, "FOOTER")
                elif (zero and authorized and state["fixture"] and state["header"]
                      and not state["finished"] and not state["zero"] and not state["details"]):
                    if zero[1] != context.unit["condition"].get("rule"):
                        yield UnparsedRegion(line.span, "RULE_MISMATCH")
                    else:
                        state["records"] += 1
                        state["zero"] = True
                        yield ParsedRecord(self.fields(context, zero[1], "INFO", 0,
                                                       object=context.run["design"], sample_zero_proof=True), line.span)
                else:
                    yield UnparsedRegion(line.span, "INVALID_SAMPLE_ENVELOPE")
                continue
            if state["finished"]:
                yield UnparsedRegion(line.span, "TRAILING_CONTENT")
                continue
            if self.continuation(text, state, pending):
                pending.append(line)
                continue
            yield from flush()
            action, value = self.consume(text, context, state)
            if action == "record":
                state["details"] += 1
                state["pending_fields"] = value
                pending.append(line)
            elif action == "metadata":
                yield ReportMetadata(line.span, "BLANK" if not text else value)
            else:
                yield UnparsedRegion(line.span, value)
        yield from flush()
        if not state["header"]:
            state["blockers"].append("INVALID_HEADER")
        if not state["fixture"] or not state["finished"]:
            state["blockers"].append("UNVERIFIED_REPORT_COMPLETION")
        if state["expected"] is not None and state["details"] != state["expected"]:
            state["blockers"].append("SUMMARY_DETAIL_MISMATCH")
        if state["zero"] and state["details"]:
            state["blockers"].append("ZERO_PROOF_CONFLICT")
        if state["expected"] == 0 and not state["zero"]:
            state["blockers"].append("ZERO_RESULT_WITHOUT_PROOF")
        declared = state["declared"] if state["declared"] is not None else state["records"]
        yield ParseFinished(declared, not state["blockers"], tuple(state["blockers"]))

    def continuation(self, text, state, pending):
        return bool(pending) and text.startswith("+") and not re.fullmatch(r"[+=*-]+", text)


class GcaPublicTextParser(_PublicParser):
    """Subset of full text report_constraint_analysis: one design/scenario/rule."""
    family = "GCA"

    def consume(self, text, context, state):
        if text == "Report : report_constraint_analysis":
            state["header"] = True
            return "metadata", "HEADER"
        match = re.fullmatch(r"Design:\s*(\S+)", text)
        if match:
            if state["design"] is not None:
                state["blockers"].append("MULTIPLE_DESIGNS_UNSUPPORTED")
            state["design"] = match[1]
            if match[1] != context.run["design"]:
                state["blockers"].append("DESIGN_MISMATCH")
            return "metadata", "HEADER"
        match = re.fullmatch(r"(\S+)\s+(\d+)\s+(\d+)\s+Scenario violations", text)
        if match:
            state["scenario"] = match[1]
            if match[1] != context.unit["condition"].get("mode"):
                state["blockers"].append("MODE_MISMATCH")
            return "metadata", "HEADER"
        match = re.fullmatch(r"(error|warning|info|fatal)\s+(\d+)\s+(\d+)", text, re.I)
        if match:
            state["severity"] = match[1].upper()
            return "metadata", "HEADER"
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*_\d{4})\s+(\d+)\s+(\d+)\s+(.+)", text)
        if match:
            if state["rule"] is not None:
                state["blockers"].append("MULTIPLE_RULES_UNSUPPORTED")
            state["rule"], state["expected"] = match[1], int(match[2])
            if match[1] != context.unit["condition"].get("rule"):
                state["blockers"].append("RULE_MISMATCH")
            if int(match[3]):
                state["blockers"].append("WAIVER_REVIEW_REQUIRED")
            return "metadata", "HEADER"
        match = re.fullmatch(r"(\d+)\s+of\s+(\d+)\s+(\d+)\s+(.+)", text)
        if match:
            if not state["rule"] or not state["severity"] or not state["scenario"]:
                return "unknown", "MISSING_RULE_CONTEXT"
            if int(match[1]) != state["details"] + 1 or int(match[2]) != state["expected"]:
                state["blockers"].append("TOTAL_MISMATCH")
            if int(match[3]):
                state["blockers"].append("WAIVER_REVIEW_REQUIRED")
            obj = re.search(r"(?:pin|port|clock|object)\s+'([^']+)'", match[4])
            return "record", self.fields(context, state["rule"], state["severity"], 1,
                                          object=obj[1] if obj else None, design=state["design"],
                                          scenario=state["scenario"], waived=int(match[3]))
        if (not text or re.fullmatch(r"[*=+-]+", text) or text.startswith(("Version:", "Date :"))
                or text in ("-include {violations}", "-style {full}", "Scenario Violations Count Waived Description")):
            return "metadata", "HEADER"
        return "unknown", "UNRECOGNIZED_GCA_CONTENT"


class VclpPublicTextParser(_PublicParser):
    """Tree summary counts and Tag/debug-field blocks; no summary-only PASS."""
    family = "VCLP"

    def continuation(self, text, state, pending):
        if not pending:
            return False
        match = re.fullmatch(r"(ViolationSource|ViolationSink|DomainSource|DomainSink|Strategy|Description)\s*:\s*(.+)", text)
        if match:
            state["pending_fields"][match[1]] = match[2]
            if match[1] in ("ViolationSource", "Strategy"):
                state["pending_fields"]["object"] = match[2]
            return True
        return text.startswith("+") and not re.fullmatch(r"[+=*-]+", text)

    def consume(self, text, context, state):
        if text == "report_violations -app LP -verbose":
            state["header"] = True
            return "metadata", "HEADER"
        if text == "Tree Summary":
            state["stage"] = "summary"
            return "metadata", "HEADER"
        match = re.fullmatch(r"Design top\s*:\s*(\S+)", text)
        if match:
            if state["design"] is not None:
                state["blockers"].append("MULTIPLE_DESIGNS_UNSUPPORTED")
            state["design"] = match[1]
            if match[1] != context.run["design"]:
                state["blockers"].append("DESIGN_MISMATCH")
            return "metadata", "HEADER"
        match = re.fullmatch(r"(error|warning|info|fatal)\s+(UPF|Design|PG)\s+([A-Z][A-Z0-9_]+)\s+(\d+)", text, re.I)
        if match:
            if state["rule"] is not None:
                state["blockers"].append("MULTIPLE_TAGS_UNSUPPORTED")
            state["severity"], state["rule"], state["expected"] = match[1].upper(), match[3], int(match[4])
            if match[3] != context.unit["condition"].get("rule"):
                state["blockers"].append("RULE_MISMATCH")
            return "metadata", "HEADER"
        match = re.fullmatch(r"Tag\s*:\s*([A-Z][A-Z0-9_]+)", text)
        if match:
            if match[1] != state["rule"] or not state["severity"]:
                return "unknown", "TAG_SUMMARY_MISMATCH"
            return "record", self.fields(context, match[1], state["severity"], 1)
        if re.fullmatch(r"Total\s+\d+", text):
            if int(text.split()[-1]) != state["expected"]:
                state["blockers"].append("TOTAL_MISMATCH")
            return "metadata", "FOOTER"
        if (not text or re.fullmatch(r"[-=+*]+", text) or text.startswith("Version:")
                or text in ("Severity Stage Tag Count", "Violation Details")):
            return "metadata", "HEADER"
        return "unknown", "UNRECOGNIZED_VCLP_CONTENT"


class SpyglassMoresimpleParser(_PublicParser):
    """Non-turbo moresimple ID/Rule/Alias/Severity/File/Line/Wt/Message rows."""
    family = "SPYGLASS"

    def consume(self, text, context, state):
        if text == "MORESIMPLE REPORT:":
            state["header"] = True
            return "metadata", "HEADER"
        match = re.fullmatch(r"\[(\d+)\]\s+(\S+)\s+(\S+)\s+(Error|Warning|Info|FATAL)\s+(\S+)\s+(\d+)\s+(\d+)\s+(.+)", text)
        if match:
            if match[2] != context.unit["condition"].get("rule"):
                state["blockers"].append("RULE_MISMATCH")
            return "record", self.fields(context, match[2], match[4].upper(), 1,
                                          object=f"{match[5]}:{match[6]}", source_file=match[5],
                                          source_line=int(match[6]), weight=int(match[7]),
                                          message_id=match[1], alias=match[3])
        if text == "MORESIMPLE_TURBO REPORT:" or "ParentID" in text:
            return "unknown", "SPYGLASS_TURBO_UNSUPPORTED"
        if (not text or re.fullmatch(r"[+=*-]+", text) or text.startswith("Version:")
                or re.fullmatch(r"#+\s*BuiltIn -> RuleGroup=Lint\s*#+", text)
                or text == "ID Rule Alias Severity File Line Wt Message"):
            return "metadata", "HEADER"
        return "unknown", "UNRECOGNIZED_SPYGLASS_CONTENT"


def create_public_registry():
    registry = ParserRegistry()
    for family, parser in (("GCA", GcaPublicTextParser()), ("VCLP", VclpPublicTextParser()),
                           ("SPYGLASS", SpyglassMoresimpleParser())):
        spec = FAMILIES[family]
        registry.register(spec["parser"], parser, implementation_version="1", evaluation="numeric",
                          item_ids=(spec["item"],), tool=spec["tool"], tool_versions=(PROFILE,),
                          rule_versions=(RULE_VERSION,))
    return registry
