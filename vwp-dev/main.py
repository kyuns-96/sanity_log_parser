#!/usr/bin/env python3
"""Snapshot a completed sanity run; demo inputs are synthetic, not real tools."""

import argparse
import copy
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sanity_common import (ReviewError, atomic_json, atomic_text, digest, read_json,
                           require_id, safe_path, sha256_file)


def _require(condition, message):
    if not condition:
        raise ReviewError("INVALID_PLAN", message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_plan(plan):
    """Validate collection metadata without guessing report parser semantics."""
    _require(isinstance(plan, dict), "plan must be an object")
    _require(type(plan.get("schema_version")) is int and plan["schema_version"] == 1,
             "schema_version must be 1")
    run = plan.get("run")
    _require(isinstance(run, dict), "run must be an object")
    for key in ("run_id", "project", "design", "stage", "tool", "tool_version"):
        _require(_text(run.get(key)), f"missing run.{key}")
    require_id(run["run_id"], "run_id")
    inventory = plan.get("inventory")
    _require(isinstance(inventory, dict), "inventory must be an object")
    _require(type(inventory.get("confirmed")) is bool, "inventory.confirmed must be boolean")
    for key in ("source", "version"):
        _require(_text(inventory.get(key)), f"missing inventory.{key}")
    units = inventory.get("units")
    _require(isinstance(units, list), "inventory.units must be a list")
    unit_ids, reports = set(), set()
    for unit in units:
        _require(isinstance(unit, dict), "unit must be an object")
        uid = require_id(unit.get("review_unit_id"), "review_unit_id")
        require_id(unit.get("item_id"), "item_id")
        _require(uid not in unit_ids, "duplicate review_unit_id")
        unit_ids.add(uid)
        report = unit.get("report")
        _require(isinstance(report, str) and report.startswith("reports/"),
                 "report must start with reports/")
        parts = PurePosixPath(report).parts
        _require(len(parts) >= 2 and not any(p in (".", "..") for p in parts)
                 and "\\" not in report and "\x00" not in report
                 and str(PurePosixPath(report)) == report,
                 "report must be a canonical relative path")
        _require(report not in reports, "duplicate report path")
        reports.add(report)
        for key in ("parser", "rule_version"):
            _require(_text(unit.get(key)), f"missing {key}")
        _require(isinstance(unit.get("condition"), dict), "condition must be an object")
        fields = unit.get("identity_fields")
        _require(isinstance(fields, list) and all(_text(f) for f in fields)
                 and len(fields) == len(set(fields)), "invalid identity_fields")
        _require(unit.get("execution_status") in ("COMPLETED", "FAILED", "UNKNOWN", "NOT_RUN"),
                 "invalid execution_status")
        _require(type(unit.get("applicable")) is bool, "applicable must be boolean")
        # Missing exclusion approval is intentionally retained for coverage to flag.
    return reports


def _prepare_output(out):
    out = Path(out).absolute()
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise ReviewError("OUTPUT_EXISTS", "output must be absent or an empty directory")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def collect(plan_path, reports_dir, out):
    plan = read_json(plan_path)
    expected = validate_plan(plan)
    if Path(reports_dir).is_symlink():
        raise ReviewError("UNSAFE_SOURCE", "source symlinks are not supported")
    source = Path(reports_dir).resolve(strict=True)
    if not source.is_dir():
        raise ReviewError("INVALID_SOURCE", "reports must be a directory")
    out = Path(out).absolute()
    resolved_out = out.resolve()
    if resolved_out == source or source in resolved_out.parents:
        raise ReviewError("UNSAFE_OUTPUT", "output must not be inside source reports")
    out = _prepare_output(out)
    with tempfile.TemporaryDirectory(prefix=".sanity-collect-", dir=out.parent) as temporary:
        staged = Path(temporary) / "snapshot"
        staged.mkdir()
        (staged / "reports").mkdir()
        files = []
        # Reject directory links too: silently omitting them could hide reports.
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise ReviewError("UNSAFE_SOURCE", "source symlinks are not supported")
            if path.is_dir():
                continue
            if not path.is_file():
                raise ReviewError("UNSAFE_SOURCE", "source must contain regular files only")
            relative = "reports/" + path.relative_to(source).as_posix()
            destination = safe_path(staged, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            before = path.stat()
            before_hash = sha256_file(path)
            shutil.copyfile(path, destination)
            after = path.stat()
            copied_hash = sha256_file(destination)
            after_hash = sha256_file(path)
            if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                     after.st_ctime_ns) or before_hash != copied_hash or after_hash != copied_hash):
                raise ReviewError("SOURCE_CHANGED", "report changed during collection")
            files.append({"file_id": "f-" + digest(relative), "path": relative,
                          "size_bytes": destination.stat().st_size, "sha256": copied_hash})
        result = copy.deepcopy(plan)
        result["inventory"].pop("sha256", None)
        result["inventory"]["sha256"] = digest(result["inventory"])
        result["files"] = files
        result["unexpected_files"] = [f["path"] for f in files if f["path"] not in expected]
        result["ready"] = True
        atomic_json(staged / "run.json", result)
        # The directory becomes visible only after the full snapshot is complete.
        if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
            raise ReviewError("OUTPUT_EXISTS", "output changed during collection")
        os.replace(staged, out)
    return result


def demo_plan(scenario):
    numeric = {"review_unit_id": "u-numeric", "item_id": "numeric_limits",
               "report": "reports/numeric.csv", "parser": "numeric_csv_v1",
               "rule_version": "1", "condition": {"mode": "M1"},
               "identity_fields": ["object", "check_point", "mode"],
               "evaluation": {"operator": "le", "limit": 10, "unit": "ns"},
               "execution_status": "COMPLETED", "applicable": True, "exclusion": None}
    summary = {"review_unit_id": "u-summary", "item_id": "summary_checks",
               "report": "reports/summary.json", "parser": "summary_json_v1",
               "rule_version": "1", "condition": {"mode": "M1"},
               "identity_fields": ["object", "check_point", "mode"],
               "execution_status": "COMPLETED", "applicable": True, "exclusion": None}
    excluded = {"review_unit_id": "u-excluded", "item_id": "optional_checks",
                "report": "reports/optional.json", "parser": "summary_json_v1",
                "rule_version": "1", "condition": {"mode": "M1"},
                "identity_fields": ["object"], "execution_status": "NOT_RUN",
                "applicable": False,
                "exclusion": {"reason": "Synthetic design has no optional block",
                              "approved_by": "demo-owner", "reference": "DEMO-SCOPE-1"}}
    if scenario == "unsupported":
        summary["parser"] = "vendor_summary_v99"
    return {"schema_version": 1,
            "run": {"run_id": "demo-" + scenario, "project": "demo", "design": "chip",
                    "stage": "sanity", "tool": "synthetic", "tool_version": "1"},
            "inventory": {"confirmed": True, "source": "synthetic-demo-plan",
                          "version": "demo-v1", "units": [numeric, summary, excluded]}}


def demo(out, scenario="complete"):
    with tempfile.TemporaryDirectory(prefix="sanity-demo-") as temporary:
        root = Path(temporary)
        reports = root / "reports"
        reports.mkdir()
        plan = demo_plan(scenario)
        atomic_json(root / "plan.json", plan)
        numeric = ("object,check_point,mode,code,severity,observed,unit\n"
                   "top/A,P1,M1,CHECK-101,WARNING,12,ns\n"
                   "top/A,P1,M1,CHECK-101,WARNING,13,ns\n"
                   "top/B,P1,M1,CHECK-101,WARNING,12,ns\n"
                   "top/C,P1,M1,CHECK-101,WARNING,8,ns\n"
                   "# END rows=4\n")
        if scenario == "malformed":
            numeric = numeric.replace("# END rows=4\n", "")
        atomic_text(reports / "numeric.csv", numeric)
        if scenario != "missing":
            results = [
                {"object": "top/D", "check_point": "S1", "mode": "M1", "code": "CHECK-201",
                 "severity": "ERROR", "status": "PASS"},
                {"object": "top/E", "check_point": "S1", "mode": "M1", "code": "CHECK-201",
                 "severity": "ERROR", "status": "FAIL"}]
            atomic_json(reports / "summary.json",
                        {"format_version": 1, "complete": True, "results": results, "total": 2})
        return collect(root / "plan.json", reports, out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("demo", help="create synthetic collected reports")
    command.add_argument("--out", required=True)
    command.add_argument("--scenario", choices=("complete", "missing", "malformed", "unsupported"),
                         default="complete")
    command = commands.add_parser("collect", help="snapshot already-finished reports")
    command.add_argument("--plan", required=True)
    command.add_argument("--reports", required=True)
    command.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        result = demo(args.out, args.scenario) if args.command == "demo" else collect(
            args.plan, args.reports, args.out)
        print(f"status=READY run_id={result['run']['run_id']} "
              f"units={len(result['inventory']['units'])} files={len(result['files'])} "
              f"unexpected={len(result['unexpected_files'])}")
        return 0
    except ReviewError as error:
        print(f"code={error.code} {error}", file=sys.stderr)
    except (OSError, ValueError, TypeError) as error:
        print(f"code=COLLECT_ERROR {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
