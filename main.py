from __future__ import annotations

import sys
import json

from template_manager import RuleTemplateManager
from parser import SubutaiParser
from logic_clusterer import LogicClusterer
from ai_clusterer import AIClusterer


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python main.py <LOG_FILE> <TEMPLATE_FILE>")
        sys.exit(1)

    log_file = sys.argv[1]
    rule_file = sys.argv[2]

    # 1. Parsing
    tm = RuleTemplateManager(rule_file)
    parser = SubutaiParser(tm)
    parsed_logs = []
    
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith(('-', '=', 'Rule', 'Severity')): continue
            res = parser.parse_line(stripped)
            if res: parsed_logs.append(res)

    # 2. Logic Clustering [1st grouping: original method]
    logic_results = LogicClusterer().run(parsed_logs)
    print(f"\n📊 Stage 1 - Logic Clustering (Original Method - Variables Only):")
    print(f"   Input logs: {len(parsed_logs):,}")
    print(f"   Output groups: {len(logic_results):,}")
    print(f"   Compression ratio: {len(parsed_logs) / len(logic_results):.2f}x")

    # 3. AI Clustering [2nd grouping: semantic merging]
    results = [] # Store all results here

    ai_clusterer = AIClusterer()
    if ai_clusterer.ai_available:
        # 2nd grouping: semantically re-merge 1st logic groups using AI
        results = ai_clusterer.run(logic_results)
        print(f"\n🤖 Stage 2 - AI Clustering (Semantic Merging of 1st-Groups):")
        print(f"   Input 1st-groups: {len(logic_results):,}")
        print(f"   Output 2nd-groups: {len(results):,}")
        print(f"   Final compression ratio: {len(parsed_logs) / len(results):.2f}x")
    else:
        # Return only Logic results if AI unavailable
        for logic_group in logic_results:
            # Recover original logs from Logic group
            raw_logs = [m['raw_log'] for m in logic_group['members']]
            results.append({
                "type": "LogicGroup",
                "rule_id": logic_group['rule_id'],
                "representative_pattern": logic_group['pattern'],
                "total_count": logic_group['count'],
                "original_logs": raw_logs
            })

    # 4. Output results and save to file
    print("\n" + "="*80)
    print(f"✅ Final Results: {len(results)} Groups Created.")
    print("="*80)
    
    # Display on screen (sample)
    for i, result in enumerate(results[:5]):
        pattern_display = result.get('representative_pattern', 'N/A')
        merged_info = f" (merged {result.get('merged_variants_count', 1)} groups)" if result.get('merged_variants_count', 1) > 1 else ""
        print(f"{i+1:02d}. [{result['rule_id']}] {pattern_display}{merged_info}")
        print(f"    Count: {result['total_count']:,}")
        print(f"    Original Logs Sample (Top 2):")
        for log in result['original_logs'][:2]:
            print(f"      - {log}")
        print("-" * 60)

    # Save results to file (JSON)
    output_filename = "subutai_results.json"
    with open(output_filename, "w", encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 All results (including original logs) saved to '{output_filename}'.")


if __name__ == "__main__":
    main()
