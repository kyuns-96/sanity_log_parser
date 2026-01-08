#!/usr/bin/env python3
"""
실제 로그 예제 parsing 테스트
"""
import re
import sys
sys.path.insert(0, '/home/lee/ai_project/sanity_log_parser')

from log_parser import RuleTemplateManager, SubutaiParser

def analyze_log():
    """로그 파싱 프로세스 상세 분석"""
    
    log_line = "LINT-01: Signal 'top/u_cpu/decode/pipe_4' float Signal 'top/u_cpu/decode/pipe_5' float 'top/u_cpu/decode/pipe_5' signal conflicted"
    
    print("=" * 100)
    print("📋 로그 파싱 프로세스 분석")
    print("=" * 100)
    
    print(f"\n[INPUT] 원본 로그:")
    print(f"  {log_line}")
    
    # 1단계: 기본 필터링 (N of M 패턴 체크)
    print(f"\n[STEP 1] 'N of M' 패턴 필터링")
    has_pattern = re.search(r'\b\d+\s+of\s+\d+\b', log_line)
    print(f"  패턴 검사: 'N of M' 형태 찾기")
    print(f"  결과: {'✅ 발견됨' if has_pattern else '❌ 없음 (파싱 불가)'}")
    
    if not has_pattern:
        print(f"\n  ⚠️  이 로그는 'N of M' 패턴이 없어서 파싱되지 않습니다.")
        print(f"  예: '1 of 5' 또는 '3 of 10' 같은 형태가 필요합니다.")
    
    # 2단계: 변수 추출 (N of M이 있다면)
    print(f"\n[STEP 2] 변수 추출 (N of M 이후의 text)")
    var_pattern = re.compile(r"'(.*?)'")
    variables = var_pattern.findall(log_line)
    print(f"  정규식: r\"'(.*?)'\" - 작은따옴표 안의 내용")
    print(f"  추출 결과: {len(variables)}개 발견")
    for i, var in enumerate(variables, 1):
        print(f"    {i}. '{var}'")
    
    # 3단계: Rule ID 추출
    print(f"\n[STEP 3] Rule ID 추출")
    rule_match = re.match(r'^([A-Z\-0-9]+):', log_line)
    if rule_match:
        rule_id = rule_match.group(1)
        print(f"  Rule ID: {rule_id}")
    
    # 4단계: Template 생성 (변수 마스킹)
    print(f"\n[STEP 4] Template 생성 (변수 → '<VAR>', 숫자 → '<NUM>')")
    tm = RuleTemplateManager(None)
    template = tm._get_pure_template(log_line)
    print(f"  원본: {log_line}")
    print(f"  Template: {template}")
    
    # 5단계: 실제 parser 실행
    print(f"\n[STEP 5] 실제 Parser 실행")
    parser = SubutaiParser(tm)
    result = parser.parse_line(log_line)
    
    if result is None:
        print(f"  ❌ 파싱 실패 (None 반환)")
        print(f"  \n사유: 'N of M' 패턴이 필수인데 이 로그에는 없습니다.")
    else:
        print(f"  ✅ 파싱 성공")
        print(f"  \n결과:")
        for key, value in result.items():
            print(f"    {key}: {value}")
    
    # 개선 제안
    print(f"\n" + "=" * 100)
    print("💡 개선 방안")
    print("=" * 100)
    
    print(f"\n[방안 1] 'N of M' 패턴 필터링 제거 (선택적)")
    print(f"  - 이 로그를 파싱하려면 parser.parse_line()의 필터링 조건 수정 필요")
    print(f"  - 로직: \"N of M\" 패턴이 없으면 전체 문장을 사용")
    
    print(f"\n[방안 2] Rule별 필터링 규칙 추가")
    print(f"  - rule_clustering_config.json에 'parse_rules' 추가")
    print(f"  - LINT-01: 'N of M' 불필요, 변수만 추출하면 됨")
    
    print(f"\n[방안 3] 현재 설정 유지 (권장)")
    print(f"  - 'N of M' 패턴이 있는 로그만 처리하도록 filter 유지")
    print(f"  - 필요시 template file에 'N of M' 추가하기")
    
    print(f"\n" + "=" * 100)
    print("📊 변수 처리 방식 상세")
    print("=" * 100)
    
    if variables:
        print(f"\n발견된 변수들: {len(variables)}개")
        print(f"변수 tuple: {tuple(variables)}")
        
        print(f"\n각 변수 분석:")
        for i, var in enumerate(variables, 1):
            print(f"  {i}. '{var}'")
            if '/' in var:
                parts = var.split('/')
                print(f"     → 경로: {len(parts)} 레벨")
                print(f"     → 뒷부분 (tail): '{parts[-1]}'")
            
            # variable_position_weights 설정 시 어떻게 되는지
            if i == 1:
                print(f"     → variable_position_weights=[3,1]이면 3배 강조")
            elif i == 2:
                print(f"     → variable_position_weights=[3,1]이면 1배 (덜 중요)")

if __name__ == "__main__":
    analyze_log()
