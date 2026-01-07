import os
import sys
import re
import json
import hashlib
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
    AI_AVAILABLE = False

# ==============================================================================
# 1. Template Manager
# ==============================================================================
class RuleTemplateManager:
    def __init__(self, template_file):
        self.template_dict = {} 
        self.var_pattern = re.compile(r"'(.*?)'")
        
        if template_file:
            print(f"📂 Loading Rule Templates from: {template_file}")
            self._load_templates(template_file)

    def _get_pure_template(self, text):
        # 1. 변수 영역 보호
        temp = self.var_pattern.sub("'<VAR>'", text)
        # 2. 독립된 숫자만 마스킹
        temp = re.sub(r"\b\d+\b", "<NUM>", temp)
        return temp.strip()

    def _load_templates(self, file_path):
        if not os.path.exists(file_path):
            return
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(('-', 'Rule', 'Severity')): continue
                parts = line.split(maxsplit=3)
                if len(parts) < 4: continue
                rule_id, message = parts[0], parts[3]
                pure_temp = self._get_pure_template(message)
                self.template_dict[pure_temp] = rule_id

    def get_rule_id(self, log_template):
        return self.template_dict.get(log_template, f"UNKNOWN_{hashlib.md5(log_template.encode()).hexdigest()[:6].upper()}")

# ==============================================================================
# 2. Parser
# ==============================================================================
class SubutaiParser:
    def __init__(self, template_manager):
        self.var_pattern = re.compile(r"'(.*?)'")
        self.tm = template_manager

    def parse_line(self, line):
        line = line.strip()
        if not line: return None
        
        variables = self.var_pattern.findall(line)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)
        template = self.tm._get_pure_template(line)
        rule_id = self.tm.get_rule_id(template)
        
        return {
            "rule_id": rule_id,
            "variables": var_tuple,
            "template": template,
            "raw_log": line  # <--- 원본 로그 저장됨
        }

# ==============================================================================
# 3. Logic Clusterer
# ==============================================================================
class LogicClusterer:
    def get_logic_signature(self, var_tuple):
        if not var_tuple or var_tuple == ("NO_VAR",): return "NO_VAR"
        sigs = [re.sub(r"\d+", "*", str(v)) for v in var_tuple]
        return " / ".join(sigs)

    def run(self, parsed_logs):
        groups = defaultdict(list)
        for p in parsed_logs:
            sig = self.get_logic_signature(p['variables'])
            key = (p['rule_id'], sig, p['template'])
            groups[key].append(p)

        results = []
        for (rule_id, sig, temp), members in groups.items():
            results.append({
                "type": "LogicGroup",
                "rule_id": rule_id,
                "pattern": sig,
                "template": temp,
                "count": len(members),
                "members": members  # <--- 여기에 raw_log가 포함된 파싱 객체들이 있음
            })
        results.sort(key=lambda x: x['count'], reverse=True)
        return results

# ==============================================================================
# 4. AI Clusterer
# ==============================================================================
class AIClusterer:
    def __init__(self, model_path='all-MiniLM-L6-v2'):
        if AI_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_path)
            except:
                global AI_AVAILABLE
                AI_AVAILABLE = False

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return []

        print(f"🤖 AI analyzing {len(logic_groups)} logic groups...")
        embedding_inputs = [f"{g['template']} {g['pattern']}" for g in logic_groups]
        embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
        
        clustering = DBSCAN(eps=0.2, min_samples=1, metric='cosine').fit(embeddings)
        
        ai_grouped = defaultdict(lambda: {"total_count": 0, "logic_subgroups": []})
        for label, logic_group in zip(clustering.labels_, logic_groups):
            cluster_key = f"{logic_group['rule_id']}_SG_{label}"
            ai_grouped[cluster_key]["total_count"] += logic_group['count']
            ai_grouped[cluster_key]["logic_subgroups"].append(logic_group)

        final_output = []
        for key, data in ai_grouped.items():
            main = max(data["logic_subgroups"], key=lambda x: x['count'])
            
            # [핵심] 원본 로그 복구 로직
            # AI 그룹 -> Logic 서브그룹 -> 멤버 -> raw_log 순으로 추출하여 합침
            all_raw_logs = []
            for sub in data["logic_subgroups"]:
                for member in sub["members"]:
                    all_raw_logs.append(member["raw_log"])

            final_output.append({
                "type": "AISuperGroup",
                "super_group_id": key,
                "rule_id": main['rule_id'],
                "representative_template": main['representative_template'] if 'representative_template' in main else main['template'],
                "representative_pattern": main['representative_pattern'] if 'representative_pattern' in main else main['pattern'],
                "total_count": data["total_count"],
                "merged_variants_count": len(data["logic_subgroups"]),
                "original_logs": all_raw_logs  # <--- 복구된 원본 로그 리스트
            })
        
        final_output.sort(key=lambda x: x['total_count'], reverse=True)
        return final_output

# ==============================================================================
# 5. Main Execution
# ==============================================================================
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python subutai_reviewer.py <LOG_FILE> <TEMPLATE_FILE>")
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

    # 2. Logic Clustering
    logic_results = LogicClusterer().run(parsed_logs)

    # 3. AI Clustering & Result Aggregation
    results = [] # <--- 여기에 모든 결과를 저장합니다.

    if AI_AVAILABLE:
        # AI 결과에는 이미 original_logs 복구 로직이 포함되어 있음
        results = AIClusterer().run(logic_results)
    else:
        # AI가 없으면 Logic 결과를 포맷팅하여 저장
        for g in logic_results:
            # Logic 그룹의 원본 로그 복구
            raw_logs = [m['raw_log'] for m in g['members']]
            results.append({
                "type": "LogicGroup",
                "rule_id": g['rule_id'],
                "representative_pattern": g['pattern'],
                "total_count": g['count'],
                "original_logs": raw_logs
            })

    # 4. 결과 출력 및 파일 저장
    print("\n" + "="*80)
    print(f"✅ Final Results: {len(results)} Groups Created.")
    print("="*80)
    
    # 화면 출력 (샘플)
    for i, res in enumerate(results[:5]):
        print(f"{i+1:02d}. [{res['rule_id']}] {res['representative_pattern']}")
        print(f"    Count: {res['total_count']:,}")
        print(f"    Original Logs Sample (Top 2):")
        for log in res['original_logs'][:2]:
            print(f"      - {log}")
        print("-" * 60)

    # 결과 파일 저장 (JSON)
    output_filename = "subutai_results.json"
    with open(output_filename, "w", encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 모든 결과(원본 로그 포함)가 '{output_filename}'에 저장되었습니다.")