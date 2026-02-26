from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_ALLOWED_TOP_KEYS = {
    "default_eps",
    "default_template_weight",
    "default_variable_weight",
    "rules",
}
_ALLOWED_RULE_KEYS = {"eps", "template_weight", "variables"}
_ALLOWED_VARIABLE_KEYS = {"weight", "levels"}


class ConfigError(Exception):
    """Raised when a GCA config file is invalid."""


@dataclass(frozen=True)
class VariableConfig:
    weight: float = 0.7
    levels: list[int] | None = None


@dataclass(frozen=True)
class GcaRuleConfig:
    eps: float = 0.2
    template_weight: float = 0.3
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

    return GcaRuleConfig(eps=eps, template_weight=template_weight, variables=variables)


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

    return VariableConfig(weight=weight, levels=levels)


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


def get_gca_rule_config(gca_config: GcaConfig, rule_id: str) -> GcaRuleConfig:
    """Look up rule-specific config, falling back to top-level defaults."""
    if rule_id in gca_config.rules:
        return gca_config.rules[rule_id]
    return GcaRuleConfig(
        eps=gca_config.default_eps,
        template_weight=gca_config.default_template_weight,
    )
