import json
from pathlib import Path

import pytest

from sanity_log_parser.config.embeddings import load_embeddings_config


def _write_config(tmp_path: Path, payload: dict[str, object]) -> str:
    path = tmp_path / "config.json"
    _ = path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_load_embeddings_config_defaults_to_local_when_config_missing(
    tmp_path: Path,
) -> None:
    warnings: list[str] = []
    config = load_embeddings_config(
        str(tmp_path / "missing.json"), warn=warnings.append
    )

    assert config.backend == "local"
    assert config.openai_compatible is None
    assert warnings == []


def test_load_embeddings_config_invalid_backend_falls_back_to_local_with_warning(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path, {"embeddings_backend": "nonsense"})
    warnings: list[str] = []

    config = load_embeddings_config(config_path=config_path, warn=warnings.append)

    assert config.backend == "local"
    assert config.openai_compatible is None
    assert warnings == [
        "Invalid embeddings_backend 'nonsense'. Falling back to 'local'."
    ]


def test_load_embeddings_config_openai_compatible_requires_base_url(
    tmp_path: Path,
) -> None:
    config_path = _write_config(
        tmp_path,
        {
            "embeddings_backend": "openai_compatible",
            "openai_compatible": {"base_url": "  "},
        },
    )
    warnings: list[str] = []

    config = load_embeddings_config(config_path=config_path, warn=warnings.append)

    assert config.backend == "local"
    assert config.openai_compatible is None
    assert len(warnings) == 1
    assert "Missing openai_compatible.base_url" in warnings[0]


@pytest.mark.parametrize(
    "config_key, environ_key, expected_key",
    [
        ("cfg-key", None, "cfg-key"),
        (None, "env-key", "env-key"),
    ],
)
def test_load_embeddings_config_prefers_config_api_key_and_falls_back_to_environ(
    tmp_path: Path,
    config_key: str | None,
    environ_key: str | None,
    expected_key: str,
) -> None:
    openai_payload = {
        "base_url": "https://example.org/v1",
        "model": "text-embedding-3-small",
    }
    if config_key is not None:
        openai_payload["api_key"] = config_key

    config_path = _write_config(
        tmp_path,
        {
            "embeddings_backend": "openai_compatible",
            "openai_compatible": openai_payload,
        },
    )
    env: dict[str, str] = {}
    if environ_key is not None:
        env["OPENAI_API_KEY"] = environ_key

    config = load_embeddings_config(config_path=config_path, environ=env)

    assert config.backend == "openai_compatible"
    assert config.openai_compatible is not None
    assert config.openai_compatible.base_url == "https://example.org/v1"
    assert config.openai_compatible.api_key == expected_key
