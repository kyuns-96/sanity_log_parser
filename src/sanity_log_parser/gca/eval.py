"""Evaluate AI clustering results against a human-labeled ground truth."""

from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any


def evaluate(
    logic_path: str | Path,
    ai_path: str | Path,
    ground_truth_path: str | Path,
    f1_threshold: float = 0.97,
) -> list[dict[str, Any]]:
    """Compare AI clustering output to ground truth, return per-rule metrics."""
    logic_data = _load_json(logic_path)
    ai_data = _load_json(ai_path)
    ground_truth: dict[str, list[list[str]]] = _load_json(ground_truth_path)

    raw_to_logic = _build_raw_log_to_logic_id(logic_data)
    ai_clusters = _build_ai_clusters(ai_data, raw_to_logic)

    results: list[dict[str, Any]] = []
    for rule_id, gt_clusters in ground_truth.items():
        gt_pairs = _cluster_list_to_pairs(gt_clusters)
        ai_rule_clusters = ai_clusters.get(rule_id, [])
        ai_pairs = _cluster_list_to_pairs(ai_rule_clusters)

        tp = len(gt_pairs & ai_pairs)
        fp = len(ai_pairs - gt_pairs)
        fn = len(gt_pairs - ai_pairs)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        results.append(
            {
                "rule_id": rule_id,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "gt_clusters": len(gt_clusters),
                "ai_clusters": len(ai_rule_clusters),
                "status": "PASS" if f1 >= f1_threshold else "FAIL",
            }
        )

    return results


def format_results(results: list[dict[str, Any]]) -> str:
    """Format evaluation results as a fixed-width table."""
    lines: list[str] = []
    header = (
        f"{'Rule ID':<20} {'P':>6} {'R':>6} {'F1':>6} "
        f"{'TP':>4} {'FP':>4} {'FN':>4} "
        f"{'GT#':>4} {'AI#':>4} {'Status'}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for r in results:
        lines.append(
            f"{r['rule_id']:<20} {r['precision']:>6.2f} {r['recall']:>6.2f} {r['f1']:>6.2f} "
            f"{r['tp']:>4} {r['fp']:>4} {r['fn']:>4} "
            f"{r['gt_clusters']:>4} {r['ai_clusters']:>4} {r['status']}"
        )
    return "\n".join(lines)


# ---- internal helpers ----


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def _build_raw_log_to_logic_id(logic_data: dict[str, Any]) -> dict[str, str]:
    """Map each raw log text to its logic group_id."""
    mapping: dict[str, str] = {}
    groups = logic_data.get("groups", logic_data) if isinstance(logic_data, dict) else logic_data
    if isinstance(groups, dict):
        groups = groups.get("groups", [])
    for group in groups:
        if group.get("group_type") != "logic":
            continue
        gid: str = group["group_id"]
        for raw_log in group.get("original_logs", []):
            mapping[raw_log] = gid
    return mapping


def _build_ai_clusters(
    ai_data: dict[str, Any],
    raw_to_logic: dict[str, str],
) -> dict[str, list[set[str]]]:
    """Convert AI output to {rule_id: [set_of_logic_group_ids, ...]}."""
    groups = ai_data.get("groups", ai_data) if isinstance(ai_data, dict) else ai_data
    if isinstance(groups, dict):
        groups = groups.get("groups", [])

    clusters_by_rule: dict[str, list[set[str]]] = {}
    for group in groups:
        rule_id: str = group["rule_id"]
        logic_ids: set[str] = set()
        for raw_log in group.get("original_logs", []):
            lid = raw_to_logic.get(raw_log)
            if lid is not None:
                logic_ids.add(lid)
        clusters_by_rule.setdefault(rule_id, []).append(logic_ids)

    return clusters_by_rule


def _cluster_list_to_pairs(
    clusters: list[set[str]] | list[list[str]],
) -> set[tuple[str, str]]:
    """Convert a list of clusters to a set of (a, b) same-cluster pairs where a < b."""
    pairs: set[tuple[str, str]] = set()
    for cluster in clusters:
        items = sorted(cluster)
        for a, b in combinations(items, 2):
            pairs.add((a, b))
    return pairs
