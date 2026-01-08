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
        self.delimiters = [('/', 1), ('_', 2), ('-', 3)]  # (delimiter, priority)
    
    def extract_variable_stems(self, variable):
        """
        Extract semantic stems from variable respecting delimiter priority.
        Priority: '/' (highest) > '_' > '-' (lowest)
        
        Strategy: Split by priority delimiters but keep meaningful components.
        - '/' is hierarchy separator: splits into distinct components
        - '_' is compound separator within a component: may keep together or split
        - '-' is sub-component separator: splits into atoms
        
        Example: 'BLK_CPU/A/B/C/mem_top_ABC' -> ['BLK_CPU', 'A', 'B', 'C', 'mem_top', 'ABC']
        Example: 'mem_top_ABC' -> ['mem_top', 'ABC']
        
        Returns list of stem components in hierarchical order.
        """
        if not variable:
            return []
        
        # Step 1: Split by highest priority delimiter ('/')
        parts = variable.split('/')
        stems = []
        
        for part in parts:
            if not part:
                continue
            
            # Step 2: For each part, decide whether to split by '_' or '-'
            # Strategy: If the part is a known hierarchy marker (A, B, C, X, Y, etc.) or very short, keep it
            # Otherwise split by '_' (compound names like mem_top), then by '-'
            
            if len(part) <= 3 or part.isupper():
                # Single letters or uppercase markers like BLK, CPU, SENSOR - keep as one stem
                stems.append(part)
            else:
                # Compound names: split by '_' first, then '-'
                sub_parts = part.split('_')
                for sub_part in sub_parts:
                    if sub_part:
                        # Final split by '-' for components like 'ABC', '123-456'
                        final_parts = sub_part.split('-')
                        stems.extend([p for p in final_parts if p])
        
        return stems

    def parse_line(self, line):
        line = line.strip()
        if not line: return None
        
        if re.search(r'\b\d+\s+of\s+\d+\b', line):
            pass 
        else:
            return None

        line = " ".join(line.split()[4:])

        variables = self.var_pattern.findall(line)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)
        
        # Extract variable stems for hierarchical grouping
        variable_stems = []
        if var_tuple and var_tuple != ("NO_VAR",):
            for var in var_tuple:
                stems = self.extract_variable_stems(var)
                variable_stems.extend(stems)
        
        stems_tuple = tuple(variable_stems) if variable_stems else ("NO_STEM",)
        
        template = self.tm._get_pure_template(line)
        rule_id = self.tm.get_rule_id(template)
        
        return {
            "rule_id": rule_id,
            "variables": var_tuple,
            "variable_stems": stems_tuple,
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
    
    def get_stem_signature(self, stem_tuple):
        """
        Create signature from variable stems, replacing numbers with wildcards.
        Stems are already decomposed, so this focuses on semantic components.
        
        Example: ('mem_top', 'ABC', 'value', '123') -> 'mem_top ABC value *'
        """
        if not stem_tuple or stem_tuple == ("NO_STEM",): 
            return "NO_STEM"
        
        # Replace numeric stems with wildcard, keep semantic stems
        sigs = []
        for stem in stem_tuple:
            if stem.isdigit():
                sigs.append("*")
            else:
                # Keep non-numeric stems as-is (they're already atomic)
                sigs.append(stem)
        
        return " ".join(sigs)

    def run(self, parsed_logs):
        groups = defaultdict(list)
        for p in parsed_logs:
            # [1차 그룹핑] 원래 방식: variables만 사용 (stem 무시)
            full_sig = self.get_logic_signature(p['variables'])
            key = (p['rule_id'], full_sig, p['template'])
            groups[key].append(p)

        results = []
        for (rule_id, full_sig, temp), members in groups.items():
            results.append({
                "type": "LogicGroup",
                "rule_id": rule_id,
                "pattern": full_sig,  # 원본 방식: 변수 기반 패턴
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
        global AI_AVAILABLE
        if AI_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_path)
            except:
                AI_AVAILABLE = False

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return []

        print(f"🤖 Stage 2 - AI Clustering: analyzing {len(logic_groups)} logic groups...")
        # [2차 그룹핑] AI clustering: 1차 로직 그룹들을 의미적으로 재병합
        embedding_inputs = [f"{g['template']} {g['pattern']}" for g in logic_groups]
        embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
        
        # DBSCAN: 의미적으로 유사한 로직 그룹들을 병합
        clustering = DBSCAN(eps=0.3, min_samples=1, metric='cosine').fit(embeddings)
        
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
                "representative_template": main['template'],
                "representative_pattern": main['pattern'],
                "total_count": data["total_count"],
                "merged_variants_count": len(data["logic_subgroups"]),
                "original_logs": all_raw_logs
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

    # 2. Logic Clustering [1차 그룹핑: 원래 방식]
    logic_results = LogicClusterer().run(parsed_logs)
    print(f"\n📊 Stage 1 - Logic Clustering (Original Method - Variables Only):")
    print(f"   Input logs: {len(parsed_logs):,}")
    print(f"   Output groups: {len(logic_results):,}")
    print(f"   Compression ratio: {len(parsed_logs) / len(logic_results):.2f}x")

    # 3. AI Clustering [2차 그룹핑: 의미적 병합]
    results = [] # <--- 여기에 모든 결과를 저장합니다.

    if AI_AVAILABLE:
        # 2차 그룹핑: 1차 로직 그룹들을 AI로 의미적으로 재병합
        results = AIClusterer().run(logic_results)
        print(f"\n🤖 Stage 2 - AI Clustering (Semantic Merging of 1st-Groups):")
        print(f"   Input 1st-groups: {len(logic_results):,}")
        print(f"   Output 2nd-groups: {len(results):,}")
        print(f"   Final compression ratio: {len(parsed_logs) / len(results):.2f}x")
    else:
        # AI가 없으면 Logic 결과만 반환
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
        pattern_display = res.get('representative_pattern', 'N/A')
        merged_info = f" (merged {res.get('merged_variants_count', 1)} groups)" if res.get('merged_variants_count', 1) > 1 else ""
        print(f"{i+1:02d}. [{res['rule_id']}] {pattern_display}{merged_info}")
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