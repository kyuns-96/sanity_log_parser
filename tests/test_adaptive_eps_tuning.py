from __future__ import annotations

import json
from pathlib import Path

import pytest

from sanity_log_parser.gca.adaptive_eps_tuning import (
    DEFAULT_ADAPTIVE_EPS_FEATURES_V1,
    _build_sparse_candidate_edges,
    extract_rule_logic_groups,
    load_feature_defs,
    update_rule_config_with_adaptive_eps_tree,
)


def test_load_feature_defs_uses_default_when_path_missing() -> None:
    features = load_feature_defs(None)
    assert features == DEFAULT_ADAPTIVE_EPS_FEATURES_V1
    assert features is not DEFAULT_ADAPTIVE_EPS_FEATURES_V1
    assert all(feature["kind"] != "level_jaccard" for feature in features)


def test_load_feature_defs_from_json_file(tmp_path: Path) -> None:
    path = tmp_path / "features.json"
    path.write_text(
        json.dumps([{"kind": "level_exact", "levels": [-6]}]),
        encoding="utf-8",
    )

    features = load_feature_defs(str(path))

    assert features == ({"kind": "level_exact", "levels": [-6]},)


def test_extract_rule_logic_groups_requires_complete_ground_truth() -> None:
    logic_data = {
        "groups": [
            {
                "group_type": "logic",
                "group_id": "DES_0001::logic::000001",
                "rule_id": "DES_0001",
                "representative_template": "T1",
                "representative_pattern": "top/a/reg1",
                "total_count": 2,
                "original_logs": ["log a", "log b"],
            },
            {
                "group_type": "logic",
                "group_id": "DES_0001::logic::000002",
                "rule_id": "DES_0001",
                "representative_template": "T1",
                "representative_pattern": "top/a/reg2",
                "total_count": 1,
                "original_logs": ["log c"],
            },
        ]
    }
    ground_truth = {"DES_0001": [["DES_0001::logic::000001"]]}

    with pytest.raises(ValueError, match="missing 1 logic group"):
        extract_rule_logic_groups(
            logic_data=logic_data,
            ground_truth_data=ground_truth,
            rule_id="DES_0001",
        )


def test_extract_rule_logic_groups_maps_logic_groups_to_cluster_labels() -> None:
    logic_data = {
        "groups": [
            {
                "group_type": "logic",
                "group_id": "DES_0001::logic::000001",
                "rule_id": "DES_0001",
                "representative_template": "T1",
                "representative_pattern": "top/a/reg1",
                "total_count": 2,
                "original_logs": ["log a", "log b"],
            },
            {
                "group_type": "logic",
                "group_id": "DES_0001::logic::000002",
                "rule_id": "DES_0001",
                "representative_template": "T2",
                "representative_pattern": "top/a/reg2",
                "total_count": 1,
                "original_logs": ["log c"],
            },
        ]
    }
    ground_truth = {
        "DES_0001": [
            ["DES_0001::logic::000001"],
            ["DES_0001::logic::000002"],
        ]
    }

    groups, labels = extract_rule_logic_groups(
        logic_data=logic_data,
        ground_truth_data=ground_truth,
        rule_id="DES_0001",
    )

    assert labels == [0, 1]
    assert groups == [
        {
            "group_id": "DES_0001::logic::000001",
            "template": "T1",
            "pattern": "top/a/reg1",
            "count": 2,
            "members": [{"raw_log": "log a"}, {"raw_log": "log b"}],
        },
        {
            "group_id": "DES_0001::logic::000002",
            "template": "T2",
            "pattern": "top/a/reg2",
            "count": 1,
            "members": [{"raw_log": "log c"}],
        },
    ]


def test_build_sparse_candidate_edges_includes_gt_chain() -> None:
    rule_groups = [
        {
            "group_id": "DES_0001::logic::000001",
            "pattern": "'top/blk_a/reg_1/CK'",
            "template": "T",
            "count": 1,
            "members": [],
        },
        {
            "group_id": "DES_0001::logic::000002",
            "pattern": "'top/blk_z/reg_9/CK'",
            "template": "T",
            "count": 1,
            "members": [],
        },
        {
            "group_id": "DES_0001::logic::000003",
            "pattern": "'top/blk_b/reg_2/CK'",
            "template": "T",
            "count": 1,
            "members": [],
        },
    ]
    pair_i, pair_j, protected = _build_sparse_candidate_edges(
        rule_groups,
        [0, 0, 1],
        ({"kind": "level_exact", "levels": [-2]},),
    )

    edges = set(zip(pair_i.tolist(), pair_j.tolist(), strict=True))
    assert (0, 1) in edges
    assert (0, 1) in protected


def test_update_rule_config_with_adaptive_eps_tree_replaces_pairwise_tree() -> None:
    raw_config = {
        "rules": {
            "DES_0001": {
                "eps": 0.2,
                "template_weight": 0.0,
                "pairwise_tree": {
                    "features": [{"kind": "level_exact", "levels": [-6]}],
                    "nodes": [{"value": 1}],
                },
                "variables": {"0": {"weight": 1.0, "levels": [-2]}},
            }
        }
    }
    adaptive_tree = {
        "features": [{"kind": "level_exact", "levels": [-6]}],
        "nodes": [{"value": 0.123}],
    }

    updated, removed_pairwise = update_rule_config_with_adaptive_eps_tree(
        raw_config=raw_config,
        rule_id="DES_0001",
        tree=adaptive_tree,
    )

    assert removed_pairwise is True
    assert raw_config["rules"]["DES_0001"]["eps"] == 0.2
    assert "adaptive_eps_tree" not in raw_config["rules"]["DES_0001"]
    assert updated["rules"]["DES_0001"]["eps"] == 1.0
    assert "pairwise_tree" not in updated["rules"]["DES_0001"]
    assert updated["rules"]["DES_0001"]["adaptive_eps_tree"] == adaptive_tree
