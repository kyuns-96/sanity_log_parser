"""Tests for sanity_log_parser.gca.eval."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sanity_log_parser.gca.eval import evaluate, format_results


def _write_json(path: Path, name: str, data: object) -> str:
    p = path / name
    _ = p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


def _make_logic_json(groups: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "run": {},
        "groups": groups,
    }


def _logic_group(
    rule_id: str, seq: int, raw_logs: list[str]
) -> dict[str, object]:
    return {
        "group_type": "logic",
        "group_id": f"{rule_id}::logic::{seq:06d}",
        "rule_id": rule_id,
        "representative_template": "T",
        "representative_pattern": "P",
        "total_count": len(raw_logs),
        "merged_variants_count": 1,
        "original_logs": raw_logs,
    }


def _ai_group(
    rule_id: str, seq: int, raw_logs: list[str]
) -> dict[str, object]:
    return {
        "group_type": "ai_super",
        "group_id": f"{rule_id}::ai::{seq:06d}",
        "rule_id": rule_id,
        "representative_template": "T",
        "representative_pattern": "P",
        "total_count": len(raw_logs),
        "merged_variants_count": 1,
        "original_logs": raw_logs,
    }


# --- Perfect clustering ---


def test_perfect_match(tmp_path: Path) -> None:
    """AI clusters match ground truth exactly -> F1=1.0."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, ["a"]),
            _logic_group("R1", 2, ["b"]),
            _logic_group("R1", 3, ["c"]),
        ]
    )
    # AI merges groups 1+2, keeps 3 alone
    ai = _make_logic_json(
        [
            _ai_group("R1", 1, ["a", "b"]),
            _ai_group("R1", 2, ["c"]),
        ]
    )
    gt = {
        "R1": [
            ["R1::logic::000001", "R1::logic::000002"],
            ["R1::logic::000003"],
        ]
    }

    lp = _write_json(tmp_path, "logic.json", logic)
    ap = _write_json(tmp_path, "ai.json", ai)
    gp = _write_json(tmp_path, "gt.json", gt)

    results = evaluate(lp, ap, gp)
    assert len(results) == 1
    r = results[0]
    assert r["precision"] == 1.0
    assert r["recall"] == 1.0
    assert r["f1"] == 1.0
    assert r["status"] == "PASS"


# --- Over-clustering (low precision) ---


def test_over_clustering(tmp_path: Path) -> None:
    """AI merges everything -> precision drops."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, ["a"]),
            _logic_group("R1", 2, ["b"]),
            _logic_group("R1", 3, ["c"]),
        ]
    )
    # AI merges all 3 into one group
    ai = _make_logic_json(
        [_ai_group("R1", 1, ["a", "b", "c"])]
    )
    # Ground truth says 1+2 together, 3 alone
    gt = {
        "R1": [
            ["R1::logic::000001", "R1::logic::000002"],
            ["R1::logic::000003"],
        ]
    }

    lp = _write_json(tmp_path, "logic.json", logic)
    ap = _write_json(tmp_path, "ai.json", ai)
    gp = _write_json(tmp_path, "gt.json", gt)

    results = evaluate(lp, ap, gp)
    r = results[0]
    assert r["recall"] == 1.0  # all GT merges found
    assert r["precision"] < 1.0  # but extra merges happened
    assert r["fp"] == 2  # (1,3) and (2,3) are false positives
    assert r["status"] == "FAIL"


# --- Under-clustering (low recall) ---


def test_under_clustering(tmp_path: Path) -> None:
    """AI keeps everything separate -> recall drops."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, ["a"]),
            _logic_group("R1", 2, ["b"]),
            _logic_group("R1", 3, ["c"]),
        ]
    )
    # AI keeps all 3 separate
    ai = _make_logic_json(
        [
            _ai_group("R1", 1, ["a"]),
            _ai_group("R1", 2, ["b"]),
            _ai_group("R1", 3, ["c"]),
        ]
    )
    # Ground truth says all 3 should be merged
    gt = {
        "R1": [
            ["R1::logic::000001", "R1::logic::000002", "R1::logic::000003"],
        ]
    }

    lp = _write_json(tmp_path, "logic.json", logic)
    ap = _write_json(tmp_path, "ai.json", ai)
    gp = _write_json(tmp_path, "gt.json", gt)

    results = evaluate(lp, ap, gp)
    r = results[0]
    assert r["precision"] == 1.0  # no false merges
    assert r["recall"] == 0.0  # no GT merges found
    assert r["fn"] == 3  # 3 pairs missed


# --- All singletons (vacuous) ---


def test_all_singletons(tmp_path: Path) -> None:
    """When GT is all singletons and AI also doesn't merge, perfect score."""
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, ["a"]),
            _logic_group("R1", 2, ["b"]),
        ]
    )
    ai = _make_logic_json(
        [
            _ai_group("R1", 1, ["a"]),
            _ai_group("R1", 2, ["b"]),
        ]
    )
    gt = {
        "R1": [
            ["R1::logic::000001"],
            ["R1::logic::000002"],
        ]
    }

    lp = _write_json(tmp_path, "logic.json", logic)
    ap = _write_json(tmp_path, "ai.json", ai)
    gp = _write_json(tmp_path, "gt.json", gt)

    results = evaluate(lp, ap, gp)
    r = results[0]
    assert r["f1"] == 1.0  # vacuously perfect (0 pairs expected, 0 found)


# --- Multiple rules ---


def test_multiple_rules(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, ["a"]),
            _logic_group("R1", 2, ["b"]),
            _logic_group("R2", 1, ["x"]),
            _logic_group("R2", 2, ["y"]),
        ]
    )
    ai = _make_logic_json(
        [
            _ai_group("R1", 1, ["a", "b"]),
            _ai_group("R2", 1, ["x"]),
            _ai_group("R2", 2, ["y"]),
        ]
    )
    gt = {
        "R1": [["R1::logic::000001", "R1::logic::000002"]],
        "R2": [["R2::logic::000001", "R2::logic::000002"]],
    }

    lp = _write_json(tmp_path, "logic.json", logic)
    ap = _write_json(tmp_path, "ai.json", ai)
    gp = _write_json(tmp_path, "gt.json", gt)

    results = evaluate(lp, ap, gp)
    by_rule = {r["rule_id"]: r for r in results}
    assert by_rule["R1"]["f1"] == 1.0
    assert by_rule["R2"]["f1"] == 0.0  # R2 missed the merge


# --- Format output ---


def test_format_results_contains_header() -> None:
    results = [
        {
            "rule_id": "R1",
            "precision": 1.0,
            "recall": 0.5,
            "f1": 0.6667,
            "tp": 1,
            "fp": 0,
            "fn": 1,
            "gt_clusters": 2,
            "ai_clusters": 3,
            "status": "FAIL",
        }
    ]
    output = format_results(results)
    assert "Rule ID" in output
    assert "R1" in output
    assert "FAIL" in output


# --- F1 threshold ---


def test_custom_f1_threshold(tmp_path: Path) -> None:
    logic = _make_logic_json(
        [
            _logic_group("R1", 1, ["a"]),
            _logic_group("R1", 2, ["b"]),
        ]
    )
    ai = _make_logic_json(
        [_ai_group("R1", 1, ["a", "b"])]
    )
    gt = {"R1": [["R1::logic::000001", "R1::logic::000002"]]}

    lp = _write_json(tmp_path, "logic.json", logic)
    ap = _write_json(tmp_path, "ai.json", ai)
    gp = _write_json(tmp_path, "gt.json", gt)

    results = evaluate(lp, ap, gp, f1_threshold=1.0)
    assert results[0]["status"] == "PASS"
