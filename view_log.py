from __future__ import annotations

import json
import sys
import os

# ==============================================================================
# ANSI Color Codes (터미널 가독성용)
# ==============================================================================
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

def print_pretty_report(json_file_path: str) -> None:
    if not os.path.exists(json_file_path):
        print(f"{Colors.FAIL}❌ 파일을 찾을 수 없습니다: {json_file_path}{Colors.ENDC}")
        return

    print(f"{Colors.GREEN}📂 Loading results from: {json_file_path}...{Colors.ENDC}")
    
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"{Colors.FAIL}❌ JSON 읽기 실패: {e}{Colors.ENDC}")
        return

    total_groups = len(data)
    total_errors = sum(item.get('total_count', 0) for item in data)

    # --- [Summary Header] ---
    print("\n" + "="*80)
    print(f"{Colors.BOLD}{Colors.HEADER}📊 SUBUTAI ANALYSIS REPORT{Colors.ENDC}")
    print("="*80)
    print(f" • Total Log Lines : {Colors.FAIL}{total_errors:,}{Colors.ENDC}")
    print(f" • Compressed Groups: {Colors.BLUE}{total_groups:,}{Colors.ENDC}")
    print(f" • Compression Rate : {Colors.GREEN}{(1 - total_groups/total_errors)*100:.2f}%{Colors.ENDC}" if total_errors > 0 else "N/A")
    print("="*80 + "\n")

    # --- [Detail Body] ---
    for i, group in enumerate(data):
        rank = i + 1
        rule_id = group.get('rule_id', 'UNKNOWN')
        count = group.get('total_count', 0)
        pattern = group.get('representative_pattern', 'N/A')
        template = group.get('representative_template', 'N/A')
        logs = group.get('original_logs', [])
        
        # 그룹 헤더 출력
        print(f"{Colors.BOLD}[Rank {rank:02d}] {Colors.WARNING}{rule_id}{Colors.ENDC} (Count: {Colors.FAIL}{count:,}{Colors.ENDC})")
        print(f" {Colors.BLUE}├─ Pattern :{Colors.ENDC} {pattern}")
        print(f" {Colors.BLUE}├─ Template:{Colors.ENDC} {template}")
        
        # 원본 로그 출력 (너무 길면 줄임표 처리)
        print(f" {Colors.BLUE}└─ Original Logs ({len(logs)}):{Colors.ENDC}")
        
        preview_limit = 5 # 그룹당 보여줄 로그 개수 (조절 가능)
        
        for j, log in enumerate(logs[:preview_limit]):
            prefix = "   └─" if j == len(logs)-1 or j == preview_limit-1 else "   ├─"
            print(f"    {prefix} {log}")
            
        if len(logs) > preview_limit:
            remain = len(logs) - preview_limit
            print(f"       {Colors.CYAN}... (+ {remain:,} more lines hidden) ...{Colors.ENDC}")
        
        print("-" * 80) # 그룹 간 구분선

if __name__ == "__main__":
    # 기본 파일명 설정 (앞선 코드에서 저장한 이름)
    target_file = "subutai_results.json"
    
    # 인자로 파일명을 받으면 그걸 사용
    if len(sys.argv) > 1:
        target_file = sys.argv[1]
        
    print_pretty_report(target_file)