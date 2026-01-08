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
    def __init__(self, model_path='all-MiniLM-L6-v2', config_file='rule_clustering_config.json'):
        global AI_AVAILABLE
        if AI_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_path)
            except:
                AI_AVAILABLE = False
        
        # 설정 파일에서 rule별 eps와 tail_weight 로드
        self.rule_config = self._load_config(config_file)
        
        # 기본 설정 (모든 rule 적용)
        self.default_eps = 0.2
        self.default_tail_weight = 2

    def _load_config(self, config_file):
        """설정 파일에서 rule별 파라미터 로드"""
        if not os.path.exists(config_file):
            print(f"   ⚠️  Config file '{config_file}' not found. Using default settings.")
            return {}
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print(f"   ✅ Loaded rule config from '{config_file}'")
            return config.get('rules', {})
        except Exception as e:
            print(f"   ⚠️  Error loading config: {e}. Using default settings.")
            return {}

    def get_rule_config(self, rule_id):
        """Rule별 설정 조회, 없으면 기본값 반환"""
        if rule_id in self.rule_config:
            config = self.rule_config[rule_id].copy()
            if 'eps' not in config:
                config['eps'] = self.default_eps
            if 'tail_weight' not in config:
                config['tail_weight'] = self.default_tail_weight
            if 'tail_levels' not in config:
                config['tail_levels'] = 1  # 기본: 뒷부분 1개 레벨
            if 'tail_weights' not in config:
                config['tail_weights'] = None  # 기본: 균등 가중치
            if 'variable_position_weights' not in config:
                config['variable_position_weights'] = None  # 기본: 변수 위치 가중치 없음
            if 'variable_value_weights' not in config:
                config['variable_value_weights'] = None  # 기본: 변수 값 기반 가중치 없음
            return config
        return {
            'eps': self.default_eps,
            'tail_weight': self.default_tail_weight,
            'tail_levels': 1,
            'tail_weights': None,
            'variable_position_weights': None,
            'variable_value_weights': None
        }

    def extract_variable_tail(self, full_pattern, tail_levels=1, tail_weights=None, variable_position_weights=None):
        """
        VLSI 변수의 뒷부분 추출 (뒷부분이 더 중요함)
        변수 위치별 가중치도 지원
        
        Args:
            full_pattern: 'BLK_CPU/A/B/C/mem_top_ABC' 형태
            tail_levels: 뒷부분 몇 개 레벨을 추출할지 (기본 1)
            tail_weights: 각 레벨별 가중치 리스트
                          예: [2, 3] → 마지막은 2배, 그 앞은 3배
            variable_position_weights: 변수 위치별 가중치
                          예: [3, 2, 1] → 첫번째 변수는 3배, 둘째는 2배, 셋째는 1배
        
        Returns:
            가중치가 적용된 뒷부분 문자열
        
        Example:
            full_pattern = 'BLK_CPU/A/B/C/mem_top_ABC'
            
            tail_levels=1, tail_weights=[2]
            → 'ABC ABC'
            
            tail_levels=2, tail_weights=[3, 2]
            → 'mem_top mem_top mem_top ABC ABC'
            
            변수 튜플이 ('var1', 'var2', 'var3')이고
            variable_position_weights=[3, 2, 1]이면
            → 'var1 var1 var1 var2 var2 var3'
        """
        if ' / ' not in full_pattern:
            return full_pattern
        
        parts = full_pattern.split(' / ')
        
        # 뒷부분 레벨 추출
        tail_parts = parts[-tail_levels:] if tail_levels <= len(parts) else parts
        
        # 가중치 설정 (기본값: 모두 1)
        if tail_weights is None:
            tail_weights = [1] * len(tail_parts)
        else:
            # tail_weights가 부족하면 마지막 값으로 채우기
            while len(tail_weights) < len(tail_parts):
                tail_weights.append(tail_weights[-1] if tail_weights else 1)
        
        # 각 부분을 가중치만큼 반복
        result = []
        for part, weight in zip(tail_parts, tail_weights):
            result.extend([part] * weight)
        
        # 변수 위치별 가중치가 있으면 추가 적용
        if variable_position_weights:
            result = self._apply_variable_position_weights(result, variable_position_weights)
        
        return ' '.join(result)
    
    def _apply_variable_position_weights(self, parts, variable_position_weights):
        """
        변수 위치별 가중치를 부분 문자열들에 적용
        예: parts=['mem_top', 'ABC'], variable_position_weights=[3, 2]
        → ['mem_top', 'mem_top', 'mem_top', 'ABC', 'ABC']
        """
        if not parts or not variable_position_weights:
            return parts
        
        result = []
        for i, part in enumerate(parts):
            # 위치별 가중치 조회 (부족하면 마지막 값 사용)
            weight_idx = min(i, len(variable_position_weights) - 1)
            weight = variable_position_weights[weight_idx]
            result.extend([part] * weight)
        
        return result
    
    def _apply_variable_value_weights(self, variables, variable_value_weights):
        """
        변수 값 기반 가중치를 적용 (변수 값 자체별로 가중치 설정)
        예: variables=['pipe_4', 'pipe_5', 'pipe_5']
             variable_value_weights={'pipe_4': 3, 'pipe_5': 1}
        → ['pipe_4', 'pipe_4', 'pipe_4', 'pipe_5', 'pipe_5']
        """
        if not variables or not variable_value_weights:
            return variables
        
        result = []
        for var in variables:
            # 변수 값에 해당하는 가중치 조회 (없으면 1)
            weight = variable_value_weights.get(var, 1)
            result.extend([var] * weight)
        
        return result

    def run(self, logic_groups):
        if not AI_AVAILABLE or not logic_groups: return []

        print(f"🤖 Stage 2 - AI Clustering: analyzing {len(logic_groups)} logic groups...")
        
        # rule_id별로 그룹 분류
        groups_by_rule = defaultdict(list)
        for g in logic_groups:
            groups_by_rule[g['rule_id']].append(g)
        
        print(f"   Grouping by rule_id: {len(groups_by_rule)} different rules")
        
        final_output = []
        ai_group_counter = 0
        
        # Rule별로 따로 AI Clustering 수행
        for rule_id, rule_groups in groups_by_rule.items():
            config = self.get_rule_config(rule_id)
            eps = config['eps']
            tail_weight = config['tail_weight']
            tail_levels = config.get('tail_levels', 1)
            tail_weights = config.get('tail_weights', None)
            variable_position_weights = config.get('variable_position_weights', None)
            variable_value_weights = config.get('variable_value_weights', None)
            
            if len(rule_groups) < 2:
                # 그룹이 1개면 병합할 것이 없음
                for g in rule_groups:
                    ai_group_counter += 1
                    all_raw_logs = [m['raw_log'] for m in g['members']]
                    final_output.append({
                        "type": "AISuperGroup",
                        "super_group_id": f"{rule_id}_SG_{ai_group_counter}",
                        "rule_id": rule_id,
                        "representative_template": g['template'],
                        "representative_pattern": g['pattern'],
                        "total_count": g['count'],
                        "merged_variants_count": 1,
                        "original_logs": all_raw_logs
                    })
                continue
            
            # 동일 rule_id 내에서만 embedding 및 clustering
            # VLSI 변수의 뒷부분(실제 중요 정보)에 가중치를 두기 위해
            embedding_inputs = []
            for g in rule_groups:
                # 뒷부분 레벨별 가중치 + 변수 위치별 가중치 + 변수 값 기반 가중치 적용
                if tail_weights:
                    # 설정 파일에서 지정한 레벨별 가중치 사용
                    tail_text = self.extract_variable_tail(g['pattern'], tail_levels, tail_weights, variable_position_weights)
                else:
                    # 단순 반복 (tail_weight 사용)
                    tail = self.extract_variable_tail(g['pattern'], tail_levels, None, variable_position_weights)
                    tail_text = ' '.join([tail] * tail_weight)
                
                # 변수 값 기반 가중치 적용 (tail_text를 다시 가중치 처리)
                if variable_value_weights:
                    # tail_text는 이미 처리된 변수들이므로, pattern에서 추출한 변수로 처리
                    # pattern에서 변수 추출
                    var_pattern = re.compile(r"'(.*?)'")
                    # g['pattern']에는 " / " 형태로 경로가 있음
                    pattern_text = g['pattern'].replace(' / ', ' ')
                    variables = var_pattern.findall(pattern_text)
                    
                    # 변수 값 기반 가중치 적용
                    weighted_vars = self._apply_variable_value_weights(variables, variable_value_weights)
                    var_text = ' '.join(weighted_vars)
                    embedding_input = f"{g['template']} {var_text}"
                else:
                    embedding_input = f"{g['template']} {tail_text}"
                
                embedding_inputs.append(embedding_input)
            
            embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
            
            # Rule별 설정된 eps로 clustering
            clustering = DBSCAN(eps=eps, min_samples=1, metric='cosine').fit(embeddings)
            
            ai_grouped = defaultdict(lambda: {"total_count": 0, "logic_subgroups": []})
            for label, logic_group in zip(clustering.labels_, rule_groups):
                cluster_key = f"{rule_id}_SG_{label}"
                ai_grouped[cluster_key]["total_count"] += logic_group['count']
                ai_grouped[cluster_key]["logic_subgroups"].append(logic_group)

            # 결과 생성
            for key, data in ai_grouped.items():
                ai_group_counter += 1
                main = max(data["logic_subgroups"], key=lambda x: x['count'])
                
                all_raw_logs = []
                for sub in data["logic_subgroups"]:
                    for member in sub["members"]:
                        all_raw_logs.append(member["raw_log"])

                final_output.append({
                    "type": "AISuperGroup",
                    "super_group_id": key,
                    "rule_id": rule_id,
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