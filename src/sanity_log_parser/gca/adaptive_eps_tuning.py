from __future__ import annotations

import json
import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from sanity_log_parser.clustering.ai.pairwise_tree import (
    build_pair_feature_dataset_for_edges,
    _extract_primary_path,
    _segment_token_sequence,
)
from sanity_log_parser.clustering.ai.clusterer import (
    _compute_distance_matrix,
    _prepare_embedding_components,
)
from sanity_log_parser.clustering.ai.weights import select_levels
from sanity_log_parser.gca.adaptive_eps_fit import (
    AdaptiveEpsFitResult,
    SparseAdaptiveEpsDataset,
    fit_adaptive_eps_tree,
    fit_adaptive_eps_tree_approx,
)
from sanity_log_parser.gca.config import GcaConfig, get_gca_rule_config

logger = logging.getLogger(__name__)

_APPROX_BUCKET_FANOUT = 8
_APPROX_MAX_EDGES_PER_NODE = 32


DEFAULT_ADAPTIVE_EPS_FEATURES_V1: tuple[dict[str, object], ...] = (
    {"kind": "path_tfidf_char_wb", "ngram_range": [3, 6]},
    {"kind": "suffix_similarity", "max_shift": 3, "decay": 0.65},
    {"kind": "level_exact", "levels": [-4, -3]},
    {"kind": "path_length_diff"},
)


def load_feature_defs(features_json: str | None) -> tuple[dict[str, object], ...]:
    if features_json is None:
        return tuple(deepcopy(feature) for feature in DEFAULT_ADAPTIVE_EPS_FEATURES_V1)

    raw = json.loads(Path(features_json).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw or not all(isinstance(item, dict) for item in raw):
        msg = f"Feature definition file '{features_json}' must contain a non-empty JSON list of objects."
        raise ValueError(msg)
    return tuple(deepcopy(item) for item in raw)


def fit_adaptive_eps_rule(
    *,
    logic_data: dict[str, Any],
    ground_truth_data: dict[str, list[list[str]]],
    gca_config: GcaConfig,
    rule_id: str,
    embed_fn: Callable[[list[str]], Any],
    feature_defs: Sequence[dict[str, object]],
    max_depth_candidates: Sequence[int] = tuple(range(1, 8)),
    min_samples_leaf_candidates: Sequence[int] = tuple(range(1, 16)),
    round_decimals: int = 3,
    min_eps: float = 0.001,
    fit_mode: str = "exact",
    jobs: int = 1,
) -> AdaptiveEpsFitResult:
    started_at = time.perf_counter()
    rule_groups, cluster_labels = extract_rule_logic_groups(
        logic_data=logic_data,
        ground_truth_data=ground_truth_data,
        rule_id=rule_id,
    )
    logger.info(
        "Adaptive eps fit '%s': mode=%s, groups=%d, features=%d.",
        rule_id,
        fit_mode,
        len(rule_groups),
        len(feature_defs),
    )
    if fit_mode == "exact":
        base_distances = compute_rule_base_distance_matrix(
            rule_groups=rule_groups,
            gca_config=gca_config,
            rule_id=rule_id,
            embed_fn=embed_fn,
        )
        result = fit_adaptive_eps_tree(
            rule_groups,
            base_distances,
            cluster_labels,
            feature_defs,
            max_depth_candidates=max_depth_candidates,
            min_samples_leaf_candidates=min_samples_leaf_candidates,
            round_decimals=round_decimals,
            min_eps=min_eps,
        )
    elif fit_mode == "approx":
        dataset = build_sparse_adaptive_eps_dataset(
            rule_groups=rule_groups,
            cluster_labels=cluster_labels,
            feature_defs=feature_defs,
            gca_config=gca_config,
            rule_id=rule_id,
            embed_fn=embed_fn,
        )
        result = fit_adaptive_eps_tree_approx(
            dataset,
            feature_defs,
            max_depth_candidates=max_depth_candidates,
            min_samples_leaf_candidates=min_samples_leaf_candidates,
            round_decimals=round_decimals,
            min_eps=min_eps,
            jobs=jobs,
        )
    else:
        msg = f"Unsupported adaptive eps fit mode: {fit_mode}"
        raise ValueError(msg)

    logger.info(
        "Adaptive eps fit '%s': completed in %.2fs.",
        rule_id,
        time.perf_counter() - started_at,
    )
    return result


def extract_rule_logic_groups(
    *,
    logic_data: dict[str, Any],
    ground_truth_data: dict[str, list[list[str]]],
    rule_id: str,
) -> tuple[list[dict[str, Any]], list[int]]:
    groups = logic_data.get("groups", [])
    if not isinstance(groups, list):
        msg = "logic.json must contain a top-level 'groups' list"
        raise ValueError(msg)

    raw_rule_groups = [
        group
        for group in groups
        if group.get("group_type") == "logic" and group.get("rule_id") == rule_id
    ]
    if not raw_rule_groups:
        msg = f"No logic groups found for rule '{rule_id}'."
        raise ValueError(msg)

    gt_clusters = ground_truth_data.get(rule_id)
    if not gt_clusters:
        msg = f"No ground truth clusters found for rule '{rule_id}'."
        raise ValueError(msg)

    group_to_cluster: dict[str, int] = {}
    for cluster_index, cluster in enumerate(gt_clusters):
        if not isinstance(cluster, list) or not cluster:
            msg = f"Ground truth cluster {cluster_index} for rule '{rule_id}' must be a non-empty list."
            raise ValueError(msg)
        for logic_group_id in cluster:
            if logic_group_id in group_to_cluster:
                msg = f"Logic group '{logic_group_id}' appears multiple times in ground truth for rule '{rule_id}'."
                raise ValueError(msg)
            group_to_cluster[logic_group_id] = cluster_index

    rule_groups: list[dict[str, Any]] = []
    cluster_labels: list[int] = []
    missing: list[str] = []
    extra = set(group_to_cluster)
    for group in raw_rule_groups:
        group_id = group.get("group_id")
        if not isinstance(group_id, str):
            msg = f"Rule '{rule_id}' has a logic group without a valid group_id."
            raise ValueError(msg)
        cluster_index = group_to_cluster.get(group_id)
        if cluster_index is None:
            missing.append(group_id)
            continue
        extra.discard(group_id)
        rule_groups.append(
            {
                "group_id": group_id,
                "template": group["representative_template"],
                "pattern": group["representative_pattern"],
                "count": group["total_count"],
                "members": [{"raw_log": raw_log} for raw_log in group.get("original_logs", [])],
            }
        )
        cluster_labels.append(cluster_index)

    if missing:
        msg = (
            f"Ground truth for rule '{rule_id}' is missing {len(missing)} logic group(s): "
            f"{', '.join(sorted(missing)[:10])}"
        )
        raise ValueError(msg)
    if extra:
        msg = (
            f"Ground truth for rule '{rule_id}' references unknown logic group(s): "
            f"{', '.join(sorted(extra)[:10])}"
        )
        raise ValueError(msg)

    return rule_groups, cluster_labels


def compute_rule_base_distance_matrix(
    *,
    rule_groups: list[dict[str, Any]],
    gca_config: GcaConfig,
    rule_id: str,
    embed_fn: Callable[[list[str]], Any],
) -> Any:
    rule_config = get_gca_rule_config(gca_config, rule_id)
    components, var_weights, var_modes = _prepare_embedding_components(
        rule_groups,
        rule_config,
        gca_config.default_variable_weight,
    )

    batch_texts: list[str] = [component["template"] for component in components]
    template_keys = [component["template"] for component in components]
    var_slices: list[tuple[int, int, list[bool], list[str], str]] = []

    max_vars = max(len(component["variables"]) for component in components) if components else 0
    for index in range(max_vars):
        mode = var_modes[index] if index < len(var_modes) else "embedding"
        mask: list[bool] = []
        var_keys: list[str] = []
        for component in components:
            if index < len(component["variables"]) and component["variables"][index].strip():
                mask.append(True)
                var_keys.append(component["variables"][index])
            else:
                mask.append(False)
                var_keys.append("_")
        start = len(batch_texts)
        batch_texts.extend(var_keys)
        var_slices.append((start, len(batch_texts), mask, var_keys, mode))

    all_embeddings = np.asarray(embed_fn(batch_texts))
    template_embs = all_embeddings[: len(template_keys)]

    var_embeddings: list[tuple[Any, list[bool], list[str]]] = []
    for start, end, mask, var_keys, mode in var_slices:
        var_embeddings.append((all_embeddings[start:end], mask, var_keys))

    return _compute_distance_matrix(
        len(components),
        template_embs,
        template_keys,
        var_embeddings,
        rule_config,
        gca_config.default_variable_weight,
        var_weights=var_weights,
        var_modes=var_modes,
    )


def update_rule_config_with_adaptive_eps_tree(
    *,
    raw_config: dict[str, Any],
    rule_id: str,
    tree: dict[str, object],
) -> tuple[dict[str, Any], bool]:
    updated = deepcopy(raw_config)
    rules = updated.setdefault("rules", {})
    if not isinstance(rules, dict):
        msg = "Top-level 'rules' must be a JSON object."
        raise ValueError(msg)

    rule_entry = rules.setdefault(rule_id, {})
    if not isinstance(rule_entry, dict):
        msg = f"Rule '{rule_id}' entry must be a JSON object."
        raise ValueError(msg)

    removed_pairwise = "pairwise_tree" in rule_entry
    rule_entry.pop("pairwise_tree", None)
    rule_entry["eps"] = 1.0
    rule_entry["adaptive_eps_tree"] = tree
    return updated, removed_pairwise


def build_sparse_adaptive_eps_dataset(
    *,
    rule_groups: list[dict[str, Any]],
    cluster_labels: list[int],
    feature_defs: Sequence[dict[str, object]],
    gca_config: GcaConfig,
    rule_id: str,
    embed_fn: Callable[[list[str]], Any],
) -> SparseAdaptiveEpsDataset:
    pair_i, pair_j, protected_edges = _build_sparse_candidate_edges(
        rule_groups,
        cluster_labels,
        feature_defs,
    )
    if pair_i.size == 0:
        msg = f"No sparse candidate edges generated for rule '{rule_id}'."
        raise ValueError(msg)

    base_distances = compute_rule_base_distances_for_edges(
        rule_groups=rule_groups,
        pair_i=pair_i,
        pair_j=pair_j,
        gca_config=gca_config,
        rule_id=rule_id,
        embed_fn=embed_fn,
    )
    initial_edge_count = int(pair_i.size)
    pair_i, pair_j, base_distances = _prune_sparse_candidate_edges(
        len(rule_groups),
        pair_i,
        pair_j,
        base_distances,
        protected_edges,
    )
    logger.info(
        "Adaptive eps approx '%s': sparse edges %d -> %d after pruning.",
        rule_id,
        initial_edge_count,
        int(pair_i.size),
    )
    features = build_pair_feature_dataset_for_edges(
        rule_groups,
        tuple(dict(feature) for feature in feature_defs),
        pair_i,
        pair_j,
    )
    labels_arr = np.asarray(cluster_labels, dtype=np.int32)
    edge_labels = (labels_arr[pair_i] == labels_arr[pair_j]).astype(np.int32)
    return SparseAdaptiveEpsDataset(
        group_count=len(rule_groups),
        pair_i=pair_i,
        pair_j=pair_j,
        X=features,
        y=edge_labels,
        pair_distances=base_distances.astype(np.float32, copy=False),
        cluster_labels=tuple(int(label) for label in cluster_labels),
        initial_edge_count=initial_edge_count,
        retained_edge_count=int(pair_i.size),
    )


def compute_rule_base_distances_for_edges(
    *,
    rule_groups: list[dict[str, Any]],
    pair_i: np.ndarray,
    pair_j: np.ndarray,
    gca_config: GcaConfig,
    rule_id: str,
    embed_fn: Callable[[list[str]], Any],
) -> np.ndarray:
    rule_config = get_gca_rule_config(gca_config, rule_id)
    components, var_weights, _var_modes = _prepare_embedding_components(
        rule_groups,
        rule_config,
        gca_config.default_variable_weight,
    )

    batch_texts: list[str] = [component["template"] for component in components]
    template_keys = [component["template"] for component in components]
    var_slices: list[tuple[int, int, list[bool], list[str]]] = []

    max_vars = max(len(component["variables"]) for component in components) if components else 0
    for index in range(max_vars):
        mask: list[bool] = []
        var_keys: list[str] = []
        for component in components:
            if index < len(component["variables"]) and component["variables"][index].strip():
                mask.append(True)
                var_keys.append(component["variables"][index])
            else:
                mask.append(False)
                var_keys.append("_")
        start = len(batch_texts)
        batch_texts.extend(var_keys)
        var_slices.append((start, len(batch_texts), mask, var_keys))

    all_embeddings = np.asarray(embed_fn(batch_texts), dtype=np.float32)
    template_embs = _normalize_rows(all_embeddings[: len(template_keys)])
    template_dist = _cosine_distance_for_edges(template_embs, pair_i, pair_j)

    template_weight = float(rule_config.template_weight)
    weight_sum = np.full(pair_i.size, template_weight, dtype=np.float32)
    numerator = template_weight * template_dist
    uniform_num = template_dist.copy()
    uniform_count = np.ones(pair_i.size, dtype=np.float32)

    for slot_index, (start, end, mask, _var_keys) in enumerate(var_slices):
        slot_weight = float(var_weights[slot_index])
        if slot_weight == 0:
            continue
        var_embs = _normalize_rows(all_embeddings[start:end])
        mask_arr = np.asarray(mask, dtype=bool)
        pair_mask = (mask_arr[pair_i] & mask_arr[pair_j]).astype(np.float32)
        var_dist = _cosine_distance_for_edges(var_embs, pair_i, pair_j)
        weight_sum += slot_weight * pair_mask
        numerator += slot_weight * var_dist * pair_mask
        uniform_num += var_dist * pair_mask
        uniform_count += pair_mask

    zero_weight = weight_sum == 0
    safe_weight = np.where(zero_weight, 1.0, weight_sum)
    safe_active = np.maximum(uniform_count, 1.0)
    return np.where(zero_weight, uniform_num / safe_active, numerator / safe_weight).astype(
        np.float32,
        copy=False,
    )


def _build_sparse_candidate_edges(
    rule_groups: list[dict[str, Any]],
    cluster_labels: Sequence[int],
    feature_defs: Sequence[dict[str, object]],
) -> tuple[np.ndarray, np.ndarray, set[tuple[int, int]]]:
    path_texts = [_extract_primary_path(group["pattern"]) for group in rule_groups]
    segment_sequences = [_segment_token_sequence(path) for path in path_texts]
    group_ids = [str(group.get("group_id", f"{index:06d}")) for index, group in enumerate(rule_groups)]

    buckets: dict[tuple[str, object], list[int]] = {}
    for index, (path, segments) in enumerate(zip(path_texts, segment_sequences, strict=True)):
        if segments:
            _append_bucket(buckets, ("last1", " ".join(segments[-1])), index)
            if len(segments) >= 2:
                _append_bucket(
                    buckets,
                    ("last2", f"{' '.join(segments[-2])}/{' '.join(segments[-1])}"),
                    index,
                )
        _append_bucket(buckets, ("length", len(segments)), index)
        for feature in feature_defs:
            levels = feature.get("levels")
            if not levels:
                continue
            selected = select_levels(path, list(levels))
            if selected:
                _append_bucket(buckets, ("levels", tuple(levels), selected), index)

    edge_set: set[tuple[int, int]] = set()
    for bucket_indices in buckets.values():
        ordered = sorted(dict.fromkeys(bucket_indices), key=lambda idx: (group_ids[idx], idx))
        for offset, left in enumerate(ordered):
            for right in ordered[offset + 1 : offset + 1 + _APPROX_BUCKET_FANOUT]:
                edge = (left, right) if left < right else (right, left)
                edge_set.add(edge)

    protected_edges: set[tuple[int, int]] = set()
    clusters: dict[int, list[int]] = {}
    for index, label in enumerate(cluster_labels):
        clusters.setdefault(int(label), []).append(index)
    for indices in clusters.values():
        ordered = sorted(indices, key=lambda idx: (group_ids[idx], idx))
        for left, right in zip(ordered, ordered[1:], strict=False):
            edge = (left, right) if left < right else (right, left)
            edge_set.add(edge)
            protected_edges.add(edge)

    if not edge_set:
        return (
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.int32),
            protected_edges,
        )

    ordered_edges = sorted(edge_set)
    return (
        np.asarray([edge[0] for edge in ordered_edges], dtype=np.int32),
        np.asarray([edge[1] for edge in ordered_edges], dtype=np.int32),
        protected_edges,
    )


def _prune_sparse_candidate_edges(
    group_count: int,
    pair_i: np.ndarray,
    pair_j: np.ndarray,
    base_distances: np.ndarray,
    protected_edges: set[tuple[int, int]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if pair_i.size == 0:
        return pair_i, pair_j, base_distances

    ranks: list[list[tuple[int, int, float, int]]] = [[] for _ in range(group_count)]
    for edge_index, (left, right, distance) in enumerate(
        zip(pair_i, pair_j, base_distances, strict=True)
    ):
        edge = (int(left), int(right))
        priority = 0 if edge in protected_edges else 1
        ranks[int(left)].append((priority, int(right), float(distance), edge_index))
        ranks[int(right)].append((priority, int(left), float(distance), edge_index))

    top_per_node: list[set[int]] = []
    for incident in ranks:
        incident.sort(key=lambda item: (item[0], item[2], item[1], item[3]))
        top_per_node.append({item[3] for item in incident[:_APPROX_MAX_EDGES_PER_NODE]})

    keep = np.zeros(pair_i.size, dtype=bool)
    for edge_index, (left, right) in enumerate(zip(pair_i, pair_j, strict=True)):
        if edge_index in top_per_node[int(left)] and edge_index in top_per_node[int(right)]:
            keep[edge_index] = True

    if not np.any(keep):
        msg = "Sparse adaptive-eps pruning removed all candidate edges."
        raise ValueError(msg)
    return pair_i[keep], pair_j[keep], base_distances[keep]


def _append_bucket(
    buckets: dict[tuple[str, object], list[int]],
    key: tuple[str, object] | tuple[str, object, object],
    index: int,
) -> None:
    buckets.setdefault(key, []).append(index)


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def _cosine_distance_for_edges(
    normalized_vectors: np.ndarray,
    pair_i: np.ndarray,
    pair_j: np.ndarray,
) -> np.ndarray:
    sims = np.sum(
        normalized_vectors[pair_i] * normalized_vectors[pair_j],
        axis=1,
        dtype=np.float32,
    )
    np.clip(sims, -1.0, 1.0, out=sims)
    return (1.0 - sims).astype(np.float32, copy=False)
