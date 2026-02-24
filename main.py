from __future__ import annotations

import argparse
import os
import json
from typing import cast

import cli_ui
from template_manager import RuleTemplateManager
from parser import SubutaiParser
from logic_clusterer import LogicClusterer
from ai_clusterer import AIClusterer


def _build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="main.py",
        description="Cluster logs using the logic + AI workflow and write grouped results to JSON.",
        epilog=(
            "Examples:\n"
            "  python main.py LOG_FILE TEMPLATE_FILE\n"
            "  python main.py events.log rules.json\n\n"
            "  python view_log.py subutai_results.json\n\n"
            "Environment:\n"
            "  NO_COLOR=1 python main.py ...  # force-disable color output\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def _check_readable(path: str, label: str, parser: argparse.ArgumentParser) -> str:
    if not os.path.isfile(path):
        parser.error(f"{label} '{path}' does not exist or is not a file.")

    if not os.access(path, os.R_OK):
        parser.error(f"{label} '{path}' is not readable.")

    return path


def _ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "N/A"
    return f"{numerator / denominator:.2f}x"


def main() -> None:
    parser = _build_parser()
    _ = parser.add_argument(
        "log_file", help="Path to the log file to parse.", metavar="LOG_FILE"
    )
    _ = parser.add_argument(
        "template_file",
        help="Path to the rule template file.",
        metavar="TEMPLATE_FILE",
    )
    _ = parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output (the NO_COLOR env var is also respected).",
    )

    args = parser.parse_args()
    console = cli_ui.Console(use_color=False if cast(bool, args.no_color) else None)

    log_file = _check_readable(cast(str, args.log_file), "Log file", parser)
    rule_file = _check_readable(cast(str, args.template_file), "Template file", parser)

    tm = RuleTemplateManager(rule_file, console)
    parser = SubutaiParser(tm)
    parsed_logs: list[dict[str, object]] = []
    
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith(('-', '=', 'Rule', 'Severity')): continue
            res = parser.parse_line(stripped)
            if res:
                parsed_logs.append(cast(dict[str, object], res))

    # 2. Logic Clustering [1st grouping: original method]
    logic_results = cast(list[dict[str, object]], LogicClusterer().run(parsed_logs))
    console.section("📊 Stage 1 - Logic Clustering (Original Method - Variables Only):")
    console.kv("Input logs", f"{len(parsed_logs):,}")
    console.kv("Output groups", f"{len(logic_results):,}")
    console.kv("Compression ratio", _ratio(len(parsed_logs), len(logic_results)))

    # 3. AI Clustering [2nd grouping: semantic merging]
    results: list[dict[str, object]] = []

    ai_clusterer = AIClusterer(console=console)
    if ai_clusterer.ai_available:
        # 2nd grouping: semantically re-merge 1st logic groups using AI
        results = ai_clusterer.run(logic_results)
        console.section("🤖 Stage 2 - AI Clustering (Semantic Merging of 1st-Groups):")
        console.kv("Input 1st-groups", f"{len(logic_results):,}")
        console.kv("Output 2nd-groups", f"{len(results):,}")
        console.kv("Final compression ratio", _ratio(len(parsed_logs), len(results)))
    else:
        # Return only Logic results if AI unavailable
        for logic_group in logic_results:
            # Recover original logs from Logic group
            raw_members = cast(list[dict[str, object]], logic_group.get("members", []))
            raw_logs = [str(m.get('raw_log')) for m in raw_members]
            results.append({
                "type": "LogicGroup",
                "rule_id": logic_group['rule_id'],
                "representative_pattern": logic_group['pattern'],
                "total_count": logic_group['count'],
                "original_logs": raw_logs
            })
    
    # 4. Output results and save to file
    console.section("✅ Final Results")
    console.kv("Groups Created", f"{len(results):,}")
    console.kv("Final compression ratio", _ratio(len(parsed_logs), len(results)))
    
    # Display on screen (sample)
    for i, result in enumerate(results[:5]):
        pattern_display = str(result.get('representative_pattern', 'N/A'))
        merged_count = int(cast(int, result.get('merged_variants_count', 1)))
        merged_info = f" (merged {merged_count} groups)" if merged_count > 1 else ""
        console.info(f"{i+1:02d}. [{result['rule_id']}] {pattern_display}{merged_info}")
        console.kv("Count", f"{int(cast(int, result['total_count'])):,}")
        console.info("Original Logs Sample (Top 2):")
        original_logs = cast(list[str], result['original_logs'])
        for log in original_logs[:2]:
            console.info(f"- {log}")
        console.info("-" * 60)

    # Save results to file (JSON)
    output_filename = "subutai_results.json"
    with open(output_filename, "w", encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    console.info(f"💾 All results (including original logs) saved to '{output_filename}'.")


if __name__ == "__main__":
    main()
