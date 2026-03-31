from __future__ import annotations

import numpy as np
import pytest

from sanity_log_parser.gca.adaptive_eps_fit import (
    SparseAdaptiveEpsDataset,
    fit_adaptive_eps_tree,
    fit_adaptive_eps_tree_approx,
)


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


def test_fit_adaptive_eps_tree_approx_finds_perfect_small_tree() -> None:
    feature_defs = [
        {"kind": "level_exact", "levels": [-1]},
    ]
    dataset = SparseAdaptiveEpsDataset(
        group_count=4,
        pair_i=np.asarray([0, 0, 0, 1, 1, 2], dtype=np.int32),
        pair_j=np.asarray([1, 2, 3, 2, 3, 3], dtype=np.int32),
        X=np.asarray(
            [
                [1.0],
                [0.0],
                [0.0],
                [0.0],
                [0.0],
                [1.0],
            ],
            dtype=np.float32,
        ),
        y=np.asarray([1, 0, 0, 0, 0, 1], dtype=np.int32),
        pair_distances=np.asarray([0.18, 0.72, 0.68, 0.70, 0.66, 0.16], dtype=np.float32),
        cluster_labels=(0, 0, 1, 1),
        initial_edge_count=6,
        retained_edge_count=6,
    )

    result = fit_adaptive_eps_tree_approx(
        dataset,
        feature_defs,
        max_depth_candidates=(1, 2),
        min_samples_leaf_candidates=(1, 2),
        jobs=1,
    )

    assert result.f1 == 1.0
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.node_count <= 3
    assert result.tree["features"] == tuple(feature_defs)


@pytest.mark.filterwarnings(
    "ignore:This process .* is multi-threaded, use of fork\\(\\) may lead to deadlocks in the child.:DeprecationWarning"
)
def test_fit_adaptive_eps_tree_approx_is_deterministic_across_jobs() -> None:
    feature_defs = [
        {"kind": "level_exact", "levels": [-1]},
    ]
    dataset = SparseAdaptiveEpsDataset(
        group_count=4,
        pair_i=np.asarray([0, 0, 0, 1, 1, 2], dtype=np.int32),
        pair_j=np.asarray([1, 2, 3, 2, 3, 3], dtype=np.int32),
        X=np.asarray(
            [
                [1.0],
                [0.0],
                [0.0],
                [0.0],
                [0.0],
                [1.0],
            ],
            dtype=np.float32,
        ),
        y=np.asarray([1, 0, 0, 0, 0, 1], dtype=np.int32),
        pair_distances=np.asarray([0.18, 0.72, 0.68, 0.70, 0.66, 0.16], dtype=np.float32),
        cluster_labels=(0, 0, 1, 1),
        initial_edge_count=6,
        retained_edge_count=6,
    )

    serial = fit_adaptive_eps_tree_approx(
        dataset,
        feature_defs,
        max_depth_candidates=(1, 2),
        min_samples_leaf_candidates=(1, 2),
        jobs=1,
    )
    parallel = fit_adaptive_eps_tree_approx(
        dataset,
        feature_defs,
        max_depth_candidates=(1, 2),
        min_samples_leaf_candidates=(1, 2),
        jobs=2,
    )

    assert parallel.f1 == serial.f1
    assert parallel.precision == serial.precision
    assert parallel.recall == serial.recall
    assert parallel.node_count == serial.node_count
    assert parallel.max_depth == serial.max_depth
    assert parallel.min_samples_leaf == serial.min_samples_leaf
    assert parallel.tree == serial.tree
