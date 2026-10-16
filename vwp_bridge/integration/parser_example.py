"""Synthetic teaching adapter ONLY. Not registered or suitable for real reports.

Format: SYNTHETIC REPORT v1\n, zero or more pipe-separated result rows,
then END records=N\n. Explicit clean result: CLEAN object|check_point|mode.
"""
import re

from common.sanity_parsers import ParseFinished, ParsedRecord, ReportMetadata, ReportParser, UnparsedRegion


class SyntheticTextParser(ReportParser):
    def parse(self, source, context):
        rows = 0
        declared = None
        footer = False
        blockers = []
        for line in source.lines():
            text, span = line.text, line.span
            if not line.encoding_valid:
                yield UnparsedRegion(span, "INVALID_ENCODING")
                continue
            if span.line_start == 1:
                if text == "SYNTHETIC REPORT v1":
                    yield ReportMetadata(span, "HEADER")
                else:
                    yield UnparsedRegion(span, "INVALID_HEADER")
                continue
            if footer:
                yield UnparsedRegion(span, "TRAILING_CONTENT")
                continue
            match = re.fullmatch(r"END records=(0|[1-9][0-9]{0,8})", text)
            if match:
                declared = int(match[1])
                footer = True
                yield ReportMetadata(span, "FOOTER")
                continue
            if text.startswith("CLEAN "):
                parts = text[6:].split("|")
                if len(parts) == 3 and all(parts):
                    rows += 1
                    yield ParsedRecord({"object": parts[0], "check_point": parts[1],
                                        "mode": parts[2], "code": "SYNTHETIC-CLEAN",
                                        "severity": "INFO", "status": "PASS"}, span)
                    continue
            parts = text.split("|")
            if len(parts) == 6:
                rows += 1
                yield ParsedRecord(dict(zip(("object", "check_point", "mode", "code",
                                              "severity", "status"), parts)), span)
            else:
                yield UnparsedRegion(span, "MALFORMED_ROW")
        if not footer:
            blockers.append("MISSING_END_MARKER")
        yield ParseFinished(declared if declared is not None else rows, footer, tuple(blockers))
