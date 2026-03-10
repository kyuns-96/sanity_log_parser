from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.tree import DecisionTreeClassifier

from sanity_log_parser.clustering.ai.pairwise_tree import (
    _build_feature_matrices,
    compute_adaptive_eps_distance_matrix,
)


@dataclass(frozen=True)
class AdaptiveEpsFitResult:
    tree: dict[str, object]
    precision: float
    recall: float
    f1: float
    node_count: int
    max_depth: int
    min_samples_leaf: int


def fit_adaptive_eps_tree(
    rule_groups: list[dict[str, Any]],
    base_distances: Any,
    cluster_labels: Sequence[str | int],
    feature_defs: Sequence[dict[str, object]],
    *,
    max_depth_candidates: Sequence[int] = tuple(range(1, 8)),
    min_samples_leaf_candidates: Sequence[int] = tuple(range(1, 16)),
    round_decimals: int = 3,
    min_eps: float = 0.001,
    random_state: int = 0,
) -> AdaptiveEpsFitResult:
    """Fit a compact adaptive-eps tree from labeled logic groups.

    The learned model is intentionally generic:
    - it uses only structural pairwise features
    - it learns same-cluster vs different-cluster partitions
    - each leaf is converted into a positive eps value using the observed
      base embedding distances for that leaf

    The search objective is clustering F1 over the provided logic groups.
    Among ties, the fitter prefers smaller trees.
    """
    if len(rule_groups) != len(cluster_labels):
        msg = "rule_groups and cluster_labels must have the same length"
        raise ValueError(msg)
    if len(rule_groups) < 2:
        msg = "at least two rule groups are required"
        raise ValueError(msg)
    if not feature_defs:
        msg = "feature_defs must be non-empty"
        raise ValueError(msg)
    if round_decimals < 0:
        msg = "round_decimals must be non-negative"
        raise ValueError(msg)
    if min_eps <= 0:
        msg = "min_eps must be positive"
        raise ValueError(msg)

    feature_tuple = tuple(dict(feature) for feature in feature_defs)
    feature_matrices = _build_feature_matrices(rule_groups, feature_tuple)
    X, y, pair_distances = _build_pair_dataset(
        feature_matrices,
        base_distances,
        list(cluster_labels),
    )

    best: AdaptiveEpsFitResult | None = None
    for max_depth in max_depth_candidates:
        for min_samples_leaf in min_samples_leaf_candidates:
            classifier = DecisionTreeClassifier(
                max_depth=int(max_depth),
                min_samples_leaf=int(min_samples_leaf),
                random_state=random_state,
            )
            classifier.fit(X, y)

            tree = _classifier_to_adaptive_eps_tree(
                classifier,
                X,
                y,
                pair_distances,
                feature_tuple,
                round_decimals=round_decimals,
                min_eps=min_eps,
            )
            metrics = _score_adaptive_tree(rule_groups, base_distances, cluster_labels, tree)
            candidate = AdaptiveEpsFitResult(
                tree=tree,
                precision=metrics["precision"],
                recall=metrics["recall"],
                f1=metrics["f1"],
                node_count=int(classifier.tree_.node_count),
                max_depth=int(max_depth),
                min_samples_leaf=int(min_samples_leaf),
            )
            if _is_better_fit(candidate, best):
                best = candidate

    if best is None:
        msg = "no adaptive-eps candidate could be fit"
        raise RuntimeError(msg)
    return best


def _build_pair_dataset(
    feature_matrices: list[Any],
    base_distances: Any,
    cluster_labels: list[str | int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(cluster_labels)
    rows: list[list[float]] = []
    labels: list[int] = []
    pair_distances: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            rows.append([float(matrix[i, j]) for matrix in feature_matrices])
            labels.append(1 if cluster_labels[i] == cluster_labels[j] else 0)
            pair_distances.append(float(base_distances[i, j]))
    return (
        np.asarray(rows, dtype=np.float32),
        np.asarray(labels, dtype=np.int32),
        np.asarray(pair_distances, dtype=np.float32),
    )


def _classifier_to_adaptive_eps_tree(
    classifier: DecisionTreeClassifier,
    X: np.ndarray,
    y: np.ndarray,
    pair_distances: np.ndarray,
    feature_defs: tuple[dict[str, object], ...],
    *,
    round_decimals: int,
    min_eps: float,
) -> dict[str, object]:
    tree_ = classifier.tree_
    leaf_assignments = classifier.apply(X)
    leaf_values = _derive_leaf_eps_values(
        leaf_assignments,
        y,
        pair_distances,
        round_decimals=round_decimals,
        min_eps=min_eps,
    )

    nodes: list[dict[str, object]] = []
    for index in range(tree_.node_count):
        left = int(tree_.children_left[index])
        right = int(tree_.children_right[index])
        if left == -1 and right == -1:
            nodes.append({"value": leaf_values[index]})
            continue
        nodes.append(
            {
                "feature": int(tree_.feature[index]),
                "threshold": round(float(tree_.threshold[index]), round_decimals),
                "left": left,
                "right": right,
            }
        )

    return {
        "features": feature_defs,
        "nodes": tuple(nodes),
    }


def _derive_leaf_eps_values(
    leaf_assignments: np.ndarray,
    y: np.ndarray,
    pair_distances: np.ndarray,
    *,
    round_decimals: int,
    min_eps: float,
) -> dict[int, float]:
    leaf_stats: dict[int, dict[str, list[float]]] = {}
    for leaf_id, label, distance in zip(leaf_assignments, y, pair_distances, strict=True):
        stats = leaf_stats.setdefault(int(leaf_id), {"pos": [], "neg": []})
        key = "pos" if int(label) == 1 else "neg"
        stats[key].append(float(distance))

    leaf_values: dict[int, float] = {}
    for leaf_id, stats in leaf_stats.items():
        pos = stats["pos"]
        neg = stats["neg"]
        if pos and neg:
            max_pos = max(pos)
            min_neg = min(neg)
            if max_pos < min_neg:
                eps = (max_pos + min_neg) / 2.0
            else:
                pos_ratio = len(pos) / (len(pos) + len(neg))
                if pos_ratio >= 0.5:
                    eps = max_pos
                else:
                    eps = max(min_eps, min_neg * 0.5)
        elif pos:
            eps = max(pos)
        elif neg:
            eps = max(min_eps, min(neg) * 0.5)
        else:
            eps = min_eps

        leaf_values[leaf_id] = max(min_eps, round(float(eps), round_decimals))

    return leaf_values


def _score_adaptive_tree(
    rule_groups: list[dict[str, Any]],
    base_distances: Any,
    cluster_labels: Sequence[str | int],
    tree: dict[str, object],
) -> dict[str, float]:
    normalized = compute_adaptive_eps_distance_matrix(
        rule_groups,
        base_distances,
        tree,
    )
    predicted = DBSCAN(
        eps=1.0,
        min_samples=1,
        metric="precomputed",
    ).fit(normalized).labels_
    return _cluster_pair_metrics(cluster_labels, predicted)


def _cluster_pair_metrics(
    expected: Sequence[str | int],
    predicted: Sequence[int],
) -> dict[str, float]:
    gt_pairs = _pairs_from_labels(expected)
    pred_pairs = _pairs_from_labels(predicted)
    tp = len(gt_pairs & pred_pairs)
    fp = len(pred_pairs - gt_pairs)
    fn = len(gt_pairs - pred_pairs)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return {"precision": precision, "recall": recall, "f1": f1}


def _pairs_from_labels(labels: Sequence[str | int]) -> set[tuple[int, int]]:
    grouped: dict[str | int, list[int]] = {}
    for index, label in enumerate(labels):
        grouped.setdefault(label, []).append(index)

    pairs: set[tuple[int, int]] = set()
    for members in grouped.values():
        for offset, left in enumerate(members):
            for right in members[offset + 1 :]:
                pairs.add((left, right))
    return pairs


def _is_better_fit(
    candidate: AdaptiveEpsFitResult,
    current: AdaptiveEpsFitResult | None,
) -> bool:
    if current is None:
        return True

    current_perfect = current.f1 == 1.0
    candidate_perfect = candidate.f1 == 1.0
    if candidate_perfect != current_perfect:
        return candidate_perfect

    if candidate.f1 != current.f1:
        return candidate.f1 > current.f1
    if candidate.precision != current.precision:
        return candidate.precision > current.precision
    if candidate.recall != current.recall:
        return candidate.recall > current.recall
    if candidate.node_count != current.node_count:
        return candidate.node_count < current.node_count
    if candidate.max_depth != current.max_depth:
        return candidate.max_depth < current.max_depth
    return candidate.min_samples_leaf > current.min_samples_leaf
