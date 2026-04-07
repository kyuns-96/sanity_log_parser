from __future__ import annotations

import logging
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Sequence, cast

import numpy as np
from sklearn.cluster import DBSCAN
from sklearn.tree import DecisionTreeClassifier

from sanity_log_parser.clustering.ai.pairwise_tree import (
    _build_feature_matrices,
    compute_adaptive_eps_distance_matrix,
)

logger = logging.getLogger(__name__)

TreeNode = dict[str, object]
AdaptiveTree = dict[str, object]
ScoreTreeFn = Callable[[AdaptiveTree], dict[str, float]]


@dataclass(frozen=True)
class AdaptiveEpsFitResult:
    tree: dict[str, object]
    precision: float
    recall: float
    f1: float
    node_count: int
    max_depth: int
    min_samples_leaf: int


@dataclass(frozen=True)
class SparseAdaptiveEpsDataset:
    group_count: int
    pair_i: np.ndarray
    pair_j: np.ndarray
    X: np.ndarray
    y: np.ndarray
    pair_distances: np.ndarray
    cluster_labels: tuple[int, ...]
    initial_edge_count: int
    retained_edge_count: int


_approx_fit_worker_data: SparseAdaptiveEpsDataset | None = None


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
    min_precision: float = 0.0,
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

    logger.info(
        "Adaptive eps exact: %d groups, %d pair rows, %d features, %d candidates.",
        len(rule_groups),
        X.shape[0],
        X.shape[1],
        len(max_depth_candidates) * len(min_samples_leaf_candidates),
    )

    best: AdaptiveEpsFitResult | None = None
    total_candidates = len(max_depth_candidates) * len(min_samples_leaf_candidates)
    progress_interval = _adaptive_progress_interval(total_candidates)
    started_at = time.perf_counter()
    candidate_index = 0
    pair_i, pair_j = _dense_pair_indices(len(rule_groups))
    for max_depth in max_depth_candidates:
        for min_samples_leaf in min_samples_leaf_candidates:
            candidate_index += 1
            classifier = DecisionTreeClassifier(
                max_depth=int(max_depth),
                min_samples_leaf=int(min_samples_leaf),
                random_state=random_state,
            )
            classifier.fit(X, y)
            leaf_assignments = classifier.apply(X)
            leaf_values = _derive_leaf_eps_values(
                leaf_assignments,
                y,
                pair_distances,
                round_decimals=round_decimals,
                min_eps=min_eps,
            )
            leaf_values, metrics = _optimize_leaf_values(
                group_count=len(rule_groups),
                pair_i=pair_i,
                pair_j=pair_j,
                pair_distances=pair_distances,
                cluster_labels=cluster_labels,
                leaf_assignments=leaf_assignments,
                leaf_values=leaf_values,
                round_decimals=round_decimals,
                min_eps=min_eps,
            )
            tree = _classifier_to_adaptive_eps_tree_from_leaf_values(
                classifier,
                feature_tuple,
                leaf_values,
                round_decimals=round_decimals,
            )
            compact_tree, compact_metrics = _compact_adaptive_tree(
                tree,
                lambda candidate_tree: _score_adaptive_tree(
                    rule_groups,
                    base_distances,
                    cluster_labels,
                    candidate_tree,
                ),
                metrics,
            )
            candidate = AdaptiveEpsFitResult(
                tree=compact_tree,
                precision=compact_metrics["precision"],
                recall=compact_metrics["recall"],
                f1=compact_metrics["f1"],
                node_count=len(cast(tuple[TreeNode, ...], compact_tree["nodes"])),
                max_depth=_tree_max_depth(compact_tree),
                min_samples_leaf=int(min_samples_leaf),
            )
            if _is_better_fit(candidate, best, min_precision=min_precision):
                best = candidate
                logger.info(
                    "Adaptive eps exact: new best at %d/%d (%.1f%%) elapsed=%.2fs F1=%.4f P=%.4f R=%.4f nodes=%d depth=%d min_leaf=%d",
                    candidate_index,
                    total_candidates,
                    (candidate_index / total_candidates) * 100.0,
                    time.perf_counter() - started_at,
                    candidate.f1,
                    candidate.precision,
                    candidate.recall,
                    candidate.node_count,
                    candidate.max_depth,
                    candidate.min_samples_leaf,
                )
            elif (
                candidate_index == total_candidates
                or candidate_index % progress_interval == 0
            ):
                current_best = best
                logger.info(
                    "Adaptive eps exact: progress %d/%d (%.1f%%) elapsed=%.2fs current F1=%.4f best F1=%.4f",
                    candidate_index,
                    total_candidates,
                    (candidate_index / total_candidates) * 100.0,
                    time.perf_counter() - started_at,
                    candidate.f1,
                    current_best.f1 if current_best is not None else 0.0,
                )

    if best is None:
        msg = "no adaptive-eps candidate could be fit"
        raise RuntimeError(msg)
    logger.info(
        "Adaptive eps exact: completed %d candidates in %.2fs. Best F1=%.4f.",
        total_candidates,
        time.perf_counter() - started_at,
        best.f1,
    )
    return best


def fit_adaptive_eps_tree_approx(
    dataset: SparseAdaptiveEpsDataset,
    feature_defs: Sequence[dict[str, object]],
    *,
    max_depth_candidates: Sequence[int] = tuple(range(1, 8)),
    min_samples_leaf_candidates: Sequence[int] = tuple(range(1, 16)),
    round_decimals: int = 3,
    min_eps: float = 0.001,
    random_state: int = 0,
    jobs: int = 1,
    rerank_top_k: int = 0,
    exact_rule_groups: list[dict[str, Any]] | None = None,
    exact_base_distances: Any | None = None,
    exact_cluster_labels: Sequence[str | int] | None = None,
    min_precision: float = 0.0,
) -> AdaptiveEpsFitResult:
    if dataset.group_count < 2:
        msg = "at least two rule groups are required"
        raise ValueError(msg)
    if dataset.X.shape[0] == 0:
        msg = "approximate adaptive-eps dataset must contain at least one edge"
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
    if jobs < 0:
        msg = "jobs must be >= 0"
        raise ValueError(msg)
    if not np.any(dataset.y == 1):
        msg = "approximate adaptive-eps dataset must contain at least one positive edge"
        raise ValueError(msg)
    if not np.any(dataset.y == 0):
        msg = "approximate adaptive-eps dataset must contain at least one negative edge"
        raise ValueError(msg)

    feature_tuple = tuple(dict(feature) for feature in feature_defs)
    total_candidates = len(max_depth_candidates) * len(min_samples_leaf_candidates)
    candidate_params = [
        (int(max_depth), int(min_samples_leaf))
        for max_depth in max_depth_candidates
        for min_samples_leaf in min_samples_leaf_candidates
    ]
    worker_jobs = _resolve_approx_jobs(jobs)
    logger.info(
        "Adaptive eps approx: %d groups, %d candidate edges, %d retained edges, %d features, %d candidates, jobs=%d.",
        dataset.group_count,
        dataset.initial_edge_count,
        dataset.retained_edge_count,
        dataset.X.shape[1],
        total_candidates,
        worker_jobs,
    )

    if rerank_top_k < 0:
        msg = "rerank_top_k must be >= 0"
        raise ValueError(msg)
    if not 0.0 <= min_precision <= 1.0:
        msg = "min_precision must be between 0.0 and 1.0"
        raise ValueError(msg)

    started_at = time.perf_counter()
    if worker_jobs <= 1:
        candidates = _fit_adaptive_eps_tree_approx_serial(
            dataset,
            feature_tuple=feature_tuple,
            candidate_params=candidate_params,
            round_decimals=round_decimals,
            min_eps=min_eps,
            random_state=random_state,
            started_at=started_at,
        )
    else:
        candidates = _fit_adaptive_eps_tree_approx_parallel(
            dataset,
            feature_tuple=feature_tuple,
            candidate_params=candidate_params,
            round_decimals=round_decimals,
            min_eps=min_eps,
            random_state=random_state,
            jobs=worker_jobs,
            started_at=started_at,
        )

    best = _select_best_fit(candidates, min_precision=min_precision)
    if (
        rerank_top_k > 0
        and exact_rule_groups is not None
        and exact_base_distances is not None
        and exact_cluster_labels is not None
    ):
        best = _rerank_approx_candidates_exact(
            candidates,
            rerank_top_k=rerank_top_k,
            rule_groups=exact_rule_groups,
            base_distances=exact_base_distances,
            cluster_labels=exact_cluster_labels,
            min_precision=min_precision,
        )

    logger.info(
        "Adaptive eps approx: final selected candidate F1=%.4f P=%.4f R=%.4f nodes=%d depth=%d min_leaf=%d.",
        best.f1,
        best.precision,
        best.recall,
        best.node_count,
        best.max_depth,
        best.min_samples_leaf,
    )
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
    for leaf_id, label, distance in zip(
        leaf_assignments, y, pair_distances, strict=True
    ):
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


def _optimize_leaf_values(
    *,
    group_count: int,
    pair_i: np.ndarray,
    pair_j: np.ndarray,
    pair_distances: np.ndarray,
    cluster_labels: Sequence[str | int],
    leaf_assignments: np.ndarray,
    leaf_values: dict[int, float],
    round_decimals: int,
    min_eps: float,
    max_passes: int = 2,
    max_candidates_per_leaf: int = 24,
) -> tuple[dict[int, float], dict[str, float]]:
    current_values = dict(leaf_values)
    current_metrics = _score_leaf_values(
        group_count=group_count,
        pair_i=pair_i,
        pair_j=pair_j,
        pair_distances=pair_distances,
        cluster_labels=cluster_labels,
        leaf_assignments=leaf_assignments,
        leaf_values=current_values,
    )
    candidate_values = _build_leaf_candidate_values(
        leaf_assignments=leaf_assignments,
        pair_distances=pair_distances,
        leaf_values=current_values,
        round_decimals=round_decimals,
        min_eps=min_eps,
        max_candidates_per_leaf=max_candidates_per_leaf,
    )

    for _ in range(max_passes):
        improved = False
        for leaf_id in sorted(candidate_values):
            best_value = current_values[leaf_id]
            best_metrics = current_metrics
            for candidate_value in candidate_values[leaf_id]:
                if candidate_value == current_values[leaf_id]:
                    continue
                trial_values = dict(current_values)
                trial_values[leaf_id] = candidate_value
                trial_metrics = _score_leaf_values(
                    group_count=group_count,
                    pair_i=pair_i,
                    pair_j=pair_j,
                    pair_distances=pair_distances,
                    cluster_labels=cluster_labels,
                    leaf_assignments=leaf_assignments,
                    leaf_values=trial_values,
                )
                if _is_better_metric_scores(trial_metrics, best_metrics):
                    best_value = candidate_value
                    best_metrics = trial_metrics

            if best_value != current_values[leaf_id]:
                current_values[leaf_id] = best_value
                current_metrics = best_metrics
                improved = True

        if not improved:
            break

    return current_values, current_metrics


def _build_leaf_candidate_values(
    *,
    leaf_assignments: np.ndarray,
    pair_distances: np.ndarray,
    leaf_values: dict[int, float],
    round_decimals: int,
    min_eps: float,
    max_candidates_per_leaf: int,
) -> dict[int, tuple[float, ...]]:
    by_leaf: dict[int, set[float]] = {}
    step = 10 ** (-round_decimals)
    for leaf_id, distance in zip(leaf_assignments, pair_distances, strict=True):
        rounded = round(float(distance), round_decimals)
        leaf_key = int(leaf_id)
        bucket = by_leaf.setdefault(leaf_key, {leaf_values[leaf_key], min_eps})
        bucket.add(max(min_eps, rounded))
        bucket.add(max(min_eps, round(rounded - step, round_decimals)))

    result: dict[int, tuple[float, ...]] = {}
    for leaf_id, values in by_leaf.items():
        ordered = sorted(values)
        if len(ordered) > max_candidates_per_leaf:
            indices = np.linspace(
                0,
                len(ordered) - 1,
                num=max_candidates_per_leaf,
                dtype=int,
            )
            ordered = [ordered[index] for index in sorted(set(indices.tolist()))]
            if leaf_values[leaf_id] not in ordered:
                ordered.append(leaf_values[leaf_id])
                ordered.sort()
        result[leaf_id] = tuple(ordered)
    return result


def _score_leaf_values(
    *,
    group_count: int,
    pair_i: np.ndarray,
    pair_j: np.ndarray,
    pair_distances: np.ndarray,
    cluster_labels: Sequence[str | int],
    leaf_assignments: np.ndarray,
    leaf_values: dict[int, float],
) -> dict[str, float]:
    predicted = _predict_sparse_cluster_labels(
        group_count,
        pair_i,
        pair_j,
        pair_distances,
        leaf_assignments,
        leaf_values,
    )
    return _cluster_pair_metrics(cluster_labels, predicted.tolist())


def _compact_adaptive_tree(
    tree: AdaptiveTree,
    score_tree: ScoreTreeFn,
    initial_metrics: dict[str, float],
) -> tuple[AdaptiveTree, dict[str, float]]:
    current_tree = _remap_tree(tree)
    current_metrics = dict(initial_metrics)

    while True:
        improved = False
        for node_index in _postorder_internal_nodes(current_tree):
            subtree_values = _subtree_leaf_values(
                cast(tuple[TreeNode, ...], current_tree["nodes"]),
                node_index,
            )
            for leaf_value in subtree_values:
                candidate_tree = _replace_subtree_with_leaf(
                    current_tree,
                    node_index,
                    leaf_value,
                )
                candidate_metrics = score_tree(candidate_tree)
                if _is_same_metric_scores(candidate_metrics, current_metrics):
                    current_tree = candidate_tree
                    current_metrics = candidate_metrics
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return current_tree, current_metrics


def _remap_tree(tree: AdaptiveTree) -> AdaptiveTree:
    nodes = cast(tuple[TreeNode, ...], tree["nodes"])
    ordered: list[int] = []
    index_map: dict[int, int] = {}

    def visit(node_index: int) -> None:
        if node_index in index_map:
            return
        index_map[node_index] = len(index_map)
        ordered.append(node_index)
        node = cast(TreeNode, nodes[node_index])
        if "value" not in node:
            visit(cast(int, node["left"]))
            visit(cast(int, node["right"]))

    visit(0)
    remapped_nodes: list[dict[str, object]] = []
    for old_index in ordered:
        node = cast(TreeNode, nodes[old_index])
        if "value" in node:
            remapped_nodes.append({"value": node["value"]})
            continue
        remapped_nodes.append(
            {
                "feature": node["feature"],
                "threshold": node["threshold"],
                "left": index_map[cast(int, node["left"])],
                "right": index_map[cast(int, node["right"])],
            }
        )

    return {
        "features": tree["features"],
        "nodes": tuple(remapped_nodes),
    }


def _postorder_internal_nodes(tree: AdaptiveTree) -> list[int]:
    nodes = cast(tuple[TreeNode, ...], tree["nodes"])
    order: list[int] = []

    def visit(node_index: int) -> None:
        node = cast(TreeNode, nodes[node_index])
        if "value" in node:
            return
        visit(cast(int, node["left"]))
        visit(cast(int, node["right"]))
        order.append(node_index)

    visit(0)
    return order


def _subtree_leaf_values(
    nodes: tuple[TreeNode, ...], node_index: int
) -> tuple[float, ...]:
    node = cast(TreeNode, nodes[node_index])
    if "value" in node:
        return (float(cast(float, node["value"])),)
    values = _subtree_leaf_values(
        nodes, cast(int, node["left"])
    ) + _subtree_leaf_values(nodes, cast(int, node["right"]))
    return tuple(sorted(set(values)))


def _replace_subtree_with_leaf(
    tree: AdaptiveTree,
    node_index: int,
    leaf_value: float,
) -> AdaptiveTree:
    nodes = [dict(node) for node in cast(tuple[TreeNode, ...], tree["nodes"])]
    nodes[node_index] = {"value": leaf_value}
    return _remap_tree({"features": tree["features"], "nodes": tuple(nodes)})


def _is_same_metric_scores(
    left: dict[str, float],
    right: dict[str, float],
    *,
    tol: float = 1e-12,
) -> bool:
    return (
        abs(left["f1"] - right["f1"]) <= tol
        and abs(left["precision"] - right["precision"]) <= tol
        and abs(left["recall"] - right["recall"]) <= tol
    )


def _tree_max_depth(tree: AdaptiveTree) -> int:
    nodes = cast(tuple[TreeNode, ...], tree["nodes"])

    def visit(node_index: int) -> int:
        node = cast(TreeNode, nodes[node_index])
        if "value" in node:
            return 0
        return 1 + max(visit(cast(int, node["left"])), visit(cast(int, node["right"])))

    return visit(0)


def _is_better_metric_scores(
    candidate: dict[str, float],
    current: dict[str, float],
) -> bool:
    if candidate["f1"] != current["f1"]:
        return candidate["f1"] > current["f1"]
    if candidate["precision"] != current["precision"]:
        return candidate["precision"] > current["precision"]
    if candidate["recall"] != current["recall"]:
        return candidate["recall"] > current["recall"]
    return False


def _dense_pair_indices(group_count: int) -> tuple[np.ndarray, np.ndarray]:
    pair_i, pair_j = np.triu_indices(group_count, 1)
    return pair_i.astype(np.int32, copy=False), pair_j.astype(np.int32, copy=False)


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
    predicted = (
        DBSCAN(
            eps=1.0,
            min_samples=1,
            metric="precomputed",
        )
        .fit(normalized)
        .labels_
    )
    return _cluster_pair_metrics(cluster_labels, predicted.tolist())


def _cluster_pair_metrics(
    expected: Sequence[str | int],
    predicted: Sequence[int],
) -> dict[str, float]:
    expected_counts: dict[str | int, int] = {}
    predicted_counts: dict[int, int] = {}
    overlaps: dict[tuple[int, str | int], int] = {}
    for exp_label, pred_label in zip(expected, predicted, strict=True):
        expected_counts[exp_label] = expected_counts.get(exp_label, 0) + 1
        predicted_counts[pred_label] = predicted_counts.get(pred_label, 0) + 1
        key = (pred_label, exp_label)
        overlaps[key] = overlaps.get(key, 0) + 1

    tp = sum(_n_choose_2(count) for count in overlaps.values())
    pred_pairs = sum(_n_choose_2(count) for count in predicted_counts.values())
    gt_pairs = sum(_n_choose_2(count) for count in expected_counts.values())
    fp = pred_pairs - tp
    fn = gt_pairs - tp
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _is_better_fit(
    candidate: AdaptiveEpsFitResult,
    current: AdaptiveEpsFitResult | None,
    *,
    min_precision: float = 0.0,
) -> bool:
    candidate_eligible = candidate.precision >= min_precision
    current_eligible = current is not None and current.precision >= min_precision
    if candidate_eligible != current_eligible:
        return candidate_eligible
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


def _n_choose_2(count: int) -> int:
    return count * (count - 1) // 2


def _adaptive_progress_interval(total_candidates: int) -> int:
    if total_candidates < 1:
        msg = "total_candidates must be >= 1"
        raise ValueError(msg)
    return max(1, total_candidates // 20)


def _fit_adaptive_eps_tree_approx_serial(
    dataset: SparseAdaptiveEpsDataset,
    *,
    feature_tuple: tuple[dict[str, object], ...],
    candidate_params: list[tuple[int, int]],
    round_decimals: int,
    min_eps: float,
    random_state: int,
    started_at: float,
) -> list[AdaptiveEpsFitResult]:
    best: AdaptiveEpsFitResult | None = None
    candidates: list[AdaptiveEpsFitResult] = []
    total_candidates = len(candidate_params)
    progress_interval = _adaptive_progress_interval(total_candidates)

    for index, (max_depth, min_samples_leaf) in enumerate(candidate_params, start=1):
        candidate = _evaluate_approx_candidate(
            dataset,
            feature_tuple=feature_tuple,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            round_decimals=round_decimals,
            min_eps=min_eps,
            random_state=random_state,
        )
        candidates.append(candidate)
        if _is_better_fit(candidate, best):
            best = candidate
            logger.info(
                "Adaptive eps approx: new best at %d/%d (%.1f%%) elapsed=%.2fs F1=%.4f P=%.4f R=%.4f nodes=%d depth=%d min_leaf=%d",
                index,
                total_candidates,
                (index / total_candidates) * 100.0,
                time.perf_counter() - started_at,
                candidate.f1,
                candidate.precision,
                candidate.recall,
                candidate.node_count,
                candidate.max_depth,
                candidate.min_samples_leaf,
            )
        elif index == total_candidates or index % progress_interval == 0:
            current_best = best
            logger.info(
                "Adaptive eps approx: progress %d/%d (%.1f%%) elapsed=%.2fs current F1=%.4f best F1=%.4f",
                index,
                total_candidates,
                (index / total_candidates) * 100.0,
                time.perf_counter() - started_at,
                candidate.f1,
                current_best.f1 if current_best is not None else 0.0,
            )

    assert best is not None
    logger.info(
        "Adaptive eps approx: completed %d candidates in %.2fs. Best F1=%.4f.",
        total_candidates,
        time.perf_counter() - started_at,
        best.f1,
    )
    return candidates


def _fit_adaptive_eps_tree_approx_parallel(
    dataset: SparseAdaptiveEpsDataset,
    *,
    feature_tuple: tuple[dict[str, object], ...],
    candidate_params: list[tuple[int, int]],
    round_decimals: int,
    min_eps: float,
    random_state: int,
    jobs: int,
    started_at: float,
) -> list[AdaptiveEpsFitResult]:
    global _approx_fit_worker_data

    mp_context = multiprocessing.get_context("fork")
    total_candidates = len(candidate_params)
    progress_interval = _adaptive_progress_interval(total_candidates)
    completed = 0
    best: AdaptiveEpsFitResult | None = None
    candidates: list[AdaptiveEpsFitResult] = []
    _approx_fit_worker_data = dataset
    try:
        with ProcessPoolExecutor(max_workers=jobs, mp_context=mp_context) as executor:
            future_to_candidate = {
                executor.submit(
                    _evaluate_approx_candidate_worker,
                    max_depth,
                    min_samples_leaf,
                    feature_tuple,
                    round_decimals,
                    min_eps,
                    random_state,
                ): (max_depth, min_samples_leaf)
                for max_depth, min_samples_leaf in candidate_params
            }
            for future in as_completed(future_to_candidate):
                candidate = future.result()
                candidates.append(candidate)
                completed += 1
                if _is_better_fit(candidate, best):
                    best = candidate
                    logger.info(
                        "Adaptive eps approx: new best at %d/%d (%.1f%%) elapsed=%.2fs F1=%.4f P=%.4f R=%.4f nodes=%d depth=%d min_leaf=%d",
                        completed,
                        total_candidates,
                        (completed / total_candidates) * 100.0,
                        time.perf_counter() - started_at,
                        candidate.f1,
                        candidate.precision,
                        candidate.recall,
                        candidate.node_count,
                        candidate.max_depth,
                        candidate.min_samples_leaf,
                    )
                elif (
                    completed == total_candidates or completed % progress_interval == 0
                ):
                    current_best = best
                    logger.info(
                        "Adaptive eps approx: progress %d/%d (%.1f%%) elapsed=%.2fs current F1=%.4f best F1=%.4f",
                        completed,
                        total_candidates,
                        (completed / total_candidates) * 100.0,
                        time.perf_counter() - started_at,
                        candidate.f1,
                        current_best.f1 if current_best is not None else 0.0,
                    )
    finally:
        _approx_fit_worker_data = None

    if best is None:
        msg = "no adaptive-eps candidate could be fit"
        raise RuntimeError(msg)
    logger.info(
        "Adaptive eps approx: completed %d candidates in %.2fs. Best F1=%.4f.",
        total_candidates,
        time.perf_counter() - started_at,
        best.f1,
    )
    return candidates


def _select_best_fit(
    candidates: Sequence[AdaptiveEpsFitResult],
    *,
    min_precision: float = 0.0,
) -> AdaptiveEpsFitResult:
    best: AdaptiveEpsFitResult | None = None
    for candidate in candidates:
        if _is_better_fit(candidate, best, min_precision=min_precision):
            best = candidate
    assert best is not None
    return best


def _rerank_approx_candidates_exact(
    candidates: Sequence[AdaptiveEpsFitResult],
    *,
    rerank_top_k: int,
    rule_groups: list[dict[str, Any]],
    base_distances: Any,
    cluster_labels: Sequence[str | int],
    min_precision: float,
) -> AdaptiveEpsFitResult:
    finalists = _select_exact_rerank_finalists(candidates, rerank_top_k)
    logger.info(
        "Adaptive eps approx: starting exact rerank for %d finalist(s) (top_k=%d).",
        len(finalists),
        rerank_top_k,
    )

    best: AdaptiveEpsFitResult | None = None
    for index, candidate in enumerate(finalists, start=1):
        logger.info(
            "Adaptive eps approx: exact rerank finalist %d/%d (approx F1=%.4f P=%.4f R=%.4f nodes=%d depth=%d min_leaf=%d).",
            index,
            len(finalists),
            candidate.f1,
            candidate.precision,
            candidate.recall,
            candidate.node_count,
            candidate.max_depth,
            candidate.min_samples_leaf,
        )
        exact_metrics = _score_adaptive_tree(
            rule_groups,
            base_distances,
            cluster_labels,
            candidate.tree,
        )
        logger.info(
            "Adaptive eps approx: finalist %d/%d exact replay F1=%.4f P=%.4f R=%.4f before compaction.",
            index,
            len(finalists),
            exact_metrics["f1"],
            exact_metrics["precision"],
            exact_metrics["recall"],
        )
        compact_tree, compact_metrics = _compact_adaptive_tree(
            candidate.tree,
            lambda candidate_tree: _score_adaptive_tree(
                rule_groups,
                base_distances,
                cluster_labels,
                candidate_tree,
            ),
            exact_metrics,
        )
        logger.info(
            "Adaptive eps approx: finalist %d/%d compacted to %d node(s), final exact F1=%.4f P=%.4f R=%.4f.",
            index,
            len(finalists),
            len(cast(tuple[TreeNode, ...], compact_tree["nodes"])),
            compact_metrics["f1"],
            compact_metrics["precision"],
            compact_metrics["recall"],
        )
        exact_candidate = AdaptiveEpsFitResult(
            tree=compact_tree,
            precision=compact_metrics["precision"],
            recall=compact_metrics["recall"],
            f1=compact_metrics["f1"],
            node_count=len(cast(tuple[TreeNode, ...], compact_tree["nodes"])),
            max_depth=_tree_max_depth(compact_tree),
            min_samples_leaf=candidate.min_samples_leaf,
        )
        if _is_better_reranked_fit(
            exact_candidate,
            best,
            min_precision=min_precision,
        ):
            best = exact_candidate
            logger.info(
                "Adaptive eps approx: exact rerank new best at finalist %d/%d F1=%.4f P=%.4f R=%.4f nodes=%d.",
                index,
                len(finalists),
                exact_candidate.f1,
                exact_candidate.precision,
                exact_candidate.recall,
                exact_candidate.node_count,
            )

    assert best is not None
    return best


def _select_exact_rerank_finalists(
    candidates: Sequence[AdaptiveEpsFitResult],
    rerank_top_k: int,
) -> list[AdaptiveEpsFitResult]:
    limit = max(1, min(rerank_top_k, len(candidates)))
    by_f1 = sorted(candidates, key=_approx_rank_key_f1, reverse=True)[:limit]
    by_precision = sorted(candidates, key=_approx_rank_key_precision, reverse=True)[
        :limit
    ]

    merged: dict[str, AdaptiveEpsFitResult] = {}
    for candidate in [*by_f1, *by_precision]:
        merged[repr(candidate.tree)] = candidate
    return list(merged.values())


def _approx_rank_key_f1(
    candidate: AdaptiveEpsFitResult,
) -> tuple[float, float, float, int, int, int]:
    return (
        candidate.f1,
        candidate.precision,
        candidate.recall,
        -candidate.node_count,
        -candidate.max_depth,
        candidate.min_samples_leaf,
    )


def _approx_rank_key_precision(
    candidate: AdaptiveEpsFitResult,
) -> tuple[float, float, float, int, int, int]:
    return (
        candidate.precision,
        candidate.f1,
        candidate.recall,
        -candidate.node_count,
        -candidate.max_depth,
        candidate.min_samples_leaf,
    )


def _is_better_reranked_fit(
    candidate: AdaptiveEpsFitResult,
    current: AdaptiveEpsFitResult | None,
    *,
    min_precision: float = 0.0,
) -> bool:
    candidate_eligible = candidate.precision >= min_precision
    current_eligible = current is not None and current.precision >= min_precision
    if candidate_eligible != current_eligible:
        return candidate_eligible
    if current is None:
        return True
    if candidate.precision != current.precision:
        return candidate.precision > current.precision
    if candidate.f1 != current.f1:
        return candidate.f1 > current.f1
    if candidate.recall != current.recall:
        return candidate.recall > current.recall
    if candidate.node_count != current.node_count:
        return candidate.node_count < current.node_count
    if candidate.max_depth != current.max_depth:
        return candidate.max_depth < current.max_depth
    return candidate.min_samples_leaf > current.min_samples_leaf


def _evaluate_approx_candidate_worker(
    max_depth: int,
    min_samples_leaf: int,
    feature_tuple: tuple[dict[str, object], ...],
    round_decimals: int,
    min_eps: float,
    random_state: int,
) -> AdaptiveEpsFitResult:
    if _approx_fit_worker_data is None:
        msg = "approximate adaptive-eps worker data is not initialized"
        raise RuntimeError(msg)
    return _evaluate_approx_candidate(
        _approx_fit_worker_data,
        feature_tuple=feature_tuple,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        round_decimals=round_decimals,
        min_eps=min_eps,
        random_state=random_state,
    )


def _evaluate_approx_candidate(
    dataset: SparseAdaptiveEpsDataset,
    *,
    feature_tuple: tuple[dict[str, object], ...],
    max_depth: int,
    min_samples_leaf: int,
    round_decimals: int,
    min_eps: float,
    random_state: int,
) -> AdaptiveEpsFitResult:
    classifier = DecisionTreeClassifier(
        max_depth=int(max_depth),
        min_samples_leaf=int(min_samples_leaf),
        random_state=random_state,
    )
    classifier.fit(dataset.X, dataset.y)
    leaf_assignments = classifier.apply(dataset.X)
    leaf_values = _derive_leaf_eps_values(
        leaf_assignments,
        dataset.y,
        dataset.pair_distances,
        round_decimals=round_decimals,
        min_eps=min_eps,
    )
    leaf_values, metrics = _optimize_leaf_values(
        group_count=dataset.group_count,
        pair_i=dataset.pair_i,
        pair_j=dataset.pair_j,
        pair_distances=dataset.pair_distances,
        cluster_labels=dataset.cluster_labels,
        leaf_assignments=leaf_assignments,
        leaf_values=leaf_values,
        round_decimals=round_decimals,
        min_eps=min_eps,
    )
    tree = _classifier_to_adaptive_eps_tree_from_leaf_values(
        classifier,
        feature_tuple,
        leaf_values,
        round_decimals=round_decimals,
    )
    return AdaptiveEpsFitResult(
        tree=tree,
        precision=metrics["precision"],
        recall=metrics["recall"],
        f1=metrics["f1"],
        node_count=int(classifier.tree_.node_count),
        max_depth=int(max_depth),
        min_samples_leaf=int(min_samples_leaf),
    )


def _classifier_to_adaptive_eps_tree_from_leaf_values(
    classifier: DecisionTreeClassifier,
    feature_defs: tuple[dict[str, object], ...],
    leaf_values: dict[int, float],
    *,
    round_decimals: int,
) -> dict[str, object]:
    tree_ = classifier.tree_
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


def _predict_sparse_cluster_labels(
    group_count: int,
    pair_i: np.ndarray,
    pair_j: np.ndarray,
    pair_distances: np.ndarray,
    leaf_assignments: np.ndarray,
    leaf_values: dict[int, float],
) -> np.ndarray:
    normalized = np.asarray(
        [
            float(distance) / float(leaf_values[int(leaf_id)])
            for distance, leaf_id in zip(pair_distances, leaf_assignments, strict=True)
        ],
        dtype=np.float32,
    )
    uf = _UnionFind(group_count)
    active = normalized <= 1.0
    for left, right in zip(pair_i[active], pair_j[active], strict=True):
        uf.union(int(left), int(right))
    return uf.labels()


def _resolve_approx_jobs(jobs: int) -> int:
    if jobs == 0:
        jobs = os.cpu_count() or 1
    if jobs <= 1:
        return 1
    if os.name != "posix":
        logger.warning(
            "Adaptive eps approx multiprocessing requires POSIX fork; falling back to 1 job."
        )
        return 1
    return jobs


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, item: int) -> int:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1

    def labels(self) -> np.ndarray:
        root_to_label: dict[int, int] = {}
        labels = np.empty(len(self.parent), dtype=np.int32)
        for index in range(len(self.parent)):
            root = self.find(index)
            label = root_to_label.get(root)
            if label is None:
                label = len(root_to_label)
                root_to_label[root] = label
            labels[index] = label
        return labels
