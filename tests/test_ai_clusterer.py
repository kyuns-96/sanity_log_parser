"""Tests for select_levels in clustering.ai.weights."""

from sanity_log_parser.clustering.ai.weights import select_levels


def test_select_levels_front():
    assert select_levels("top/cpu/alu/pipe", [0, 1]) == "top cpu"


def test_select_levels_tail():
    assert select_levels("top/cpu/alu/pipe", [-2, -1]) == "alu pipe"


def test_select_levels_mixed():
    assert select_levels("top/cpu/alu/pipe", [0, -1]) == "top pipe"


def test_select_levels_single():
    assert select_levels("top/cpu/alu/pipe", [1]) == "cpu"


def test_select_levels_none_returns_full():
    assert select_levels("top/cpu/alu/pipe", None) == "top cpu alu pipe"


def test_select_levels_no_hierarchy():
    assert select_levels("clk_main", [0]) == "clk_main"


def test_select_levels_out_of_bounds_partial():
    assert select_levels("a/b", [0, 5]) == "a"


def test_select_levels_all_out_of_bounds():
    assert select_levels("a/b", [5, 6]) == ""
