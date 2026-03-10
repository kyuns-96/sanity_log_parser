from __future__ import annotations

import json
from pathlib import Path

import pytest

import sanity_log_parser.cli as cli
from sanity_log_parser.cli import PipelineOptions
from sanity_log_parser.config.embeddings import EmbeddingsConfig
from sanity_log_parser.config.resolution import LoadedEmbeddingsConfig


def _loaded_embeddings_config() -> LoadedEmbeddingsConfig:
    return LoadedEmbeddingsConfig(
        config=EmbeddingsConfig(
            backend="local",
            openai_compatible=None,
            embed_batch_size=512,
        ),
        config_path=None,
        warnings=[],
    )


def _opts(tmp_path: Path, **overrides: object) -> PipelineOptions:
    values = {
        "out": str(tmp_path / "results.json"),
        "ai_mode": "off",
        "embeddings_config": None,
        "rule_config": None,
        "json_indent": 2,
        "max_original_logs": 0,
        "no_color": True,
        "verbose": False,
        "log_file": "input.log",
        "template_file": None,
        "sanity_item": None,
    }
    values.update(overrides)
    return PipelineOptions(**values)


def _parsed_logs() -> list[dict[str, object]]:
    return [
        {
            "rule_id": "CLK_0035",
            "variables": ("bar_1",),
            "template": "Path '<VAR>' violates constraint",
            "raw_log": "Path 'bar_1' violates constraint",
        },
        {
            "rule_id": "CLK_0035",
            "variables": ("bar_2",),
            "template": "Path '<VAR>' violates constraint",
            "raw_log": "Path 'bar_2' violates constraint",
        },
    ]


def test_run_pipeline_skips_ai_init_when_ai_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "load_resolved_embeddings_config",
        lambda embeddings_config_arg: _loaded_embeddings_config(),
    )

    def fail_ai_clusterer(*args: object, **kwargs: object) -> object:
        raise AssertionError("AIClusterer should not be initialized when --ai off")

    monkeypatch.setattr(cli, "_build_ai_clusterer", lambda **kwargs: fail_ai_clusterer())

    rc = cli._run_pipeline(_parsed_logs(), _opts(tmp_path, ai_mode="off"))

    assert rc == 0


def test_run_pipeline_fails_when_ai_on_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "load_resolved_embeddings_config",
        lambda embeddings_config_arg: _loaded_embeddings_config(),
    )

    class UnavailableAI:
        ai_available = False

    monkeypatch.setattr(cli, "_build_ai_clusterer", lambda **kwargs: UnavailableAI())

    rc = cli._run_pipeline(_parsed_logs(), _opts(tmp_path, ai_mode="on"))

    captured = capsys.readouterr()
    assert rc == 1
    assert "AI clustering requested with --ai on" in captured.err


def test_run_pipeline_fails_when_strict_ai_run_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "load_resolved_embeddings_config",
        lambda embeddings_config_arg: _loaded_embeddings_config(),
    )

    class BrokenAI:
        ai_available = True

        def run(
            self,
            logic_groups: list[dict[str, object]],
            *,
            strict: bool = False,
        ) -> list[dict[str, object]]:
            assert strict is True
            raise RuntimeError("AI clustering failed during embedding computation.")

    monkeypatch.setattr(cli, "_build_ai_clusterer", lambda **kwargs: BrokenAI())

    rc = cli._run_pipeline(_parsed_logs(), _opts(tmp_path, ai_mode="on"))

    captured = capsys.readouterr()
    assert rc == 1
    assert "AI clustering failed during embedding computation." in captured.err


def test_run_pipeline_applies_max_original_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "load_resolved_embeddings_config",
        lambda embeddings_config_arg: _loaded_embeddings_config(),
    )

    def fail_ai_clusterer(*args: object, **kwargs: object) -> object:
        raise AssertionError("AIClusterer should not be initialized when --ai off")

    monkeypatch.setattr(cli, "_build_ai_clusterer", lambda **kwargs: fail_ai_clusterer())

    output_path = tmp_path / "results.json"
    rc = cli._run_pipeline(
        _parsed_logs(),
        _opts(tmp_path, ai_mode="off", out=str(output_path), max_original_logs=1),
    )

    assert rc == 0
    with output_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert len(payload["groups"]) == 1
    assert payload["groups"][0]["original_logs"] == [
        "Path 'bar_1' violates constraint"
    ]


def test_run_pipeline_persists_embeddings_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "load_resolved_embeddings_config",
        lambda embeddings_config_arg: LoadedEmbeddingsConfig(
            config=EmbeddingsConfig(
                backend="local",
                openai_compatible=None,
                embed_batch_size=512,
            ),
            config_path=None,
            warnings=["bad config"],
        ),
    )

    def fail_ai_clusterer(*args: object, **kwargs: object) -> object:
        raise AssertionError("AIClusterer should not be initialized when --ai off")

    monkeypatch.setattr(cli, "_build_ai_clusterer", lambda **kwargs: fail_ai_clusterer())

    output_path = tmp_path / "results.json"
    rc = cli._run_pipeline(
        _parsed_logs(),
        _opts(tmp_path, ai_mode="off", out=str(output_path)),
    )

    assert rc == 0
    with output_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    assert payload["run"]["ai"]["warnings"] == ["bad config"]
