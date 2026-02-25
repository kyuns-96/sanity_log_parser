from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys
from typing import cast, Literal

from .config.resolution import (
    LoadedEmbeddingsConfig,
    load_resolved_embeddings_config,
    resolve_rule_config_path,
)
from .console import Console
from .clustering.logic import LogicClusterer
from .clustering.ai.clusterer import AIClusterer
from .parsing.log_parser import SubutaiParser
from .parsing.template_manager import RuleTemplateManager
from .results.schema_v2 import (
    Group,
    RunMetadata,
    write_results_v2,
)
from .view import print_report


def _build_parser() -> argparse.ArgumentParser:
    epilog = """Examples:\n  sanity-log-parser cluster LOG_FILE TEMPLATE_FILE --config config.json --no-color"""
    parser = argparse.ArgumentParser(
        prog="sanity-log-parser",
        description="Parse logs and render clustering reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    cluster = subparsers.add_parser("cluster", help="Cluster a log file into JSON results.")
    _ = cluster.add_argument("log_file", metavar="LOG_FILE", help="Path to the log file to parse.")
    _ = cluster.add_argument("template_file", metavar="TEMPLATE_FILE", help="Path to the template file.")
    _ = cluster.add_argument("--out", default="subutai_results.json", help="Output JSON path.")
    _ = cluster.add_argument(
        "--embeddings-config",
        "--config",
        dest="embeddings_config",
        default=None,
        help="Embeddings config path (alias: --config).",
    )
    _ = cluster.add_argument("--rule-config", default=None, help="Rule clustering config path.")
    _ = cluster.add_argument(
        "--ai",
        choices=("auto", "on", "off"),
        default="auto",
        help="AI mode: auto, on, off.",
    )
    _ = cluster.add_argument("--json-indent", type=int, default=2, help="Indent used when writing JSON output.")
    _ = cluster.add_argument(
        "--max-original-logs",
        type=int,
        default=0,
        help="Maximum original logs per group (0 = all).",
    )
    _ = cluster.add_argument("--no-color", action="store_true", help="Disable ANSI color output.")
    _ = cluster.add_argument("-v", "--verbose", action="store_true", help="Enable verbose (INFO-level) logging.")

    view = subparsers.add_parser("view", help="Render report from a results JSON file.")
    _ = view.add_argument(
        "results_json",
        nargs="?",
        default="subutai_results.json",
        metavar="RESULTS_JSON",
        help="Path to a results JSON file.",
    )
    _ = view.add_argument("--top", type=int, default=50, help="Maximum number of groups to show.")
    _ = view.add_argument("--no-color", action="store_true", help="Disable ANSI color output.")
    return parser


def _validate_input_files(log_file: str, template_file: str) -> str | None:
    """Return error message or None if valid."""
    if not os.path.isfile(log_file):
        return f"Error: Log file '{log_file}' does not exist or is not a file."
    if not os.access(log_file, os.R_OK):
        return f"Error: Log file '{log_file}' is not readable."
    if not os.path.isfile(template_file):
        return f"Error: Template file '{template_file}' does not exist or is not a file."
    if not os.access(template_file, os.R_OK):
        return f"Error: Template file '{template_file}' is not readable."
    return None


def _parse_log_file(
    log_file: str,
    template_file: str,
) -> list[dict[str, object]]:
    """Load templates, parse log lines, return parsed logs."""
    tm = RuleTemplateManager(template_file)
    parser = SubutaiParser(tm)
    parsed_logs: list[dict[str, object]] = []

    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith(("-", "=", "Rule", "Severity")):
                continue
            res = parser.parse_line(stripped)
            if res:
                parsed_logs.append(cast(dict[str, object], res))

    return parsed_logs


def _run_ai_stage(
    ai_mode: str,
    ai_clusterer: AIClusterer,
    logic_results: list[dict[str, object]],
    loaded_embeddings: LoadedEmbeddingsConfig,
    console: Console,
) -> tuple[list[dict[str, object]], bool, Literal["local", "openai_compatible"] | None]:
    """Run AI clustering stage. Returns (results, ai_enabled, ai_backend)."""
    if ai_mode == "off":
        return logic_results, False, None

    if ai_clusterer.ai_available and ai_mode in ("on", "auto"):
        results = ai_clusterer.run(logic_results)
        backend = cast(Literal["local", "openai_compatible"], loaded_embeddings.config.backend)
        console.section("🤖 Stage 2 - AI Clustering (Semantic Merging of 1st-Groups):")
        console.kv("Input 1st-groups", f"{len(logic_results):,}")
        console.kv("Output 2nd-groups", f"{len(results):,}")
        return results, True, backend

    return logic_results, False, None


def _build_final_groups(results: list[dict[str, object]]) -> list[Group]:
    """Convert raw clustering results to Group TypedDict format."""
    final_groups: list[Group] = []
    for group in results:
        raw_members = cast(list[dict[str, object]], group.get("members", []))
        raw_logs = [str(m.get("raw_log")) for m in raw_members]
        rule_id = cast(str, group.get("rule_id", "UNKNOWN"))
        group_id = f"{rule_id}::logic::{len(final_groups) + 1:06d}"

        final_groups.append({
            "group_type": "logic",
            "group_id": group_id,
            "rule_id": rule_id,
            "representative_template": cast(str, group.get("template", "N/A")),
            "representative_pattern": cast(str, group.get("pattern", "N/A")),
            "total_count": cast(int, group.get("count", 0)),
            "merged_variants_count": 1,
            "original_logs": raw_logs,
        })
    return final_groups


def _run_cluster(args: argparse.Namespace) -> int:
    """Cluster logs: validate -> parse -> logic cluster -> AI cluster -> write results."""
    log_file = cast(str, args.log_file)
    template_file = cast(str, args.template_file)

    error = _validate_input_files(log_file, template_file)
    if error:
        print(error, file=sys.stderr)
        return 1

    no_color = cast(bool, args.no_color)
    console = Console(use_color=False if no_color else None)

    verbose = cast(bool, args.verbose)
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    loaded_embeddings = load_resolved_embeddings_config(
        embeddings_config_arg=cast(str | None, args.embeddings_config),
    )
    for warning in loaded_embeddings.warnings:
        console.warn(warning)

    rule_config_path = resolve_rule_config_path(
        rule_config_arg=cast(str | None, args.rule_config),
    )

    parsed_logs = _parse_log_file(log_file, template_file)

    logic_results = cast(list[dict[str, object]], LogicClusterer().run(parsed_logs))
    console.section("📊 Stage 1 - Logic Clustering (Original Method - Variables Only):")
    console.kv("Input logs", f"{len(parsed_logs):,}")
    console.kv("Output groups", f"{len(logic_results):,}")

    ai_clusterer = AIClusterer(
        embeddings_config_file=loaded_embeddings.config_path or "",
        config_file=rule_config_path,
    )
    ai_mode = cast(str, args.ai)
    results, ai_enabled, ai_backend = _run_ai_stage(
        ai_mode, ai_clusterer, logic_results, loaded_embeddings, console,
    )

    final_groups = _build_final_groups(results)

    run_meta: RunMetadata = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "log_file": log_file,
        "template_file": template_file,
        "counts": {
            "parsed_logs": len(parsed_logs),
            "logic_groups": len(logic_results),
            "final_groups": len(final_groups),
        },
        "ai": {
            "enabled": ai_enabled,
            "backend": ai_backend,
            "warnings": [],
        },
    }

    out_path = cast(str, args.out)
    json_indent = cast(int, args.json_indent)
    write_results_v2(path=out_path, run=run_meta, groups=final_groups, indent=json_indent)

    console.section("✅ Final Results")
    console.kv("Groups Created", f"{len(final_groups):,}")
    console.info(f"💾 Results saved to '{out_path}'.")
    return 0


def _run_view(args: argparse.Namespace) -> int:
    """Render report from results JSON file."""
    results_json = cast(str, args.results_json)
    top = cast(int, args.top)
    no_color = cast(bool, args.no_color)
    return print_report(results_json, top=top, no_color=no_color)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    command = cast(str, args.command)
    if command == "cluster":
        return _run_cluster(args)
    return _run_view(args)
