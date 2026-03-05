"""Compute and display pairwise distance matrices for GCA rule tuning."""

from __future__ import annotations

import json
import re
from itertools import combinations
from pathlib import Path
from typing import Any

from sanity_log_parser.gca.config import (
    GcaConfig,
    VariableConfig,
    get_gca_rule_config,
)
from sanity_log_parser.clustering.ai.clusterer import (
    _prepare_embedding_components,
    _compute_distance_matrix,
)

_SLOT_SPLIT_RE = re.compile(r"\s+/\s+")


def compute_distances(
    logic_path: str | Path,
    rule_id: str,
    gca_config: GcaConfig,
    embed_fn: Any,
    ground_truth: dict[str, list[list[str]]] | None = None,
) -> dict[str, Any]:
    """Compute pairwise distances for a single rule.

    Parameters
    ----------
    logic_path : path to logic.json (AI-off output)
    rule_id : which rule to analyze
    gca_config : loaded GCA config
    embed_fn : callable(list[str]) -> ndarray of shape (N, D)
    ground_truth : optional ground truth for same-cluster annotation

    Returns a dict with keys: rule_id, config_summary, groups, pairs, eps,
    and level_analysis (per-variable hierarchy decomposition).
    """
    import numpy as np

    logic_data = _load_json(logic_path)
    groups_raw = logic_data.get("groups", [])

    # Filter to the requested rule's logic groups
    rule_groups = [
        g for g in groups_raw
        if g.get("rule_id") == rule_id and g.get("group_type") == "logic"
    ]

    if not rule_groups:
        return {
            "rule_id": rule_id,
            "error": f"No logic groups found for rule '{rule_id}'",
        }

    rule_config = get_gca_rule_config(gca_config, rule_id)

    # Adapt schema_v2 groups to internal clusterer format
    internal_groups = [
        {
            "template": g["representative_template"],
            "pattern": g["representative_pattern"],
            "count": g["total_count"],
            "members": [{"raw_log": rl} for rl in g.get("original_logs", [])],
        }
        for g in rule_groups
    ]
    group_ids = [g["group_id"] for g in rule_groups]

    # Prepare embedding components
    components, var_weights = _prepare_embedding_components(
        internal_groups, rule_config, gca_config.default_variable_weight
    )

    # Collect all texts for embedding
    batch_texts: list[str] = []
    t_keys: list[str] = [c["template"] for c in components]
    batch_texts.extend(t_keys)

    n = len(components)
    max_vars = max(len(c["variables"]) for c in components) if components else 0

    var_slices: list[tuple[int, int, list[bool], list[str]]] = []
    for i in range(max_vars):
        v_start = len(batch_texts)
        mask: list[bool] = []
        v_keys: list[str] = []
        for c in components:
            if i < len(c["variables"]) and c["variables"][i].strip():
                batch_texts.append(c["variables"][i])
                mask.append(True)
                v_keys.append(c["variables"][i])
            else:
                batch_texts.append("_")
                mask.append(False)
                v_keys.append("_")
        var_slices.append((v_start, len(batch_texts), mask, v_keys))

    # Embed
    all_embs = np.asarray(embed_fn(batch_texts))

    # Slice embeddings
    template_embs = all_embs[: len(t_keys)]
    var_embeddings: list[tuple[Any, list[bool], list[str]]] = []
    for v_start, v_end, mask, v_keys in var_slices:
        var_embeddings.append((all_embs[v_start:v_end], mask, v_keys))

    # Compute distance matrix
    dist_matrix = _compute_distance_matrix(
        n,
        template_embs,
        t_keys,
        var_embeddings,
        rule_config,
        gca_config.default_variable_weight,
        var_weights=var_weights,
    )

    # Build ground-truth cluster lookup (group_id -> cluster_index)
    gt_same: set[tuple[str, str]] | None = None
    gt_cluster_map: dict[str, int] | None = None
    if ground_truth and rule_id in ground_truth:
        gt_same = set()
        gt_cluster_map = {}
        for ci, cluster in enumerate(ground_truth[rule_id]):
            ids = sorted(cluster)
            for gid in ids:
                gt_cluster_map[gid] = ci
            for a, b in combinations(ids, 2):
                gt_same.add((a, b))

    # Build sorted pairs list
    pairs: list[dict[str, Any]] = []
    for i in range(n):
        for j in range(i + 1, n):
            pair_info: dict[str, Any] = {
                "a": group_ids[i],
                "b": group_ids[j],
                "distance": round(float(dist_matrix[i, j]), 6),
                "merge": float(dist_matrix[i, j]) <= rule_config.eps,
            }
            if gt_same is not None:
                key = tuple(sorted([group_ids[i], group_ids[j]]))
                pair_info["gt_same_cluster"] = key in gt_same
            pairs.append(pair_info)

    pairs.sort(key=lambda p: p["distance"])

    # Config summary (use original variable count, not expanded slots)
    orig_max_vars = max(
        len(_SLOT_SPLIT_RE.split(g["representative_pattern"].strip()))
        for g in rule_groups
    ) if rule_groups else 0
    var_summary: dict[str, Any] = {}
    for idx in range(orig_max_vars):
        vc = rule_config.variables.get(
            idx, VariableConfig(weight=gca_config.default_variable_weight)
        )
        entry: dict[str, Any] = {"weight": vc.weight}
        if vc.levels is not None:
            entry["levels"] = vc.levels
        if vc.level_weights is not None:
            entry["level_weights"] = {str(k): v for k, v in vc.level_weights.items()}
        var_summary[str(idx)] = entry

    # Level analysis: decompose each variable by hierarchy level
    level_analysis = _analyze_variable_levels(
        rule_groups, group_ids, gt_cluster_map
    )

    return {
        "rule_id": rule_id,
        "eps": rule_config.eps,
        "template_weight": rule_config.template_weight,
        "variables": var_summary,
        "n_groups": n,
        "groups": [
            {"group_id": gid, "pattern": g["representative_pattern"]}
            for gid, g in zip(group_ids, rule_groups)
        ],
        "pairs": pairs,
        "level_analysis": level_analysis,
    }


def _analyze_variable_levels(
    rule_groups: list[dict[str, Any]],
    group_ids: list[str],
    gt_cluster_map: dict[str, int] | None,
) -> list[dict[str, Any]]:
    """For each variable position, decompose paths by hierarchy level.

    Returns a list (one per variable position) of:
      {
        "var_index": int,
        "raw_values": [{"group_id": str, "value": str}, ...],
        "depth": int,
        "levels": [
          {
            "level": int,
            "unique_values": [str, ...],
            "n_unique": int,
            "signal": "constant" | "signal" | "noise" | "mixed" | "unknown",
          },
          ...
        ],
        "recommendation": str | None,
      }
    """
    # Split each group's pattern on ` / ` to get variable slots
    all_slots: list[list[str]] = []
    for g in rule_groups:
        pattern = g.get("representative_pattern", "")
        all_slots.append(_SLOT_SPLIT_RE.split(pattern.strip()))

    max_vars = max(len(s) for s in all_slots) if all_slots else 0
    results: list[dict[str, Any]] = []

    for var_idx in range(max_vars):
        # Collect raw values per group
        raw_values: list[dict[str, str]] = []
        path_parts_per_group: list[list[str]] = []
        for i, slots in enumerate(all_slots):
            val = slots[var_idx].strip("'\" ") if var_idx < len(slots) else ""
            raw_values.append({"group_id": group_ids[i], "value": val})
            path_parts_per_group.append(
                [p.strip() for p in val.split("/") if p.strip()]
            )

        max_depth = max(len(p) for p in path_parts_per_group) if path_parts_per_group else 0

        level_info: list[dict[str, Any]] = []
        signal_levels: list[int] = []
        noise_levels: list[int] = []

        for lvl in range(max_depth):
            # Value at this level for each group (empty if out of bounds)
            vals_at_level: list[str] = []
            gid_to_val: dict[str, str] = {}
            for i, parts in enumerate(path_parts_per_group):
                v = parts[lvl] if lvl < len(parts) else ""
                vals_at_level.append(v)
                gid_to_val[group_ids[i]] = v

            unique = sorted(set(v for v in vals_at_level if v))
            n_unique = len(unique)

            # Classify signal/noise using ground truth
            signal = "unknown"
            if gt_cluster_map is not None and n_unique > 1:
                signal = _classify_level(gid_to_val, gt_cluster_map)
                if signal == "signal":
                    signal_levels.append(lvl)
                elif signal == "noise":
                    noise_levels.append(lvl)

            elif n_unique <= 1:
                signal = "constant"

            level_info.append({
                "level": lvl,
                "unique_values": unique,
                "n_unique": n_unique,
                "signal": signal,
            })

        # Generate recommendation
        recommendation = _recommend_levels(
            level_info, signal_levels, noise_levels, max_depth
        )

        results.append({
            "var_index": var_idx,
            "raw_values": raw_values,
            "depth": max_depth,
            "levels": level_info,
            "recommendation": recommendation,
        })

    return results


def _classify_level(
    gid_to_val: dict[str, str],
    gt_cluster_map: dict[str, int],
) -> str:
    """Classify a single hierarchy level as signal or noise.

    - "noise": varies within at least one GT cluster (unusable, must exclude)
    - "signal": uniform within each GT cluster AND differs between clusters
    - "constant": uniform within AND between all clusters (no information)
    """
    # Group values by GT cluster
    cluster_vals: dict[int, set[str]] = {}
    for gid, val in gid_to_val.items():
        ci = gt_cluster_map.get(gid)
        if ci is None:
            continue
        cluster_vals.setdefault(ci, set()).add(val)

    if not cluster_vals:
        return "unknown"

    # If any cluster has multiple values at this level -> noise (primary check)
    varies_within = any(len(vals) > 1 for vals in cluster_vals.values())
    if varies_within:
        return "noise"

    # Check if different clusters have different values (discriminating)
    representative_vals = [next(iter(vals)) for vals in cluster_vals.values()]
    if len(set(representative_vals)) > 1:
        return "signal"

    return "constant"


def _to_negative_indices(levels: list[int], depth: int) -> list[int]:
    """Convert positive level indices to negative (from-the-end) indices.

    Negative indices generalize across projects with different hierarchy depths.
    Level 2 in a depth-4 path = level -2 (second from end).
    """
    return [lvl - depth for lvl in levels]


def _recommend_levels(
    level_info: list[dict[str, Any]],
    signal_levels: list[int],
    noise_levels: list[int],
    max_depth: int,
) -> str | None:
    """Generate a levels recommendation using negative indices."""
    if not level_info:
        return None

    all_constant = all(li["n_unique"] <= 1 for li in level_info)
    if all_constant:
        return "All levels constant -- this variable has no signal. Set weight=0."

    if signal_levels and noise_levels:
        neg = _to_negative_indices(signal_levels, max_depth)
        neg_noise = _to_negative_indices(noise_levels, max_depth)
        return f"levels={neg} (signal only, strips noise at levels {neg_noise})"

    if signal_levels and not noise_levels:
        return None  # all varying levels are signal, no levels config needed

    if noise_levels and not signal_levels:
        # Everything that varies is noise
        return "All varying levels are noise within GT clusters. Consider weight=0."

    # No ground truth info — heuristic based on cardinality
    high_unique = [li for li in level_info if li["n_unique"] > max_depth]
    if high_unique:
        noisy_indices = [li["level"] for li in high_unique]
        keep = [li["level"] for li in level_info if li not in high_unique and li["n_unique"] > 1]
        if keep:
            neg_keep = _to_negative_indices(keep, max_depth)
            neg_noisy = _to_negative_indices(noisy_indices, max_depth)
            return f"Try levels={neg_keep} (levels {neg_noisy} have high cardinality, may be noise)"

    return None


def format_distances(result: dict[str, Any]) -> str:
    """Format distance results as human-readable text."""
    if "error" in result:
        return f"ERROR: {result['error']}"

    lines: list[str] = []
    lines.append(f"Rule: {result['rule_id']}")
    lines.append(
        f"  eps={result['eps']}  template_weight={result['template_weight']}"
    )
    for var_idx, var_info in result.get("variables", {}).items():
        w = var_info["weight"]
        lvl = var_info.get("levels")
        lw = var_info.get("level_weights")
        lvl_str = f"  levels={lvl}" if lvl is not None else ""
        lw_str = f"  level_weights={lw}" if lw is not None else ""
        lines.append(f"  var_{var_idx}: weight={w}{lvl_str}{lw_str}")

    lines.append(f"  {result['n_groups']} logic groups")
    lines.append("")

    # Level analysis (before distances — this is the diagnostic the agent needs first)
    for va in result.get("level_analysis", []):
        depth = va["depth"]
        lines.append(f"Variable {va['var_index']} Level Analysis (depth={depth}):")
        for li in va["levels"]:
            uv = li["unique_values"]
            n = li["n_unique"]
            sig = li["signal"]
            pos = li["level"]
            neg = pos - depth  # negative index
            # Show up to 5 unique values, then truncate
            if len(uv) <= 5:
                val_str = ", ".join(uv)
            else:
                val_str = ", ".join(uv[:5]) + f", ... ({n} total)"
            tag = _signal_tag(sig)
            lines.append(f"  Level {pos} ({neg:+d}): {{{val_str}}} ({n} unique) {tag}")

        rec = va.get("recommendation")
        if rec:
            lines.append(f"  >> RECOMMENDATION: {rec}")
        lines.append("")

    # Groups table
    lines.append("Logic Groups:")
    for g in result["groups"]:
        lines.append(f"  {g['group_id']}  {g['pattern']}")
    lines.append("")

    # Pairwise distances
    eps = result["eps"]
    has_gt = any("gt_same_cluster" in p for p in result["pairs"])

    header = f"{'Distance':>10}  {'A':<30} {'B':<30} {'Merge?':<6}"
    if has_gt:
        header += f" {'GT same?':<8}"
    lines.append("Pairwise Distances (sorted):")
    lines.append(header)
    lines.append("-" * len(header))

    eps_line_printed = False
    for p in result["pairs"]:
        if not eps_line_printed and p["distance"] > eps:
            lines.append(f"{'---- eps = ' + str(eps) + ' ----':^{len(header)}}")
            eps_line_printed = True

        # Shorten group IDs for readability
        a_short = _shorten_id(p["a"])
        b_short = _shorten_id(p["b"])
        merge_str = "YES" if p["merge"] else "no"
        line = f"{p['distance']:>10.6f}  {a_short:<30} {b_short:<30} {merge_str:<6}"
        if has_gt:
            gt_str = "YES" if p.get("gt_same_cluster") else "no"
            line += f" {gt_str:<8}"
        lines.append(line)

    if not eps_line_printed:
        lines.append(f"{'---- eps = ' + str(eps) + ' (all pairs merge) ----':^{len(header)}}")

    return "\n".join(lines)


def _signal_tag(signal: str) -> str:
    tags = {
        "constant": "-- constant, no signal",
        "signal": "<< SIGNAL (discriminates GT clusters)",
        "noise": "<< NOISE (varies within GT clusters, EXCLUDE with levels)",
        "unknown": "",
    }
    return tags.get(signal, "")


def _shorten_id(group_id: str) -> str:
    """Shorten 'CGR_0018::logic::000001' to '::000001' if too long."""
    if len(group_id) > 28:
        parts = group_id.split("::")
        if len(parts) >= 3:
            return f"..{parts[-1]}"
    return group_id


def _load_json(path: str | Path) -> Any:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)
