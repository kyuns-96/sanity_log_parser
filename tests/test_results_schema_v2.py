import json
from pathlib import Path
from typing import Literal, TypedDict, cast

from sanity_log_parser.results.schema_v2 import (
    write_results_v2,
    read_results,
    RunMetadata,
    RunCounts,
    RunAI,
    Group,
)


def _sample_run() -> RunMetadata:
    return {
        "timestamp_utc": "2026-02-24T10:30:00Z",
        "log_file": "events.log",
        "template_file": "rules.log",
        "counts": {
            "parsed_logs": 10,
            "logic_groups": 3,
            "final_groups": 2,
        },
        "ai": {
            "enabled": False,
            "backend": None,
            "warnings": [],
        },
    }


def _sample_groups() -> list[Group]:
    return [
        {
            "group_type": "logic",
            "group_id": "R001::logic::000001",
            "rule_id": "R001",
            "representative_template": "Signal 'u_top' not found",
            "representative_pattern": "'u_top'",
            "total_count": 4,
            "merged_variants_count": 1,
            "original_logs": ["Signal 'u_top' not found"],
        }
    ]


def test_write_results_v2_writes_object_envelope(tmp_path: Path) -> None:
    output_path = tmp_path / "subutai_results.json"

    write_results_v2(
        path=output_path,
        run=_sample_run(),
        groups=_sample_groups(),
        indent=2,
    )

    with output_path.open("r", encoding="utf-8") as handle:
        payload = cast(dict[str, object], json.load(handle))

    assert isinstance(payload, dict)
    assert payload.get("schema_version") == 2
    run = cast(dict[str, object], payload.get("run"))
    counts = cast(dict[str, object], run.get("counts"))
    groups = cast(list[dict[str, object]], payload.get("groups"))
    assert counts.get("final_groups") == 2
    assert groups[0].get("group_id") == "R001::logic::000001"


def test_read_results_accepts_v2_object_root(tmp_path: Path) -> None:
    output_path = tmp_path / "subutai_results.json"
    write_results_v2(output_path, _sample_run(), _sample_groups(), indent=2)

    parsed = read_results(output_path)

    assert parsed["schema_version"] == 2
    assert parsed["run"] is not None
    assert parsed["run"]["template_file"] == "rules.log"
    assert parsed["groups"][0]["group_type"] == "logic"


def test_read_results_accepts_legacy_list_root(tmp_path: Path) -> None:
    output_path = tmp_path / "legacy_results.json"
    legacy = [
        {
            "type": "LogicGroup",
            "rule_id": "R001",
            "representative_pattern": "'u_top'",
            "total_count": 1,
            "original_logs": ["Signal 'u_top' not found"],
        }
    ]
    _ = output_path.write_text(json.dumps(legacy), encoding="utf-8")

    parsed = read_results(output_path)

    assert parsed["schema_version"] == 1
    assert parsed["run"] is None
    assert len(parsed["groups"]) == 1
    assert parsed["groups"][0]["rule_id"] == "R001"
