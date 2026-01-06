import re
import json
import os
from collections import defaultdict

# ==============================================================================
# 1. Log Reader Module (파일 읽기 및 필터링)
# ==============================================================================
class SubutaiLogReader:
    def __init__(self, file_path):
        self.file_path = file_path

    def _is_ignorable(self, line_num, line):
        """
        [USER TODO] 무시할 라인(Noise)을 결정하는 필터 로직
        True 리턴 시 해당 라인은 분석에서 제외됩니다.
        """
        stripped = line.strip()

        # 1. 빈 줄 무시
        if not stripped:
            return True
        
        # 2. 구분선 무시
        if stripped.startswith("---") or stripped.startswith("==="):
            return True
        
        # 3. 단순 정보성 메시지 (Info) 무시
        # 예: "Info: SpyGlass Version 1.0..."
        if stripped.startswith("Info:") and "Version" in stripped:
            return True

        # 4. 페이지 번호 무시
        if "Page" in stripped and "of" in stripped:
            return True

        return False

    def stream_valid_lines(self):
        """제너레이터를 사용하여 메모리 효율적으로 유효 라인만 반환"""
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"File not found: {self.file_path}")

        with open(self.file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for i, line in enumerate(f, 1):
                if self._is_ignorable(i, line):
                    continue
                yield line.strip()

# ==============================================================================
# 2. Parser Module (구조 분해)
# ==============================================================================
class SubutaiParser:
    def __init__(self):
        # Rule ID 패턴 (예: LINT-05, TIM-01)
        self.rule_pattern = re.compile(r"^([A-Z]+-\d+)")
        # 변수 추출 패턴 (따옴표 안의 내용)
        self.var_pattern = re.compile(r"['\"](.*?)['\"]")

    def parse_line(self, line):
        # 1. Rule ID 추출
        match = self.rule_pattern.search(line)
        rule_id = match.group(1) if match else "UNKNOWN"
        
        # 2. 변수 튜플 추출 (N-Tuple)
        variables = self.var_pattern.findall(line)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)
        
        # 3. 뼈대(Template) 생성 (변수 -> <VAR>, 숫자 -> <NUM>)
        # 예: Signal 'A' is 1 -> Signal '<VAR>' is <NUM>
        template = self.var_pattern.sub("'<VAR>'", line)
        template = re.sub(r"\d+", "<NUM>", template)
        
        return {
            "rule_id": rule_id,
            "template": template,
            "variables": var_tuple,
            "raw_log": line
        }

# ==============================================================================
# 3. Clusterer Module (핵심 로직 엔진)
# ==============================================================================
class SubutaiClusterer:
    def __init__(self):
        pass

    def _check_token_similarity(self, str1, str2):
        """[Logic Option] 토큰 자카드 유사도 계산 (중간이 다를 때 사용)"""
        tokens1 = set(str1.split('_'))
        tokens2 = set(str2.split('_'))
        
        # 합집합이 0이면(완전 다름) 0 리턴
        if not tokens1 or not tokens2: 
            return False

        intersection = len(tokens1.intersection(tokens2))
        union = len(tokens1.union(tokens2))
        
        score = intersection / union if union > 0 else 0.0
        
        # 유사도가 60% 이상이면 같은 그룹으로 간주
        return score >= 0.6

    def analyze_numeric_distribution(self, var_list):
        """[Phase 2] 숫자 분포 및 문자열 유사도 분석"""
        
        # 1. 숫자를 마스킹하여 임시 그룹핑
        masked_map = defaultdict(list)
        for v in var_list:
            # u_cpu_0 -> u_cpu_*
            # axi_read -> axi_read (변화 없음)
            masked = re.sub(r"\d+", "*", v)
            masked_map[masked].append(v)
            
        final_sub_groups = []

        # 2. 각 마스킹 그룹별 판단
        for pattern, members in masked_map.items():
            
            # Case A: 숫자가 포함된 패턴 (예: u_cpu_*)
            if "*" in pattern:
                if len(members) > 1:
                    # 멤버가 여러 개 -> Bus Error -> Waive Candidate
                    final_sub_groups.append({
                        "pattern": pattern,
                        "count": len(members),
                        "type": "Bus Error (Waive)",
                        "members": members[:3] # 샘플
                    })
                else:
                    # 멤버가 1개 -> Pinpoint Error -> Fix Candidate
                    # 패턴을 '*' 대신 원본(u_cpu_0)으로 복구
                    final_sub_groups.append({
                        "pattern": members[0],
                        "count": 1,
                        "type": "Pinpoint (Fix)",
                        "members": members
                    })
            
            # Case B: 숫자가 없는 문자열 패턴 (예: axi_read)
            else:
                # [Phase 3] 여기서 Semantic Check 수행
                # 만약 리스트에 이미 비슷한 형제(axi_write)가 있다면 합칠 수도 있음
                # (간소화를 위해 여기서는 개별 등록 후, 후처리로 병합 가능성을 열어둠)
                final_sub_groups.append({
                    "pattern": pattern,
                    "count": len(members),
                    "type": "Semantic Check Needed",
                    "members": members[:3]
                })

        return final_sub_groups

    def run(self, parsed_logs):
        """전체 파이프라인 실행"""
        
        # Step 1: Template Grouping (물리적 1차 분류)
        template_groups = defaultdict(list)
        for p in parsed_logs:
            key = (p['rule_id'], p['template'])
            template_groups[key].append(p['variables'])

        final_results = []

        # Step 2: 세부 분석
        for (rule_id, template), var_tuples in template_groups.items():
            
            # N-Tuple 중 '첫 번째 변수'를 기준으로 패턴 분석 (Primary Key)
            # (필요 시 두 번째 변수도 루프 돌며 분석 가능)
            first_vars = [t[0] for t in var_tuples]
            
            analyzed_groups = self.analyze_numeric_distribution(first_vars)
            
            for group in analyzed_groups:
                # 최종 결과 조립
                final_results.append({
                    "rule_id": rule_id,
                    "pattern": group['pattern'],
                    "count": group['count'],
                    "category": group['type'],
                    "template_hash": hash(template), # DB Key용
                    "sample_logs": [
                        f"{rule_id}: ... {group['members'][0]} ..." 
                    ]
                })

        return final_results

# ==============================================================================
# 4. Main Execution (Orchestrator)
# ==============================================================================
if __name__ == "__main__":
    # --- 0. 테스트용 더미 파일 생성 ---
    dummy_file = "test_run.log"
    with open(dummy_file, "w") as f:
        f.write("Info: SpyGlass Version 1.0 Start\n")
        f.write("--------------------------------\n")
        # Case 1: Bus Error (Waive 대상)
        f.write("LINT-01: Signal 'u_cpu_data_0' is floating\n")
        f.write("LINT-01: Signal 'u_cpu_data_1' is floating\n")
        f.write("LINT-01: Signal 'u_cpu_data_2' is floating\n")
        # Case 2: Pinpoint Error (Fix 대상)
        f.write("LINT-01: Signal 'u_ctrl_sig_0' is floating\n")
        # Case 3: Semantic Split (분리 대상)
        f.write("TIM-05: Path 'axi_read_data' setup violation\n")
        f.write("TIM-05: Path 'axi_write_data' setup violation\n")
        # Case 4: N-Tuple (Context)
        f.write("LINT-99: Port 'dft_scan' connects to 'nc_port'\n")
        f.write("LINT-99: Port 'dft_scan' connects to 'sys_clk'\n")
        f.write("--------------------------------\n")
        f.write("Info: End of Report\n")

    print(f"🚀 Analyzing {dummy_file}...\n")

    # --- 1. 파일 읽기 (Filter) ---
    reader = SubutaiLogReader(dummy_file)
    valid_lines = list(reader.stream_valid_lines())
    print(f"📋 Valid Lines: {len(valid_lines)} lines found (Filtered)\n")

    # --- 2. 파싱 (N-Tuple Extraction) ---
    parser = SubutaiParser()
    parsed_data = [parser.parse_line(line) for line in valid_lines]

    # --- 3. 클러스터링 (Logic Engine) ---
    clusterer = SubutaiClusterer()
    results = clusterer.run(parsed_data)

    # --- 4. 결과 출력 ---
    print(json.dumps(results, indent=2))
    
    # (Clean up)
    if os.path.exists(dummy_file):
        os.remove(dummy_file)