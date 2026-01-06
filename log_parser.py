import os
import sys
import re
import json
import time
from collections import defaultdict

# ==============================================================================
# [Dependency Check] 폐쇄망 배포 시 이 라이브러리들이 설치되어 있어야 합니다.
# ==============================================================================
try:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import DBSCAN
    import numpy as np
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# ==============================================================================
# 1. Parser (Rule ID 우선 + 변수/경로 중심 추출)
# ==============================================================================
class SubutaiParser:
    def __init__(self):
        # Rule ID: 로그 맨 앞의 에러 코드 (예: LINT-01, TIMING-101)
        self.rule_pattern = re.compile(r"^([A-Z0-9_]+-\d+|[A-Z0-9_]+(?=:))")
        # 변수: 따옴표(' ' 또는 " ") 내부의 인스턴스 경로/변수명
        self.var_pattern = re.compile(r"['\"](.*?)['\"]")

    def parse_line(self, line):
        line = line.strip()
        rule_match = self.rule_pattern.search(line)
        rule_id = rule_match.group(1) if rule_match else "GENERAL_ERR"
        
        # 명령어(set_timing_derate 등)는 배제하고 '변수'만 추출
        variables = self.var_pattern.findall(line)
        
        return {
            "rule_id": rule_id,
            "variables": variables,
            "raw_log": line
        }

# ==============================================================================
# 2. Logic Layer: Full Path Masking (숫자만 마스킹하여 1차 그룹핑)
# ==============================================================================
class LogicClusterer:
    def get_logic_signature(self, var_list):
        """변수 리스트에서 숫자만 *로 치환하여 경로 패턴 생성"""
        if not var_list: return "NO_VAR"
        sigs = [re.sub(r"\d+", "*", v) for v in var_list]
        return " / ".join(sigs)

    def run(self, parsed_logs):
        # Key: (Rule_ID, Variable_Signature)
        groups = defaultdict(list)
        for p in parsed_logs:
            sig = self.get_logic_signature(p['variables'])
            key = (p['rule_id'], sig)
            groups[key].append(p)

        logic_results = []
        for (rule_id, sig), members in groups.items():
            logic_results.append({
                "rule_id": rule_id,
                "pattern": sig,
                "count": len(members),
                "members": members 
            })
        # 에러 발생 빈도순 정렬
        logic_results.sort(key=lambda x: x['count'], reverse=True)
        return logic_results

# ==============================================================================
# 3. AI Layer: Semantic Semantic Merge (의미 기반 2차 압축)
# ==============================================================================
class AIClusterer:
    def __init__(self, model_path='all-MiniLM-L6-v2'):
        """폐쇄망일 경우 model_path에 로컬 폴더 경로를 넣으세요."""
        if AI_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_path)
            except Exception as e:
                print(f"⚠️ 모델 로드 실패: {e}")
                global AI_AVAILABLE
                AI_AVAILABLE = False

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return logic_groups

        # Rule ID와 패턴을 합쳐서 임베딩 (Error Type이 다르면 멀어지게 유도)
        embedding_inputs = [f"{g['rule_id']} {g['pattern']}" for g in logic_groups]
        embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
        
        # DBSCAN: 코사인 유사도 기반 클러스터링
        clustering = DBSCAN(eps=0.2, min_samples=1, metric='cosine').fit(embeddings)
        labels = clustering.labels_

        ai_grouped_result = defaultdict(lambda: {
            "total_count": 0, "representative_pattern": "", "rule_id": "", "logic_subgroups": []
        })

        for label, logic_group in zip(labels, logic_groups):
            # Rule ID가 다르면 절대 합쳐지지 않도록 키에 포함
            cluster_key = f"{logic_group['rule_id']}_SG_{label}"
            
            group_data = ai_grouped_result[cluster_key]
            group_data["total_count"] += logic_group['count']
            group_data["rule_id"] = logic_group['rule_id']
            group_data["logic_subgroups"].append(logic_group)

        final_output = []
        for key, data in ai_grouped_result.items():
            # 가장 count가 높은 로직 패턴을 대표 이름으로 선정
            main_sub = max(data["logic_subgroups"], key=lambda x: x['count'])
            data["representative_pattern"] = main_sub["pattern"]
            final_output.append(data)

        final_output.sort(key=lambda x: x['total_count'], reverse=True)
        return final_output

# ==============================================================================
# 4. Main Controller (전체 공정 관리 및 보고)
# ==============================================================================
def main():
    if len(sys.argv) < 2:
        print("❌ Usage: python subutai_final.py <log_file_path>")
        return
    
    log_file = sys.argv[1]
    if not os.path.exists(log_file):
        print(f"❌ File not found: {log_file}")
        return

    print(f"\n[System] Starting analysis on: {log_file}")
    
    # 1. Load & Filter
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        # 무시할 라인 처리 (헤더, 빈줄, 대시 등)
        raw_lines = [line.strip() for line in f if line.strip() and not line.startswith(('---', '===', 'Info'))]
    
    # 2. Parsing
    parser = SubutaiParser()
    parsed_logs = [parser.parse_line(line) for line in raw_lines]
    
    # 3. Stage 1: Logic Clustering
    logic_engine = LogicClusterer()
    logic_results = logic_engine.run(parsed_logs)
    
    print("\n" + "="*80)
    print(f"STAG 1: Logic Clustering Done. ({len(parsed_logs):,} Lines -> {len(logic_results):,} Groups)")
    print("="*80)
    for i, g in enumerate(logic_results[:10]): # 상위 10개 예시
        print(f"L{i+1:02d}. [{g['rule_id']}] Pattern: {g['pattern']}")
        print(f"    Count: {g['count']:,}")
        # 실제 포함된 변수 샘플들
        samples = list(set(["/".join(m['variables']) for m in g['members'] if m['variables']]))
        print(f"    Samples: {samples[:3]}")
        print("-" * 60)

    # 4. Stage 2: AI Clustering
    if AI_AVAILABLE:
        ai_engine = AIClusterer()
        final_results = ai_engine.run(logic_results)
        
        print("\n" + "="*80)
        print(f"STAGE 2: AI Semantic Compression Done. ({len(logic_results):,} -> {len(final_results):,} Super Groups)")
        print("="*80)
        for i, g in enumerate(final_results[:10]):
            print(f"A{i+1:02d}. [{g['rule_id']}] Rep.Pattern: {g['representative_pattern']}")
            print(f"    Total Count: {g['total_count']:,} (Merged {len(g['logic_subgroups'])} logic patterns)")
            if len(g['logic_subgroups']) > 1:
                sub_list = [s['pattern'] for s in sorted(g['logic_subgroups'], key=lambda x: x['count'], reverse=True)]
                print(f"    Merged Variants: {sub_list[:3]} ...")
            print("-" * 60)
    else:
        print("\n[System] AI Clustering skipped due to missing libraries.")

if __name__ == "__main__":
    main()