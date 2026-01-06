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
# 1. Template Manager (Rule File Loader)
# ==============================================================================
class RuleTemplateManager:
    def __init__(self, template_file):
        self.template_dict = {} # {Pure_Template_String : Rule_ID}
        self.var_pattern = re.compile(r"'(.*?)'")
        
        if template_file:
            print(f"📂 Loading Rule Templates from: {template_file}")
            self._load_templates(template_file)
        else:
            print("⚠️ No template file provided.")

    def _get_pure_template(self, text):
        """
        [핵심 로직] 메시지에서 변수와 숫자를 안전하게 마스킹
        1. 변수('...')를 먼저 <VAR>로 치환하여 변수명 내부 보호
        2. 그 후, 남은 텍스트에서 '단어 경계가 있는 숫자'만 <NUM>으로 치환
        """
        # 1. 변수 영역 보호 (<VAR>)
        temp = self.var_pattern.sub("'<VAR>'", text)
        
        # 2. 독립된 숫자만 마스킹 (\b는 단어 경계를 의미)
        # 예: "Size 100" -> "Size <NUM>", "u_cpu_0" -> "u_cpu_0" (변화 없음)
        temp = re.sub(r"\b\d+\b", "<NUM>", temp)
        
        return temp.strip()

    def _load_templates(self, file_path):
        if not os.path.exists(file_path):
            print(f"❌ Template file not found: {file_path}")
            sys.exit(1)

        count = 0
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                # 헤더나 구분선 건너뛰기
                if not line or line.startswith(('-', 'Rule', 'Severity')): continue
                
                # 파싱: Rule_ID ... Message
                # 공백으로 3번만 쪼개서 마지막 나머지를 Message로 간주
                parts = line.split(maxsplit=3)
                if len(parts) < 4: continue
                
                rule_id = parts[0]
                message = parts[3]
                
                # 뼈대 추출 및 등록
                pure_temp = self._get_pure_template(message)
                self.template_dict[pure_temp] = rule_id
                count += 1
        
        print(f"✅ Loaded {count} templates.")

    def get_rule_id(self, log_template):
        # 템플릿 사전에 있으면 Rule ID 반환, 없으면 해시 ID 생성
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
        
        if re.search(r'\b\d+\s+of\s+\d+\b', line):
            pass # 예: "3 of 5"
        else:
            return None
        
        line = " ".join(line.split()[4:]) # 앞 4개 토큰 제거

        # 1. 변수 추출 (있는 그대로)
        variables = self.var_pattern.findall(line)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)
        
        # 2. 템플릿 생성 (매니저와 동일한 안전 로직 사용)
        template = self.tm._get_pure_template(line)
        
        # 3. Rule ID 매칭
        rule_id = self.tm.get_rule_id(template)
        
        return {
            "rule_id": rule_id,
            "variables": var_tuple,
            "template": template,
            "raw_log": line
        }

# ==============================================================================
# 3. Logic Clusterer
# ==============================================================================
class LogicClusterer:
    def get_logic_signature(self, var_tuple):
        if not var_tuple or var_tuple == ("NO_VAR",): return "NO_VAR"
        
        # 그룹핑을 위해 변수 경로 내의 숫자는 여기서 마스킹 (*)
        # u_cpu_0 -> u_cpu_*
        sigs = [re.sub(r"\d+", "*", str(v)) for v in var_tuple]
        return " / ".join(sigs)

    def run(self, parsed_logs):
        groups = defaultdict(list)
        
        for p in parsed_logs:
            # 그룹핑 키: Rule ID + 변수 패턴 + 문장 뼈대
            sig = self.get_logic_signature(p['variables'])
            key = (p['rule_id'], sig, p['template'])
            groups[key].append(p)

        results = []
        for (rule_id, sig, temp), members in groups.items():
            results.append({
                "rule_id": rule_id,
                "pattern": sig,
                "template": temp,
                "count": len(members),
                "members": members
            })
        
        # Count 내림차순 정렬
        results.sort(key=lambda x: x['count'], reverse=True)
        return results

# ==============================================================================
# 4. AI Clusterer
# ==============================================================================
class AIClusterer:
    def __init__(self, model_path='all-MiniLM-L6-v2'):
        if AI_AVAILABLE:
            print(f"⏳ Loading AI Model ({model_path})...")
            try:
                self.model = SentenceTransformer(model_path)
            except Exception as e:
                print(f"⚠️ Model load failed: {e}")
                global AI_AVAILABLE
                AI_AVAILABLE = False

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return logic_groups

        print(f"🤖 AI analyzing {len(logic_groups)} logic groups...")
        
        # 임베딩 입력: 템플릿(문장의미) + 패턴(변수구조)
        embedding_inputs = [f"{g['template']} {g['pattern']}" for g in logic_groups]
        embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
        
        # DBSCAN: 코사인 거리 0.2 이내 (유사도 80% 이상)
        clustering = DBSCAN(eps=0.2, min_samples=1, metric='cosine').fit(embeddings)
        labels = clustering.labels_

        ai_grouped = defaultdict(lambda: {
            "total_count": 0, "logic_subgroups": []
        })

        for label, logic_group in zip(labels, logic_groups):
            # Rule ID가 다르면 섞이지 않도록 키 설정
            cluster_key = f"{logic_group['rule_id']}_SG_{label}"
            
            group_data = ai_grouped[cluster_key]
            group_data["total_count"] += logic_group['count']
            group_data["logic_subgroups"].append(logic_group)

        final_output = []
        for key, data in ai_grouped.items():
            # 가장 빈도 높은 로직 그룹을 대표로 선정
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
# 5. Main Execution
# ==============================================================================
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("\n❌ Usage: python subutai_reviewer.py <LOG_FILE> <TEMPLATE_FILE>")
        print("   Ex: python subutai_reviewer.py run.log rules.txt\n")
        sys.exit(1)

    log_file = sys.argv[1]
    rule_file = sys.argv[2]

    # 1. 템플릿 로드
    tm = RuleTemplateManager(rule_file)
    
    # 2. 로그 파일 읽기 & 파싱
    print(f"📂 Parsing Log File: {log_file}")
    parser = SubutaiParser(tm)
    parsed_logs = []
    
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                # 헤더나 공백 라인 필터링 (사용자 환경에 맞춰 조정 가능)
                stripped = line.strip()
                if not stripped or stripped.startswith(('-', '=', 'Rule', 'Severity')):
                    continue
                
                res = parser.parse_line(stripped)
                if res: parsed_logs.append(res)
    else:
        print(f"❌ Log file not found: {log_file}")
        sys.exit(1)

    print(f"✅ Parsed {len(parsed_logs)} lines.")

    # 3. Stage 1: Logic Clustering
    logic_engine = LogicClusterer()
    logic_results = logic_engine.run(parsed_logs)

    print("\n" + "="*80)
    print(f"📊 STAGE 1 REPORT: Logic Clustering ({len(logic_results)} Groups)")
    print("="*80)
    for i, g in enumerate(logic_results[:10]):
        print(f"L{i+1:02d}. [{g['rule_id']}] Count: {g['count']:,}")
        print(f"     Template: {g['template']}")
        print(f"     Pattern : {g['pattern']}")
        
        # 실제 변수 샘플 확인 (변수명 훼손 여부 체크용)
        sample_vars = list(set(["/".join(m['variables']) for m in g['members'] if m['variables'] != ("NO_VAR",)]))
        if sample_vars:
            print(f"     Samples : {sample_vars[:2]}")
        print("-" * 60)

    # 4. Stage 2: AI Clustering
    if AI_AVAILABLE:
        ai_engine = AIClusterer() # 모델 경로는 필요시 수정 (예: './model_folder')
        final_results = ai_engine.run(logic_results)

        print("\n" + "="*80)
        print(f"🚀 STAGE 2 REPORT: AI Semantic Merge ({len(final_results)} Super Groups)")
        print("="*80)
        for i, g in enumerate(final_results[:15]):
            print(f"A{i+1:02d}. [{g['rule_id']}] Count: {g['total_count']:,}")
            print(f"     Rep.Template: {g['representative_template']}")
            print(f"     Rep.Pattern : {g['representative_pattern']}")
            
            if len(g['logic_subgroups']) > 1:
                print(f"     >>> Merged {len(g['logic_subgroups'])} variants:")
                # 병합된 하위 패턴들 보여주기
                sub_list = sorted(g['logic_subgroups'], key=lambda x: x['count'], reverse=True)
                for sub in sub_list[:3]:
                    print(f"         - {sub['pattern']} (cnt: {sub['count']})")
                if len(sub_list) > 3:
                    print(f"         - ... and {len(sub_list)-3} more")
            print("-" * 60)
    else:
        print("\n⚠️ AI Library not found. Skipping Stage 2.")
