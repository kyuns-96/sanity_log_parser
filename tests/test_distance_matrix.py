"""Tests for the multi-embedding weighted distance matrix and pattern merging."""

from __future__ import annotations

import numpy as np

from sanity_log_parser.clustering.ai.clusterer import (
    _compute_distance_matrix,
    _merge_patterns,
    _prepare_embedding_components,
)
from sanity_log_parser.gca.config import GcaRuleConfig, VariableConfig


def _mock_embeddings(n: int, dim: int = 4) -> np.ndarray:
    """Generate deterministic mock embeddings."""
    rng = np.random.default_rng(42)
    embs = rng.standard_normal((n, dim))
    # Normalize for cosine distance stability
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    return embs / norms


def test_distance_matrix_symmetric() -> None:
    n = 3
    template_embs = _mock_embeddings(n)
    rule_config = GcaRuleConfig(eps=0.2, template_weight=1.0)
    d = _compute_distance_matrix(n, template_embs, [], rule_config, 0.7)
    np.testing.assert_array_almost_equal(d, d.T)


def test_distance_matrix_diagonal_zero() -> None:
    n = 3
    template_embs = _mock_embeddings(n)
    rule_config = GcaRuleConfig(eps=0.2, template_weight=1.0)
    d = _compute_distance_matrix(n, template_embs, [], rule_config, 0.7)
    for i in range(n):
        assert d[i][i] == 0.0


def test_distance_matrix_uniform_weights_on_zero_total() -> None:
    """When all weights are 0, uniform 1/n_active is used."""
    n = 2
    template_embs = _mock_embeddings(n)
    rule_config = GcaRuleConfig(eps=0.2, template_weight=0.0)
    d = _compute_distance_matrix(n, template_embs, [], rule_config, 0.0)
    # Should not raise and should produce a valid distance
    assert d[0][1] >= 0
    assert d[0][1] == d[1][0]


def test_distance_matrix_per_pair_renorm_asymmetric_vars() -> None:
    """Different pairs have different active variable sets."""
    n = 3
    template_embs = _mock_embeddings(n)

    # One variable position: group 0 and 1 active, group 2 inactive
    var_embs = _mock_embeddings(n)
    mask = [True, True, False]
    var_embeddings = [(var_embs, mask)]

    rule_config = GcaRuleConfig(
        eps=0.2,
        template_weight=0.3,
        variables={0: VariableConfig(weight=0.7)},
    )
    d = _compute_distance_matrix(n, template_embs, var_embeddings, rule_config, 0.7)

    # Pair (0,1) uses both template and variable
    # Pair (0,2) and (1,2) use only template
    assert d[0][1] != d[0][2]  # Different normalization
    np.testing.assert_array_almost_equal(d, d.T)


def test_distance_matrix_empty_select_levels_marks_inactive() -> None:
    """Variables that produce empty text are masked inactive."""
    rule_config = GcaRuleConfig(
        eps=0.2,
        template_weight=0.3,
        variables={0: VariableConfig(weight=0.7, levels=[99])},
    )
    groups = [
        {"template": "T1", "pattern": "'a/b' rest", "count": 1, "members": []},
        {"template": "T2", "pattern": "'c/d' rest", "count": 1, "members": []},
    ]
    components = _prepare_embedding_components(groups, rule_config, 0.7)
    # levels=[99] on "a/b" → empty string → should be masked
    assert components[0]["variables"][0] == ""
    assert components[1]["variables"][0] == ""


def test_distance_matrix_template_only_no_vars() -> None:
    """Distance matrix with no variables is pure template distance."""
    n = 2
    template_embs = _mock_embeddings(n)
    rule_config = GcaRuleConfig(eps=0.2, template_weight=1.0)
    d = _compute_distance_matrix(n, template_embs, [], rule_config, 0.7)
    # With only template, the distance should equal the cosine distance
    from scipy.spatial.distance import cosine as cosine_distance

    expected = cosine_distance(template_embs[0], template_embs[1])
    np.testing.assert_almost_equal(d[0][1], expected)


# --- _merge_patterns tests ---


def test_merge_patterns_single() -> None:
    """Single pattern returned as-is."""
    assert _merge_patterns(["'clk1' / 'sig_a'"]) == "'clk1' / 'sig_a'"


def test_merge_patterns_identical() -> None:
    """Identical patterns merge to the same pattern."""
    assert _merge_patterns(["'clk1' / 'sig_a'", "'clk1' / 'sig_a'"]) == "'clk1' / 'sig_a'"


def test_merge_patterns_differing_positions() -> None:
    """Differing positions become '*'."""
    result = _merge_patterns(["'clk1' / 'sig_a'", "'clk2' / 'sig_a'"])
    assert result == "* / 'sig_a'"


def test_merge_patterns_all_differ() -> None:
    """All positions differ → all become '*'."""
    result = _merge_patterns(["'clk1' / 'sig_a'", "'clk2' / 'sig_b'"])
    assert result == "* / *"


def test_merge_patterns_different_lengths() -> None:
    """Shorter patterns get '*' for missing positions."""
    result = _merge_patterns(["'a' / 'b' / 'c'", "'a' / 'b'"])
    assert result == "'a' / 'b' / *"


def test_merge_patterns_three_subgroups() -> None:
    """Three subgroups: position matches only if all three agree."""
    result = _merge_patterns(["'x' / 'y'", "'x' / 'z'", "'x' / 'y'"])
    assert result == "'x' / *"
