from sanity_log_parser.config.resolution import (
    EMBEDDINGS_CONFIG_ENV_VAR,
    resolve_embeddings_config_path,
)


def test_resolve_embeddings_config_path_prefers_explicit_arg() -> None:
    resolved = resolve_embeddings_config_path(embeddings_config_arg="/tmp/custom.json")
    assert resolved == "/tmp/custom.json"


def test_resolve_embeddings_config_path_uses_env_variable() -> None:
    resolved = resolve_embeddings_config_path(
        embeddings_config_arg=None,
        environ={EMBEDDINGS_CONFIG_ENV_VAR: "/tmp/from-env.json"},
    )
    assert resolved == "/tmp/from-env.json"


def test_resolve_embeddings_config_path_falls_back_to_none_when_no_sources(
    tmp_path,
) -> None:
    resolved = resolve_embeddings_config_path(
        embeddings_config_arg=None, environ={}, cwd=tmp_path
    )
    assert resolved is None
