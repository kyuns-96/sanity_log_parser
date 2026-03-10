from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast, Literal

from .config.resolution import (
    LoadedEmbeddingsConfig,
    load_resolved_embeddings_config,
)
from .console import Console
from .clustering.logic import LogicClusterer
from .parsing import parse_log_file
from .results.schema_v2 import (
    Group,
    RunMetadata,
    write_results_v2,
)
from .view import print_report

if TYPE_CHECKING:
    from .clustering.ai.clusterer import AIClusterer

logger = logging.getLogger(__name__)


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

    # --- gca-eval subcommand ---
    gca_eval = subparsers.add_parser(
        "gca-eval",
        help="Evaluate AI clustering results against a ground truth.",
    )
    _ = gca_eval.add_argument(
        "--logic", required=True, help="Path to logic.json (AI-off output)."
    )
    _ = gca_eval.add_argument(
        "--ai", required=True, dest="ai_json", help="Path to ai.json (AI-on output)."
    )
    _ = gca_eval.add_argument(
        "--ground-truth", required=True, help="Path to ground truth JSON."
    )
    _ = gca_eval.add_argument(
        "--f1-threshold",
        type=float,
        default=0.97,
        help="F1 threshold for PASS/FAIL (default: 0.97).",
    )

    # --- gca-distances subcommand ---
    gca_dist = subparsers.add_parser(
        "gca-distances",
        help="Show pairwise distance matrix for a GCA rule.",
    )
    _ = gca_dist.add_argument(
        "--logic", required=True, help="Path to logic.json (AI-off output)."
    )
    _ = gca_dist.add_argument(
        "--rule-config", default=None, help="Rule clustering config path."
    )
    _ = gca_dist.add_argument(
        "--rule-id", required=True, help="Rule ID to analyze."
    )
    _ = gca_dist.add_argument(
        "--embeddings-config",
        "--config",
        dest="embeddings_config",
        default=None,
        help="Embeddings config path.",
    )
    _ = gca_dist.add_argument(
        "--ground-truth", default=None, help="Optional ground truth JSON for annotation."
    )
    _ = gca_dist.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging."
    )

    # --- gca-fit-adaptive-eps subcommand ---
    gca_fit_adaptive = subparsers.add_parser(
        "gca-fit-adaptive-eps",
        help="Fit an adaptive-eps tree for one GCA rule from logic.json + ground truth.",
    )
    _ = gca_fit_adaptive.add_argument(
        "--logic", required=True, help="Path to logic.json (AI-off output)."
    )
    _ = gca_fit_adaptive.add_argument(
        "--ground-truth", required=True, help="Path to ground truth JSON."
    )
    _ = gca_fit_adaptive.add_argument(
        "--rule-id", required=True, help="Rule ID to fit."
    )
    _ = gca_fit_adaptive.add_argument(
        "--rule-config", default=None, help="Input rule clustering config path."
    )
    _ = gca_fit_adaptive.add_argument(
        "--out-rule-config",
        required=True,
        help="Output path for the updated rule clustering config JSON.",
    )
    _ = gca_fit_adaptive.add_argument(
        "--features-json",
        default=None,
        help="Optional JSON file containing a list of adaptive-eps feature definitions.",
    )
    _ = gca_fit_adaptive.add_argument(
        "--embeddings-config",
        "--config",
        dest="embeddings_config",
        default=None,
        help="Embeddings config path.",
    )
    _ = gca_fit_adaptive.add_argument(
        "--max-depth",
        type=int,
        default=7,
        help="Maximum decision-tree depth to try (default: 7).",
    )
    _ = gca_fit_adaptive.add_argument(
        "--max-min-samples-leaf",
        type=int,
        default=15,
        help="Largest min_samples_leaf value to try (default: 15).",
    )
    _ = gca_fit_adaptive.add_argument(
        "--round-decimals",
        type=int,
        default=3,
        help="Decimal places for fitted thresholds/leaf values (default: 3).",
    )
    _ = gca_fit_adaptive.add_argument(
        "--min-eps",
        type=float,
        default=0.001,
        help="Minimum positive adaptive leaf eps value (default: 0.001).",
    )
    _ = gca_fit_adaptive.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging."
    )

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
        results = ai_clusterer.run(logic_results, strict=ai_mode == "on")
        backend = cast(
            Literal["local", "openai_compatible"], loaded_embeddings.config.backend
        )
        console.section("🤖 Stage 2 - AI Clustering (Semantic Merging of 1st-Groups):")
        console.kv("Input 1st-groups", f"{len(logic_results):,}")
        console.kv("Output 2nd-groups", f"{len(results):,}")
        return results, True, backend

    if ai_mode == "on":
        raise RuntimeError("AI clustering requested with --ai on, but AI is unavailable.")

    return logic_results, False, None


def _build_ai_clusterer(
    *,
    embeddings_config_file: str,
    gca_config: Any,
    embed_batch_size: int,
) -> AIClusterer:
    from .clustering.ai.clusterer import AIClusterer

    return AIClusterer(
        embeddings_config_file=embeddings_config_file,
        gca_config=gca_config,
        embed_batch_size=embed_batch_size,
    )


def _build_final_groups(
    results: list[dict[str, object]],
    *,
    max_original_logs: int = 0,
) -> list[Group]:
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

        if max_original_logs > 0:
            raw_logs = raw_logs[:max_original_logs]

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
    pipeline_t0 = time.perf_counter()
    console = Console(use_color=False if opts.no_color else None)

    logging.basicConfig(
        level=logging.INFO if opts.verbose else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    t0 = time.perf_counter()
    loaded_embeddings = load_resolved_embeddings_config(
        embeddings_config_arg=opts.embeddings_config,
    )
    for warning in loaded_embeddings.warnings:
        console.warn(warning)

    gca_config: Any = None
    if opts.sanity_item == "gca":
        from .gca import GCA_DEFAULT_CONFIG_PATH
        from .gca.config import ConfigError, load_gca_config

        config_path = opts.rule_config or str(GCA_DEFAULT_CONFIG_PATH)
        strict = opts.rule_config is not None
        try:
            gca_config = load_gca_config(config_path, strict=strict)
        except ConfigError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    elif opts.rule_config is not None:
        print(
            "Warning: --rule-config is only supported by the 'gca' subcommand; ignoring.",
            file=sys.stderr,
        )
    logger.info("[timing] config loading: %.3fs", time.perf_counter() - t0)

    t0 = time.perf_counter()
    logic_results = cast(list[dict[str, object]], LogicClusterer().run(parsed_logs))
    logger.info("[timing] logic clustering: %.3fs", time.perf_counter() - t0)
    console.section("📊 Stage 1 - Logic Clustering (Original Method - Variables Only):")
    console.kv("Input logs", f"{len(parsed_logs):,}")
    console.kv("Output groups", f"{len(logic_results):,}")

    if opts.ai_mode == "off":
        results = logic_results
        ai_enabled = False
        ai_backend = None
    else:
        t0 = time.perf_counter()
        ai_clusterer = _build_ai_clusterer(
            embeddings_config_file=loaded_embeddings.config_path or "",
            gca_config=gca_config,
            embed_batch_size=loaded_embeddings.config.embed_batch_size,
        )
        logger.info("[timing] AI clusterer init: %.3fs", time.perf_counter() - t0)

        t0 = time.perf_counter()
        try:
            results, ai_enabled, ai_backend = _run_ai_stage(
                opts.ai_mode,
                ai_clusterer,
                logic_results,
                loaded_embeddings,
                console,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        logger.info("[timing] AI clustering total: %.3fs", time.perf_counter() - t0)

    t0 = time.perf_counter()
    final_groups = _build_final_groups(
        results, max_original_logs=opts.max_original_logs
    )
    logger.info("[timing] build final groups: %.3fs", time.perf_counter() - t0)

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

    t0 = time.perf_counter()
    write_results_v2(
        path=opts.out, run=run_meta, groups=final_groups, indent=opts.json_indent
    )
    logger.info("[timing] write results: %.3fs", time.perf_counter() - t0)

    logger.info("[timing] pipeline total: %.3fs", time.perf_counter() - pipeline_t0)
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
        t0 = time.perf_counter()
        parsed_logs = _validate_and_parse(opts.log_file, opts.template_file)
        logger.info("[timing] parsing: %.3fs", time.perf_counter() - t0)
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
        t0 = time.perf_counter()
        parsed_logs = _validate_and_parse(opts.log_file, opts.template_file)
        logger.info("[timing] parsing: %.3fs", time.perf_counter() - t0)
    except ParseError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _run_pipeline(parsed_logs, opts)


def _run_gca_eval(args: argparse.Namespace) -> int:
    """Evaluate AI clustering against ground truth."""
    from .gca.eval import evaluate, format_results

    logic = cast(str, args.logic)
    ai_json = cast(str, args.ai_json)
    gt = cast(str, args.ground_truth)
    threshold = cast(float, args.f1_threshold)

    try:
        results = evaluate(logic, ai_json, gt, f1_threshold=threshold)
    except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(format_results(results))

    all_pass = all(r["status"] == "PASS" for r in results)
    return 0 if all_pass else 1


def _run_gca_distances(args: argparse.Namespace) -> int:
    """Show pairwise distance matrix for a rule."""
    from .clustering.ai.clusterer import AIClusterer
    from .gca.config import ConfigError, load_gca_config
    from .gca.distances import compute_distances, format_distances
    from .gca import GCA_DEFAULT_CONFIG_PATH

    logging.basicConfig(
        level=logging.INFO if cast(bool, args.verbose) else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    config_path = cast(str | None, args.rule_config) or str(GCA_DEFAULT_CONFIG_PATH)
    strict = args.rule_config is not None
    try:
        gca_config = load_gca_config(config_path, strict=strict)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Set up embeddings
    loaded_embeddings = load_resolved_embeddings_config(
        embeddings_config_arg=cast(str | None, args.embeddings_config),
    )
    for warning in loaded_embeddings.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    ai_clusterer = AIClusterer(
        embeddings_config_file=loaded_embeddings.config_path or "",
        gca_config=gca_config,
        embed_batch_size=loaded_embeddings.config.embed_batch_size,
    )
    if not ai_clusterer.ai_available:
        print("Error: AI embeddings not available. Check embeddings config.", file=sys.stderr)
        return 1

    def embed_fn(texts: list[str]) -> Any:
        result = ai_clusterer._compute_embeddings_batched(texts)
        if result is None:
            raise RuntimeError("Embedding computation failed")
        return result

    gt: dict[str, list[list[str]]] | None = None
    gt_path = cast(str | None, args.ground_truth)
    if gt_path:
        with open(gt_path, encoding="utf-8") as f:
            gt = json.load(f)

    try:
        result = compute_distances(
            logic_path=cast(str, args.logic),
            rule_id=cast(str, args.rule_id),
            gca_config=gca_config,
            embed_fn=embed_fn,
            ground_truth=gt,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(format_distances(result))
    return 0


def _run_gca_fit_adaptive_eps(args: argparse.Namespace) -> int:
    """Fit an adaptive-eps tree for one rule and write an updated config."""
    from .clustering.ai.clusterer import AIClusterer
    from .gca import GCA_DEFAULT_CONFIG_PATH
    from .gca.adaptive_eps_tuning import (
        fit_adaptive_eps_rule,
        load_feature_defs,
        update_rule_config_with_adaptive_eps_tree,
    )
    from .gca.config import ConfigError, load_gca_config

    logging.basicConfig(
        level=logging.INFO if cast(bool, args.verbose) else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )

    config_path = cast(str | None, args.rule_config) or str(GCA_DEFAULT_CONFIG_PATH)
    strict = args.rule_config is not None
    try:
        gca_config = load_gca_config(config_path, strict=strict)
        raw_config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(raw_config, dict):
            raise ValueError("Rule config JSON must be an object.")
        logic_data = json.loads(Path(cast(str, args.logic)).read_text(encoding="utf-8"))
        if not isinstance(logic_data, dict):
            raise ValueError("logic.json must be a JSON object.")
        ground_truth_data = json.loads(
            Path(cast(str, args.ground_truth)).read_text(encoding="utf-8")
        )
        if not isinstance(ground_truth_data, dict):
            raise ValueError("ground truth JSON must be a JSON object.")
        feature_defs = load_feature_defs(cast(str | None, args.features_json))
    except (OSError, json.JSONDecodeError, ValueError, ConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if cast(int, args.max_depth) < 1:
        print("Error: --max-depth must be >= 1.", file=sys.stderr)
        return 1
    if cast(int, args.max_min_samples_leaf) < 1:
        print("Error: --max-min-samples-leaf must be >= 1.", file=sys.stderr)
        return 1

    loaded_embeddings = load_resolved_embeddings_config(
        embeddings_config_arg=cast(str | None, args.embeddings_config),
    )
    for warning in loaded_embeddings.warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    ai_clusterer = AIClusterer(
        embeddings_config_file=loaded_embeddings.config_path or "",
        gca_config=gca_config,
        embed_batch_size=loaded_embeddings.config.embed_batch_size,
    )
    if not ai_clusterer.ai_available:
        print("Error: AI embeddings not available. Check embeddings config.", file=sys.stderr)
        return 1

    def embed_fn(texts: list[str]) -> Any:
        result = ai_clusterer._compute_embeddings_batched(texts)
        if result is None:
            raise RuntimeError("Embedding computation failed")
        return result

    try:
        fit_result = fit_adaptive_eps_rule(
            logic_data=logic_data,
            ground_truth_data=cast(dict[str, list[list[str]]], ground_truth_data),
            gca_config=gca_config,
            rule_id=cast(str, args.rule_id),
            embed_fn=embed_fn,
            feature_defs=feature_defs,
            max_depth_candidates=tuple(range(1, cast(int, args.max_depth) + 1)),
            min_samples_leaf_candidates=tuple(
                range(1, cast(int, args.max_min_samples_leaf) + 1)
            ),
            round_decimals=cast(int, args.round_decimals),
            min_eps=cast(float, args.min_eps),
        )
        updated_config, removed_pairwise = update_rule_config_with_adaptive_eps_tree(
            raw_config=raw_config,
            rule_id=cast(str, args.rule_id),
            tree=fit_result.tree,
        )
        out_path = Path(cast(str, args.out_rule_config))
        out_path.write_text(json.dumps(updated_config, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Rule ID: {args.rule_id}")
    print(f"Features: {len(feature_defs)}")
    print(f"Nodes: {fit_result.node_count}")
    print(f"Max depth: {fit_result.max_depth}")
    print(f"Min samples leaf: {fit_result.min_samples_leaf}")
    print(f"Precision: {fit_result.precision:.4f}")
    print(f"Recall: {fit_result.recall:.4f}")
    print(f"F1: {fit_result.f1:.4f}")
    if removed_pairwise:
        print("Removed pairwise_tree from the target rule so adaptive_eps_tree takes effect.")
    print(f"Updated config written to: {out_path}")
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
    if command == "gca":
        return _run_gca(args)
    if command == "cluster":
        return _run_cluster(args)
    if command == "gca-eval":
        return _run_gca_eval(args)
    if command == "gca-distances":
        return _run_gca_distances(args)
    if command == "gca-fit-adaptive-eps":
        return _run_gca_fit_adaptive_eps(args)
    return _run_view(args)
