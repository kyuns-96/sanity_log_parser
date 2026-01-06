import re
import json
import os
import difflib
from collections import defaultdict

# ==============================================================================
# 1. Log Reader (노이즈 필터링)
# ==============================================================================
class SubutaiLogReader:
    def __init__(self, file_path):
        self.file_path = file_path

    def _is_ignorable(self, line_num, line):
        """
        [User Custom Logic] 분석할 가치가 없는 라인을 True로 리턴
        """
        stripped = line.strip()
        if not stripped: return True
        if stripped.startswith("---") or stripped.startswith("==="): return True
        if stripped.startswith("Info:") or "Page" in stripped: return True
        return False

    def stream_valid_lines(self):
        if not os.path.exists(self.file_path):
            return []
        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f, 1):
                if not self._is_ignorable(i, line):
                    yield line.strip()

# ==============================================================================
# 2. Parser (N-Tuple 추출)
# ==============================================================================
class SubutaiParser:
    def __init__(self):
        # 예: LINT-01, TIM-05
        self.rule_pattern = re.compile(r"^([A-Z]+-\d+)")
        # 따옴표 안의 변수 추출
        self.var_pattern = re.compile(r"['\"](.*?)['\"]")

    def parse_line(self, line):
        match = self.rule_pattern.search(line)
        rule_id = match.group(1) if match else "UNKNOWN"
        
        variables = self.var_pattern.findall(line)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)
        
        # 뼈대만 남기기 (숫자와 변수 내용 제거)
        template = self.var_pattern.sub("'<VAR>'", line)
        template = re.sub(r"\d+", "<NUM>", template)
        
        return {
            "rule_id": rule_id,
            "template": template,
            "variables": var_tuple,
            "raw_log": line
        }

# ==============================================================================
# 3. Aggressive Clusterer (경로 일반화 핵심 엔진)
# ==============================================================================
class AggressiveClusterer:
    def __init__(self):
        pass

    def generalize_pattern(self, str1, str2):
        """
        두 문자열(변수명/경로)을 비교하여 '다른 부분'만 '*'로 치환한 패턴 반환
        Ex) 'u_cpu_core' + 'u_gpu_core' -> 'u_*_core'
        """
        # 1. 길이 차이가 너무 크면 구조가 다른 것임 -> 병합 안 함
        if abs(len(str1) - len(str2)) > 10: 
            return None

        # 2. 구분자(Delimiter) 기준으로 토큰화
        # 경로(/), 언더바(_), 점(.) 등을 기준으로 쪼갬
        seps = r"([/_.\-])"
        parts1 = re.split(seps, str1)
        parts2 = re.split(seps, str2)

        # 구조적 길이(토큰 수)가 다르면 병합 불가
        if len(parts1) != len(parts2):
            return None

        new_parts = []
        diff_count = 0
        
        for p1, p2 in zip(parts1, parts2):
            if p1 == p2:
                new_parts.append(p1)
            elif '*' in p1: # 이미 와일드카드가 있는 경우 유지
                new_parts.append(p1)
            else:
                # 다르다면 '*'로 치환
                diff_count += 1
                new_parts.append("*")
        
        # 3. 안전장치: 전체 토큰 중 40% 이상이 다르면 "너무 다르다"고 판단하여 병합 거부
        # (너무 뭉뚱그려지는 것 방지)
        total_tokens = len(parts1)
        if diff_count > max(1, total_tokens * 0.4):
            return None
            
        return "".join(new_parts)

    def run(self, parsed_logs):
        # Step 1: Template Grouping (물리적 1차 분류)
        template_groups = defaultdict(list)
        for p in parsed_logs:
            # Rule ID와 템플릿이 같은 것끼리 모음
            key = (p['rule_id'], p['template'])
            # 여기서는 편의상 첫 번째 변수(variables[0])를 기준으로 클러스터링
            if p['variables'] and p['variables'][0] != "NO_VAR":
                template_groups[key].append(p['variables'][0])

        final_results = []

        # Step 2: Iterative Aggressive Merge
        for (rule_id, template), var_list in template_groups.items():
            
            # [핵심] 정렬을 해야 비슷한 것끼리 붙어서 병합 확률이 높아짐
            var_list.sort()
            
            merged_groups = []
            if not var_list: continue

            # 첫 번째 요소를 시작 패턴으로 잡음
            current_pattern = var_list[0]
            current_count = 1
            sample_members = [var_list[0]]

            for i in range(1, len(var_list)):
                next_var = var_list[i]
                
                # 현재 패턴과 다음 변수를 일반화 시도
                generalized = self.generalize_pattern(current_pattern, next_var)
                
                if generalized:
                    # 병합 성공! 패턴 업데이트 (구체적 -> 일반적)
                    current_pattern = generalized
                    current_count += 1
                    if len(sample_members) < 3: sample_members.append(next_var)
                else:
                    # 병합 실패! 지금까지 뭉친 그룹 저장하고 새로 시작
                    merged_groups.append({
                        "pattern": current_pattern,
                        "count": current_count,
                        "samples": sample_members
                    })
                    current_pattern = next_var
                    current_count = 1
                    sample_members = [next_var]
            
            # 루프 끝나고 남은 마지막 그룹 저장
            merged_groups.append({
                "pattern": current_pattern,
                "count": current_count,
                "samples": sample_members
            })

            # 결과 포맷팅
            for mg in merged_groups:
                # 카테고리 태깅
                if "*" in mg['pattern']:
                    cat = "Grouped Pattern (Waive Check)"
                else:
                    cat = "Single Issue (Fix Check)"

                final_results.append({
                    "rule_id": rule_id,
                    "final_pattern": mg['pattern'],
                    "count": mg['count'],
                    "category": cat,
                    "template": template,
                    "example_vars": mg['samples']
                })

        return final_results

# ==============================================================================
# 4. Main Execution (Test)
# ==============================================================================
if __name__ == "__main__":
    # --- 테스트용 더미 파일 생성 (복잡한 경로 포함) ---
    dummy_file = "aggressive_test.log"

    print("🚀 Running Aggressive Clustering...\n")

    # 1. Read
    reader = SubutaiLogReader(dummy_file)
    lines = list(reader.stream_valid_lines())
    
    # 2. Parse
    parser = SubutaiParser()
    parsed_data = [parser.parse_line(line) for line in lines]
    
    # 3. Cluster (Aggressive)
    clusterer = AggressiveClusterer()
    results = clusterer.run(parsed_data)
    
    # 4. Result
    print(json.dumps(results, indent=2))

