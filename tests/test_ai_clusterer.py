def test_extract_variable_tail_doc_example_single_level(ai_clusterer_no_init):
    full_pattern = "BLK_CPU / A / B / C / mem_top / ABC"
    result = ai_clusterer_no_init.extract_variable_tail(
        full_pattern,
        tail_levels=1,
        tail_weights=[2],
    )
    assert result == "ABC ABC"


def test_extract_variable_tail_doc_example_two_levels(ai_clusterer_no_init):
    full_pattern = "BLK_CPU / A / B / C / mem_top / ABC"
    result = ai_clusterer_no_init.extract_variable_tail(
        full_pattern,
        tail_levels=2,
        tail_weights=[3, 2],
    )
    assert result == "mem_top mem_top mem_top ABC ABC"


def test_extract_variable_tail_with_variable_position_weights(ai_clusterer_no_init):
    full_pattern = "BLK / CORE / ALU"
    result = ai_clusterer_no_init.extract_variable_tail(
        full_pattern,
        tail_levels=2,
        tail_weights=[1, 1],
        variable_position_weights=[3, 2],
    )
    assert result == "CORE CORE CORE ALU ALU"


def test_apply_variable_position_weights_repeats_parts_by_position(ai_clusterer_no_init):
    parts = ["mem_top", "ABC"]
    result = ai_clusterer_no_init._apply_variable_position_weights(parts, [3, 2])
    assert result == ["mem_top", "mem_top", "mem_top", "ABC", "ABC"]
