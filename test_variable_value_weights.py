#!/usr/bin/env python3
"""
변수 값 기반 가중치 (Variable Value Weights) 기능 테스트
"""
import json
import sys
sys.path.insert(0, '/home/lee/ai_project/sanity_log_parser')

from log_parser import AIClusterer

def test_variable_value_weights():
    """변수 값 기반 가중치 추출 테스트"""
    
    ai_clusterer = AIClusterer()
    
    print("=" * 100)
    print("🧪 변수 값 기반 가중치 (variable_value_weights) 테스트")
    print("=" * 100)
    
    # 테스트 케이스 1: 기본 variable_value_weights 적용
    print("\n[TEST 1] 기본 variable_value_weights 적용")
    variables = ['top/u_cpu/decode/pipe_4', 'top/u_cpu/decode/pipe_5', 'top/u_cpu/decode/pipe_5']
    variable_value_weights = {
        'top/u_cpu/decode/pipe_4': 3,
        'top/u_cpu/decode/pipe_5': 1
    }
    
    result1 = ai_clusterer._apply_variable_value_weights(variables, variable_value_weights)
    print(f"  입력 변수: {variables}")
    print(f"  가중치 설정: {variable_value_weights}")
    print(f"  결과: {result1}")
    
    expected1 = ['top/u_cpu/decode/pipe_4', 'top/u_cpu/decode/pipe_4', 'top/u_cpu/decode/pipe_4',
                 'top/u_cpu/decode/pipe_5', 'top/u_cpu/decode/pipe_5']
    print(f"  기대값: {expected1}")
    print(f"  ✅ PASS" if result1 == expected1 else f"  ❌ FAIL")
    
    # 테스트 케이스 2: 일부 변수만 설정
    print("\n[TEST 2] 일부 변수만 설정 (없는 변수는 기본값 1)")
    variable_value_weights2 = {
        'top/u_cpu/decode/pipe_4': 5
    }
    result2 = ai_clusterer._apply_variable_value_weights(variables, variable_value_weights2)
    print(f"  입력 변수: {variables}")
    print(f"  가중치 설정: {variable_value_weights2}")
    print(f"  결과: {result2}")
    
    expected2 = ['top/u_cpu/decode/pipe_4'] * 5 + ['top/u_cpu/decode/pipe_5'] + ['top/u_cpu/decode/pipe_5']
    print(f"  기대값: {expected2}")
    print(f"  ✅ PASS" if result2 == expected2 else f"  ❌ FAIL")
    
    # 테스트 케이스 3: 빈 가중치 (None)
    print("\n[TEST 3] 가중치 None (아무것도 안 함)")
    result3 = ai_clusterer._apply_variable_value_weights(variables, None)
    print(f"  입력 변수: {variables}")
    print(f"  가중치 설정: None")
    print(f"  결과: {result3}")
    print(f"  기대값: {variables}")
    print(f"  ✅ PASS" if result3 == variables else f"  ❌ FAIL")
    
    # 테스트 케이스 4: 설정 파일 로드
    print("\n[TEST 4] 설정 파일에서 variable_value_weights 로드")
    ai_clusterer._load_config('rule_clustering_config.json')
    rule_003_config = ai_clusterer.get_rule_config('RULE_003')
    print(f"RULE_003 설정:")
    print(f"  variable_value_weights: {rule_003_config.get('variable_value_weights')}")
    expected_weights = {'top/u_cpu/decode/pipe_4': 3, 'top/u_cpu/decode/pipe_5': 1}
    print(f"  기대값: {expected_weights}")
    print(f"  ✅ PASS" if rule_003_config.get('variable_value_weights') == expected_weights else f"  ❌ FAIL")
    
    # 테스트 케이스 5: Embedding 입력 생성
    print("\n[TEST 5] Embedding 입력 생성")
    template = "'<VAR>' float Signal '<VAR>' float '<VAR>' signal conflicted"
    weighted_vars = ai_clusterer._apply_variable_value_weights(variables, variable_value_weights)
    var_text = ' '.join(weighted_vars)
    embedding_input = f"{template} {var_text}"
    
    print(f"  변수: {variables}")
    print(f"  가중치: {variable_value_weights}")
    print(f"  Template: {template}")
    print(f"  최종 Embedding 입력:")
    print(f"    {embedding_input}")
    
    # 검증: pipe_4가 3회, pipe_5가 2회
    pipe4_count = var_text.count('pipe_4')
    pipe5_count = var_text.count('pipe_5')
    print(f"\n  pipe_4 등장: {pipe4_count}회 (기대: 3회)")
    print(f"  pipe_5 등장: {pipe5_count}회 (기대: 2회)")
    print(f"  ✅ PASS" if pipe4_count == 3 and pipe5_count == 2 else f"  ❌ FAIL")
    
    # 테스트 케이스 6: 다른 변수들
    print("\n[TEST 6] 다른 형태의 변수")
    variables6 = ['addr_0x1000', 'addr_0x2000', 'addr_0x1000']
    weights6 = {
        'addr_0x1000': 2,
        'addr_0x2000': 3
    }
    result6 = ai_clusterer._apply_variable_value_weights(variables6, weights6)
    print(f"  입력: {variables6}")
    print(f"  가중치: {weights6}")
    print(f"  결과: {result6}")
    
    expected6 = ['addr_0x1000', 'addr_0x1000', 'addr_0x2000', 'addr_0x2000', 'addr_0x2000', 'addr_0x1000', 'addr_0x1000']
    print(f"  기대값: {expected6}")
    print(f"  ✅ PASS" if result6 == expected6 else f"  ❌ FAIL")
    
    print("\n" + "=" * 100)
    print("🎯 모든 테스트 완료!")
    print("=" * 100)

if __name__ == "__main__":
    test_variable_value_weights()
