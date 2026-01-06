import os
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
    print("⚠️ 경고: AI 라이브러리 미설치. (pip install sentence-transformers scikit-learn)")
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
        if not os.path.exists(self.file_path): return []
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
# 2. Logic Layer: Full Path Logic (절삭 없음!)
# ==============================================================================
class LogicClusterer:
    def __init__(self):
        pass

    def get_logic_signature(self, var_str):
        """
        [수정됨] 계층 구조를 자르지 않습니다.
        대신 숫자(Index)만 마스킹하여 전체 경로를 보존합니다.
        
        Input:  top/u_cpu_0/core/reg_128
        Output: top/u_cpu_*/core/reg_*
        """
        # 숫자를 모두 *로 치환 (경로는 그대로 유지)
        masked_path = re.sub(r"\d+", "*", var_str)
        return masked_path

    def run(self, parsed_logs):
        groups = defaultdict(list)
        
        for p in parsed_logs:
            if not p['variables']:
                sig = "NO_VAR"
            else:
                # 첫 번째 변수 기준 (필요시 Source/Dest 모두 고려 가능)
                sig = self.get_logic_signature(p['variables'][0])
            
            # Rule ID + Full Path Pattern으로 1차 그룹핑
            key = (p['rule_id'], sig)
            groups[key].append(p)

        # AI 엔진 연동용 데이터 포맷
        logic_results = []
        for (rule_id, sig), members in groups.items():
            logic_results.append({
                "rule_id": rule_id,
                "pattern": sig,  # 전체 경로가 살아있는 패턴
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
            print(f"⏳ AI 모델 로딩 중... ({model_name})")
            self.model = SentenceTransformer(model_name)
            print("✅ 모델 로딩 완료!")

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return logic_groups

        print(f"🤖 AI 분석 시작: {len(logic_groups)}개의 패턴을 분석합니다.")
        t0 = time.time()

        # Input: Rule ID + Full Path Pattern
        # 예: "LINT-01 top/u_cpu_*/core/reg_*"
        embedding_inputs = [f"{g['rule_id']} {g['pattern']}" for g in logic_groups]

        # 벡터화
        embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=True)

        # 클러스터링 (DBSCAN)
        # eps=0.25: 유사도 약 75% 이상이면 같은 그룹
        clustering = DBSCAN(eps=0.25, min_samples=2, metric='cosine').fit(embeddings)
        labels = clustering.labels_

        # 결과 병합
        ai_grouped_result = defaultdict(lambda: {
            "super_group_id": None, "total_count": 0, 
            "representative_pattern": "", "sub_patterns": []
        })

        for label, logic_group in zip(labels, logic_groups):
            # Noise(-1)는 개별 그룹으로 처리
            cluster_key = f"SG_{label}" if label != -1 else f"NOISE_{logic_group['pattern']}"
            
            group_data = ai_grouped_result[cluster_key]
            group_data["super_group_id"] = cluster_key
            group_data["total_count"] += logic_group['count']
            group_data["sub_patterns"].append(logic_group)

        # 최종 정리
        final_output = []
        for key, data in ai_grouped_result.items():
            # 가장 빈도 높은 패턴을 대표 이름으로
            main_sub = max(data["sub_patterns"], key=lambda x: x['count'])
            data["representative_pattern"] = main_sub["pattern"]
            data["rule_id"] = main_sub["rule_id"]
            final_output.append(data)

        final_output.sort(key=lambda x: x['total_count'], reverse=True)
        print(f"⚡ AI 분석 완료 ({time.time()-t0:.2f}초)")
        return final_output

# ==============================================================================
# 4. Main Execution
# ==============================================================================
if __name__ == "__main__":
    # --- 테스트용 데이터 생성 ---
    log_filename = "test_run.log"
    with open(log_filename, "w") as f:
        f.write("Info: Start\n")
        # [Case 1] 경로가 깊지만 내용은 유사한 경우 -> Logic은 분리하지만 AI가 묶어야 함
        # 기존: top/u_cpu/* 로 잘렸음 (Truncation)
        # 변경: top/u_cpu/decode/pipe_* (Full Path 유지)
        for i in range(10): f.write(f"LINT-01: Signal 'top/u_cpu/decode/pipe_{i}' float\n")
        for i in range(10): f.write(f"LINT-01: Signal 'top/u_cpu/execute/pipe_{i}' float\n")
        
        # [Case 2] 글자가 다르지만 의미가 같은 경우 (AI 역할)
        f.write("TIM-01: Path 'top/mem/ddr_phy_ctrl' violation\n")
        f.write("TIM-01: Path 'top/mem/ddr_controller' violation\n")

    print("🚀 Pipeline Start\n")

    # 1. Read & Parse
    reader = SubutaiLogReader(log_filename)
    parser = SubutaiParser()
    parsed_logs = [parser.parse_line(line) for line in reader.stream_valid_lines()]
    
    # 2. Logic (Full Path with Masking)
    # 절삭(Truncation) 없이 순수하게 숫자만 마스킹합니다.
    logic_engine = LogicClusterer()
    logic_results = logic_engine.run(parsed_logs)
    print(f"✅ Logic Result: {len(logic_results)} groups (Full Path Preserved)")
    
    # 3. AI (Semantic Merge)
    # 살아있는 Full Path 정보를 이용해 정확하게 묶습니다.
    ai_engine = AIClusterer()
    final_results = ai_engine.run(logic_results)
    
    # 4. Report
    print("\n" + "="*50)
    for group in final_results[:5]:
        print(f"[{group['representative_pattern']}] (Count: {group['total_count']})")
        if len(group['sub_patterns']) > 1:
            print(f"  └ Merged: {[sub['pattern'] for sub in group['sub_patterns']]}")
    
    if os.path.exists(log_filename): os.remove(log_filename)