import os
import sys
import re
import json
import time
import hashlib
from collections import defaultdict

# AI 라이브러리 체크
try:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import DBSCAN
    import numpy as np
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# ==============================================================================
# 1. Template Manager (sys.argv[2] 처리)
# ==============================================================================
class RuleTemplateManager:
    def __init__(self, template_file):
        self.template_dict = {} # {Template_String: Rule_ID}
        self.var_pattern = re.compile(r"'(.*?)'")
        if template_file:
            self._load_templates(template_file)

    def _load_templates(self, file_path):
        if not os.path.exists(file_path):
            print(f"⚠️ 템플릿 파일을 찾을 수 없습니다: {file_path}")
            return

        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(('-', 'Rule', 'Severity')): continue
                
                # 가정한 포맷: Rule_ID Severity Status Message...
                # 메시지 부분에서 변수를 <VAR>로 치환하여 키 생성
                parts = line.split(maxsplit=3)
                if len(parts) < 4: continue
                
                rule_id = parts[0]
                message = parts[3]
                
                # 메시지 뼈대 추출
                template = self.var_pattern.sub("'<VAR>'", message)
                template = re.sub(r"\d+", "<NUM>", template)
                self.template_dict[template] = rule_id
        
        print(f"✅ {len(self.template_dict)}개의 룰 템플릿 로드 완료.")

    def get_rule_id(self, log_template):
        # 로드된 템플릿 사전에서 Rule ID 조회, 없으면 해시 생성
        return self.template_dict.get(log_template, f"REV_{hashlib.md5(log_template.encode()).hexdigest()[:6].upper()}")

# ==============================================================================
# 2. Parser & Clusterer
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
        
        # 로그에서 뼈대 추출
        template = self.var_pattern.sub("'<VAR>'", line)
        template = re.sub(r"\d+", "<NUM>", template)
        
        # 템플릿 매니저를 통해 실제 Rule ID 획득
        rule_id = self.tm.get_rule_id(template)
        
        return {
            "rule_id": rule_id,
            "variables": var_tuple,
            "template": template,
            "raw_log": line
        }

class LogicClusterer:
    def get_logic_signature(self, var_tuple):
        if not var_tuple or var_tuple == ("NO_VAR",): return "NO_VAR"
        return " / ".join([re.sub(r"\d+", "*", str(v)) for v in var_tuple])

    def run(self, parsed_logs):
        groups = defaultdict(list)
        for p in parsed_logs:
            sig = self.get_logic_signature(p['variables'])
            key = (p['rule_id'], sig, p['template'])
            groups[key].append(p)

        results = []
        for (rule_id, sig, temp), members in groups.items():
            results.append({
                "rule_id": rule_id, "pattern": sig, "template": temp,
                "count": len(members), "members": members
            })
        results.sort(key=lambda x: x['count'], reverse=True)
        return results

# ==============================================================================
# 3. AI Layer (Semantic Logic Merge)
# ==============================================================================
class AIClusterer:
    def __init__(self, model_path='all-MiniLM-L6-v2'):
        if AI_AVAILABLE:
            self.model = SentenceTransformer(model_path)

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return logic_groups

        embedding_inputs = [f"{g['template']} {g['pattern']}" for g in logic_groups]
        embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
        
        clustering = DBSCAN(eps=0.2, min_samples=1, metric='cosine').fit(embeddings)
        labels = clustering.labels_

        ai_grouped = defaultdict(lambda: {"total_count": 0, "logic_subgroups": []})
        for label, logic_group in zip(labels, logic_groups):
            cluster_key = f"{logic_group['rule_id']}_SG_{label}"
            ai_grouped[cluster_key]["total_count"] += logic_group['count']
            ai_grouped[cluster_key]["logic_subgroups"].append(logic_group)

        final_output = []
        for key, data in ai_grouped.items():
            main = max(data["logic_subgroups"], key=lambda x: x['count'])
            final_output.append({
                "super_group_id": key,
                "rule_id": main['rule_id'],
                "representative_template": main['template'],
                "representative_pattern": main['pattern'],
                "total_count": data["total_count"],
                "logic_subgroups": data["logic_subgroups"]
            })
        final_output.sort(key=lambda x: x['total_count'], reverse=True)
        return final_output

# ==============================================================================
# 4. Main
# ==============================================================================
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("❌ Usage: python subutai.py <log_file> <template_rule_file>")
        sys.exit(1)

    log_file = sys.argv[1]
    rule_file = sys.argv[2]

    # 1. 템플릿 로드
    tm = RuleTemplateManager(rule_file)
    
    # 2. 로그 파싱
    parser = SubutaiParser(tm)
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        parsed_logs = [parser.parse_line(line) for line in f if line.strip()]
    parsed_logs = [p for p in parsed_logs if p] # None 제거

    # 3. Stage 1: Logic Clustering
    logic_results = LogicClusterer().run(parsed_logs)
    
    print("\n" + "="*80)
    print(f"STAGE 1: Logic Groups ({len(logic_results)})")
    print("="*80)
    for i, g in enumerate(logic_results[:5]):
        print(f"L{i+1}. [{g['rule_id']}] {g['template'][:70]}... | Count: {g['count']}")

    # 4. Stage 2: AI Clustering
    if AI_AVAILABLE:
        final_results = AIClusterer().run(logic_results)
        print("\n" + "="*80)
        print(f"STAGE 2: AI Super Groups ({len(final_results)})")
        print("="*80)
        for i, g in enumerate(final_results[:15]):
            print(f"A{i+1:02d}. [{g['rule_id']}] {g['representative_template']}")
            print(f"    Pattern: {g['representative_pattern']}")
            print(f"    Total Count: {g['total_count']:,} (Merged {len(g['logic_subgroups'])} logic variants)")
            print("-" * 60)