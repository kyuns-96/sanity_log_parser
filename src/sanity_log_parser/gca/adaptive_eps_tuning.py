from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from sanity_log_parser.clustering.ai.clusterer import (
    _compute_distance_matrix,
    _prepare_embedding_components,
)
from sanity_log_parser.gca.adaptive_eps_fit import AdaptiveEpsFitResult, fit_adaptive_eps_tree
from sanity_log_parser.gca.config import GcaConfig, get_gca_rule_config


DEFAULT_ADAPTIVE_EPS_FEATURES_V1: tuple[dict[str, object], ...] = (
    {"kind": "path_tfidf_char_wb", "ngram_range": [3, 6]},
    {"kind": "suffix_similarity", "max_shift": 3, "decay": 0.65},
    {"kind": "level_jaccard", "levels": [-4, -3]},
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
) -> AdaptiveEpsFitResult:
    rule_groups, cluster_labels = extract_rule_logic_groups(
        logic_data=logic_data,
        ground_truth_data=ground_truth_data,
        rule_id=rule_id,
    )
    base_distances = compute_rule_base_distance_matrix(
        rule_groups=rule_groups,
        gca_config=gca_config,
        rule_id=rule_id,
        embed_fn=embed_fn,
    )
    return fit_adaptive_eps_tree(
        rule_groups,
        base_distances,
        cluster_labels,
        feature_defs,
        max_depth_candidates=max_depth_candidates,
        min_samples_leaf_candidates=min_samples_leaf_candidates,
        round_decimals=round_decimals,
        min_eps=min_eps,
    )


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
        if mode != "jaccard":
            start = len(batch_texts)
            batch_texts.extend(var_keys)
            var_slices.append((start, len(batch_texts), mask, var_keys, mode))
        else:
            var_slices.append((-1, -1, mask, var_keys, mode))

    all_embeddings = np.asarray(embed_fn(batch_texts))
    template_embs = all_embeddings[: len(template_keys)]

    var_embeddings: list[tuple[Any, list[bool], list[str]]] = []
    for start, end, mask, var_keys, mode in var_slices:
        if mode != "jaccard":
            var_embeddings.append((all_embeddings[start:end], mask, var_keys))
        else:
            var_embeddings.append((None, mask, var_keys))

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
