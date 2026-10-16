"""Deterministic synthetic report library: 10 users, 3 projects, 9 scoped blocks.

Writes original synthetic messages only. Vendor reports/documents are not copied.
Data is parsed by the initial public extractors, not assigned UI verdicts here.
"""
from pathlib import Path

from common.sanity_common import atomic_json, atomic_text
from vwp_bridge.integration.public_parsers import FAMILIES, PROFILE, RULE_VERSION

SOURCES = {
    "GCA": {"title": "Synopsys PrimeTime GCA User Guide L-2016.06 · Example 2-8",
            "url": "https://www.scribd.com/document/807828202/PrimeTime-GCA-User-Guide-2016"},
    "VCLP": {"title": "Synopsys VC LP User Guide · Message Reporting and Iteration",
             "url": "https://www.scribd.com/document/624722971/VC-LP-UG"},
    "SPYGLASS": {"title": "Synopsys SpyGlass Explorer User Guide W-2024.09 · Reports",
                 "url": "https://studylib.net/doc/28896253/spyglass-explorer-userguide"},
}
RULES = {"GCA": ("CLK_0003", "CAS_0003", "CLK_0026", "EXD_0012"),
         "VCLP": ("ISO_CONTROL_CONN", "LS_MAP_UNAVAIL", "ISO_STRATEGY_REDUND"),
         "SPYGLASS": ("W415", "W123", "W396")}


def sample_body(family, rule, design, index, case):
    count = 0 if case == "clean" else 1
    lines = [f"# SANITY_SAMPLE family={family} profile={PROFILE}"]
    obj = f"{design.lower()}/u_sample_{index:04d}/D"
    if family == "GCA":
        lines += ["****************************************", "Report : report_constraint_analysis",
                  "-include {violations}", "-style {full}", "Version: synthetic public reference",
                  "****************************************", "Scenario Violations Count Waived Description",
                  f"Design: {design}", f"FUNC_SS {count} 0 Scenario violations", f"error {count} 0",
                  f"{rule} {count} 0 Synthetic constraint check"]
        if count:
            lines += [f"1 of 1 0 Synthetic constraint violation at pin '{obj}'",
                      "+ clock=sample_clk source=sample_source"]
    elif family == "VCLP":
        lines += ["report_violations -app LP -verbose", "Version: synthetic public reference",
                  f"Design top : {design}", "Tree Summary", "Severity Stage Tag Count",
                  f"error Design {rule} {count}", f"Total {count}", "Violation Details"]
        if count:
            lines += [f"Tag : {rule}", "Description : Synthetic power-domain crossing check",
                      f"ViolationSource : {obj}", f"ViolationSink : {design.lower()}/u_receiver/D",
                      "DomainSource : PD_SAMPLE_OFF", "DomainSink : PD_SAMPLE_ON"]
    else:
        lines += ["MORESIMPLE REPORT:", "Version: synthetic public reference",
                  "############### BuiltIn -> RuleGroup=Lint ###############",
                  "ID Rule Alias Severity File Line Wt Message", "=" * 72]
        if count:
            lines += [f"[{index}] {rule} {rule} Warning rtl/{design.lower()}_sample.sv {10 + index % 400} 10 Synthetic RTL assignment finding",
                      "+ instance=u_sample signal=sample_signal"]
    if not count:
        lines.append(f"# SANITY_ZERO rule={rule} violations=0")
    if case == "unparsed":
        lines.append("UNSUPPORTED SYNTHETIC SECTION: additional detail needs site verification")
    if case != "truncated":
        lines.append("# SANITY_END records=1")
    return "\n".join(lines) + "\n"


def generate_library(out, count=1000):
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use an empty sample output directory")
    out.mkdir(parents=True, exist_ok=True)
    users = [{"id": f"user-{i:02d}", "name": f"가상 사용자 {i:02d}",
              "role": "TOP" if i <= 3 else "블록 담당자"} for i in range(1, 11)]
    projects, blocks, runs, items = [], [], {}, []
    names = ("Atlas", "Boreal", "Cygnus")
    owners = (4, 5, 6, 7, 8, 9, 10, 4, 5)
    for pi, name in enumerate(names):
        project_id = f"project-{pi + 1}"
        projects.append({"id": project_id, "name": name, "top_user": users[pi]["id"]})
        for bi, block_name in enumerate(("CPU", "GPU", "DDR")):
            blocks.append({"id": f"{project_id}-{block_name.lower()}", "project_id": project_id,
                           "name": block_name, "owner": f"user-{owners[pi * 3 + bi]:02d}",
                           "net_ver": "NET_001", "revision": f"SYNTHETIC_{name}_{block_name}_R01",
                           "layout": "POST", "eco": "chip_finish"})
    for index in range(count):
        block = blocks[index % 9]
        project = projects[(index % 9) // 3]
        # Rotate inside a block too; the project/block dimension is independent of tool.
        family = tuple(FAMILIES)[(index // 9 + index % 9) % 3]
        spec = FAMILIES[family]
        rule = RULES[family][(index // 3) % len(RULES[family])]
        remainder = index % 20
        case = {0: "missing", 1: "truncated", 2: "unparsed"}.get(remainder, "finding" if remainder < 8 else "clean")
        run_key = f"{block['id']}-{family.lower()}"
        run_dir = out / run_key
        if run_key not in runs:
            (run_dir / "reports").mkdir(parents=True, exist_ok=True)
            runs[run_key] = {"directory": run_dir, "schema_version": 1,
                            "run": {"run_id": "sample-" + run_key, "project": project["name"],
                                    "design": block["name"], "stage": "POST",
                                    "tool": spec["tool"], "tool_version": PROFILE},
                            "inventory": {"confirmed": True, "source": "synthetic-public-report-library",
                                          "version": PROFILE, "units": []}}
        uid = f"sample-{index + 1:04d}"
        report = f"reports/{uid}.rpt"
        unit = {"review_unit_id": uid, "item_id": spec["item"], "report": report,
                "parser": spec["parser"], "rule_version": RULE_VERSION,
                "condition": {"mode": "FUNC_SS", "rule": rule, "sample_profile": PROFILE},
                "evaluation": {"operator": "le", "limit": 0, "unit": "violations"},
                "identity_fields": ["object", "check_point", "mode"],
                "execution_status": "COMPLETED", "applicable": True, "exclusion": None}
        runs[run_key]["inventory"]["units"].append(unit)
        if case != "missing":
            atomic_text(run_dir / report, sample_body(family, rule, block["name"], index + 1, case))
        items.append({"id": uid, "run_key": run_key, "project_id": project["id"],
                      "block_id": block["id"], "family": family, "rule": rule,
                      "report_path": str(run_dir / report), "case": case})
    for value in runs.values():
        plan = {key: value[key] for key in ("schema_version", "run", "inventory")}
        atomic_json(value["directory"] / "plan.json", plan)
    return {"users": users, "projects": projects, "blocks": blocks, "runs": runs,
            "items": items, "sources": SOURCES}
