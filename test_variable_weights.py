#!/usr/bin/env python3
"""
변수 위치별 가중치 기능 테스트
"""
import json
import sys
sys.path.insert(0, '/home/lee/ai_project/sanity_log_parser')

from log_parser import AIClusterer

def test_variable_position_weights():
    """변수 위치별 가중치 추출 테스트"""
    
    ai_clusterer = AIClusterer()
    
    print("=" * 80)
    print("🧪 변수 위치별 가중치 (variable_position_weights) 테스트")
    print("=" * 80)
    
    # 테스트 케이스 1: 뒷부분 추출 (variable_position_weights 없음) - 마지막 1레벨
    print("\n[TEST 1] 기본 뒷부분 추출 (가중치 없음) - tail_levels=1")
    pattern1 = "BLK_CPU / A / B / C / mem_top_ABC"
    result1 = ai_clusterer.extract_variable_tail(pattern1, tail_levels=1, tail_weights=[2])
    print(f"패턴: {pattern1}")
    print(f"설정: tail_levels=1, tail_weights=[2], variable_position_weights=None")
    print(f"결과: {result1}")
    expected1 = "mem_top_ABC mem_top_ABC"
    print(f"기대값: {expected1}")
    print(f"✅ PASS" if result1 == expected1 else f"❌ FAIL")
    
    # 테스트 케이스 2: 뒷부분 2레벨 추출
    print("\n[TEST 2] 뒷부분 2레벨 추출 - tail_levels=2")
    pattern2 = "BLK_CPU / A / B / C / mem_top_ABC"
    result2 = ai_clusterer.extract_variable_tail(pattern2, tail_levels=2, tail_weights=[2, 1])
    print(f"패턴: {pattern2}")
    print(f"설정: tail_levels=2, tail_weights=[2, 1], variable_position_weights=None")
    print(f"결과: {result2}")
    expected2 = "C C mem_top_ABC"
    print(f"기대값: {expected2}")
    print(f"✅ PASS" if result2 == expected2 else f"❌ FAIL")
    
    # 테스트 케이스 3: 변수 위치별 가중치 적용 (첫 부분 중요)
    print("\n[TEST 3] 변수 위치별 가중치: [3, 2] - tail_levels=2")
    result3 = ai_clusterer.extract_variable_tail(
        pattern2, 
        tail_levels=2, 
        tail_weights=[1, 1],
        variable_position_weights=[3, 2]
    )
    print(f"패턴: {pattern2}")
    print(f"설정: tail_levels=2, tail_weights=[1, 1], variable_position_weights=[3, 2]")
    print(f"결과: {result3}")
    # C (첫 부분) → 3배 = C C C
    # mem_top_ABC (둘째 부분) → 2배 = mem_top_ABC mem_top_ABC
    expected3 = "C C C mem_top_ABC mem_top_ABC"
    print(f"기대값: {expected3}")
    print(f"✅ PASS" if result3 == expected3 else f"❌ FAIL")
    
    # 테스트 케이스 4: 첫 부분만 강조
    print("\n[TEST 4] 변수 위치별 가중치: [4, 1] - tail_levels=2")
    result4 = ai_clusterer.extract_variable_tail(
        pattern2, 
        tail_levels=2, 
        tail_weights=[1, 1],
        variable_position_weights=[4, 1]
    )
    print(f"패턴: {pattern2}")
    print(f"설정: tail_levels=2, tail_weights=[1, 1], variable_position_weights=[4, 1]")
    print(f"결과: {result4}")
    # C → 4배 = C C C C
    # mem_top_ABC → 1배 = mem_top_ABC
    expected4 = "C C C C mem_top_ABC"
    print(f"기대값: {expected4}")
    print(f"✅ PASS" if result4 == expected4 else f"❌ FAIL")
    
    # 테스트 케이스 5: 설정 파일 로드
    print("\n[TEST 5] 설정 파일에서 variable_position_weights 로드")
    config = ai_clusterer._load_config('rule_clustering_config.json')
    rule_003_config = ai_clusterer.get_rule_config('RULE_003')
    print(f"RULE_003 설정:")
    for key, value in rule_003_config.items():
        print(f"  {key}: {value}")
    print(f"variable_position_weights = {rule_003_config.get('variable_position_weights')}")
    print(f"✅ PASS" if rule_003_config.get('variable_position_weights') == [3, 1] else f"❌ FAIL")
    
    # 테스트 케이스 6: 복합 예시 (tail_weights + variable_position_weights)
    print("\n[TEST 6] 복합 사용: tail_weights + variable_position_weights")
    result6 = ai_clusterer.extract_variable_tail(
        pattern2,
        tail_levels=2,
        tail_weights=[2, 3],  # C 2배, mem_top_ABC 3배
        variable_position_weights=[3, 1]  # 첫 부분 3배, 둘째 1배
    )
    print(f"패턴: {pattern2}")
    print(f"설정: tail_levels=2, tail_weights=[2, 3], variable_position_weights=[3, 1]")
    print(f"결과: {result6}")
    # Step 1 - tail_weights 적용: 
    #   C C (2배), mem_top_ABC mem_top_ABC mem_top_ABC (3배)
    # Step 2 - variable_position_weights 적용 (단어 단위로):
    #   parts = ['C', 'C', 'mem_top_ABC', 'mem_top_ABC', 'mem_top_ABC']
    #   index 0: 'C' → weight 3 → C C C
    #   index 1: 'C' → weight 1 (index >= len([3,1])-1) → C
    #   index 2: 'mem_top_ABC' → weight 1 → mem_top_ABC
    #   index 3: 'mem_top_ABC' → weight 1 → mem_top_ABC
    #   index 4: 'mem_top_ABC' → weight 1 → mem_top_ABC
    expected6 = "C C C C mem_top_ABC mem_top_ABC mem_top_ABC"
    print(f"기대값: {expected6}")
    print(f"✅ PASS" if result6 == expected6 else f"❌ FAIL")
    
    print("\n" + "=" * 80)
    print("🎯 모든 테스트 완료!")
    print("=" * 80)

if __name__ == "__main__":
    test_variable_position_weights()

