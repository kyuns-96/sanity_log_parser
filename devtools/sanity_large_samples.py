"""One synthetic native-subset report with many real parser output records.

Uses the public-reference fixture completion markers, never company reports.
Record IDs and line spans come from an actual complete parse, not UI seeding.
"""
import importlib.util
from pathlib import Path
import tempfile

from common.sanity_common import sha256_file
from vwp_bridge.integration.public_parsers import FAMILIES, PROFILE, RULE_VERSION, create_public_registry

ROOT = Path(__file__).resolve().parents[1]
RULES = {"GCA": "CLK_0003", "VCLP": "ISO_CONTROL_CONN", "SPYGLASS": "W415"}


def large_body(family, count=10000, groups=100, design="CPU"):
    if family not in FAMILIES or not 1 <= groups <= count <= 100000:
        raise ValueError("Expected a known family and 1..100,000 synthetic validation records")
    rule = RULES[family]
    lines = [f"# SANITY_SAMPLE family={family} profile={PROFILE}"]
    if family == "GCA":
        lines += ["Report : report_constraint_analysis", "Version: synthetic large public reference",
                  "Scenario Violations Count Waived Description", f"Design: {design}",
                  f"FUNC_SS {count} 0 Scenario violations", f"error {count} 0", f"{rule} {count} 0 Synthetic constraint check"]
    elif family == "VCLP":
        lines += ["report_violations -app LP -verbose", "Version: synthetic large public reference",
                  f"Design top : {design}", "Tree Summary", "Severity Stage Tag Count",
                  f"error Design {rule} {count}", f"Total {count}", "Violation Details"]
    else:
        lines += ["MORESIMPLE REPORT:", "Version: synthetic large public reference",
                  "ID Rule Alias Severity File Line Wt Message"]
    for index in range(1, count+1):
        group = (index-1) % groups
        obj = f"{design.lower()}/u_large_{group:05d}/D"
        if family == "GCA":
            lines.append(f"{index} of {count} 0 Synthetic constraint violation at pin '{obj}'")
        elif family == "VCLP":
            lines += [f"Tag : {rule}", f"ViolationSource : {obj}", f"ViolationSink : {design.lower()}/u_receiver/D"]
        else:
            lines.append(f"[{index}] {rule} {rule} Warning rtl/{design.lower()}_large.sv {10+group} 10 Synthetic RTL finding {obj}")
    lines.append(f"# SANITY_END records={count}")
    return "\n".join(lines)+"\n"


def large_report(family, *, report_id=None, count=10000, groups=100, design="CPU"):
    report_id = report_id or f"large-{family.lower()}-{count}"
    body = large_body(family, count, groups, design)
    info, rule = FAMILIES[family], RULES[family]
    records = []

    class Capture:
        run = {"run_id": report_id, "project": "Synthetic", "design": design, "stage": "POST",
               "tool": info["tool"], "tool_version": PROFILE}

        def record(self, unit, file, index, fields, raw, location, status, reasons):
            records.append({"id": f"{report_id}-e-{len(records)+1:05d}", "fields": fields, "raw": raw,
                            "line_start": location["line_start"], "line_end": location["line_end"],
                            "check_status": status, "reasons": reasons})

    unit = {"review_unit_id": report_id, "item_id": info["item"], "report": "reports/large.rpt",
            "parser": info["parser"], "rule_version": RULE_VERSION,
            "condition": {"mode": "FUNC_SS", "rule": rule, "sample_profile": PROFILE},
            "evaluation": {"operator": "le", "limit": 0, "unit": "violations"},
            "identity_fields": ["object", "check_point", "mode"], "execution_status": "COMPLETED",
            "applicable": True, "exclusion": None}
    spec = importlib.util.spec_from_file_location("large_public_bridge", ROOT / "vwp_bridge/main.py")
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    with tempfile.TemporaryDirectory(prefix="sanity-large-public-") as temp:
        root = Path(temp)
        source = root / unit["report"]
        source.parent.mkdir()
        source.write_text(body)
        result = bridge.analyze_unit(unit, {"file_id": report_id, "path": unit["report"],
            "size_bytes": source.stat().st_size, "sha256": sha256_file(source)}, root, Capture(), create_public_registry())
    if result["parse_status"] != "COMPLETE" or result["record_count"] != count or len(records) != count:
        raise ValueError("Synthetic large report did not parse completely")
    return {"id": report_id, "family": family, "item_id": info["item"], "rule": rule,
            "parser": info["parser"], "rule_version": unit["rule_version"], "records": records,
            "record_count": count, "raw_report": body, "parse_status": result["parse_status"],
            "check_status": result["check_status"], "analysis_ready": True, "blockers": result["blockers"],
            "data_origin": "SYNTHETIC_PUBLIC_REFERENCE_LARGE", "condition": unit["condition"]}
