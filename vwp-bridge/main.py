#!/usr/bin/env python3
"""Item-specific, offline analysis. No Jira or AI access occurs at this stage."""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sanity_common import (ReviewError, atomic_json, canonical_json, digest, read_json, require_id,
                           safe_path, sha256_file, validate_payload,
                           identity_value_valid)
from sanity_db import Database
from sanity_evidence import PgEvidence, PRODUCER_VERSION
from sanity_parsers import ParseContext, ParserRegistry, checked_records

HEADER = ["object", "check_point", "mode", "code", "severity", "observed", "unit"]
JSON_LIMIT = 16 * 1024 * 1024
LINE_LIMIT = 1024 * 1024
EXAMPLE_LIMIT = 2048
DECIMAL_PATTERN = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"


def require(condition, code, message):
    if not condition:
        raise ReviewError(code, message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def validate_manifest(manifest, root):
    require(isinstance(manifest, dict), "BAD_MANIFEST", "Manifest must be an object")
    require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1 and manifest.get("ready") is True,
            "NOT_READY", "Expected ready schema version 1 manifest")
    run = manifest.get("run")
    require(isinstance(run, dict), "BAD_MANIFEST", "Missing run")
    for field in ("run_id", "project", "design", "stage", "tool", "tool_version"):
        require(nonempty(run.get(field)), "BAD_MANIFEST", "Invalid run field " + field)
    require_id(run["run_id"], "run_id")
    inventory = manifest.get("inventory")
    require(isinstance(inventory, dict), "BAD_MANIFEST", "Missing inventory")
    require(type(inventory.get("confirmed")) is bool and nonempty(inventory.get("source"))
            and nonempty(inventory.get("version")), "BAD_MANIFEST", "Invalid inventory provenance")
    require(inventory.get("sha256") == digest({k: v for k, v in inventory.items()
                                               if k != "sha256"}),
            "INVENTORY_INTEGRITY", "Inventory hash mismatch")
    units = inventory.get("units")
    require(isinstance(units, list), "BAD_MANIFEST", "Inventory units must be a list")
    unit_ids, reports = set(), set()
    for unit in units:
        require(isinstance(unit, dict), "BAD_MANIFEST", "Invalid unit")
        uid = require_id(unit.get("review_unit_id"), "review_unit_id")
        require_id(unit.get("item_id"), "item_id")
        require(uid not in unit_ids, "BAD_MANIFEST", "Duplicate review unit")
        unit_ids.add(uid)
        path = unit.get("report")
        require(isinstance(path, str) and path.startswith("reports/"),
                "BAD_MANIFEST", "Report must be under reports/")
        safe_path(root, path)
        require(path not in reports, "BAD_MANIFEST", "Duplicate report path")
        reports.add(path)
        require(type(unit.get("applicable")) is bool, "BAD_MANIFEST", "Invalid applicability")
        require(unit.get("execution_status") in ("COMPLETED", "FAILED", "UNKNOWN", "NOT_RUN"),
                "BAD_MANIFEST", "Invalid execution status")
        require(nonempty(unit.get("rule_version")), "BAD_MANIFEST", "Missing rule version")
    files = manifest.get("files")
    require(isinstance(files, list), "BAD_MANIFEST", "Missing file list")
    by_path, ids = {}, set()
    for file in files:
        require(isinstance(file, dict), "BAD_MANIFEST", "Invalid source file")
        fid = require_id(file.get("file_id"), "file_id")
        path = file.get("path")
        require(isinstance(path, str) and path.startswith("reports/"),
                "BAD_MANIFEST", "Invalid source path")
        source = safe_path(root, path)
        require(path not in by_path and fid not in ids, "BAD_MANIFEST", "Duplicate source file")
        require(type(file.get("size_bytes")) is int and file["size_bytes"] >= 0,
                "BAD_MANIFEST", "Invalid file size")
        require(source.is_file() and source.stat().st_size == file["size_bytes"],
                "FILE_INTEGRITY", "Missing source or size mismatch")
        require(sha256_file(source) == file.get("sha256"), "FILE_INTEGRITY", "Source hash mismatch")
        by_path[path] = file
        ids.add(fid)
    unexpected = manifest.get("unexpected_files")
    require(isinstance(unexpected, list) and all(isinstance(p, str) for p in unexpected),
            "BAD_MANIFEST", "Invalid unexpected files")
    require(set(unexpected) == set(by_path) - reports and len(set(unexpected)) == len(unexpected),
            "BAD_MANIFEST", "Unexpected file list mismatch")
    actual_files = set()
    report_root = root / "reports"
    require(not report_root.is_symlink(), "UNSAFE_PATH", "Report directory cannot be a symlink")
    if report_root.exists():
        require(report_root.is_dir(), "BAD_MANIFEST", "Report root must be a directory")
        for entry in report_root.rglob("*"):
            require(not entry.is_symlink(), "UNSAFE_PATH", "Report symlinks are not allowed")
            if entry.is_file():
                actual_files.add(entry.relative_to(root).as_posix())
            else:
                require(entry.is_dir(), "UNSAFE_PATH", "Unsupported report file type")
    require(actual_files == set(by_path), "FILE_INTEGRITY", "Unlisted or missing snapshot file")
    return by_path


class Evidence(PgEvidence):
    """Item identity rules over bounded PostgreSQL-backed evidence batches."""

    def record(self, unit, file, index, fields, raw, location, status, reasons):
        rid = "r-" + digest([self.run["run_id"], unit["review_unit_id"], file["file_id"], index])
        example = {"record_id": rid, "file_id": file["file_id"], **location,
                   "raw": raw[:EXAMPLE_LIMIT]}
        if len(raw) > EXAMPLE_LIMIT:
            example["raw_omitted_chars"] = len(raw) - EXAMPLE_LIMIT
        for key in ("observed", "expected", "unit"):
            if key in fields:
                example[key] = fields[key]
        gid = None
        if status != "PASS":
            identity_fields = unit.get("identity_fields")
            valid_rule = (isinstance(identity_fields, list) and bool(identity_fields)
                          and all(nonempty(f) for f in identity_fields)
                          and len(identity_fields) == len(set(identity_fields)))
            condition = unit.get("condition", {})
            values = {**self.run, **(condition if isinstance(condition, dict) else {}), **fields}
            identity = {f: values.get(f) for f in identity_fields} if valid_rule else {}
            complete = valid_rule and all(identity_value_valid(field, value)
                                          for field, value in identity.items())
            complete = complete and nonempty(fields.get("code")) and nonempty(fields.get("severity"))
            key = {"run_id": self.run["run_id"], "review_unit_id": unit["review_unit_id"],
                   "item_id": unit["item_id"], "code": fields.get("code"),
                   "severity": fields.get("severity"), "rule_version": unit["rule_version"],
                   "identity": identity}
            if not complete:
                key["isolated_record"] = rid
            gid = "g-" + digest(key)
            group = {"finding_group_id": gid, "review_unit_id": unit["review_unit_id"],
                         "item_id": unit["item_id"], "message_code": fields.get("code") if nonempty(fields.get("code")) else "UNKNOWN",
                         "severity": fields.get("severity") if nonempty(fields.get("severity")) else "UNKNOWN", "identity": identity,
                         "identity_status": "SAME_FINDING" if complete else "UNDECIDED",
                         "rule_version": unit["rule_version"], "occurrence_count": 0,
                         "status_counts": {"PASS": 0, "FAIL": 0, "UNKNOWN": 0},
                         "examples": [], "examples_omitted": 0}
            self.stage("GROUP", gid, {"key": key, "template": group}, "RECORDED", unit["review_unit_id"],
                       self.row_id("UNIT", unit["review_unit_id"]))
        evidence = {**{k: v for k, v in example.items() if k != "raw"}, "fields": fields, "check_status": status,
                    "reasons": reasons, "review_unit_id": unit["review_unit_id"],
                    "item_id": unit["item_id"], "rule_version": unit["rule_version"],
                    "finding_group_id": gid, "record_index": index, "example": example}
        self.stage("EVIDENCE", rid, evidence, "RECORDED", unit["review_unit_id"],
                   self.row_id("UNIT", unit["review_unit_id"]), raw=raw)


def evaluate(fields, unit, numeric=False):
    reasons = []
    for key in ("code", "severity"):
        if not nonempty(fields.get(key)):
            reasons.append("MISSING_" + key.upper())
    if fields.get("severity") not in ("ERROR", "WARNING", "INFO", "FATAL"):
        reasons.append("INVALID_SEVERITY")
    # Identity-only omissions are allowed; malformed identity values are not silently coerced.
    for key in ("object", "check_point", "mode"):
        value = fields.get(key)
        if value is not None and not isinstance(value, str):
            reasons.append("INVALID_" + key.upper())
    condition = unit.get("condition", {})
    if not isinstance(condition, dict):
        reasons.append("INVALID_CONDITION")
    elif "mode" in condition and fields.get("mode") != condition["mode"]:
        reasons.append("MODE_MISMATCH")
    if numeric:
        rule = unit.get("evaluation")
        if not isinstance(rule, dict):
            rule = {}
        limit = rule.get("limit")
        valid_limit = type(limit) is int or (isinstance(limit, str) and
                                           re.fullmatch(DECIMAL_PATTERN, limit) is not None)
        try:
            exact_limit = Decimal(str(limit)) if valid_limit else None
            valid_limit = exact_limit is not None and exact_limit.is_finite()
        except InvalidOperation:
            valid_limit = False
        if rule.get("operator") not in ("le", "ge") or not valid_limit or not nonempty(rule.get("unit")):
            reasons.append("INVALID_EVALUATION_RULE")
        if valid_limit:
            fields["expected"] = limit
        if fields.get("unit") != rule.get("unit") or not nonempty(fields.get("unit")):
            reasons.append("UNIT_MISMATCH")
        try:
            observed = fields.get("observed")
            if not isinstance(observed, str) or not re.fullmatch(DECIMAL_PATTERN, observed):
                raise ValueError()
            value = Decimal(observed)
            if not value.is_finite():
                raise ValueError()
            # Keep the exact lexical measurement as JSON text; never round before comparison.
            fields["observed"] = observed
        except (InvalidOperation, ValueError, TypeError, OverflowError):
            reasons.append("INVALID_MEASUREMENT")
        if reasons:
            return "UNKNOWN", reasons
        passed = value <= exact_limit if rule["operator"] == "le" else value >= exact_limit
        return ("PASS" if passed else "FAIL"), []
    if fields.get("status") not in ("PASS", "FAIL"):
        reasons.append("INVALID_STATUS")
    for key in ("object", "check_point", "mode"):
        if not nonempty(fields.get(key)):
            reasons.append("MISSING_" + key.upper())
    return ("UNKNOWN", reasons) if reasons else (fields["status"], [])


def numeric_records(path, unit, blockers):
    """One physical line per CSV record is the explicit prototype format contract."""
    count = 0
    footer = False
    with path.open("rb") as stream:
        for number, encoded in enumerate(stream, 1):
            location = {"line_start": number, "line_end": number}
            try:
                raw = encoded.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                blockers.add("INVALID_ENCODING")
                raw = "hex:" + encoded.hex()
                if number > 1:
                    count += 1
                yield {}, raw, location, "UNKNOWN", ["INVALID_ENCODING"]
                continue
            if len(encoded) > LINE_LIMIT:
                blockers.add("RECORD_SIZE_LIMIT")
                if number > 1:
                    count += 1
                yield {}, raw, location, "UNKNOWN", ["RECORD_SIZE_LIMIT"]
                continue
            if number == 1:
                if raw != ",".join(HEADER):
                    blockers.add("INVALID_HEADER")
                    yield {}, raw, location, "UNKNOWN", ["INVALID_HEADER"]
                continue
            if raw.startswith("# END") and not footer:
                footer = True
                match = re.fullmatch(r"# END rows=(0|[1-9][0-9]*)", raw)
                if not match or match[1] != str(count):
                    blockers.add("TOTAL_MISMATCH")
                    yield {}, raw, location, "UNKNOWN", ["TOTAL_MISMATCH"]
                continue
            count += 1
            if footer:
                blockers.add("TRAILING_CONTENT")
                yield {}, raw, location, "UNKNOWN", ["TRAILING_CONTENT"]
                continue
            try:
                row = next(csv.reader([raw], strict=True))
                if len(row) != len(HEADER):
                    raise ValueError()
                fields = dict(zip(HEADER, row))
            except (csv.Error, ValueError, StopIteration):
                blockers.add("MALFORMED_ROW")
                yield {}, raw, location, "UNKNOWN", ["MALFORMED_ROW"]
                continue
            status, reasons = evaluate(fields, unit, numeric=True)
            blockers.update(reasons)
            yield fields, raw, location, status, reasons
    if not footer:
        blockers.add("MISSING_END_MARKER")
    if count == 0:
        blockers.add("EMPTY_REPORT")


def raw_records(path, reason):
    with path.open("rb") as stream:
        for number, encoded in enumerate(stream, 1):
            try:
                raw = encoded.decode("utf-8").rstrip("\r\n")
            except UnicodeDecodeError:
                raw = "hex:" + encoded.hex()
            yield {}, raw, {"line_start": number, "line_end": number}, "UNKNOWN", [reason]


def summary_records(path, unit, blockers):
    if path.stat().st_size > JSON_LIMIT:
        blockers.add("REPORT_SIZE_LIMIT")
        yield from raw_records(path, "REPORT_SIZE_LIMIT")
        return
    try:
        report = read_json(path)
    except (ReviewError, UnicodeError, ValueError):
        blockers.add("MALFORMED_JSON")
        yield from raw_records(path, "MALFORMED_JSON")
        return
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        blockers.add("INVALID_SUMMARY")
        yield from raw_records(path, "INVALID_SUMMARY")
        return
    if type(report.get("format_version")) is not int or report["format_version"] != 1 or report.get("complete") is not True:
        blockers.add("INCOMPLETE_SUMMARY")
    if type(report.get("total")) is not int or report["total"] != len(report["results"]):
        blockers.add("TOTAL_MISMATCH")
    if not report["results"]:
        blockers.add("EMPTY_REPORT")
    with path.open("rb") as stream:
        line_count = sum(1 for _ in stream)
    for index, original in enumerate(report["results"]):
        fields = dict(original) if isinstance(original, dict) else {}
        status, reasons = evaluate(fields, unit)
        blockers.update(reasons)
        raw = canonical_json(original).decode()
        yield fields, raw, {"line_start": 1, "line_end": max(1, line_count),
                            "json_pointer": "/results/" + str(index)}, status, reasons


def analyze_unit(unit, source, root, evidence, parser_registry=None):
    result = {key: unit.get(key) for key in ("review_unit_id", "item_id", "report",
              "execution_status", "applicable", "exclusion", "rule_version", "parser", "condition",
              "evaluation", "identity_fields")}
    result.update(parse_status="COMPLETE", check_status="UNKNOWN", blockers=[],
                  record_count=0, evaluated_count=0, unknown_count=0,
                  status_counts={"PASS": 0, "FAIL": 0, "UNKNOWN": 0})
    blockers = set()
    if not unit["applicable"]:
        exclusion = unit.get("exclusion")
        if not isinstance(exclusion, dict) or not all(nonempty(exclusion.get(f)) for f in ("reason", "approved_by", "reference")):
            blockers.add("INVALID_EXCLUSION")
        else:
            result.update(parse_status="NOT_APPLICABLE", check_status="NOT_APPLICABLE")
        if unit["execution_status"] == "FAILED":
            blockers.add("FAILED_EXECUTION_EXCLUDED")
        result["blockers"] = sorted(blockers)
        return result
    if unit["execution_status"] != "COMPLETED":
        blockers.add("EXECUTION_NOT_COMPLETED")
    if source is None:
        blockers.add("REPORT_MISSING")
        result.update(parse_status="MISSING", blockers=sorted(blockers))
        return result
    parser = unit.get("parser")
    path = safe_path(root, source["path"])
    if parser == "numeric_csv_v1":
        records = numeric_records(path, unit, blockers)
    elif parser == "summary_json_v1":
        records = summary_records(path, unit, blockers)
    elif parser_registry is not None and (registered := parser_registry.resolve(parser, evidence.run, unit)) is not None:
        context = ParseContext(evidence.run, unit)
        records = checked_records(registered.parser, path, context, blockers,
                                  lambda fields, numeric: evaluate(fields, unit, numeric=numeric),
                                  evaluation=registered.evaluation)
    else:
        blockers.add("UNSUPPORTED_PARSER")
        result["parse_status"] = "UNSUPPORTED"
        records = raw_records(path, "UNSUPPORTED_PARSER")
    for index, (fields, raw, location, status, reasons) in enumerate(records, 1):
        evidence.record(unit, source, index, fields, raw, location, status, reasons)
        result["status_counts"][status] += 1
        result["record_count"] += 1
    result["unknown_count"] = result["status_counts"]["UNKNOWN"]
    result["evaluated_count"] = result["status_counts"]["PASS"] + result["status_counts"]["FAIL"]
    if result["record_count"] == 0:
        blockers.add("EMPTY_REPORT")
    if blockers and result["parse_status"] == "COMPLETE":
        result["parse_status"] = "PARTIAL"
    result["blockers"] = sorted(blockers)
    result["check_status"] = ("FAIL" if result["status_counts"]["FAIL"] else
                              "UNKNOWN" if blockers or result["unknown_count"] else "PASS")
    return result


def analyze(input_path, out_dir=None, parser_registry=None):
    input_path = Path(input_path)
    manifest = read_json(input_path)
    root = input_path.resolve().parent
    files = validate_manifest(manifest, root)
    parser_contract = None
    if parser_registry is not None:
        require(isinstance(parser_registry, ParserRegistry), "PARSER_REGISTRATION_ERROR", "Invalid parser registry")
        parser_contract = parser_registry.contract(manifest["run"], manifest["inventory"]["units"])
    with Database("vwp") as db:
        evidence = Evidence(db, manifest, parser_contract=parser_contract)
        inventory = manifest["inventory"]
        units = sorted(inventory["units"], key=lambda unit: unit["review_unit_id"])
        results = []
        for unit in units:
            result = analyze_unit(unit, files.get(unit["report"]), root, evidence, parser_registry)
            evidence.unit_result(result)
            results.append(result)
        global_blockers = []
        if inventory.get("confirmed") is not True:
            global_blockers.append("INVENTORY_UNCONFIRMED")
        if not units:
            global_blockers.append("EMPTY_INVENTORY")
        if not nonempty(inventory.get("source")) or not nonempty(inventory.get("version")):
            global_blockers.append("INVENTORY_PROVENANCE_MISSING")
        if manifest["unexpected_files"]:
            global_blockers.append("UNEXPECTED_REPORTS")
        payload = {"schema_version": 1, "run": manifest["run"],
                   "inventory": {"confirmed": inventory.get("confirmed") is True,
                                 "source": inventory.get("source", ""), "version": inventory.get("version", ""),
                                 "sha256": inventory["sha256"],
                                 "expected_unit_ids": [u["review_unit_id"] for u in units]},
                   "review_units": results, "finding_groups": evidence.groups(),
                   "global_blockers": sorted(global_blockers),
                   "source_files": sorted(files.values(), key=lambda f: f["path"]),
                   "producer": {"version": PRODUCER_VERSION}}
        if parser_contract:
            payload["producer"]["parsers"] = parser_contract
        payload["bundle_id"] = "b-" + digest(payload)
        validate_payload(payload)
        # Recheck after parsing to catch a report changing during analysis.
        validate_manifest(manifest, root)
        receipt = evidence.seal(payload)
    if out_dir is not None:
        atomic_json(Path(out_dir) / "receipt.json", receipt)
    return payload, receipt


def main(argv=None, parser_registry=None):
    parser = argparse.ArgumentParser(description="VWP parser and PostgreSQL evidence/transport bridge")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("analyze")
    command.add_argument("--input", required=True)
    command.add_argument("--out", help="optional local receipt directory; never used as transport")
    respond = commands.add_parser("respond", help="handle a specific OA evidence request through PostgreSQL")
    respond.add_argument("--request-id", required=True)
    permission = respond.add_mutually_exclusive_group()
    permission.add_argument("--approve-export", action="store_true", help="compatibility flag; normal report access is already allowed")
    permission.add_argument("--reject", action="store_true", help="record a final export rejection")
    worker = commands.add_parser("work", help="poll DB classification and ordinary evidence requests")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        if args.command == "work":
            from sanity_workers import work
            print(json.dumps(work("vwp", args.once, args.interval), sort_keys=True))
            return 0
        if args.command == "respond":
            from sanity_exchange import respond_evidence
            print(json.dumps(respond_evidence(args.request_id, not args.reject), sort_keys=True))
            return 0
        payload, receipt = analyze(args.input, args.out, parser_registry)
        print(json.dumps({"status": "ENQUEUED", **receipt, "units": len(payload["review_units"]),
                          "groups": len(payload["finding_groups"])}, sort_keys=True))
        return 0
    except ReviewError as exc:
        print("code=" + exc.code, file=sys.stderr)
        return 2
    except OSError:
        print("code=IO_ERROR", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
