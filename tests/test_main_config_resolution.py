import os

import main


def test_resolve_embeddings_config_path_prefers_explicit_arg() -> None:
    assert main._resolve_embeddings_config_path("/tmp/custom.json") == "/tmp/custom.json"


def test_resolve_embeddings_config_path_uses_source_location_first(monkeypatch) -> None:
    monkeypatch.setattr(main, "__file__", "/repo/sanity_log_parser/main.py")

    source_config = os.path.join("/repo/sanity_log_parser", "config.json")

    def fake_isfile(path: str) -> bool:
        return path == source_config

    monkeypatch.setattr(main.os.path, "isfile", fake_isfile)

    assert main._resolve_embeddings_config_path(None) == source_config


def test_resolve_embeddings_config_path_falls_back_to_cwd(monkeypatch) -> None:
    monkeypatch.setattr(main, "__file__", "/repo/sanity_log_parser/main.py")
    monkeypatch.setattr(main.os.path, "isfile", lambda _path: False)

    assert main._resolve_embeddings_config_path(None) == "config.json"
