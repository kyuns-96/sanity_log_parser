import os
import sys
import re
import json
import time
from collections import defaultdict

# ==============================================================================
# [Dependency Check]
# ==============================================================================
try:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import DBSCAN
    import numpy as np
    AI_AVAILABLE = True
except ImportError:
    print("⚠️  [Warning] AI 라이브러리(sentence-transformers)가 없습니다.")
    print("   AI Clustering 단계는 스킵되고 Logic 단계 결과만 출력됩니다.")
    AI_AVAILABLE = False

# ==============================================================================
# 1. Log Reader & Parser
# ==============================================================================
class SubutaiLogReader:
    def __init__(self, file_path):
        self.file_path = file_path

    def _is_ignorable(self, line):
        line = line.strip()
        if not line or line.startswith(("---", "===", "Info:", "Page")): return True
        return False

    def stream_valid_lines(self):
        if not os.path.exists(self.file_path):
            print(f"❌ Error: File not found ({self.file_path})")
            sys.exit(1)
            
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                if not self._is_ignorable(line): yield line.strip()

class SubutaiParser:
    def __init__(self):
        self.rule_pattern = re.compile(r"^([A-Z]+-\d+)")
        self.var_pattern = re.compile(r"['\"](.*?)['\"]")

    def parse_line(self, line):
        match = self.rule_pattern.search(line)
        rule_id = match.group(1) if match else "UNKNOWN"
        variables = self.var_pattern.findall(line)
        # 템플릿: 변수 내용 제거
        template = self.var_pattern.sub("'<VAR>'", line)
        template = re.sub(r"\d+", "<NUM>", template)
        return {
            "rule_id": rule_id,
            "template": template,
            "variables": variables,
            "raw_log": line
        }

# ==============================================================================
# 2. Logic Layer: Full Path Preservation (숫자만 마스킹)
# ==============================================================================
class LogicClusterer:
    def __init__(self):
        pass

    def get_logic_signature(self, var_str):
        # 전체 경로를 유지하되, 숫자만 *로 치환
        # top/u_cpu_0/wire -> top/u_cpu_*/wire
        return re.sub(r"\d+", "*", var_str)

    def run(self, parsed_logs):
        groups = defaultdict(list)
        
        for p in parsed_logs:
            if not p['variables']:
                sig = "NO_VAR"
            else:
                sig = self.get_logic_signature(p['variables'][0])
            
            key = (p['rule_id'], sig)
            groups[key].append(p)

        logic_results = []
        for (rule_id, sig), members in groups.items():
            logic_results.append({
                "rule_id": rule_id,
                "pattern": sig,
                "count": len(members),
                "template": members[0]['template'],
                "sample_log": members[0]['raw_log']
            })
            
        return logic_results

# ==============================================================================
# 3. AI Layer: Semantic Clusterer
# ==============================================================================
class AIClusterer:
    def __init__(self, model_name='all-MiniLM-L6-v2'):
        if AI_AVAILABLE:
            print(f"⏳  [System] Loading AI Model ({model_name})...")
            self.model = SentenceTransformer(model_name)
            print("✅  [System] AI Model Loaded.")

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return logic_groups

        print(f"🤖  [System] AI analyzing {len(logic_groups)} patterns...")
        t0 = time.time()

        # Input 생성
        embedding_inputs = [f"{g['rule_id']} {g['pattern']}" for g in logic_groups]

        # 벡터화 & 클러스터링
        embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
        clustering = DBSCAN(eps=0.25, min_samples=2, metric='cosine').fit(embeddings)
        labels = clustering.labels_

        # 결과 병합
        ai_grouped_result = defaultdict(lambda: {
            "super_group_id": None, "total_count": 0, 
            "representative_pattern": "", "sub_patterns": []
        })

        for label, logic_group in zip(labels, logic_groups):
            # Noise(-1)는 개별 처리
            cluster_key = f"SG_{label}" if label != -1 else f"NOISE_{logic_group['pattern']}"
            
            group_data = ai_grouped_result[cluster_key]
            group_data["super_group_id"] = cluster_key
            group_data["total_count"] += logic_group['count']
            group_data["sub_patterns"].append(logic_group)

        # 최종 리스트 변환
        final_output = []
        for key, data in ai_grouped_result.items():
            main_sub = max(data["sub_patterns"], key=lambda x: x['count'])
            data["representative_pattern"] = main_sub["pattern"]
            data["rule_id"] = main_sub["rule_id"]
            final_output.append(data)

        final_output.sort(key=lambda x: x['total_count'], reverse=True)
        print(f"⚡  [System] AI Analysis done in {time.time()-t0:.2f}s")
        return final_output

# ==============================================================================
# 4. Main Execution
# ==============================================================================
if __name__ == "__main__":
    # 1. 입력 확인
    if len(sys.argv) < 2:
        print("Usage: python subutai_final.py <log_file_path>")
        sys.exit(1)
    
    log_file = sys.argv[1]
    print(f"\n🚀  Subutai AI Reviewer Started. Target: {log_file}")
    print("=" * 60)

    # --- Stage 0: Parse ---
    reader = SubutaiLogReader(log_file)
    parser = SubutaiParser()
    
    raw_lines = list(reader.stream_valid_lines())
    parsed_logs = [parser.parse_line(line) for line in raw_lines]
    
    print(f"\n[Stage 0] Parsing Completed")
    print(f"   - Input Lines (Valid): {len(raw_lines):,}")
    print(f"   - Parsed Elements    : {len(parsed_logs):,}")

    # --- Stage 1: Logic Clustering ---
    logic_engine = LogicClusterer()
    logic_results = logic_engine.run(parsed_logs)
    
    # 통계 계산
    logic_groups_cnt = len(logic_results)
    logic_total_elements = sum(g['count'] for g in logic_results)
    
    print(f"\n[Stage 1] Logic Clustering (Full Path Masking)")
    print(f"   - Groups Created     : {logic_groups_cnt:,}")
    print(f"   - Total Elements     : {logic_total_elements:,}")
    if logic_total_elements != len(parsed_logs):
        print("   ⚠️  [Warning] Count Mismatch in Logic Stage!")

    # --- Stage 2: AI Clustering ---
    if AI_AVAILABLE:
        ai_engine = AIClusterer()
        final_results = ai_engine.run(logic_results)
        
        # 통계 계산
        ai_groups_cnt = len(final_results)
        ai_total_elements = sum(g['total_count'] for g in final_results)
        
        print(f"\n[Stage 2] AI Semantic Clustering (DBSCAN)")
        print(f"   - Super Groups       : {ai_groups_cnt:,}")
        print(f"   - Total Elements     : {ai_total_elements:,}")
        
        # 압축률 계산
        compression_ratio = (1 - (ai_groups_cnt / len(parsed_logs))) * 100
        print(f"   - Compression Ratio  : {compression_ratio:.2f}%")
        
        if ai_total_elements != len(parsed_logs):
            print("   ⚠️  [Warning] Count Mismatch in AI Stage!")
            
    else:
        final_results = logic_results
        print("\n[Stage 2] Skipped (AI Library Not Found)")

    # --- Final Report ---
    print("\n" + "=" * 60)
    print(f"📊  TOP 10 ISSUE GROUPS")
    print("=" * 60)
    
    for i, group in enumerate(final_results[:10]):
        # 대표 패턴
        pat = group.get('representative_pattern', group.get('pattern'))
        # 갯수
        cnt = group.get('total_count', group.get('count'))
        # 서브 그룹 개수 (AI 썼을 때만 존재)
        merged_info = ""
        if 'sub_patterns' in group and len(group['sub_patterns']) > 1:
            merged_info = f"(Merged {len(group['sub_patterns'])} variants)"
            
        print(f"{i+1:02d}. [{pat}]")
        print(f"    Count: {cnt:,} {merged_info}")
        
        # 병합된 하위 패턴 예시 출력
        if 'sub_patterns' in group and len(group['sub_patterns']) > 1:
            # 상위 3개만 보여줌
            sorted_subs = sorted(group['sub_patterns'], key=lambda x: x['count'], reverse=True)
            for sub in sorted_subs[:3]:
                print(f"      └ {sub['pattern']} (cnt: {sub['count']})")
            if len(sorted_subs) > 3:
                print(f"      └ ... and {len(sorted_subs)-3} more")
        
        print("-" * 40)