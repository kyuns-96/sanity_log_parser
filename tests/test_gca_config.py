"""Tests for sanity_log_parser.gca.config."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from sanity_log_parser.gca.config import (
    ConfigError,
    GcaConfig,
    GcaRuleConfig,
    VariableConfig,
    get_gca_rule_config,
    load_gca_config,
)


def _write_config(path: Path, payload: object) -> str:
    p = path / "rule_clustering_config.json"
    _ = p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


# --- Config loading (7) ---


def test_load_gca_config_defaults(tmp_path: Path) -> None:
    cfg = load_gca_config(str(tmp_path / "missing.json"), strict=False)
    assert cfg == GcaConfig()


def test_load_gca_config_nonstrict_unreadable_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    p = tmp_path / "unreadable.json"
    _ = p.write_text("{}", encoding="utf-8")
    p.chmod(0o000)
    try:
        with caplog.at_level(logging.WARNING):
            cfg = load_gca_config(str(p), strict=False)
        assert cfg == GcaConfig()
        assert "Using defaults" in caplog.text
    finally:
        p.chmod(0o644)


def test_load_gca_config_nonstrict_bad_structure(tmp_path: Path) -> None:
    path = _write_config(tmp_path, [1, 2, 3])
    cfg = load_gca_config(path, strict=False)
    assert cfg == GcaConfig()


def test_load_gca_config_strict_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_gca_config(str(tmp_path / "missing.json"), strict=True)


def test_load_gca_config_strict_malformed_json(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    _ = p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_gca_config(str(p), strict=True)


def test_load_gca_config_with_rules(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.3,
            "default_template_weight": 0.4,
            "default_variable_weight": 0.6,
            "rules": {
                "CGR_0018": {
                    "eps": 0.15,
                    "template_weight": 0.1,
                    "variables": {
                        "0": {"weight": 0.7, "levels": [1]},
                        "1": {"weight": 0.2, "levels": [-2, -1]},
                    },
                }
            },
        },
    )
    cfg = load_gca_config(path, strict=True)
    assert cfg.default_eps == 0.3
    assert cfg.default_template_weight == 0.4
    assert cfg.default_variable_weight == 0.6
    assert "CGR_0018" in cfg.rules
    rule = cfg.rules["CGR_0018"]
    assert rule.eps == 0.15
    assert rule.template_weight == 0.1
    assert rule.variables[0] == VariableConfig(weight=0.7, levels=[1])
    assert rule.variables[1] == VariableConfig(weight=0.2, levels=[-2, -1])


def test_load_gca_config_with_level_weights(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"level_weights": {"-3": 0.5, "-2": 1.0}},
                    },
                },
            },
        },
    )
    cfg = load_gca_config(path, strict=True)
    vc = cfg.rules["R001"].variables[0]
    assert vc.level_weights == {-3: 0.5, -2: 1.0}
    assert vc.levels is None  # levels not set


def test_strict_level_weights_and_levels_mutual_exclusion(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"levels": [-1], "level_weights": {"-1": 0.5}},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_gca_config(path, strict=True)


def test_strict_level_weights_bad_key(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"level_weights": {"abc": 0.5}},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="integer"):
        load_gca_config(path, strict=True)


def test_strict_level_weights_negative_value(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"level_weights": {"-1": -0.5}},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="non-negative"):
        load_gca_config(path, strict=True)


def test_load_gca_config_with_match_mode(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"weight": 1.0, "match_mode": "jaccard"},
                    },
                },
            },
        },
    )
    cfg = load_gca_config(path, strict=True)
    vc = cfg.rules["R001"].variables[0]
    assert vc.match_mode == "jaccard"


def test_load_gca_config_with_pairwise_tree(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "pairwise_tree": {
                        "features": [
                            {"kind": "path_tfidf_char_wb", "ngram_range": [3, 6]},
                            {"kind": "level_exact", "levels": [-6]},
                        ],
                        "nodes": [
                            {"feature": 0, "threshold": 0.5, "left": 1, "right": 2},
                            {"value": 0},
                            {"value": 1},
                        ],
                    },
                }
            },
        },
    )
    cfg = load_gca_config(path, strict=True)
    tree = cfg.rules["DES_0001"].pairwise_tree
    assert tree is not None
    assert tree["features"][0]["kind"] == "path_tfidf_char_wb"
    assert tree["features"][0]["ngram_range"] == (3, 6)
    assert tree["nodes"][2]["value"] == 1


def test_strict_invalid_pairwise_tree_shape(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "pairwise_tree": [],
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="pairwise_tree"):
        load_gca_config(path, strict=True)


def test_strict_invalid_pairwise_tree_feature(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "pairwise_tree": {
                        "features": [{"kind": "unknown"}],
                        "nodes": [{"value": 1}],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="kind"):
        load_gca_config(path, strict=True)


def test_strict_invalid_pairwise_tree_feature_index_out_of_range(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "pairwise_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [
                            {"feature": 1, "threshold": 0.5, "left": 1, "right": 2},
                            {"value": 0},
                            {"value": 1},
                        ],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="feature index"):
        load_gca_config(path, strict=True)


def test_strict_invalid_pairwise_tree_child_index_out_of_range(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "pairwise_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [
                            {"feature": 0, "threshold": 0.5, "left": 1, "right": 99},
                            {"value": 0},
                        ],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="child index"):
        load_gca_config(path, strict=True)


def test_strict_invalid_pairwise_tree_cycle(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "pairwise_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [
                            {"feature": 0, "threshold": 0.5, "left": 1, "right": 2},
                            {"feature": 0, "threshold": 0.5, "left": 0, "right": 2},
                            {"value": 1},
                        ],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="cycle"):
        load_gca_config(path, strict=True)


def test_load_gca_config_with_adaptive_eps_tree(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "adaptive_eps_tree": {
                        "features": [
                            {"kind": "level_exact", "levels": [-6]},
                        ],
                        "nodes": [
                            {"feature": 0, "threshold": 0.5, "left": 1, "right": 2},
                            {"value": 0.1},
                            {"value": 0.4},
                        ],
                    },
                }
            },
        },
    )
    cfg = load_gca_config(path, strict=True)
    tree = cfg.rules["DES_0001"].adaptive_eps_tree
    assert tree is not None
    assert tree["features"][0]["kind"] == "level_exact"
    assert tree["nodes"][1]["value"] == 0.1


def test_strict_invalid_adaptive_eps_tree_child_index_out_of_range(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "adaptive_eps_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [
                            {"feature": 0, "threshold": 0.5, "left": 1, "right": 9},
                            {"value": 0.1},
                        ],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="child index"):
        load_gca_config(path, strict=True)


def test_strict_invalid_pairwise_tree_threshold_precision(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "pairwise_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [
                            {"feature": 0, "threshold": 0.1234, "left": 1, "right": 2},
                            {"value": 1},
                            {"value": 0},
                        ],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="decimal places"):
        load_gca_config(path, strict=True)


def test_strict_invalid_adaptive_eps_tree_threshold_precision(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "adaptive_eps_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [
                            {"feature": 0, "threshold": 0.1234, "left": 1, "right": 2},
                            {"value": 0.1},
                            {"value": 0.4},
                        ],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="decimal places"):
        load_gca_config(path, strict=True)


def test_strict_invalid_adaptive_eps_tree_leaf_value(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "adaptive_eps_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [{"value": -0.1}],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="positive finite"):
        load_gca_config(path, strict=True)


def test_strict_invalid_adaptive_eps_tree_leaf_precision(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "rules": {
                "DES_0001": {
                    "adaptive_eps_tree": {
                        "features": [{"kind": "level_exact", "levels": [-6]}],
                        "nodes": [{"value": 0.1234}],
                    },
                }
            },
        },
    )
    with pytest.raises(ConfigError, match="decimal places"):
        load_gca_config(path, strict=True)


def test_strict_invalid_match_mode(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"match_mode": "invalid_mode"},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="match_mode"):
        load_gca_config(path, strict=True)


def test_load_gca_config_partial_rule(tmp_path: Path) -> None:
    """Rule with only eps inherits template_weight from top-level default."""
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.5,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {"eps": 0.1},
            },
        },
    )
    cfg = load_gca_config(path, strict=True)
    assert cfg.rules["R001"].eps == 0.1
    assert cfg.rules["R001"].template_weight == 0.5


# --- Strict validation: numeric (7) ---


def test_strict_invalid_eps_zero(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"default_eps": 0})
    with pytest.raises(ConfigError, match="positive finite"):
        load_gca_config(path, strict=True)


def test_strict_invalid_eps_nan(tmp_path: Path) -> None:
    p = tmp_path / "nan.json"
    _ = p.write_text('{"default_eps": NaN}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_gca_config(str(p), strict=True)


def test_strict_invalid_eps_inf(tmp_path: Path) -> None:
    p = tmp_path / "inf.json"
    _ = p.write_text('{"default_eps": Infinity}', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_gca_config(str(p), strict=True)


def test_strict_negative_weight(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": -1.0,
        },
    )
    with pytest.raises(ConfigError, match="non-negative"):
        load_gca_config(path, strict=True)


def test_strict_inf_weight(tmp_path: Path) -> None:
    # json.dumps converts inf to Infinity which isn't valid JSON — write manually
    p = tmp_path / "inf_w.json"
    _ = p.write_text(
        '{"default_eps": 0.2, "rules": {"R001": {"template_weight": 1e309}}}',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_gca_config(str(p), strict=True)


def test_strict_nan_default_template_weight(tmp_path: Path) -> None:
    p = tmp_path / "nan_tw.json"
    _ = p.write_text(
        '{"default_eps": 0.2, "default_template_weight": NaN}', encoding="utf-8"
    )
    with pytest.raises(ConfigError):
        load_gca_config(str(p), strict=True)


def test_strict_inf_default_variable_weight(tmp_path: Path) -> None:
    p = tmp_path / "inf_vw.json"
    _ = p.write_text(
        '{"default_eps": 0.2, "default_template_weight": 0.3, "default_variable_weight": 1e309}',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_gca_config(str(p), strict=True)


# --- Strict validation: structural (8) ---


def test_strict_non_int_levels(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"weight": 0.5, "levels": ["abc"]},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="int"):
        load_gca_config(path, strict=True)


def test_strict_invalid_variable_key(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "abc": {"weight": 0.5},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="non-negative integer"):
        load_gca_config(path, strict=True)


def test_strict_unknown_top_key(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "unknown_key": True,
        },
    )
    with pytest.raises(ConfigError, match="Unknown keys"):
        load_gca_config(path, strict=True)


def test_strict_unknown_rule_key(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {"eps": 0.1, "bogus": True},
            },
        },
    )
    with pytest.raises(ConfigError, match="Unknown keys"):
        load_gca_config(path, strict=True)


def test_strict_unknown_variable_key(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"weight": 0.5, "extra": 1},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="Unknown keys"):
        load_gca_config(path, strict=True)


def test_strict_top_level_not_dict(tmp_path: Path) -> None:
    path = _write_config(tmp_path, [1, 2, 3])
    with pytest.raises(ConfigError, match="top-level dict"):
        load_gca_config(path, strict=True)


def test_strict_rules_not_dict(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": [],
        },
    )
    with pytest.raises(ConfigError, match="dict"):
        load_gca_config(path, strict=True)


def test_strict_levels_not_list(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "default_eps": 0.2,
            "default_template_weight": 0.3,
            "default_variable_weight": 0.7,
            "rules": {
                "R001": {
                    "variables": {
                        "0": {"weight": 0.5, "levels": "abc"},
                    },
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="list"):
        load_gca_config(path, strict=True)


# --- Rule lookup (2) ---


def test_get_gca_rule_config_known() -> None:
    rule = GcaRuleConfig(
        eps=0.1, template_weight=0.2, variables={0: VariableConfig(weight=0.9)}
    )
    cfg = GcaConfig(rules={"R001": rule})
    assert get_gca_rule_config(cfg, "R001") is rule


def test_get_gca_rule_config_unknown() -> None:
    cfg = GcaConfig(default_eps=0.3, default_template_weight=0.4)
    result = get_gca_rule_config(cfg, "UNKNOWN")
    assert result.eps == 0.3
    assert result.template_weight == 0.4
    assert result.variables == {}


# --- Bundled config (1) ---


def test_bundled_config_exists() -> None:
    from sanity_log_parser.gca import GCA_DEFAULT_CONFIG_PATH

    assert GCA_DEFAULT_CONFIG_PATH.is_file()
    cfg = load_gca_config(str(GCA_DEFAULT_CONFIG_PATH), strict=True)
    assert isinstance(cfg, GcaConfig)
