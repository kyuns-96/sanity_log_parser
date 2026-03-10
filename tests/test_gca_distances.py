"""Tests for sanity_log_parser.gca.distances."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from sanity_log_parser.gca.config import GcaConfig, GcaRuleConfig, VariableConfig
from sanity_log_parser.gca.distances import (
    compute_distances,
    format_distances,
    _analyze_variable_levels,
)


def _write_json(path: Path, name: str, data: object) -> str:
    p = path / name
    _ = p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def _make_logic_json(groups: list[dict[str, object]]) -> dict[str, object]:
    return {"schema_version": 2, "run": {}, "groups": groups}


def _logic_group(
    rule_id: str, seq: int, template: str, pattern: str, raw_logs: list[str]
) -> dict[str, object]:
    return {
        "group_type": "logic",
        "group_id": f"{rule_id}::logic::{seq:06d}",
        "rule_id": rule_id,
        "representative_template": template,
        "representative_pattern": pattern,
        "total_count": len(raw_logs),
        "merged_variants_count": 1,
        "original_logs": raw_logs,
    }


def _mock_embed_fn(texts: list[str]) -> np.ndarray:
    """Deterministic mock: hash each text to a unit vector."""
    dim = 16
    result = np.zeros((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        rng = np.random.default_rng(hash(t) % (2**31))
        vec = rng.standard_normal(dim).astype(np.float32)
        result[i] = vec / np.linalg.norm(vec)
    return result


def test_compute_distances_basic(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "clk_a / sig_x", ["a"]),
            _logic_group("R1", 2, "T", "clk_b / sig_x", ["b"]),
            _logic_group("R1", 3, "T", "clk_a / sig_y", ["c"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)

    config = GcaConfig(
        default_eps=0.2,
        default_template_weight=0.3,
        default_variable_weight=0.7,
    )

    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    assert result["rule_id"] == "R1"
    assert result["n_groups"] == 3
    assert len(result["pairs"]) == 3  # C(3,2) = 3 pairs
    # Pairs are sorted by distance
    dists = [p["distance"] for p in result["pairs"]]
    assert dists == sorted(dists)


def test_compute_distances_with_ground_truth(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "clk_a / sig", ["a"]),
            _logic_group("R1", 2, "T", "clk_a / sig", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig()

    gt = {"R1": [["R1::logic::000001", "R1::logic::000002"]]}
    result = compute_distances(lp, "R1", config, _mock_embed_fn, ground_truth=gt)

    assert len(result["pairs"]) == 1
    assert result["pairs"][0]["gt_same_cluster"] is True


def test_compute_distances_missing_rule(tmp_path: Path) -> None:
    logic = _make_logic_json([_logic_group("R1", 1, "T", "P", ["a"])])
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig()

    result = compute_distances(lp, "NONEXISTENT", config, _mock_embed_fn)
    assert "error" in result


def test_compute_distances_merge_flag(tmp_path: Path) -> None:
    """Pairs within eps are flagged as merge=True."""
    # Two groups with identical patterns -> distance 0 -> should merge
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "same_pattern", ["a"]),
            _logic_group("R1", 2, "T", "same_pattern", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig(default_eps=0.5)  # generous eps

    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    # Both have identical content -> distance should be 0
    assert result["pairs"][0]["distance"] == 0.0
    assert result["pairs"][0]["merge"] is True


def test_compute_distances_uses_pairwise_tree_path(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "TA", "foo", ["a"]),
            _logic_group("R1", 2, "TB", "bar", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig(
        default_variable_weight=0.0,
        rules={
            "R1": GcaRuleConfig(
                eps=0.5,
                template_weight=1.0,
                pairwise_tree={
                    "features": ({"kind": "path_length_equal"},),
                    "nodes": ({"value": 0},),
                },
            )
        },
    )

    def unexpected_embed_fn(texts: list[str]) -> np.ndarray:
        raise AssertionError(f"embed_fn should not be called for pairwise_tree: {texts}")

    result = compute_distances(lp, "R1", config, unexpected_embed_fn)
    assert result["pairs"][0]["distance"] == 0.0
    assert result["pairs"][0]["merge"] is True


def test_compute_distances_uses_adaptive_eps_threshold(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "TA", "foo", ["a"]),
            _logic_group("R1", 2, "TB", "bar", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig(
        default_variable_weight=0.0,
        rules={
            "R1": GcaRuleConfig(
                eps=0.2,
                template_weight=1.0,
                adaptive_eps_tree={
                    "features": ({"kind": "path_length_equal"},),
                    "nodes": ({"value": 2.0},),
                },
            )
        },
    )

    def orthogonal_templates(texts: list[str]) -> np.ndarray:
        rows = []
        for i, _text in enumerate(texts):
            rows.append([1.0, 0.0] if i == 0 else [0.0, 1.0])
        return np.asarray(rows, dtype=np.float32)

    result = compute_distances(lp, "R1", config, orthogonal_templates)
    assert result["eps"] == 1.0
    assert result["pairs"][0]["distance"] == 0.5
    assert result["pairs"][0]["merge"] is True


def test_format_distances_error() -> None:
    result = {"rule_id": "R1", "error": "No groups found"}
    output = format_distances(result)
    assert "ERROR" in output


def test_format_distances_table(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "clk_a / sig", ["a"]),
            _logic_group("R1", 2, "T", "clk_b / sig", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig(
        default_eps=0.1,
        rules={"R1": GcaRuleConfig(eps=0.1, template_weight=0.0)},
    )

    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    output = format_distances(result)
    assert "Rule: R1" in output
    assert "eps=" in output
    assert "Pairwise Distances" in output


def test_compute_distances_per_rule_config(tmp_path: Path) -> None:
    """Per-rule config with variable weights is applied correctly."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "clk_a / sig_x", ["a"]),
            _logic_group("R1", 2, "T", "clk_b / sig_x", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)

    config = GcaConfig(
        rules={
            "R1": GcaRuleConfig(
                eps=0.05,
                template_weight=0.0,
                variables={
                    0: VariableConfig(weight=0.0),
                    1: VariableConfig(weight=1.0),
                },
            )
        }
    )

    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    assert result["eps"] == 0.05
    assert result["template_weight"] == 0.0
    assert result["variables"]["0"]["weight"] == 0.0
    assert result["variables"]["1"]["weight"] == 1.0


# --- level_weights tests ---


def test_compute_distances_level_weights(tmp_path: Path) -> None:
    """level_weights expands variable into per-level embedding slots."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'top/sub/block_a/reg_1/CK' / clk", ["a"]),
            _logic_group("R1", 2, "T", "'top/sub/block_a/reg_1/Q' / clk", ["b"]),
            _logic_group("R1", 3, "T", "'top/sub/block_b/reg_2/CK' / clk", ["c"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)

    config = GcaConfig(
        default_eps=0.5,
        default_template_weight=0.0,
        default_variable_weight=0.0,
        rules={
            "R1": GcaRuleConfig(
                eps=0.5,
                template_weight=0.0,
                variables={
                    0: VariableConfig(level_weights={-3: 0.5, -2: 1.0}),
                    1: VariableConfig(weight=0.0),
                },
            )
        },
    )

    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    assert result["n_groups"] == 3
    # Config summary should show level_weights
    assert "level_weights" in result["variables"]["0"]
    assert result["variables"]["0"]["level_weights"] == {"-3": 0.5, "-2": 1.0}
    # Groups 1 and 2 share block_a/reg_1 -> distance 0 at signal levels
    pair_12 = next(p for p in result["pairs"] if "000001" in p["a"] and "000002" in p["b"])
    assert pair_12["distance"] == 0.0


def test_format_distances_level_weights(tmp_path: Path) -> None:
    """level_weights appears in formatted output."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'a/b/c' / x", ["1"]),
            _logic_group("R1", 2, "T", "'a/b/d' / x", ["2"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig(
        rules={
            "R1": GcaRuleConfig(
                variables={0: VariableConfig(level_weights={-1: 1.0})},
            )
        },
    )
    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    output = format_distances(result)
    assert "level_weights" in output


# --- Jaccard match_mode tests ---


def test_compute_distances_jaccard_mode(tmp_path: Path) -> None:
    """Jaccard mode gives clean 0/1 distances on structured paths."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'top/sub/block_a/reg_1' / clk", ["a"]),
            _logic_group("R1", 2, "T", "'top/sub/block_a/reg_1' / clk", ["b"]),
            _logic_group("R1", 3, "T", "'top/sub/block_b/reg_2' / clk", ["c"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)

    config = GcaConfig(
        default_eps=0.5,
        default_template_weight=0.0,
        default_variable_weight=0.0,
        rules={
            "R1": GcaRuleConfig(
                eps=0.5,
                template_weight=0.0,
                variables={
                    0: VariableConfig(weight=1.0, match_mode="jaccard"),
                    1: VariableConfig(weight=0.0),
                },
            )
        },
    )

    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    # Groups 1 and 2 have identical var 0 → Jaccard distance 0
    pair_12 = next(p for p in result["pairs"] if "000001" in p["a"] and "000002" in p["b"])
    assert pair_12["distance"] == 0.0
    assert pair_12["merge"] is True
    # Groups 1 and 3 differ → positive Jaccard distance
    pair_13 = next(p for p in result["pairs"] if "000001" in p["a"] and "000003" in p["b"])
    assert pair_13["distance"] > 0.0


def test_compute_distances_jaccard_with_levels(tmp_path: Path) -> None:
    """Jaccard + levels: only selected levels used for Jaccard comparison."""
    logic = _make_logic_json(
        [
            # Same block, different pin → levels=[-2] strips pin noise
            _logic_group("R1", 1, "T", "'top/block_a/CK' / clk", ["a"]),
            _logic_group("R1", 2, "T", "'top/block_a/Q' / clk", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)

    config = GcaConfig(
        default_eps=0.5,
        default_template_weight=0.0,
        default_variable_weight=0.0,
        rules={
            "R1": GcaRuleConfig(
                eps=0.5,
                template_weight=0.0,
                variables={
                    0: VariableConfig(weight=1.0, levels=[-2], match_mode="jaccard"),
                    1: VariableConfig(weight=0.0),
                },
            )
        },
    )

    result = compute_distances(lp, "R1", config, _mock_embed_fn)
    # After levels=[-2], both are "block_a" → Jaccard distance 0
    assert result["pairs"][0]["distance"] == 0.0


def test_compute_distances_uses_pairwise_tree_when_present(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "TA", "a", ["a"]),
            _logic_group("R1", 2, "TB", "b", ["b"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig(
        default_variable_weight=0.0,
        rules={
            "R1": GcaRuleConfig(
                eps=0.5,
                template_weight=1.0,
                pairwise_tree={
                    "features": ({"kind": "path_length_equal"},),
                    "nodes": ({"value": 0},),
                },
            )
        },
    )

    def embed_fn(texts: list[str]) -> np.ndarray:
        rows = [[1.0, 0.0], [0.0, 1.0]]
        rows.extend([[1.0, 0.0]] * max(0, len(texts) - 2))
        return np.asarray(rows[: len(texts)], dtype=np.float32)

    result = compute_distances(lp, "R1", config, embed_fn)

    assert result["pairs"][0]["distance"] == 0.0
    assert result["pairs"][0]["merge"] is True


# --- Level Analysis tests ---


def test_level_analysis_signal_vs_noise(tmp_path: Path) -> None:
    """Level analysis correctly identifies signal and noise levels with GT."""
    # Simulate DES_0001 pattern: top/sub/block_X/reg_Y/PIN
    # block (level 2) and reg (level 3) are signal, pin (level 4) is noise
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'top/sub/block_a/reg_1/CK' / clk", ["a"]),
            _logic_group("R1", 2, "T", "'top/sub/block_a/reg_1/Q' / clk", ["b"]),
            _logic_group("R1", 3, "T", "'top/sub/block_b/reg_2/CK' / clk", ["c"]),
            _logic_group("R1", 4, "T", "'top/sub/block_b/reg_2/D' / clk", ["d"]),
        ]
    )
    group_ids = [f"R1::logic::{i:06d}" for i in range(1, 5)]

    # GT: groups 1+2 in cluster A (block_a/reg_1), groups 3+4 in cluster B (block_b/reg_2)
    gt_cluster_map = {
        "R1::logic::000001": 0,
        "R1::logic::000002": 0,
        "R1::logic::000003": 1,
        "R1::logic::000004": 1,
    }

    analysis = _analyze_variable_levels(
        logic["groups"], group_ids, gt_cluster_map
    )

    # Variable 0 is the hierarchy path
    var0 = analysis[0]
    assert var0["var_index"] == 0
    assert var0["depth"] == 5

    levels = {li["level"]: li for li in var0["levels"]}
    assert levels[0]["signal"] == "constant"  # "top" everywhere
    assert levels[1]["signal"] == "constant"  # "sub" everywhere
    assert levels[2]["signal"] == "signal"    # block_a vs block_b
    assert levels[3]["signal"] == "signal"    # reg_1 vs reg_2
    assert levels[4]["signal"] == "noise"     # pin names (CK, Q, D) vary within clusters

    # Should recommend negative indices for signal levels, excluding pin noise
    assert var0["recommendation"] is not None
    assert "[-3, -2]" in var0["recommendation"]  # block + register
    assert "[-1]" in var0["recommendation"]       # pin noise excluded


def test_level_analysis_no_ground_truth(tmp_path: Path) -> None:
    """Without GT, levels are classified as 'unknown' (not signal/noise)."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'a/b/c' / x", ["a"]),
            _logic_group("R1", 2, "T", "'a/b/d' / x", ["b"]),
        ]
    )
    group_ids = ["R1::logic::000001", "R1::logic::000002"]

    analysis = _analyze_variable_levels(logic["groups"], group_ids, None)
    var0 = analysis[0]

    for li in var0["levels"]:
        assert li["signal"] in ("constant", "unknown")


def test_level_analysis_all_constant(tmp_path: Path) -> None:
    """All levels constant -> recommends weight=0."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'top/sub' / clk", ["a"]),
            _logic_group("R1", 2, "T", "'top/sub' / clk", ["b"]),
        ]
    )
    group_ids = ["R1::logic::000001", "R1::logic::000002"]
    gt_map = {"R1::logic::000001": 0, "R1::logic::000002": 0}

    analysis = _analyze_variable_levels(logic["groups"], group_ids, gt_map)
    var0 = analysis[0]
    assert "weight=0" in var0["recommendation"]


def test_level_analysis_in_format_output(tmp_path: Path) -> None:
    """Level analysis appears in formatted output."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'top/block_a/reg_1' / clk", ["a"]),
            _logic_group("R1", 2, "T", "'top/block_a/reg_2' / clk", ["b"]),
            _logic_group("R1", 3, "T", "'top/block_b/reg_1' / clk", ["c"]),
        ]
    )
    lp = _write_json(tmp_path, "logic.json", logic)
    config = GcaConfig()
    gt = {
        "R1": [
            ["R1::logic::000001", "R1::logic::000002"],
            ["R1::logic::000003"],
        ]
    }

    result = compute_distances(lp, "R1", config, _mock_embed_fn, ground_truth=gt)
    output = format_distances(result)

    assert "Level Analysis" in output
    assert "SIGNAL" in output
    assert "NOISE" in output or "constant" in output


def test_level_analysis_noise_overrides_signal(tmp_path: Path) -> None:
    """A level that varies within a cluster is always noise, even if it also discriminates."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, "T", "'a/x' / c", ["1"]),
            _logic_group("R1", 2, "T", "'a/y' / c", ["2"]),
            _logic_group("R1", 3, "T", "'b/x' / c", ["3"]),
        ]
    )
    group_ids = ["R1::logic::000001", "R1::logic::000002", "R1::logic::000003"]

    # GT: groups 1+2 same cluster (but level 1 differs: x vs y), group 3 separate
    gt_map = {
        "R1::logic::000001": 0,
        "R1::logic::000002": 0,
        "R1::logic::000003": 1,
    }

    analysis = _analyze_variable_levels(logic["groups"], group_ids, gt_map)
    var0 = analysis[0]
    levels = {li["level"]: li for li in var0["levels"]}

    # Level 0: a vs b -> signal (cluster 0 has 'a', cluster 1 has 'b')
    assert levels[0]["signal"] == "signal"
    # Level 1: x,y within cluster 0 -> noise (varies within = always noise)
    assert levels[1]["signal"] == "noise"
