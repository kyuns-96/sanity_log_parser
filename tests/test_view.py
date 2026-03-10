from __future__ import annotations

import json
from pathlib import Path

from sanity_log_parser.view import print_report


def test_print_report_accepts_single_file_results_without_template(
    tmp_path: Path,
    capsys,
) -> None:
    results_path = tmp_path / "results.json"
    payload = {
        "schema_version": 2,
        "run": {
            "timestamp_utc": "2026-03-06T00:00:00Z",
            "log_file": "report.rpt",
            "sanity_item": "gca",
            "counts": {
                "parsed_logs": 1,
                "logic_groups": 1,
                "final_groups": 1,
            },
            "ai": {
                "enabled": False,
                "backend": None,
                "warnings": [],
            },
        },
        "groups": [
            {
                "group_type": "logic",
                "group_id": "CGR_0018::logic::000001",
                "rule_id": "CGR_0018",
                "representative_template": "Clock '<VAR>' from '<VAR>'",
                "representative_pattern": "'GEN_*' / 'MSTR*'",
                "total_count": 1,
                "merged_variants_count": 1,
                "original_logs": ["Clock 'GEN_A' from 'MSTR'"],
            }
        ],
    }
    results_path.write_text(json.dumps(payload), encoding="utf-8")

    rc = print_report(results_path, no_color=True)

    output = capsys.readouterr().out
    assert rc == 0
    assert "Template file" not in output
    assert "Subutai Analysis Report" in output
