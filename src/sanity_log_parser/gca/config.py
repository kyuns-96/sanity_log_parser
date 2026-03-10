from __future__ import annotations

import json
import logging
import math
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_ALLOWED_TOP_KEYS = {
    "default_eps",
    "default_template_weight",
    "default_variable_weight",
    "rules",
}
_ALLOWED_RULE_KEYS = {"eps", "template_weight", "variables", "pairwise_tree", "adaptive_eps_tree"}
_ALLOWED_VARIABLE_KEYS = {"weight", "levels", "level_weights", "match_mode"}
_VALID_MATCH_MODES = {"embedding", "jaccard"}
_ALLOWED_PAIRWISE_TREE_KEYS = {"features", "nodes"}
_ALLOWED_PAIRWISE_FEATURE_KEYS = {
    "kind",
    "levels",
    "ngram_range",
    "max_shift",
    "decay",
}
_ALLOWED_PAIRWISE_NODE_KEYS = {"feature", "threshold", "left", "right", "value"}
_VALID_PAIRWISE_FEATURE_KINDS = {
    "path_tfidf_char_wb",
    "suffix_similarity",
    "level_jaccard",
    "level_exact",
    "path_length_equal",
    "path_length_diff",
}


class ConfigError(Exception):
    """Raised when a GCA config file is invalid."""


@dataclass(frozen=True)
class VariableConfig:
    weight: float = 0.7
    levels: list[int] | None = None
    level_weights: dict[int, float] | None = None
    match_mode: str = "embedding"


@dataclass(frozen=True)
class GcaRuleConfig:
    eps: float = 0.2
    template_weight: float = 0.3
    pairwise_tree: dict[str, object] | None = None
    adaptive_eps_tree: dict[str, object] | None = None
    variables: dict[int, VariableConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class GcaConfig:
    default_eps: float = 0.2
    default_template_weight: float = 0.3
    default_variable_weight: float = 0.7
    rules: dict[str, GcaRuleConfig] = field(default_factory=dict)


def load_gca_config(config_file: str, *, strict: bool = False) -> GcaConfig:
    """Load and validate a GCA config file.

    strict=True: raise ConfigError on any failure.
    strict=False: log warning and return defaults on any failure.
    """
    try:
        with open(config_file, encoding="utf-8") as f:
            raw = json.load(f)
        return _parse_gca_config(raw)
    except Exception as exc:
        if strict:
            raise ConfigError(str(exc)) from exc
        logger.warning(
            "Failed to load GCA config '%s': %s. Using defaults.", config_file, exc
        )
        return GcaConfig()


def _parse_gca_config(raw: object) -> GcaConfig:
    if not isinstance(raw, dict):
        msg = f"Expected top-level dict, got {type(raw).__name__}"
        raise ConfigError(msg)

    _reject_unknown_keys(raw, _ALLOWED_TOP_KEYS, "top-level")

    default_eps = raw.get("default_eps", 0.2)
    default_template_weight = raw.get("default_template_weight", 0.3)
    default_variable_weight = raw.get("default_variable_weight", 0.7)

    _validate_positive_float(default_eps, "default_eps")
    _validate_non_negative_float(default_template_weight, "default_template_weight")
    _validate_non_negative_float(default_variable_weight, "default_variable_weight")

    raw_rules = raw.get("rules", {})
    if not isinstance(raw_rules, dict):
        msg = f"Expected 'rules' to be a dict, got {type(raw_rules).__name__}"
        raise ConfigError(msg)

    rules: dict[str, GcaRuleConfig] = {}
    for rule_id, rule_raw in raw_rules.items():
        rules[rule_id] = _parse_gca_rule(
            rule_raw,
            rule_id,
            default_eps=default_eps,
            default_template_weight=default_template_weight,
        )

    return GcaConfig(
        default_eps=default_eps,
        default_template_weight=default_template_weight,
        default_variable_weight=default_variable_weight,
        rules=rules,
    )


def _parse_gca_rule(
    raw: object,
    rule_id: str,
    *,
    default_eps: float,
    default_template_weight: float,
) -> GcaRuleConfig:
    if not isinstance(raw, dict):
        msg = f"Rule '{rule_id}': expected dict, got {type(raw).__name__}"
        raise ConfigError(msg)

    _reject_unknown_keys(raw, _ALLOWED_RULE_KEYS, f"rule '{rule_id}'")

    eps = raw.get("eps", default_eps)
    template_weight = raw.get("template_weight", default_template_weight)
    pairwise_tree = _parse_pairwise_tree(raw, rule_id)
    adaptive_eps_tree = _parse_adaptive_eps_tree(raw, rule_id)

    _validate_positive_float(eps, f"rule '{rule_id}' eps")
    _validate_non_negative_float(template_weight, f"rule '{rule_id}' template_weight")

    raw_variables = raw.get("variables", {})
    if not isinstance(raw_variables, dict):
        msg = f"Rule '{rule_id}': expected 'variables' to be a dict"
        raise ConfigError(msg)

    variables: dict[int, VariableConfig] = {}
    for var_key, var_raw in raw_variables.items():
        if not isinstance(var_key, str) or not var_key.isdigit() or int(var_key) < 0:
            msg = f"Rule '{rule_id}': variable key '{var_key}' must be a non-negative integer string"
            raise ConfigError(msg)
        variables[int(var_key)] = _parse_variable(var_raw, rule_id, var_key)

    return GcaRuleConfig(
        eps=eps,
        template_weight=template_weight,
        pairwise_tree=pairwise_tree,
        adaptive_eps_tree=adaptive_eps_tree,
        variables=variables,
    )


def _parse_pairwise_tree(
    raw: dict[str, object],
    rule_id: str,
) -> dict[str, object] | None:
    tree_raw = raw.get("pairwise_tree")
    if tree_raw is None:
        return None
    if not isinstance(tree_raw, dict):
        msg = (
            f"Rule '{rule_id}': 'pairwise_tree' must be a dict, "
            f"got {type(tree_raw).__name__}"
        )
        raise ConfigError(msg)

    _reject_unknown_keys(tree_raw, _ALLOWED_PAIRWISE_TREE_KEYS, f"rule '{rule_id}' pairwise_tree")

    features_raw = tree_raw.get("features")
    nodes_raw = tree_raw.get("nodes")
    if not isinstance(features_raw, list) or not features_raw:
        msg = f"Rule '{rule_id}': pairwise_tree.features must be a non-empty list"
        raise ConfigError(msg)
    if not isinstance(nodes_raw, list) or not nodes_raw:
        msg = f"Rule '{rule_id}': pairwise_tree.nodes must be a non-empty list"
        raise ConfigError(msg)

    parsed_features: list[dict[str, object]] = []
    for idx, feature_raw in enumerate(features_raw):
        ctx = f"rule '{rule_id}' pairwise_tree.features[{idx}]"
        if not isinstance(feature_raw, dict):
            msg = f"{ctx} must be a dict"
            raise ConfigError(msg)
        _reject_unknown_keys(feature_raw, _ALLOWED_PAIRWISE_FEATURE_KEYS, ctx)
        kind = feature_raw.get("kind")
        if kind not in _VALID_PAIRWISE_FEATURE_KINDS:
            msg = f"{ctx}: 'kind' must be one of {sorted(_VALID_PAIRWISE_FEATURE_KINDS)}"
            raise ConfigError(msg)

        parsed: dict[str, object] = {"kind": kind}
        levels = feature_raw.get("levels")
        if levels is not None:
            if not isinstance(levels, list) or not levels or not all(isinstance(v, int) for v in levels):
                msg = f"{ctx}: 'levels' must be a non-empty list of ints"
                raise ConfigError(msg)
            parsed["levels"] = tuple(levels)

        ngram_range = feature_raw.get("ngram_range")
        if ngram_range is not None:
            if (
                not isinstance(ngram_range, list)
                or len(ngram_range) != 2
                or not all(isinstance(v, int) and v > 0 for v in ngram_range)
            ):
                msg = f"{ctx}: 'ngram_range' must be a two-item list of positive ints"
                raise ConfigError(msg)
            parsed["ngram_range"] = (ngram_range[0], ngram_range[1])

        max_shift = feature_raw.get("max_shift")
        if max_shift is not None:
            if not isinstance(max_shift, int) or max_shift < 0:
                msg = f"{ctx}: 'max_shift' must be a non-negative int"
                raise ConfigError(msg)
            parsed["max_shift"] = max_shift

        decay = feature_raw.get("decay")
        if decay is not None:
            _validate_positive_float(decay, f"{ctx} decay")
            parsed["decay"] = float(decay)

        parsed_features.append(parsed)

    parsed_nodes: list[dict[str, object]] = []
    for idx, node_raw in enumerate(nodes_raw):
        ctx = f"rule '{rule_id}' pairwise_tree.nodes[{idx}]"
        if not isinstance(node_raw, dict):
            msg = f"{ctx} must be a dict"
            raise ConfigError(msg)
        _reject_unknown_keys(node_raw, _ALLOWED_PAIRWISE_NODE_KEYS, ctx)

        if "value" in node_raw:
            value = node_raw["value"]
            if value not in (0, 1):
                msg = f"{ctx}: leaf 'value' must be 0 or 1"
                raise ConfigError(msg)
            parsed_nodes.append({"value": value})
            continue

        required = {"feature", "threshold", "left", "right"}
        if set(node_raw) != required:
            msg = f"{ctx}: non-leaf node must have keys {sorted(required)}"
            raise ConfigError(msg)

        feature = node_raw["feature"]
        left = node_raw["left"]
        right = node_raw["right"]
        threshold = node_raw["threshold"]
        if not isinstance(feature, int) or feature < 0:
            msg = f"{ctx}: 'feature' must be a non-negative int"
            raise ConfigError(msg)
        if not isinstance(left, int) or left < 0 or not isinstance(right, int) or right < 0:
            msg = f"{ctx}: 'left' and 'right' must be non-negative ints"
            raise ConfigError(msg)
        if not isinstance(threshold, int | float) or not math.isfinite(threshold):
            msg = f"{ctx}: 'threshold' must be a finite number"
            raise ConfigError(msg)
        _validate_max_decimal_places(
            threshold,
            max_places=3,
            name=f"{ctx} threshold",
        )
        parsed_nodes.append(
            {
                "feature": feature,
                "threshold": float(threshold),
                "left": left,
                "right": right,
            }
        )

    _validate_tree_graph(
        rule_id=rule_id,
        tree_name="pairwise_tree",
        features=parsed_features,
        nodes=parsed_nodes,
    )

    return {
        "features": tuple(parsed_features),
        "nodes": tuple(parsed_nodes),
    }


def _parse_adaptive_eps_tree(
    raw: dict[str, object],
    rule_id: str,
) -> dict[str, object] | None:
    tree_raw = raw.get("adaptive_eps_tree")
    if tree_raw is None:
        return None
    if not isinstance(tree_raw, dict):
        msg = (
            f"Rule '{rule_id}': 'adaptive_eps_tree' must be a dict, "
            f"got {type(tree_raw).__name__}"
        )
        raise ConfigError(msg)

    _reject_unknown_keys(
        tree_raw,
        _ALLOWED_PAIRWISE_TREE_KEYS,
        f"rule '{rule_id}' adaptive_eps_tree",
    )

    features_raw = tree_raw.get("features")
    nodes_raw = tree_raw.get("nodes")
    if not isinstance(features_raw, list) or not features_raw:
        msg = f"Rule '{rule_id}': adaptive_eps_tree.features must be a non-empty list"
        raise ConfigError(msg)
    if not isinstance(nodes_raw, list) or not nodes_raw:
        msg = f"Rule '{rule_id}': adaptive_eps_tree.nodes must be a non-empty list"
        raise ConfigError(msg)

    parsed_features = []
    for idx, feature_raw in enumerate(features_raw):
        parsed_features.append(
            _parse_pairwise_feature(feature_raw, f"rule '{rule_id}' adaptive_eps_tree.features[{idx}]")
        )

    parsed_nodes: list[dict[str, object]] = []
    for idx, node_raw in enumerate(nodes_raw):
        ctx = f"rule '{rule_id}' adaptive_eps_tree.nodes[{idx}]"
        if not isinstance(node_raw, dict):
            msg = f"{ctx} must be a dict"
            raise ConfigError(msg)
        _reject_unknown_keys(node_raw, _ALLOWED_PAIRWISE_NODE_KEYS, ctx)

        if "value" in node_raw:
            value = node_raw["value"]
            _validate_positive_float(value, f"{ctx} value")
            _validate_max_decimal_places(
                value,
                max_places=3,
                name=f"{ctx} value",
            )
            parsed_nodes.append({"value": float(value)})
            continue

        required = {"feature", "threshold", "left", "right"}
        if set(node_raw) != required:
            msg = f"{ctx}: non-leaf node must have keys {sorted(required)}"
            raise ConfigError(msg)

        feature = node_raw["feature"]
        left = node_raw["left"]
        right = node_raw["right"]
        threshold = node_raw["threshold"]
        if not isinstance(feature, int) or feature < 0:
            msg = f"{ctx}: 'feature' must be a non-negative int"
            raise ConfigError(msg)
        if not isinstance(left, int) or left < 0 or not isinstance(right, int) or right < 0:
            msg = f"{ctx}: 'left' and 'right' must be non-negative ints"
            raise ConfigError(msg)
        if not isinstance(threshold, int | float) or not math.isfinite(threshold):
            msg = f"{ctx}: 'threshold' must be a finite number"
            raise ConfigError(msg)
        _validate_max_decimal_places(
            threshold,
            max_places=3,
            name=f"{ctx} threshold",
        )
        parsed_nodes.append(
            {
                "feature": feature,
                "threshold": float(threshold),
                "left": left,
                "right": right,
            }
        )

    _validate_tree_graph(
        rule_id=rule_id,
        tree_name="adaptive_eps_tree",
        features=parsed_features,
        nodes=parsed_nodes,
    )

    return {
        "features": tuple(parsed_features),
        "nodes": tuple(parsed_nodes),
    }


def _validate_tree_graph(
    *,
    rule_id: str,
    tree_name: str,
    features: list[dict[str, object]],
    nodes: list[dict[str, object]],
) -> None:
    state = [0] * len(nodes)

    def visit(node_index: int) -> None:
        if node_index < 0 or node_index >= len(nodes):
            msg = (
                f"Rule '{rule_id}': {tree_name} child index {node_index} "
                f"is out of range for {len(nodes)} nodes"
            )
            raise ConfigError(msg)
        if state[node_index] == 1:
            msg = (
                f"Rule '{rule_id}': {tree_name} contains a cycle at node "
                f"{node_index}"
            )
            raise ConfigError(msg)
        if state[node_index] == 2:
            return

        state[node_index] = 1
        node = nodes[node_index]
        if "value" not in node:
            feature_index = int(node["feature"])
            if feature_index >= len(features):
                msg = (
                    f"Rule '{rule_id}': {tree_name} feature index {feature_index} "
                    f"is out of range for {len(features)} features"
                )
                raise ConfigError(msg)
            visit(int(node["left"]))
            visit(int(node["right"]))
        state[node_index] = 2

    visit(0)


def _parse_pairwise_feature(raw: object, ctx: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        msg = f"{ctx} must be a dict"
        raise ConfigError(msg)
    _reject_unknown_keys(raw, _ALLOWED_PAIRWISE_FEATURE_KEYS, ctx)
    kind = raw.get("kind")
    if kind not in _VALID_PAIRWISE_FEATURE_KINDS:
        msg = f"{ctx}: 'kind' must be one of {sorted(_VALID_PAIRWISE_FEATURE_KINDS)}"
        raise ConfigError(msg)

    parsed: dict[str, object] = {"kind": kind}
    levels = raw.get("levels")
    if levels is not None:
        if not isinstance(levels, list) or not levels or not all(isinstance(v, int) for v in levels):
            msg = f"{ctx}: 'levels' must be a non-empty list of ints"
            raise ConfigError(msg)
        parsed["levels"] = tuple(levels)

    ngram_range = raw.get("ngram_range")
    if ngram_range is not None:
        if (
            not isinstance(ngram_range, list)
            or len(ngram_range) != 2
            or not all(isinstance(v, int) and v > 0 for v in ngram_range)
        ):
            msg = f"{ctx}: 'ngram_range' must be a two-item list of positive ints"
            raise ConfigError(msg)
        parsed["ngram_range"] = (ngram_range[0], ngram_range[1])

    max_shift = raw.get("max_shift")
    if max_shift is not None:
        if not isinstance(max_shift, int) or max_shift < 0:
            msg = f"{ctx}: 'max_shift' must be a non-negative int"
            raise ConfigError(msg)
        parsed["max_shift"] = max_shift

    decay = raw.get("decay")
    if decay is not None:
        _validate_positive_float(decay, f"{ctx} decay")
        parsed["decay"] = float(decay)

    return parsed


def _parse_variable(raw: object, rule_id: str, var_key: str) -> VariableConfig:
    if not isinstance(raw, dict):
        msg = f"Rule '{rule_id}', variable '{var_key}': expected dict"
        raise ConfigError(msg)

    _reject_unknown_keys(
        raw, _ALLOWED_VARIABLE_KEYS, f"rule '{rule_id}', variable '{var_key}'"
    )

    weight = raw.get("weight", 0.7)
    _validate_non_negative_float(
        weight, f"rule '{rule_id}', variable '{var_key}' weight"
    )

    levels = raw.get("levels")
    if levels is not None:
        if not isinstance(levels, list):
            msg = f"Rule '{rule_id}', variable '{var_key}': 'levels' must be a list, got {type(levels).__name__}"
            raise ConfigError(msg)
        for i, level in enumerate(levels):
            if not isinstance(level, int):
                msg = f"Rule '{rule_id}', variable '{var_key}': levels[{i}] must be an int, got {type(level).__name__}"
                raise ConfigError(msg)

    level_weights_raw = raw.get("level_weights")
    level_weights: dict[int, float] | None = None
    if level_weights_raw is not None:
        ctx = f"rule '{rule_id}', variable '{var_key}'"
        if not isinstance(level_weights_raw, dict):
            msg = f"{ctx}: 'level_weights' must be a dict, got {type(level_weights_raw).__name__}"
            raise ConfigError(msg)
        if levels is not None:
            msg = f"{ctx}: 'levels' and 'level_weights' are mutually exclusive"
            raise ConfigError(msg)
        level_weights = {}
        for k, v in level_weights_raw.items():
            try:
                level_idx = int(k)
            except (ValueError, TypeError):
                msg = f"{ctx}: level_weights key '{k}' must be an integer"
                raise ConfigError(msg) from None
            _validate_non_negative_float(v, f"{ctx} level_weights[{k}]")
            level_weights[level_idx] = float(v)

    match_mode = raw.get("match_mode", "embedding")
    if match_mode not in _VALID_MATCH_MODES:
        ctx = f"rule '{rule_id}', variable '{var_key}'"
        msg = f"{ctx}: 'match_mode' must be one of {sorted(_VALID_MATCH_MODES)}, got '{match_mode}'"
        raise ConfigError(msg)

    return VariableConfig(
        weight=weight, levels=levels, level_weights=level_weights, match_mode=match_mode
    )


def _reject_unknown_keys(
    raw: dict[str, object], allowed: set[str], context: str
) -> None:
    unknown = set(raw.keys()) - allowed
    if unknown:
        msg = f"Unknown keys in {context}: {sorted(unknown)}"
        raise ConfigError(msg)


def _validate_positive_float(value: object, name: str) -> None:
    if not isinstance(value, int | float):
        msg = f"'{name}' must be a number, got {type(value).__name__}"
        raise ConfigError(msg)
    if not math.isfinite(value) or value <= 0:
        msg = f"'{name}' must be a positive finite number, got {value}"
        raise ConfigError(msg)


def _validate_non_negative_float(value: object, name: str) -> None:
    if not isinstance(value, int | float):
        msg = f"'{name}' must be a number, got {type(value).__name__}"
        raise ConfigError(msg)
    if not math.isfinite(value) or value < 0:
        msg = f"'{name}' must be a non-negative finite number, got {value}"
        raise ConfigError(msg)


def _validate_max_decimal_places(value: object, *, max_places: int, name: str) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        msg = f"'{name}' must be a number, got {type(value).__name__}"
        raise ConfigError(msg)

    try:
        decimal_value = Decimal(str(value))
    except InvalidOperation as exc:
        msg = f"'{name}' must be a finite decimal number, got {value!r}"
        raise ConfigError(msg) from exc

    places = max(0, -decimal_value.as_tuple().exponent)
    if places > max_places:
        msg = f"'{name}' must have at most {max_places} decimal places, got {value!r}"
        raise ConfigError(msg)


def get_gca_rule_config(gca_config: GcaConfig, rule_id: str) -> GcaRuleConfig:
    """Look up rule-specific config, falling back to top-level defaults."""
    if rule_id in gca_config.rules:
        return gca_config.rules[rule_id]
    return GcaRuleConfig(
        eps=gca_config.default_eps,
        template_weight=gca_config.default_template_weight,
    )
