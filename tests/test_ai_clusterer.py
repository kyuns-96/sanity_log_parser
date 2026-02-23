from ai_weights import apply_variable_position_weights, extract_variable_tail


def test_extract_variable_tail_doc_example_single_level():
    full_pattern = "BLK_CPU / A / B / C / mem_top / ABC"
    result = extract_variable_tail(
        full_pattern,
        tail_levels=1,
        tail_weights=[2],
    )
    assert result == "ABC ABC"


def test_extract_variable_tail_doc_example_two_levels():
    full_pattern = "BLK_CPU / A / B / C / mem_top / ABC"
    result = extract_variable_tail(
        full_pattern,
        tail_levels=2,
        tail_weights=[3, 2],
    )
    assert result == "mem_top mem_top mem_top ABC ABC"


def test_extract_variable_tail_with_variable_position_weights():
    full_pattern = "BLK / CORE / ALU"
    result = extract_variable_tail(
        full_pattern,
        tail_levels=2,
        tail_weights=[1, 1],
        variable_position_weights=[3, 2],
    )
    assert result == "CORE CORE CORE ALU ALU"


def test_apply_variable_position_weights_repeats_parts_by_position():
    parts = ["mem_top", "ABC"]
    result = apply_variable_position_weights(parts, [3, 2])
    assert result == ["mem_top", "mem_top", "mem_top", "ABC", "ABC"]
