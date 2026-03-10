from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from sanity_log_parser.gca.config import GcaConfig
from sanity_log_parser.gca.weight_tuning import (
    fit_rule_weights,
    load_weight_search_spec,
    update_rule_config_with_weight_candidate,
)


def _logic_group(
    rule_id: str,
    seq: int,
    pattern: str,
    raw_logs: list[str],
) -> dict[str, object]:
    return {
        "group_type": "logic",
        "group_id": f"{rule_id}::logic::{seq:06d}",
        "rule_id": rule_id,
        "representative_template": "T",
        "representative_pattern": pattern,
        "total_count": len(raw_logs),
        "merged_variants_count": 1,
        "original_logs": raw_logs,
    }


def _mock_embed_fn(texts: list[str]) -> np.ndarray:
    token_map: dict[str, np.ndarray] = {
        "cluster_a": np.asarray([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
        "cluster_b": np.asarray([0.0, 1.0, 0.0, 0.0], dtype=np.float32),
        "reg_1": np.asarray([0.0, 0.0, 1.0, 0.0], dtype=np.float32),
        "reg_2": np.asarray([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
        "reg_3": np.asarray([0.7, 0.0, 0.0, 0.7], dtype=np.float32),
        "reg_4": np.asarray([0.0, 0.7, 0.7, 0.0], dtype=np.float32),
    }

    rows: list[np.ndarray] = []
    for text in texts:
        cleaned = [token.strip("'\" ") for token in text.split() if token.strip("'\" ")]
        vec = np.zeros(4, dtype=np.float32)
        for token in cleaned:
            vec += token_map.get(token, 0.0)
        rows.append(vec)
    return np.asarray(rows, dtype=np.float32)


def test_fit_rule_weights_picks_signal_level() -> None:
    logic_data = {
        "groups": [
            _logic_group("DES_0001", 1, "'top/alpha/cluster_a/reg_1/CK'", ["a"]),
            _logic_group("DES_0001", 2, "'top/beta/cluster_a/reg_2/Q'", ["b"]),
            _logic_group("DES_0001", 3, "'top/gamma/cluster_b/reg_3/CK'", ["c"]),
            _logic_group("DES_0001", 4, "'top/delta/cluster_b/reg_4/Q'", ["d"]),
        ]
    }
    ground_truth = {
        "DES_0001": [
            ["DES_0001::logic::000001", "DES_0001::logic::000002"],
            ["DES_0001::logic::000003", "DES_0001::logic::000004"],
        ]
    }
    raw_config = {
        "default_eps": 0.2,
        "default_template_weight": 0.3,
        "default_variable_weight": 0.7,
        "rules": {
            "DES_0001": {
                "eps": 0.2,
                "template_weight": 0.0,
                "adaptive_eps_tree": {
                    "features": [{"kind": "level_exact", "levels": [-2]}],
                    "nodes": [{"value": 0.123}],
                },
                "variables": {
                    "0": {"weight": 1.0, "levels": [-2]},
                },
            }
        },
    }
    gca_config = GcaConfig(
        default_eps=0.2,
        default_template_weight=0.3,
        default_variable_weight=0.7,
    )
    search_spec = {
        "template_weight": [0.0],
        "eps": [0.2],
        "variables": {
            "0": [
                {"weight": 1.0, "levels": [-2]},
                {"weight": 1.0, "levels": [-3]},
            ]
        },
    }

    result = fit_rule_weights(
        logic_data=logic_data,
        ground_truth_data=ground_truth,
        gca_config=gca_config,
        raw_config=raw_config,
        rule_id="DES_0001",
        embed_fn=_mock_embed_fn,
        search_spec=search_spec,
        top_k=2,
    )

    assert result.candidates_evaluated == 2
    assert result.best.f1 == 1.0
    assert result.best.raw_rule["variables"]["0"]["levels"] == [-3]
    assert "adaptive_eps_tree" not in result.updated_config["rules"]["DES_0001"]


def test_update_rule_config_with_weight_candidate_removes_trees() -> None:
    raw_config = {
        "rules": {
            "DES_0001": {
                "eps": 1.0,
                "template_weight": 0.0,
                "pairwise_tree": {"features": [], "nodes": []},
                "adaptive_eps_tree": {"features": [], "nodes": []},
            }
        }
    }
    rule_raw = {
        "eps": 0.2,
        "template_weight": 0.0,
        "variables": {"0": {"weight": 1.0, "levels": [-3]}},
    }

    updated, removed_pairwise, removed_adaptive = update_rule_config_with_weight_candidate(
        raw_config=raw_config,
        rule_id="DES_0001",
        rule_raw=rule_raw,
    )

    assert removed_pairwise is True
    assert removed_adaptive is True
    assert updated["rules"]["DES_0001"] == rule_raw
    assert "pairwise_tree" not in updated["rules"]["DES_0001"]
    assert "adaptive_eps_tree" not in updated["rules"]["DES_0001"]


def test_load_weight_search_spec_from_json(tmp_path: Path) -> None:
    spec_path = tmp_path / "search_spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "template_weight": [0.0],
                "eps": [0.2, 0.3],
                "variables": {"0": [{"weight": 1.0, "levels": [-3]}]},
            }
        ),
        encoding="utf-8",
    )

    spec = load_weight_search_spec(
        str(spec_path),
        logic_data={"groups": []},
        gca_config=GcaConfig(),
        raw_config={},
        rule_id="DES_0001",
    )

    assert spec["eps"] == [0.2, 0.3]
    assert spec["variables"]["0"][0]["levels"] == [-3]
