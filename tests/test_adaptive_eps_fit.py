from __future__ import annotations

import numpy as np

from sanity_log_parser.gca.adaptive_eps_fit import fit_adaptive_eps_tree


def test_fit_adaptive_eps_tree_finds_perfect_small_tree() -> None:
    rule_groups = [
        {"pattern": "'TOP/UNIT_A/FOO'"},
        {"pattern": "'SUB/UNIT_A/FOO'"},
        {"pattern": "'TOP/UNIT_B/BAR'"},
        {"pattern": "'SUB/UNIT_B/BAR'"},
    ]
    base_distances = np.array(
        [
            [0.0, 0.18, 0.72, 0.68],
            [0.18, 0.0, 0.70, 0.66],
            [0.72, 0.70, 0.0, 0.16],
            [0.68, 0.66, 0.16, 0.0],
        ],
        dtype=np.float32,
    )
    cluster_labels = ["A", "A", "B", "B"]
    feature_defs = [
        {"kind": "level_exact", "levels": [-1]},
    ]

    result = fit_adaptive_eps_tree(
        rule_groups,
        base_distances,
        cluster_labels,
        feature_defs,
        max_depth_candidates=(1, 2),
        min_samples_leaf_candidates=(1, 2),
    )

    assert result.f1 == 1.0
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.node_count <= 3
    assert result.tree["features"] == tuple(feature_defs)
