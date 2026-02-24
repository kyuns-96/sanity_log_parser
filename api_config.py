from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Callable, Mapping, Any


_SUPPORTED_EMBEDDINGS_BACKENDS = {"local", "openai_compatible"}


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    model: str
    api_key: str | None


@dataclass(frozen=True)
class EmbeddingsConfig:
    backend: str
    openai_compatible: OpenAICompatibleConfig | None


def load_embeddings_config(
    config_path: str = "config.json",
    environ: Mapping[str, str] | None = None,
    warn: Callable[[str], None] | None = None,
) -> EmbeddingsConfig:
    env = os.environ if environ is None else environ
    raw_config = _load_json_config(config_path, warn)

    backend = _as_string(raw_config.get("embeddings_backend"), default="local")
    if backend not in _SUPPORTED_EMBEDDINGS_BACKENDS:
        _warn(warn, f"Invalid embeddings_backend '{backend}'. Falling back to 'local'.")
        backend = "local"

    openai_config = raw_config.get("openai_compatible")
    openai_data = openai_config if isinstance(openai_config, dict) else {}

    base_url = _as_string(openai_data.get("base_url"), default="").rstrip("/")
    model = _as_string(openai_data.get("model"), default="text-embedding-3-small")
    api_key = _as_optional_string(openai_data.get("api_key")) or _as_optional_string(env.get("OPENAI_API_KEY"))

    if backend == "openai_compatible" and not base_url:
        _warn(warn, "Missing openai_compatible.base_url in config.json. Falling back to 'local'.")
        backend = "local"

    if backend == "openai_compatible":
        return EmbeddingsConfig(
            backend=backend,
            openai_compatible=OpenAICompatibleConfig(base_url=base_url, model=model, api_key=api_key),
        )

    return EmbeddingsConfig(backend="local", openai_compatible=None)


def _load_json_config(
    config_path: str,
    warn: Callable[[str], None] | None,
) -> dict[str, Any]:
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            loaded = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        _warn(warn, f"Failed to read '{config_path}': {exc}. Falling back to defaults.")
        return {}

    if not isinstance(loaded, dict):
        _warn(warn, f"'{config_path}' must contain a JSON object. Falling back to defaults.")
        return {}
    return loaded


def _warn(warn: Callable[[str], None] | None, message: str) -> None:
    if warn is not None:
        warn(message)


def _as_string(value: Any, default: str) -> str:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed else default
    return default


def _as_optional_string(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed if trimmed else None
    return None
