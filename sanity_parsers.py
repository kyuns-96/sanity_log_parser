"""Company report extraction contracts. No database, network or company format.

Adapters implement ReportParser and are explicitly registered by company code.
The bridge owns evaluation, evidence IDs, aggregation and sealing. See
vwp-bridge/PARSER_INTEGRATION.md before implementing an adapter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import copy
from pathlib import Path
import re
from types import MappingProxyType
from typing import Iterable, Literal, Mapping

from sanity_common import ReviewError, canonical_json, read_json

LINE_LIMIT = 1024 * 1024
RAW_LIMIT = 64 * 1024 * 1024
JSON_LIMIT = 16 * 1024 * 1024
BUILTIN_PARSERS = frozenset({"numeric_csv_v1", "summary_json_v1"})
PARSER_CODES = frozenset({"PARSER_NOT_CONFIGURED", "PARSER_REGISTRATION_ERROR",
                          "PARSER_SCOPE_MISMATCH", "PARSER_INVALID_RESULT",
                          "PARSER_FAILED", "PARSER_SOURCE_LIMIT"})


class ParserError(ReviewError):
    """Fixed diagnostics only; never attach report text or an adapter exception."""

    def __init__(self, code: str):
        if code not in PARSER_CODES:
            raise ValueError("Unknown parser error code")
        super().__init__(code, "Company report parser failed")


def _require(condition, code="PARSER_INVALID_RESULT"):
    if not condition:
        raise ParserError(code)


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ParseContext:
    run: Mapping = field(repr=False)
    unit: Mapping = field(repr=False)

    def __post_init__(self):
        object.__setattr__(self, "run", _freeze(copy.deepcopy(dict(self.run))))
        object.__setattr__(self, "unit", _freeze(copy.deepcopy(dict(self.unit))))


@dataclass(frozen=True)
class SourceSpan:
    """Created by ReportSource; byte and physical-line positions are authoritative."""
    line_start: int
    line_end: int
    byte_start: int
    byte_end: int
    _origin: tuple = field(repr=False, compare=False)


@dataclass(frozen=True)
class SourceLine:
    span: SourceSpan
    text: str = field(repr=False)
    encoding_valid: bool = True


@dataclass(frozen=True)
class ParsedRecord:
    """Extract fields, not final evidence IDs or a numerical verdict.

    For a numeric registration, observed is exact decimal text; the bridge applies
    unit.evaluation. For a reported registration, status is an explicit report
    PASS/FAIL mapped to canonical fields. An event cannot change this policy.
    json_pointer is optional and must resolve within a full-document span.
    """
    fields: dict = field(repr=False)
    span: SourceSpan
    json_pointer: str | None = None


@dataclass(frozen=True)
class ReportMetadata:
    """Only format-defined headers, footers, comments and blank lines."""
    span: SourceSpan
    kind: Literal["HEADER", "FOOTER", "COMMENT", "BLANK"]


@dataclass(frozen=True)
class UnparsedRegion:
    """Retain an unexpected/malformed section as UNKNOWN evidence."""
    span: SourceSpan
    reason: str = "UNPARSED_CONTENT"


@dataclass(frozen=True)
class ParseFinished:
    """Last event, after EOF. Derive declared_records/completeness from the format.

    declared_records counts ParsedRecord events, not ignored metadata or unknown
    regions. Zero violations needs a real, explicitly evidenced summary record;
    an empty event stream never means PASS.
    """
    declared_records: int
    complete: bool
    blockers: tuple[str, ...] = ()


ParseEvent = ParsedRecord | ReportMetadata | UnparsedRegion | ParseFinished


class ReportParser(ABC):
    @abstractmethod
    def parse(self, source: ReportSource, context: ParseContext) -> Iterable[ParseEvent]:
        """Read source.lines(), emit ordered spans, finish with ParseFinished."""
        raise NotImplementedError


class ReportSource:
    """A read-only UTF-8 source with bounded physical lines and verified excerpts.

    Used as a context manager by the bridge, not constructed by adapters. This
    interface validates data contracts; it is not a sandbox for Python code.
    """

    def __init__(self, path):
        self._path = Path(path)
        self._key = object()
        self._reader = None
        self._excerpts = None
        self._bytes_read = 0
        self._line_count = 0
        self._eof = False
        self._json = None

    def __enter__(self):
        self._reader = self._path.open("rb")
        try:
            self._excerpts = self._path.open("rb")
        except Exception:
            self._reader.close()
            raise
        return self

    def __exit__(self, *args):
        self._reader.close()
        self._excerpts.close()

    def _span(self, first, last, start, end):
        return SourceSpan(first, last, start, end, (self._key, first, last, start, end))

    def _validate(self, span):
        _require(isinstance(span, SourceSpan))
        values = (span.line_start, span.line_end, span.byte_start, span.byte_end)
        _require(all(type(value) is int for value in values)
                 and 1 <= span.line_start <= span.line_end <= self._line_count
                 and 0 <= span.byte_start < span.byte_end <= self._bytes_read
                 and span._origin == (self._key, *values))

    def lines(self) -> Iterable[SourceLine]:
        _require(self._reader is not None)
        while not self._eof:
            encoded = self._reader.readline(LINE_LIMIT + 1)
            if not encoded:
                self._eof = True
                break
            _require(len(encoded) <= LINE_LIMIT, "PARSER_SOURCE_LIMIT")
            start = self._bytes_read
            self._bytes_read += len(encoded)
            self._line_count += 1
            span = self._span(self._line_count, self._line_count, start, self._bytes_read)
            try:
                text = encoded.decode("utf-8").rstrip("\r\n")
                yield SourceLine(span, text)
            except UnicodeDecodeError:
                yield SourceLine(span, "hex:" + encoded.hex(), False)

    def span(self, first: SourceLine, last: SourceLine) -> SourceSpan:
        """Combine consecutive physical lines, including multi-line records."""
        self._validate(first.span)
        self._validate(last.span)
        _require(first.span.line_start <= last.span.line_end
                 and first.span.byte_start < last.span.byte_end)
        return self._span(first.span.line_start, last.span.line_end,
                          first.span.byte_start, last.span.byte_end)

    def excerpt(self, span: SourceSpan) -> tuple[str, bool]:
        self._validate(span)
        length = span.byte_end - span.byte_start
        _require(length <= RAW_LIMIT, "PARSER_SOURCE_LIMIT")
        self._excerpts.seek(span.byte_start)
        encoded = self._excerpts.read(length)
        _require(len(encoded) == length)
        try:
            return encoded.decode("utf-8").rstrip("\r\n"), True
        except UnicodeDecodeError:
            return "hex:" + encoded.hex(), False

    def _unknown(self, start, end, first_line, reason):
        """Stream omitted input as individual UNKNOWN records, without buffering it."""
        self._excerpts.seek(start)
        number = first_line
        while start < end:
            encoded = self._excerpts.readline(min(LINE_LIMIT + 1, end - start))
            _require(bool(encoded) and len(encoded) <= LINE_LIMIT, "PARSER_SOURCE_LIMIT")
            try:
                raw = encoded.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                raw = "hex:" + encoded.hex()
            yield {}, raw, {"line_start": number, "line_end": number}, "UNKNOWN", [reason]
            start += len(encoded)
            number += 1

    def _json_excerpt(self, span, pointer):
        _require(span.byte_start == 0 and self._eof and span.byte_end == self._bytes_read)
        _require(self._bytes_read <= JSON_LIMIT, "PARSER_SOURCE_LIMIT")
        _require(isinstance(pointer, str) and pointer.startswith("/")
                 and re.search(r"~(?![01])", pointer) is None)
        if self._json is None:
            self._json = read_json(self._path)
        value = self._json
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict):
                _require(token in value)
                value = value[token]
            else:
                _require(isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token) is not None)
                _require(len(token) <= 10 and int(token) < len(value))
                value = value[int(token)]
        return canonical_json(value).decode("utf-8")


@dataclass(frozen=True)
class RegisteredParser:
    parser: ReportParser = field(repr=False)
    implementation_version: str
    evaluation: Literal["numeric", "reported"]
    item_ids: tuple[str, ...]
    tool: str
    tool_versions: tuple[str, ...]
    rule_versions: tuple[str, ...]


class ParserRegistry:
    """Explicit code registration only. A plan cannot import modules or replace built-ins."""

    def __init__(self):
        self._parsers = {}

    def register(self, parser_id: str, parser: ReportParser, *, implementation_version: str,
                 evaluation: Literal["numeric", "reported"], item_ids: tuple[str, ...],
                 tool: str, tool_versions: tuple[str, ...], rule_versions: tuple[str, ...]):
        def valid_text(value):
            return (isinstance(value, str) and bool(value) and value == value.strip()
                    and len(value) <= 100 and "*" not in value
                    and re.search(r"[\x00-\x1f\x7f]", value) is None)

        _require(isinstance(parser_id, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", parser_id)
                 and parser_id not in BUILTIN_PARSERS and parser_id not in self._parsers
                 and isinstance(parser, ReportParser) and valid_text(tool)
                 and valid_text(implementation_version) and evaluation in ("numeric", "reported"),
                 "PARSER_REGISTRATION_ERROR")
        for values in (item_ids, tool_versions, rule_versions):
            _require(isinstance(values, tuple) and bool(values)
                     and all(valid_text(value) for value in values)
                     and len(values) == len(set(values)), "PARSER_REGISTRATION_ERROR")
        self._parsers[parser_id] = RegisteredParser(parser, implementation_version, evaluation,
                                                   item_ids, tool, tool_versions, rule_versions)

    def resolve(self, parser_id, run, unit) -> RegisteredParser | None:
        _require(parser_id is None or isinstance(parser_id, str))
        registered = self._parsers.get(parser_id)
        if registered is None:
            return None
        _require(unit.get("item_id") in registered.item_ids
                 and run.get("tool") == registered.tool
                 and run.get("tool_version") in registered.tool_versions
                 and unit.get("rule_version") in registered.rule_versions
                 and not (registered.evaluation == "reported" and unit.get("evaluation") is not None),
                 "PARSER_SCOPE_MISMATCH")
        return registered

    def contract(self, run, units) -> list[dict]:
        """Only used registrations; versions participate in analysis and bundle IDs."""
        selected = {}
        for unit in units:
            if not unit["applicable"]:
                continue
            registered = self.resolve(unit.get("parser"), run, unit)
            if registered is not None:
                selected[unit["parser"]] = {
                    "parser_id": unit["parser"], "implementation_version": registered.implementation_version,
                    "evaluation": registered.evaluation, "item_ids": sorted(registered.item_ids),
                    "tool": registered.tool, "tool_versions": sorted(registered.tool_versions),
                    "rule_versions": sorted(registered.rule_versions)}
        return [selected[key] for key in sorted(selected)]


def _reason(value):
    _require(isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) is not None)
    return value


def checked_records(parser: ReportParser, path, context: ParseContext, blockers: set,
                    evaluate, *, evaluation: Literal["numeric", "reported"]) -> Iterable[tuple]:
    """Validate the complete event stream before the bridge can seal an analysis."""
    try:
        yield from _checked_records(parser, path, context, blockers, evaluate, evaluation=evaluation)
    except ParserError:
        raise
    except Exception:
        raise ParserError("PARSER_FAILED") from None


def _checked_records(parser, path, context, blockers, evaluate, *, evaluation):
    _require(evaluation in ("numeric", "reported"))
    with ReportSource(path) as source:
        try:
            events = iter(parser.parse(source, context))
        except ParserError:
            raise
        except Exception:
            raise ParserError("PARSER_FAILED") from None
        covered_end, covered_line, previous_span = 0, 0, None
        count, finished = 0, None
        while True:
            try:
                event = next(events)
            except StopIteration:
                break
            except ParserError:
                raise
            except Exception:
                raise ParserError("PARSER_FAILED") from None
            _require(finished is None)  # No records/metadata/second completion after the last event.
            if isinstance(event, ParseFinished):
                _require(type(event.declared_records) is int and event.declared_records >= 0
                         and type(event.complete) is bool and isinstance(event.blockers, tuple))
                blockers.update(_reason(code) for code in event.blockers)
                if not event.complete:
                    blockers.add("INCOMPLETE_REPORT")
                if event.declared_records != count:
                    blockers.add("TOTAL_MISMATCH")
                finished = event
                continue
            _require(isinstance(event, (ParsedRecord, ReportMetadata, UnparsedRegion)))
            source._validate(event.span)
            span = event.span
            repeated_record = (isinstance(event, ParsedRecord) and previous_span == span)
            _require(span.byte_start >= covered_end or repeated_record)
            if span.byte_start > covered_end:
                blockers.add("UNPARSED_CONTENT")
                yield from source._unknown(covered_end, span.byte_start, covered_line + 1, "UNPARSED_CONTENT")
            covered_end, covered_line = span.byte_end, span.line_end
            previous_span = span if isinstance(event, ParsedRecord) else None
            if isinstance(event, ParsedRecord) and event.json_pointer is not None:
                try:
                    raw = source._json_excerpt(span, event.json_pointer)
                except ParserError:
                    raise
                except Exception:
                    raise ParserError("PARSER_INVALID_RESULT") from None
                valid_encoding = True
            else:
                raw, valid_encoding = source.excerpt(span)
            location = {"line_start": span.line_start, "line_end": span.line_end}
            if isinstance(event, ReportMetadata):
                _require(event.kind in ("HEADER", "FOOTER", "COMMENT", "BLANK"))
                if valid_encoding:
                    if event.kind == "BLANK":
                        _require(not raw.strip())
                    continue
            if isinstance(event, ParsedRecord):
                _require(isinstance(event.fields, dict))
                try:
                    canonical_json(event.fields)
                    fields = copy.deepcopy(event.fields)
                except Exception:
                    raise ParserError("PARSER_INVALID_RESULT") from None
                count += 1
                if event.json_pointer is not None:
                    location["json_pointer"] = event.json_pointer
                if valid_encoding:
                    status, reasons = evaluate(fields, numeric=evaluation == "numeric")
                else:
                    status, reasons = "UNKNOWN", ["INVALID_ENCODING"]
            else:
                fields = {}
                status, reasons = "UNKNOWN", [_reason(event.reason)] if isinstance(event, UnparsedRegion) else ["INVALID_ENCODING"]
            blockers.update(reasons)
            yield fields, raw, location, status, reasons
        if finished is None:
            blockers.add("PARSER_MISSING_COMPLETION")
        if not source._eof:
            blockers.add("PARSER_DID_NOT_READ_EOF")
        # Common code reads all remaining bytes. They are never silently treated as metadata.
        for _ in source.lines():
            pass
        if covered_end < source._bytes_read:
            blockers.add("UNPARSED_CONTENT")
            yield from source._unknown(covered_end, source._bytes_read, covered_line + 1, "UNPARSED_CONTENT")
