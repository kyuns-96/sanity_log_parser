from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
from sklearn.cluster import DBSCAN

from sanity_log_parser.gca.adaptive_eps_tuning import (
    compute_rule_base_distance_matrix,
    extract_rule_logic_groups,
)
from sanity_log_parser.gca.config import (
    GcaConfig,
    GcaRuleConfig,
    VariableConfig,
    get_gca_rule_config,
)

_SLOT_SPLIT_RE = re.compile(r"\s+/\s+")
_ALLOWED_SEARCH_KEYS = {"template_weight", "eps", "variables"}
_ALLOWED_VARIABLE_SEARCH_KEYS = {"weight", "levels", "level_weights", "match_mode"}
_VALID_MATCH_MODES = {"embedding", "jaccard"}


@dataclass(frozen=True)
class WeightTuningCandidate:
    raw_rule: dict[str, Any]
    precision: float
    recall: float
    f1: float
    cluster_count: int
    component_count: int


@dataclass(frozen=True)
class WeightTuningResult:
    rule_id: str
    candidates_evaluated: int
    best: WeightTuningCandidate
    top_candidates: tuple[WeightTuningCandidate, ...]
    updated_config: dict[str, Any]
    removed_pairwise_tree: bool
    removed_adaptive_eps_tree: bool


def load_weight_search_spec(
    search_spec_json: str | None,
    *,
    logic_data: dict[str, Any],
    gca_config: GcaConfig,
    raw_config: dict[str, Any],
    rule_id: str,
    variable_indices: Sequence[int] | None = None,
    max_level_combo_size: int = 2,
) -> dict[str, Any]:
    if search_spec_json is None:
        return build_default_weight_search_spec(
            logic_data=logic_data,
            gca_config=gca_config,
            raw_config=raw_config,
            rule_id=rule_id,
            variable_indices=variable_indices,
            max_level_combo_size=max_level_combo_size,
        )

    raw = json.loads(Path(search_spec_json).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Weight search spec '{search_spec_json}' must be a JSON object."
        raise ValueError(msg)
    _validate_search_spec(raw)
    return raw


def build_default_weight_search_spec(
    *,
    logic_data: dict[str, Any],
    gca_config: GcaConfig,
    raw_config: dict[str, Any],
    rule_id: str,
    variable_indices: Sequence[int] | None = None,
    max_level_combo_size: int = 2,
) -> dict[str, Any]:
    if max_level_combo_size < 1:
        msg = "max_level_combo_size must be >= 1"
        raise ValueError(msg)

    rule_config = get_gca_rule_config(gca_config, rule_id)
    rule_raw = _get_rule_raw(raw_config, rule_id)
    target_vars = list(
        variable_indices
        if variable_indices is not None
        else _default_target_variables(logic_data, rule_id, rule_config)
    )
    target_vars = sorted(dict.fromkeys(target_vars))

    eps_candidates = _dedupe_float_list(
        [rule_config.eps, 0.05, 0.1, 0.15, 0.2, 0.3]
    )
    template_candidates = _dedupe_float_list(
        [rule_config.template_weight, 0.0, 0.2]
    )

    variables: dict[str, list[dict[str, Any]]] = {}
    for var_index in target_vars:
        current_var = rule_config.variables.get(
            var_index,
            VariableConfig(weight=gca_config.default_variable_weight),
        )
        candidates: list[dict[str, Any]] = [{"weight": 0.0}]

        level_options: list[list[int] | None] = [None]
        if current_var.levels is not None:
            level_options.append(list(current_var.levels))

        max_depth = _infer_variable_depth(logic_data, rule_id, var_index)
        if max_depth > 0:
            level_options.extend(_generate_level_options(max_depth, max_level_combo_size))

        positive_weights = _dedupe_float_list(
            [current_var.weight, 1.0]
        )
        match_modes = _dedupe_str_list([current_var.match_mode, "embedding", "jaccard"])

        if current_var.level_weights is not None:
            candidates.append(
                {
                    "match_mode": current_var.match_mode,
                    "level_weights": {
                        str(level): weight
                        for level, weight in sorted(current_var.level_weights.items())
                    },
                }
            )

        for weight in positive_weights:
            if weight == 0:
                continue
            for match_mode in match_modes:
                for levels in level_options:
                    candidate: dict[str, Any] = {
                        "weight": weight,
                        "match_mode": match_mode,
                    }
                    if levels is not None:
                        candidate["levels"] = levels
                    candidates.append(candidate)

        variables[str(var_index)] = _dedupe_variable_candidates(candidates)

    spec: dict[str, Any] = {
        "template_weight": template_candidates,
        "eps": eps_candidates,
        "variables": variables,
    }
    _validate_search_spec(spec)

    # If current rule had no explicit entry, keep the generated spec compact.
    if not rule_raw:
        return spec
    return spec


def fit_rule_weights(
    *,
    logic_data: dict[str, Any],
    ground_truth_data: dict[str, list[list[str]]],
    gca_config: GcaConfig,
    raw_config: dict[str, Any],
    rule_id: str,
    embed_fn: Callable[[list[str]], Any],
    search_spec: dict[str, Any],
    top_k: int = 10,
) -> WeightTuningResult:
    if top_k < 1:
        msg = "top_k must be >= 1"
        raise ValueError(msg)

    rule_groups, cluster_labels = extract_rule_logic_groups(
        logic_data=logic_data,
        ground_truth_data=ground_truth_data,
        rule_id=rule_id,
    )
    base_rule_raw = _get_rule_raw(raw_config, rule_id)
    base_rule_config = get_gca_rule_config(gca_config, rule_id)
    candidates = list(
        iter_weight_candidates(
            search_spec=search_spec,
            base_rule_raw=base_rule_raw,
            base_rule_config=base_rule_config,
            default_variable_weight=gca_config.default_variable_weight,
        )
    )
    if not candidates:
        msg = f"No search candidates generated for rule '{rule_id}'."
        raise ValueError(msg)

    cached_embed_fn = _EmbeddingCache(embed_fn)
    scored: list[WeightTuningCandidate] = []
    best: WeightTuningCandidate | None = None
    expected_cluster_count = len(set(cluster_labels))

    for candidate_raw in candidates:
        candidate_rule = _rule_config_from_raw(
            candidate_raw,
            default_eps=gca_config.default_eps,
            default_template_weight=gca_config.default_template_weight,
            default_variable_weight=gca_config.default_variable_weight,
        )
        candidate_config = GcaConfig(
            default_eps=gca_config.default_eps,
            default_template_weight=gca_config.default_template_weight,
            default_variable_weight=gca_config.default_variable_weight,
            rules={rule_id: candidate_rule},
        )
        distances = compute_rule_base_distance_matrix(
            rule_groups=rule_groups,
            gca_config=candidate_config,
            rule_id=rule_id,
            embed_fn=cached_embed_fn,
        )
        predicted = DBSCAN(
            eps=float(candidate_rule.eps),
            min_samples=1,
            metric="precomputed",
        ).fit(distances).labels_
        metrics = _cluster_pair_metrics(cluster_labels, predicted)
        score = WeightTuningCandidate(
            raw_rule=candidate_raw,
            precision=metrics["precision"],
            recall=metrics["recall"],
            f1=metrics["f1"],
            cluster_count=len(set(int(label) for label in predicted)),
            component_count=_count_components(candidate_rule),
        )
        scored.append(score)
        if _is_better_weight_candidate(score, best, expected_cluster_count):
            best = score

    assert best is not None
    scored.sort(
        key=lambda item: (
            item.f1,
            item.precision,
            item.recall,
            -abs(item.cluster_count - expected_cluster_count),
            -item.component_count,
        ),
        reverse=True,
    )

    updated_config, removed_pairwise, removed_adaptive = update_rule_config_with_weight_candidate(
        raw_config=raw_config,
        rule_id=rule_id,
        rule_raw=best.raw_rule,
    )
    return WeightTuningResult(
        rule_id=rule_id,
        candidates_evaluated=len(scored),
        best=best,
        top_candidates=tuple(scored[:top_k]),
        updated_config=updated_config,
        removed_pairwise_tree=removed_pairwise,
        removed_adaptive_eps_tree=removed_adaptive,
    )


def iter_weight_candidates(
    *,
    search_spec: dict[str, Any],
    base_rule_raw: dict[str, Any],
    base_rule_config: GcaRuleConfig,
    default_variable_weight: float,
) -> Iterable[dict[str, Any]]:
    _validate_search_spec(search_spec)

    template_weights = search_spec.get("template_weight", [base_rule_config.template_weight])
    eps_values = search_spec.get("eps", [base_rule_config.eps])
    variable_search = search_spec.get("variables", {})

    base_variables = base_rule_raw.get("variables", {})
    if not isinstance(base_variables, dict):
        base_variables = {}

    parsed_candidates: list[tuple[int, list[dict[str, Any]]]] = []
    for var_key, raw_candidates in sorted(variable_search.items(), key=lambda item: int(item[0])):
        var_index = int(var_key)
        current_var = base_rule_config.variables.get(
            var_index,
            VariableConfig(weight=default_variable_weight),
        )
        candidates = [
            _normalize_variable_candidate(
                item,
                current_var=current_var,
                default_variable_weight=default_variable_weight,
            )
            for item in raw_candidates
        ]
        parsed_candidates.append((var_index, _dedupe_variable_candidates(candidates)))

    seen: set[str] = set()
    candidate_lists = [items for _, items in parsed_candidates]
    for values in product(template_weights, eps_values, *candidate_lists):
        template_weight = float(values[0])
        eps = float(values[1])
        raw_rule = deepcopy(base_rule_raw)
        raw_rule.pop("pairwise_tree", None)
        raw_rule.pop("adaptive_eps_tree", None)
        raw_rule["template_weight"] = template_weight
        raw_rule["eps"] = eps

        merged_variables = deepcopy(base_variables)
        for (var_index, _items), candidate in zip(parsed_candidates, values[2:], strict=True):
            merged_variables[str(var_index)] = deepcopy(candidate)
        raw_rule["variables"] = merged_variables

        key = json.dumps(raw_rule, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        yield raw_rule


def update_rule_config_with_weight_candidate(
    *,
    raw_config: dict[str, Any],
    rule_id: str,
    rule_raw: dict[str, Any],
) -> tuple[dict[str, Any], bool, bool]:
    updated = deepcopy(raw_config)
    rules = updated.setdefault("rules", {})
    if not isinstance(rules, dict):
        msg = "Top-level 'rules' must be a JSON object."
        raise ValueError(msg)

    previous_entry = rules.get(rule_id)
    removed_pairwise = isinstance(previous_entry, dict) and "pairwise_tree" in previous_entry
    removed_adaptive = isinstance(previous_entry, dict) and "adaptive_eps_tree" in previous_entry

    entry = deepcopy(rule_raw)
    entry.pop("pairwise_tree", None)
    entry.pop("adaptive_eps_tree", None)
    rules[rule_id] = entry
    return updated, removed_pairwise, removed_adaptive


def format_weight_tuning_result(result: WeightTuningResult) -> str:
    lines = [
        f"Rule ID: {result.rule_id}",
        f"Candidates evaluated: {result.candidates_evaluated}",
        f"Best precision: {result.best.precision:.4f}",
        f"Best recall: {result.best.recall:.4f}",
        f"Best F1: {result.best.f1:.4f}",
        f"Best config: {_format_rule_summary(result.best.raw_rule)}",
        "Top candidates:",
    ]
    for index, candidate in enumerate(result.top_candidates, start=1):
        lines.append(
            "  "
            f"{index:02d}. "
            f"F1={candidate.f1:.4f} "
            f"P={candidate.precision:.4f} "
            f"R={candidate.recall:.4f} "
            f"clusters={candidate.cluster_count} "
            f"{_format_rule_summary(candidate.raw_rule)}"
        )
    return "\n".join(lines)


def _validate_search_spec(raw: dict[str, Any]) -> None:
    unknown = set(raw) - _ALLOWED_SEARCH_KEYS
    if unknown:
        msg = f"Unknown weight search keys: {sorted(unknown)}"
        raise ValueError(msg)

    for key in ("template_weight", "eps"):
        values = raw.get(key)
        if values is None:
            continue
        if not isinstance(values, list) or not values:
            msg = f"Weight search key '{key}' must be a non-empty list."
            raise ValueError(msg)
        for value in values:
            if not isinstance(value, (int, float)):
                msg = f"Weight search key '{key}' must contain only numbers."
                raise ValueError(msg)
            if key == "eps" and float(value) <= 0:
                msg = "All eps candidates must be positive."
                raise ValueError(msg)
            if key == "template_weight" and float(value) < 0:
                msg = "All template_weight candidates must be non-negative."
                raise ValueError(msg)

    variables = raw.get("variables")
    if variables is None:
        return
    if not isinstance(variables, dict):
        msg = "Weight search key 'variables' must be an object."
        raise ValueError(msg)

    for var_key, candidates in variables.items():
        if not isinstance(var_key, str) or not var_key.isdigit():
            msg = f"Variable search key '{var_key}' must be a non-negative integer string."
            raise ValueError(msg)
        if not isinstance(candidates, list) or not candidates:
            msg = f"Variable search key '{var_key}' must map to a non-empty list."
            raise ValueError(msg)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                msg = f"Variable search candidates for '{var_key}' must be objects."
                raise ValueError(msg)
            unknown_candidate = set(candidate) - _ALLOWED_VARIABLE_SEARCH_KEYS
            if unknown_candidate:
                msg = (
                    f"Variable '{var_key}' search candidate contains unknown keys: "
                    f"{sorted(unknown_candidate)}"
                )
                raise ValueError(msg)


def _default_target_variables(
    logic_data: dict[str, Any],
    rule_id: str,
    rule_config: GcaRuleConfig,
) -> list[int]:
    if rule_config.variables:
        return sorted(rule_config.variables)

    max_index = -1
    for group in logic_data.get("groups", []):
        if group.get("group_type") != "logic" or group.get("rule_id") != rule_id:
            continue
        pattern = group.get("representative_pattern")
        if not isinstance(pattern, str):
            continue
        max_index = max(max_index, len(_SLOT_SPLIT_RE.split(pattern.strip())) - 1)
    if max_index < 0:
        return []
    return list(range(max_index + 1))


def _infer_variable_depth(
    logic_data: dict[str, Any],
    rule_id: str,
    var_index: int,
) -> int:
    depth = 0
    for group in logic_data.get("groups", []):
        if group.get("group_type") != "logic" or group.get("rule_id") != rule_id:
            continue
        pattern = group.get("representative_pattern")
        if not isinstance(pattern, str):
            continue
        slots = _SLOT_SPLIT_RE.split(pattern.strip())
        if var_index >= len(slots):
            continue
        depth = max(depth, len([part for part in slots[var_index].split("/") if part.strip()]))
    return depth


def _generate_level_options(
    max_depth: int,
    max_level_combo_size: int,
) -> list[list[int]]:
    levels = list(range(-max_depth, 0))
    options: list[list[int]] = []
    for size in range(1, min(max_level_combo_size, max_depth) + 1):
        for start in range(0, len(levels) - size + 1):
            options.append(levels[start : start + size])
    return options


def _normalize_variable_candidate(
    raw: dict[str, Any],
    *,
    current_var: VariableConfig,
    default_variable_weight: float,
) -> dict[str, Any]:
    weight = float(raw.get("weight", current_var.weight))
    if weight < 0:
        msg = "Variable search candidate weight must be non-negative."
        raise ValueError(msg)

    if "levels" in raw and "level_weights" in raw:
        msg = "Variable search candidate cannot contain both 'levels' and 'level_weights'."
        raise ValueError(msg)

    match_mode = str(raw.get("match_mode", current_var.match_mode or "embedding"))
    if match_mode not in _VALID_MATCH_MODES:
        msg = f"Variable search candidate match_mode must be one of {sorted(_VALID_MATCH_MODES)}."
        raise ValueError(msg)

    candidate: dict[str, Any] = {"weight": weight}
    if match_mode != "embedding":
        candidate["match_mode"] = match_mode

    if "levels" in raw:
        levels = raw["levels"]
        if levels is not None:
            if not isinstance(levels, list) or not all(isinstance(level, int) for level in levels):
                msg = "Variable search candidate 'levels' must be null or a list of ints."
                raise ValueError(msg)
            candidate["levels"] = list(levels)
    if "level_weights" in raw:
        raw_level_weights = raw["level_weights"]
        if not isinstance(raw_level_weights, dict):
            msg = "Variable search candidate 'level_weights' must be an object."
            raise ValueError(msg)
        normalized: dict[str, float] = {}
        for key, value in raw_level_weights.items():
            if not isinstance(key, str) or not _is_int_string(key):
                msg = "Variable search candidate level_weights keys must be integer strings."
                raise ValueError(msg)
            if not isinstance(value, (int, float)) or float(value) < 0:
                msg = "Variable search candidate level_weights values must be non-negative numbers."
                raise ValueError(msg)
            normalized[key] = float(value)
        candidate.pop("weight", None)
        candidate["level_weights"] = normalized

    if candidate.get("weight", default_variable_weight) == 0:
        return {"weight": 0.0}
    return candidate


def _dedupe_variable_candidates(
    candidates: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for candidate in candidates:
        key = json.dumps(candidate, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _dedupe_float_list(values: Sequence[float]) -> list[float]:
    result: list[float] = []
    seen: set[float] = set()
    for value in values:
        fv = float(value)
        if fv in seen:
            continue
        seen.add(fv)
        result.append(fv)
    return result


def _dedupe_str_list(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _get_rule_raw(raw_config: dict[str, Any], rule_id: str) -> dict[str, Any]:
    rules = raw_config.get("rules", {})
    if not isinstance(rules, dict):
        return {}
    rule_raw = rules.get(rule_id, {})
    if not isinstance(rule_raw, dict):
        return {}
    return deepcopy(rule_raw)


def _rule_config_from_raw(
    raw_rule: dict[str, Any],
    *,
    default_eps: float,
    default_template_weight: float,
    default_variable_weight: float,
) -> GcaRuleConfig:
    eps = float(raw_rule.get("eps", default_eps))
    template_weight = float(raw_rule.get("template_weight", default_template_weight))
    raw_variables = raw_rule.get("variables", {})
    if not isinstance(raw_variables, dict):
        msg = "Rule 'variables' must be an object."
        raise ValueError(msg)

    variables: dict[int, VariableConfig] = {}
    for key, value in raw_variables.items():
        if not isinstance(key, str) or not key.isdigit():
            msg = f"Rule variable key '{key}' must be a non-negative integer string."
            raise ValueError(msg)
        if not isinstance(value, dict):
            msg = f"Rule variable '{key}' must be an object."
            raise ValueError(msg)
        variables[int(key)] = _variable_config_from_raw(
            value,
            default_variable_weight=default_variable_weight,
        )
    return GcaRuleConfig(
        eps=eps,
        template_weight=template_weight,
        variables=variables,
    )


def _variable_config_from_raw(
    raw: dict[str, Any],
    *,
    default_variable_weight: float,
) -> VariableConfig:
    weight = float(raw.get("weight", default_variable_weight))
    if weight < 0:
        msg = "Variable weight must be non-negative."
        raise ValueError(msg)

    levels = raw.get("levels")
    if levels is not None:
        if not isinstance(levels, list) or not all(isinstance(level, int) for level in levels):
            msg = "Variable levels must be a list of ints."
            raise ValueError(msg)
        levels = list(levels)

    raw_level_weights = raw.get("level_weights")
    level_weights: dict[int, float] | None = None
    if raw_level_weights is not None:
        if levels is not None:
            msg = "Variable config cannot contain both 'levels' and 'level_weights'."
            raise ValueError(msg)
        if not isinstance(raw_level_weights, dict):
            msg = "Variable level_weights must be an object."
            raise ValueError(msg)
        level_weights = {}
        for key, value in raw_level_weights.items():
            if isinstance(key, int):
                level_key = key
            elif isinstance(key, str) and _is_int_string(key):
                level_key = int(key)
            else:
                msg = "Variable level_weights keys must be integers."
                raise ValueError(msg)
            if not isinstance(value, (int, float)) or float(value) < 0:
                msg = "Variable level_weights values must be non-negative numbers."
                raise ValueError(msg)
            level_weights[level_key] = float(value)

    match_mode = str(raw.get("match_mode", "embedding"))
    if match_mode not in _VALID_MATCH_MODES:
        msg = f"Variable match_mode must be one of {sorted(_VALID_MATCH_MODES)}."
        raise ValueError(msg)

    return VariableConfig(
        weight=weight,
        levels=levels,
        level_weights=level_weights,
        match_mode=match_mode,
    )


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


def _count_components(rule_config: GcaRuleConfig) -> int:
    count = 1 if rule_config.template_weight > 0 else 0
    for variable in rule_config.variables.values():
        if variable.level_weights is not None:
            count += sum(1 for weight in variable.level_weights.values() if weight > 0)
        elif variable.weight > 0:
            count += 1
    return count


def _is_better_weight_candidate(
    candidate: WeightTuningCandidate,
    current: WeightTuningCandidate | None,
    expected_cluster_count: int,
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

    candidate_delta = abs(candidate.cluster_count - expected_cluster_count)
    current_delta = abs(current.cluster_count - expected_cluster_count)
    if candidate_delta != current_delta:
        return candidate_delta < current_delta
    if candidate.component_count != current.component_count:
        return candidate.component_count < current.component_count
    return _format_rule_summary(candidate.raw_rule) < _format_rule_summary(current.raw_rule)


def _format_rule_summary(rule_raw: dict[str, Any]) -> str:
    template_weight = float(rule_raw.get("template_weight", 0.0))
    eps = float(rule_raw.get("eps", 0.0))
    variable_parts: list[str] = []
    raw_variables = rule_raw.get("variables", {})
    if isinstance(raw_variables, dict):
        for key, value in sorted(raw_variables.items(), key=lambda item: int(item[0])):
            if not isinstance(value, dict):
                continue
            if float(value.get("weight", 0.0)) == 0.0 and "level_weights" not in value:
                variable_parts.append(f"var{key}=off")
                continue
            if "level_weights" in value:
                variable_parts.append(
                    f"var{key}=level_weights@{value['level_weights']}"
                )
                continue
            mode = value.get("match_mode", "embedding")
            levels = value.get("levels")
            level_str = f"@{levels}" if levels is not None else "@all"
            variable_parts.append(
                f"var{key}={mode},w={float(value.get('weight', 0.0))}{level_str}"
            )
    return f"eps={eps} template_weight={template_weight} {' '.join(variable_parts)}".strip()


def _is_int_string(value: str) -> bool:
    if not value:
        return False
    if value[0] == "-":
        return value[1:].isdigit()
    return value.isdigit()


class _EmbeddingCache:
    def __init__(self, embed_fn: Callable[[list[str]], Any]) -> None:
        self._embed_fn = embed_fn
        self._cache: dict[str, np.ndarray] = {}
        self._dim: int | None = None

    def __call__(self, texts: list[str]) -> np.ndarray:
        if not texts:
            dim = self._dim or 0
            return np.empty((0, dim), dtype=np.float32)

        missing: list[str] = []
        seen_missing: set[str] = set()
        for text in texts:
            if text in self._cache or text in seen_missing:
                continue
            seen_missing.add(text)
            missing.append(text)

        if missing:
            embedded = np.asarray(self._embed_fn(missing), dtype=np.float32)
            if embedded.ndim != 2 or embedded.shape[0] != len(missing):
                msg = "embed_fn must return an array with shape (N, D)."
                raise ValueError(msg)
            self._dim = int(embedded.shape[1])
            for text, vector in zip(missing, embedded, strict=True):
                self._cache[text] = vector

        if self._dim is None:
            self._dim = 0
        return np.asarray([self._cache[text] for text in texts], dtype=np.float32)
