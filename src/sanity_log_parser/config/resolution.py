from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

from .embeddings import EmbeddingsConfig, load_embeddings_config
from .._util import trim_to_none


EMBEDDINGS_CONFIG_ENV_VAR = "SANITY_LOG_PARSER_EMBEDDINGS_CONFIG"
EMBEDDINGS_CONFIG_FILENAME = "config.json"
RULE_CONFIG_FILENAME = "rule_clustering_config.json"


@dataclass(frozen=True)
class LoadedEmbeddingsConfig:
    config: EmbeddingsConfig
    config_path: str | None
    warnings: list[str]


def resolve_embeddings_config_path(
    embeddings_config_arg: str | None,
    legacy_config_arg: str | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> str | None:
    explicit_arg = _first_non_empty(embeddings_config_arg, legacy_config_arg)
    if explicit_arg is not None:
        return explicit_arg

    env = os.environ if environ is None else environ
    env_path = trim_to_none(env.get(EMBEDDINGS_CONFIG_ENV_VAR))
    if env_path is not None:
        return env_path

    base_dir = Path.cwd() if cwd is None else Path(cwd)
    candidate = base_dir / EMBEDDINGS_CONFIG_FILENAME
    if candidate.is_file():
        return str(candidate)

    return None


def resolve_rule_config_path(
    rule_config_arg: str | None,
    repo_root: str | Path | None = None,
) -> str:
    explicit_arg = trim_to_none(rule_config_arg)
    if explicit_arg is not None:
        return explicit_arg

    base_dir = (
        Path(__file__).resolve().parents[3] if repo_root is None else Path(repo_root)
    )
    return str(base_dir / RULE_CONFIG_FILENAME)


def load_resolved_embeddings_config(
    embeddings_config_arg: str | None,
    legacy_config_arg: str | None = None,
    environ: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> LoadedEmbeddingsConfig:
    config_path = resolve_embeddings_config_path(
        embeddings_config_arg=embeddings_config_arg,
        legacy_config_arg=legacy_config_arg,
        environ=environ,
        cwd=cwd,
    )

    warnings: list[str] = []
    config = load_embeddings_config(
        config_path=config_path or "",
        environ=environ,
        warn=warnings.append,
    )
    return LoadedEmbeddingsConfig(
        config=config, config_path=config_path, warnings=warnings
    )


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        trimmed = trim_to_none(value)
        if trimmed is not None:
            return trimmed
    return None
