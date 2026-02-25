from __future__ import annotations

from pathlib import Path

from ._util import as_int, as_optional_str, as_str, as_str_list, first_non_empty
from .console import Console
from .results.schema_v2 import read_results


def print_report(
    results_path: str | Path = "subutai_results.json",
    *,
    top: int = 50,
    no_color: bool = False,
) -> int:
    console = Console(use_color=False if no_color else None)
    path = Path(results_path)
    if not path.is_file():
        console.error(f"Results file not found: {path}")
        return 1

    try:
        parsed = read_results(path)
    except (OSError, ValueError) as exc:
        console.error(f"Failed to load results JSON: {exc}")
        return 1

    groups = parsed["groups"]
    total_groups = len(groups)
    total_logs = sum(as_int(group.get("total_count"), 0) for group in groups)
    shown = min(total_groups, max(0, top))

    console.section("Subutai Analysis Report")
    console.kv("File", str(path))
    console.kv("Schema version", parsed["schema_version"])
    console.kv("Total logs", f"{total_logs:,}")
    console.kv("Groups", f"{total_groups:,}")
    console.kv("Top shown", f"{shown:,}")

    run = parsed.get("run")
    if run is not None:
        counts = run["counts"]
        ai = run["ai"]
        console.section("Run metadata")
        console.kv("Timestamp (UTC)", run["timestamp_utc"])
        console.kv("Log file", run["log_file"])
        console.kv("Template file", run["template_file"])
        console.kv("Parsed logs", counts["parsed_logs"])
        console.kv("Logic groups", counts["logic_groups"])
        console.kv("Final groups", counts["final_groups"])
        console.kv("AI enabled", ai["enabled"])
        console.kv("AI backend", ai["backend"] if ai["backend"] is not None else "N/A")
        if ai["warnings"]:
            for warning in ai["warnings"]:
                console.warn(warning)

    for index, group in enumerate(groups[:shown], start=1):
        _print_group(console, index, group)

    if total_groups > shown:
        console.info(f"... omitted {total_groups - shown:,} groups (use --top to increase)")
    return 0


def _print_group(console: Console, rank: int, group: dict[str, object]) -> None:
    rule_id = as_str(group.get("rule_id"), "UNKNOWN")
    group_type = as_str(group.get("group_type"), as_str(group.get("type"), "logic"))
    total_count = as_int(group.get("total_count"), 0)
    pattern = as_str(group.get("representative_pattern"), "N/A")
    template = first_non_empty(
        as_optional_str(group.get("representative_template")),
        as_optional_str(group.get("template")),
        "N/A",
    )
    merged = as_int(group.get("merged_variants_count"), 1)
    original_logs = as_str_list(group.get("original_logs"))

    console.section(f"[{rank:02d}] {rule_id}")
    console.kv("Group type", group_type)
    console.kv("Count", f"{total_count:,}")
    console.kv("Merged variants", merged)
    console.kv("Pattern", pattern)
    console.kv("Template", template)
    console.kv("Original logs", len(original_logs))

    preview_limit = min(5, len(original_logs))
    for log in original_logs[:preview_limit]:
        console.info(f"- {log}")
    if len(original_logs) > preview_limit:
        console.info(f"... (+{len(original_logs) - preview_limit:,} more)")
