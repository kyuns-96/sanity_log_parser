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
    embed_batch_size: int = 512


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

    embed_batch_size = _as_positive_int(raw_config.get("embed_batch_size"), default=512)
    if embed_batch_size < 1:
        _warn(warn, f"Invalid embed_batch_size {embed_batch_size}. Using default 512.")
        embed_batch_size = 512

    openai_config = raw_config.get("openai_compatible")
    openai_data = openai_config if isinstance(openai_config, dict) else {}

    base_url = _as_string(openai_data.get("base_url"), default="").rstrip("/")
    model = _as_string(openai_data.get("model"), default="text-embedding-3-small")
    api_key = _as_optional_string(openai_data.get("api_key")) or _as_optional_string(
        env.get("OPENAI_API_KEY")
    )

    if backend == "openai_compatible" and not base_url:
        _warn(
            warn,
            "Missing openai_compatible.base_url in config.json. Falling back to 'local'.",
        )
        backend = "local"

    if backend == "openai_compatible":
        return EmbeddingsConfig(
            backend=backend,
            openai_compatible=OpenAICompatibleConfig(
                base_url=base_url, model=model, api_key=api_key
            ),
            embed_batch_size=embed_batch_size,
        )

    return EmbeddingsConfig(
        backend="local", openai_compatible=None, embed_batch_size=embed_batch_size
    )


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
        _warn(
            warn,
            f"'{config_path}' must contain a JSON object. Falling back to defaults.",
        )
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


def _as_positive_int(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default
