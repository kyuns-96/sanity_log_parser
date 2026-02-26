from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, NotRequired, TypedDict, cast


class RunCounts(TypedDict):
    parsed_logs: int
    logic_groups: int
    final_groups: int


class RunAI(TypedDict):
    enabled: bool
    backend: Literal["local", "openai_compatible"] | None
    warnings: list[str]


class RunMetadata(TypedDict):
    timestamp_utc: str
    log_file: str
    template_file: NotRequired[str]
    sanity_item: NotRequired[str]
    counts: RunCounts
    ai: RunAI


class GroupSummary(TypedDict):
    template: str
    pattern: str
    count: int


class Group(TypedDict):
    group_type: Literal["logic", "ai_super"]
    group_id: str
    rule_id: str
    representative_template: str
    representative_pattern: str
    total_count: int
    merged_variants_count: int
    original_logs: list[str]
    subgroup_summaries: NotRequired[list[GroupSummary]]


class ResultsV2(TypedDict):
    schema_version: Literal[2]
    run: RunMetadata
    groups: list[Group]


class ParsedResults(TypedDict):
    schema_version: int
    run: RunMetadata | None
    groups: list[dict[str, object]]


def write_results_v2(
    path: str | Path,
    run: RunMetadata,
    groups: list[Group],
    indent: int = 2,
) -> None:
    payload: ResultsV2 = {
        "schema_version": 2,
        "run": run,
        "groups": groups,
    }
    with Path(path).open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=indent, ensure_ascii=False)


def read_results(path: str | Path) -> ParsedResults:
    with Path(path).open("r", encoding="utf-8") as handle:
        loaded = cast(object, json.load(handle))

    if isinstance(loaded, list):
        return {
            "schema_version": 1,
            "run": None,
            "groups": cast(list[dict[str, object]], loaded),
        }

    if not isinstance(loaded, dict):
        raise ValueError(
            "Results JSON root must be an object (v2) or list (legacy v1)."
        )

    loaded_obj = cast(dict[str, object], loaded)
    schema_version = loaded_obj.get("schema_version")
    run = loaded_obj.get("run")
    groups = loaded_obj.get("groups")
    if schema_version != 2 or not isinstance(run, dict) or not isinstance(groups, list):
        raise ValueError(
            "Invalid schema-v2 results payload: expected schema_version/run/groups."
        )

    return {
        "schema_version": 2,
        "run": cast(RunMetadata, cast(object, run)),
        "groups": cast(list[dict[str, object]], groups),
    }
