# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false

import json
from pathlib import Path

from sanity_log_parser.config.resolution import (
    EMBEDDINGS_CONFIG_ENV_VAR,
    load_resolved_embeddings_config,
    resolve_embeddings_config_path,
)


def _write_config(path: Path, payload: dict[str, object]) -> None:
    _ = path.write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_embeddings_config_path_prefers_explicit_arg() -> None:
    resolved = resolve_embeddings_config_path(
        embeddings_config_arg="/tmp/explicit.json",
        legacy_config_arg="/tmp/legacy.json",
        environ={EMBEDDINGS_CONFIG_ENV_VAR: "/tmp/from-env.json"},
        cwd="/tmp",
    )

    assert resolved == "/tmp/explicit.json"


def test_resolve_embeddings_config_path_uses_legacy_alias_when_primary_missing() -> (
    None
):
    resolved = resolve_embeddings_config_path(
        embeddings_config_arg=None,
        legacy_config_arg="/tmp/legacy.json",
        environ={EMBEDDINGS_CONFIG_ENV_VAR: "/tmp/from-env.json"},
        cwd="/tmp",
    )

    assert resolved == "/tmp/legacy.json"


def test_resolve_embeddings_config_path_uses_env_before_cwd_config(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path / "config.json", {"embeddings_backend": "local"})

    resolved = resolve_embeddings_config_path(
        embeddings_config_arg=None,
        legacy_config_arg=None,
        environ={EMBEDDINGS_CONFIG_ENV_VAR: "/tmp/from-env.json"},
        cwd=tmp_path,
    )

    assert resolved == "/tmp/from-env.json"


def test_resolve_embeddings_config_path_uses_cwd_config_when_present(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, {"embeddings_backend": "local"})

    resolved = resolve_embeddings_config_path(
        embeddings_config_arg=None,
        legacy_config_arg=None,
        environ={},
        cwd=tmp_path,
    )

    assert resolved == str(config_path)


def test_resolve_embeddings_config_path_returns_none_when_no_source(
    tmp_path: Path,
) -> None:
    resolved = resolve_embeddings_config_path(
        embeddings_config_arg=None,
        legacy_config_arg=None,
        environ={},
        cwd=tmp_path,
    )

    assert resolved is None


def test_load_resolved_embeddings_config_collects_warnings(tmp_path: Path) -> None:
    _write_config(tmp_path / "config.json", {"embeddings_backend": "invalid"})

    loaded = load_resolved_embeddings_config(
        embeddings_config_arg=None,
        environ={},
        cwd=tmp_path,
    )

    assert loaded.config_path == str(tmp_path / "config.json")
    assert loaded.config.backend == "local"
    assert loaded.warnings == [
        "Invalid embeddings_backend 'invalid'. Falling back to 'local'."
    ]


def test_load_resolved_embeddings_config_preserves_openai_api_key_fallback(
    tmp_path: Path,
) -> None:
    _write_config(
        tmp_path / "config.json",
        {
            "embeddings_backend": "openai_compatible",
            "openai_compatible": {
                "base_url": "https://example.org/v1",
                "model": "text-embedding-3-small",
            },
        },
    )

    loaded = load_resolved_embeddings_config(
        embeddings_config_arg=None,
        environ={"OPENAI_API_KEY": "from-env-key"},
        cwd=tmp_path,
    )

    assert loaded.config.backend == "openai_compatible"
    assert loaded.config.openai_compatible is not None
    assert loaded.config.openai_compatible.api_key == "from-env-key"
    assert loaded.warnings == []
