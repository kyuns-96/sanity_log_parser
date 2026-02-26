from __future__ import annotations

import argparse
import dataclasses
import datetime
import logging
import os
import sys
from typing import Any, cast, Literal

from .config.resolution import (
    LoadedEmbeddingsConfig,
    load_resolved_embeddings_config,
    resolve_rule_config_path,
)
from .console import Console
from .clustering.logic import LogicClusterer
from .clustering.ai.clusterer import AIClusterer
from .parsing import parse_log_file
from .results.schema_v2 import (
    Group,
    RunMetadata,
    write_results_v2,
)
from .view import print_report


class ParseError(Exception):
    """Raised when input validation or log parsing fails."""


@dataclasses.dataclass(frozen=True)
class PipelineOptions:
    out: str
    ai_mode: str
    embeddings_config: str | None
    rule_config: str | None
    json_indent: int
    max_original_logs: int
    no_color: bool
    verbose: bool
    log_file: str
    template_file: str | None = None
    sanity_item: str | None = None


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """Attach shared clustering options to a subparser."""
    _ = parser.add_argument(
        "--out", default="subutai_results.json", help="Output JSON path."
    )
    _ = parser.add_argument(
        "--embeddings-config",
        "--config",
        dest="embeddings_config",
        default=None,
        help="Embeddings config path (alias: --config).",
    )
    _ = parser.add_argument(
        "--rule-config", default=None, help="Rule clustering config path."
    )
    _ = parser.add_argument(
        "--ai",
        choices=("auto", "on", "off"),
        default="auto",
        help="AI mode: auto, on, off.",
    )
    _ = parser.add_argument(
        "--json-indent",
        type=int,
        default=2,
        help="Indent used when writing JSON output.",
    )
    _ = parser.add_argument(
        "--max-original-logs",
        type=int,
        default=0,
        help="Maximum original logs per group (0 = all).",
    )
    _ = parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output."
    )
    _ = parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (INFO-level) logging.",
    )


def _build_parser() -> argparse.ArgumentParser:
    epilog = """Examples:\n  sanity-log-parser gca REPORT_FILE\n  sanity-log-parser cluster LOG_FILE TEMPLATE_FILE --config config.json --no-color"""
    parser = argparse.ArgumentParser(
        prog="sanity-log-parser",
        description="Parse logs and render clustering reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- gca subcommand ---
    gca = subparsers.add_parser(
        "gca",
        help="Cluster a PrimeTime Constraints (GCA) report.",
    )
    _ = gca.add_argument(
        "log_file", metavar="REPORT_FILE", help="Path to the PrimeTime report file."
    )
    _add_common_options(gca)

    # --- cluster subcommand (legacy/generic) ---
    cluster = subparsers.add_parser(
        "cluster", help="Cluster a log file into JSON results."
    )
    _ = cluster.add_argument(
        "log_file", metavar="LOG_FILE", help="Path to the log file to parse."
    )
    _ = cluster.add_argument(
        "template_file",
        metavar="TEMPLATE_FILE",
        nargs="?",
        default=None,
        help="Path to the template file. Omit for single-file PrimeTime reports.",
    )
    _add_common_options(cluster)

    # --- view subcommand ---
    view = subparsers.add_parser("view", help="Render report from a results JSON file.")
    _ = view.add_argument(
        "results_json",
        nargs="?",
        default="subutai_results.json",
        metavar="RESULTS_JSON",
        help="Path to a results JSON file.",
    )
    _ = view.add_argument(
        "--top", type=int, default=50, help="Maximum number of groups to show."
    )
    _ = view.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output."
    )
    return parser


def _validate_input_files(log_file: str, template_file: str | None) -> str | None:
    """Return error message or None if valid."""
    if not os.path.isfile(log_file):
        return f"Error: Log file '{log_file}' does not exist or is not a file."
    if not os.access(log_file, os.R_OK):
        return f"Error: Log file '{log_file}' is not readable."
    if template_file is not None:
        if not os.path.isfile(template_file):
            return f"Error: Template file '{template_file}' does not exist or is not a file."
        if not os.access(template_file, os.R_OK):
            return f"Error: Template file '{template_file}' is not readable."
    return None


def _validate_and_parse(
    log_file: str, template_file: str | None
) -> list[dict[str, Any]]:
    """Validate input files and parse logs. Raises ParseError on failure."""
    error = _validate_input_files(log_file, template_file)
    if error:
        raise ParseError(error)
    try:
        return cast(list[dict[str, Any]], parse_log_file(log_file, template_file))
    except Exception as exc:
        raise ParseError(f"Failed to parse '{log_file}': {exc}") from exc


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
        backend = cast(
            Literal["local", "openai_compatible"], loaded_embeddings.config.backend
        )
        console.section("🤖 Stage 2 - AI Clustering (Semantic Merging of 1st-Groups):")
        console.kv("Input 1st-groups", f"{len(logic_results):,}")
        console.kv("Output 2nd-groups", f"{len(results):,}")
        return results, True, backend

    return logic_results, False, None


def _build_final_groups(results: list[dict[str, object]]) -> list[Group]:
    """Convert raw clustering results to Group TypedDict format."""
    final_groups: list[Group] = []
    for group in results:
        is_ai = group.get("type") == "AISuperGroup"
        rule_id = cast(str, group.get("rule_id", "UNKNOWN"))
        seq = len(final_groups) + 1

        if is_ai:
            group_type: Literal["logic", "ai_super"] = "ai_super"
            group_id = f"{rule_id}::ai::{seq:06d}"
            template = cast(str, group.get("representative_template", "N/A"))
            pattern = cast(str, group.get("representative_pattern", "N/A"))
            total_count = cast(int, group.get("total_count", 0))
            merged = cast(int, group.get("merged_variants_count", 1))
            raw_logs = cast(list[str], group.get("original_logs", []))
        else:
            group_type = "logic"
            group_id = f"{rule_id}::logic::{seq:06d}"
            template = cast(str, group.get("template", "N/A"))
            pattern = cast(str, group.get("pattern", "N/A"))
            total_count = cast(int, group.get("count", 0))
            merged = 1
            raw_members = cast(list[dict[str, object]], group.get("members", []))
            raw_logs = [str(m.get("raw_log")) for m in raw_members]

        final_groups.append(
            {
                "group_type": group_type,
                "group_id": group_id,
                "rule_id": rule_id,
                "representative_template": template,
                "representative_pattern": pattern,
                "total_count": total_count,
                "merged_variants_count": merged,
                "original_logs": raw_logs,
            }
        )
    return final_groups


def _run_pipeline(parsed_logs: list[dict[str, Any]], opts: PipelineOptions) -> int:
    """Run the clustering pipeline: config → logic cluster → AI cluster → write."""
    console = Console(use_color=False if opts.no_color else None)

    logging.basicConfig(
        level=logging.INFO if opts.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    loaded_embeddings = load_resolved_embeddings_config(
        embeddings_config_arg=opts.embeddings_config,
    )
    for warning in loaded_embeddings.warnings:
        console.warn(warning)

    rule_config_path = resolve_rule_config_path(
        rule_config_arg=opts.rule_config,
    )

    logic_results = cast(list[dict[str, object]], LogicClusterer().run(parsed_logs))
    console.section("📊 Stage 1 - Logic Clustering (Original Method - Variables Only):")
    console.kv("Input logs", f"{len(parsed_logs):,}")
    console.kv("Output groups", f"{len(logic_results):,}")

    ai_clusterer = AIClusterer(
        embeddings_config_file=loaded_embeddings.config_path or "",
        config_file=rule_config_path,
    )
    results, ai_enabled, ai_backend = _run_ai_stage(
        opts.ai_mode,
        ai_clusterer,
        logic_results,
        loaded_embeddings,
        console,
    )

    final_groups = _build_final_groups(results)

    run_meta: RunMetadata = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "log_file": opts.log_file,
        **({"template_file": opts.template_file} if opts.template_file else {}),
        **({"sanity_item": opts.sanity_item} if opts.sanity_item else {}),
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

    write_results_v2(
        path=opts.out, run=run_meta, groups=final_groups, indent=opts.json_indent
    )

    console.section("✅ Final Results")
    console.kv("Groups Created", f"{len(final_groups):,}")
    console.info(f"💾 Results saved to '{opts.out}'.")
    return 0


def _run_cluster(args: argparse.Namespace) -> int:
    """Cluster logs via the legacy/generic subcommand."""
    opts = PipelineOptions(
        out=cast(str, args.out),
        ai_mode=cast(str, args.ai),
        embeddings_config=cast(str | None, args.embeddings_config),
        rule_config=cast(str | None, args.rule_config),
        json_indent=cast(int, args.json_indent),
        max_original_logs=cast(int, args.max_original_logs),
        no_color=cast(bool, args.no_color),
        verbose=cast(bool, args.verbose),
        log_file=cast(str, args.log_file),
        template_file=cast(str | None, args.template_file),
    )
    try:
        parsed_logs = _validate_and_parse(opts.log_file, opts.template_file)
    except ParseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _run_pipeline(parsed_logs, opts)


def _run_gca(args: argparse.Namespace) -> int:
    """Cluster a PrimeTime Constraints (GCA) report."""
    opts = PipelineOptions(
        out=cast(str, args.out),
        ai_mode=cast(str, args.ai),
        embeddings_config=cast(str | None, args.embeddings_config),
        rule_config=cast(str | None, args.rule_config),
        json_indent=cast(int, args.json_indent),
        max_original_logs=cast(int, args.max_original_logs),
        no_color=cast(bool, args.no_color),
        verbose=cast(bool, args.verbose),
        log_file=cast(str, args.log_file),
        template_file=None,
        sanity_item="gca",
    )
    try:
        parsed_logs = _validate_and_parse(opts.log_file, opts.template_file)
    except ParseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _run_pipeline(parsed_logs, opts)


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
    if command == "gca":
        return _run_gca(args)
    if command == "cluster":
        return _run_cluster(args)
    return _run_view(args)
