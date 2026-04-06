from __future__ import annotations

import numpy as np
import pytest
from typing import cast

from sanity_log_parser.gca.adaptive_eps_fit import (
    SparseAdaptiveEpsDataset,
    _compact_adaptive_tree,
    _optimize_leaf_values,
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
    feature_defs: list[dict[str, object]] = [
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
    feature_defs: list[dict[str, object]] = [
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
        pair_distances=np.asarray(
            [0.18, 0.72, 0.68, 0.70, 0.66, 0.16], dtype=np.float32
        ),
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
    feature_defs: list[dict[str, object]] = [
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
        pair_distances=np.asarray(
            [0.18, 0.72, 0.68, 0.70, 0.66, 0.16], dtype=np.float32
        ),
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


def test_optimize_leaf_values_improves_generic_threshold_choice() -> None:
    pair_i = np.asarray([0, 0, 0, 1, 1, 2], dtype=np.int32)
    pair_j = np.asarray([1, 2, 3, 2, 3, 3], dtype=np.int32)
    pair_distances = np.asarray([0.18, 0.30, 0.32, 0.34, 0.36, 0.19], dtype=np.float32)
    leaf_assignments = np.asarray([1, 1, 1, 1, 1, 1], dtype=np.int32)
    initial_leaf_values = {1: 0.18}

    leaf_values, metrics = _optimize_leaf_values(
        group_count=4,
        pair_i=pair_i,
        pair_j=pair_j,
        pair_distances=pair_distances,
        cluster_labels=(0, 0, 1, 1),
        leaf_assignments=leaf_assignments,
        leaf_values=initial_leaf_values,
        round_decimals=3,
        min_eps=0.001,
    )

    assert leaf_values[1] == 0.19
    assert metrics["f1"] == 1.0
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_compact_adaptive_tree_collapses_redundant_subtree() -> None:
    tree: dict[str, object] = {
        "features": ({"kind": "level_exact", "levels": (-1,)},),
        "nodes": (
            {"feature": 0, "threshold": 0.5, "left": 1, "right": 4},
            {"feature": 0, "threshold": 0.2, "left": 2, "right": 3},
            {"value": 0.1},
            {"value": 0.1},
            {"value": 0.4},
        ),
    }

    compact_tree, metrics = _compact_adaptive_tree(
        tree,
        lambda candidate_tree: (
            {"precision": 1.0, "recall": 1.0, "f1": 1.0}
            if len(cast(tuple[dict[str, object], ...], candidate_tree["nodes"])) > 1
            else {"precision": 0.5, "recall": 0.5, "f1": 0.5}
        ),
        {"precision": 1.0, "recall": 1.0, "f1": 1.0},
    )

    assert metrics == {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    assert compact_tree["nodes"] == (
        {"feature": 0, "threshold": 0.5, "left": 1, "right": 2},
        {"value": 0.1},
        {"value": 0.4},
    )
