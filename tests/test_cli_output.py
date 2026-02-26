import json
import re
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _run_main(
    args: list[str], cwd: Path, *, set_no_color: bool = True
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    if set_no_color:
        env["NO_COLOR"] = "1"
    else:
        if "NO_COLOR" in env:
            del env["NO_COLOR"]
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "sanity_log_parser", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


def _output(process: subprocess.CompletedProcess[str]) -> str:
    return (process.stdout or "") + (process.stderr or "")


def test_main_help_includes_usage_and_argument_placeholders(tmp_path: Path):
    process = _run_main(["cluster", "--help"], tmp_path)
    output = _output(process)

    assert process.returncode == 0
    assert "usage:" in output
    assert "LOG_FILE" in output
    assert "TEMPLATE_FILE" in output
    assert "--config" in output


def test_main_no_color_help_has_no_escape_codes(tmp_path: Path):
    process = _run_main(["cluster", "--help"], tmp_path)
    output = _output(process)

    assert "\x1b[" not in output


def test_main_no_color_flag_has_no_escape_codes_without_no_color_env(tmp_path: Path):
    process = _run_main(
        ["cluster", "--help", "--no-color"], tmp_path, set_no_color=False
    )
    output = _output(process)

    assert "\x1b[" not in output


def test_main_requires_at_least_log_file(tmp_path: Path):
    process = _run_main(["cluster"], tmp_path)
    output = _output(process)

    assert process.returncode != 0
    assert "usage:" in output


def test_main_empty_input_runs_zero_logs(tmp_path: Path):
    template_file = tmp_path / "rules.log"
    _ = template_file.write_text(
        "\n".join(
            [
                "Rule Severity Header Message",
                "R001 HIGH INFO Signal 'u_top' not found",
            ]
        ),
        encoding="utf-8",
    )
    log_file = tmp_path / "empty.log"
    _ = log_file.write_text("", encoding="utf-8")

    process = _run_main(["cluster", str(log_file), str(template_file)], tmp_path)
    output = _output(process)

    assert process.returncode == 0
    assert re.search(r"Input logs:\s+0", output)
    assert "Traceback" not in output


def test_main_single_file_primetime_mode(tmp_path: Path):
    """cluster command works with only LOG_FILE (no TEMPLATE_FILE)."""
    rpt = tmp_path / "sample.rpt"
    rpt.write_text(
        " error                  1   0\n"
        "  CGR_0018          1    0 Clock 'clk1' from 'clk2'\n"
        "       1 of 1          0    Clock 'GEN_A' from 'MSTR'\n",
        encoding="utf-8",
    )
    process = _run_main(["cluster", str(rpt)], tmp_path)
    output = _output(process)

    assert process.returncode == 0
    assert re.search(r"Input logs:\s+1", output)
    assert "Traceback" not in output


def test_main_legacy_two_file_mode_still_works(tmp_path: Path):
    """cluster command still works with LOG_FILE + TEMPLATE_FILE."""
    template_file = tmp_path / "rules.log"
    template_file.write_text(
        "Rule Severity Header Message\nR001 HIGH INFO Signal 'u_top' not found\n",
        encoding="utf-8",
    )
    log_file = tmp_path / "test.log"
    log_file.write_text(
        "4 of 4 0 Signal 'u_top' not found\n",
        encoding="utf-8",
    )
    process = _run_main(["cluster", str(log_file), str(template_file)], tmp_path)
    output = _output(process)

    assert process.returncode == 0
    assert re.search(r"Input logs:\s+1", output)
    assert "Traceback" not in output


def test_main_accepts_custom_config_path(tmp_path: Path):
    config_file = tmp_path / "custom-config.json"
    _ = config_file.write_text('{"embeddings_backend": "local"}', encoding="utf-8")

    template_file = tmp_path / "rules.log"
    _ = template_file.write_text(
        "\n".join(
            [
                "Rule Severity Header Message",
                "R001 HIGH INFO Signal 'u_top' not found",
            ]
        ),
        encoding="utf-8",
    )
    log_file = tmp_path / "empty.log"
    _ = log_file.write_text("", encoding="utf-8")

    process = _run_main(
        ["cluster", str(log_file), str(template_file), "--config", str(config_file)],
        tmp_path,
    )
    output = _output(process)

    assert process.returncode == 0
    assert re.search(r"Input logs:\s+0", output)
    assert "Traceback" not in output


# --- gca subcommand tests ---


def _sample_rpt_content() -> str:
    return (
        " error                  1   0\n"
        "  CGR_0018          1    0 Clock 'clk1' from 'clk2'\n"
        "       1 of 1          0    Clock 'GEN_A' from 'MSTR'\n"
    )


def test_main_gca_subcommand(tmp_path: Path):
    """gca REPORT_FILE succeeds and produces output with sanity_item in metadata."""
    rpt = tmp_path / "sample.rpt"
    rpt.write_text(_sample_rpt_content(), encoding="utf-8")
    out_file = tmp_path / "gca_results.json"

    process = _run_main(["gca", str(rpt), "--out", str(out_file)], tmp_path)
    output = _output(process)

    assert process.returncode == 0
    assert re.search(r"Input logs:\s+1", output)
    assert "Traceback" not in output

    with out_file.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["run"]["sanity_item"] == "gca"


def test_main_gca_missing_file(tmp_path: Path):
    """gca NONEXISTENT → exit 1, stderr contains 'Error', no traceback."""
    process = _run_main(["gca", "NONEXISTENT_FILE.rpt"], tmp_path)

    assert process.returncode == 1
    assert "Error" in (process.stderr or "")
    assert "Traceback" not in _output(process)


def test_main_gca_and_cluster_output_parity(tmp_path: Path):
    """gca and cluster on same report produce identical groups (except group_id)."""
    rpt = tmp_path / "sample.rpt"
    rpt.write_text(_sample_rpt_content(), encoding="utf-8")

    gca_out = tmp_path / "gca.json"
    cluster_out = tmp_path / "cluster.json"

    p_gca = _run_main(["gca", str(rpt), "--out", str(gca_out), "--ai", "off"], tmp_path)
    p_cluster = _run_main(
        ["cluster", str(rpt), "--out", str(cluster_out), "--ai", "off"], tmp_path
    )

    assert p_gca.returncode == 0
    assert p_cluster.returncode == 0

    with gca_out.open("r", encoding="utf-8") as f:
        gca_groups = json.load(f)["groups"]
    with cluster_out.open("r", encoding="utf-8") as f:
        cluster_groups = json.load(f)["groups"]

    assert len(gca_groups) == len(cluster_groups)

    gca_sorted = sorted(gca_groups, key=lambda g: g["group_id"])
    cluster_sorted = sorted(cluster_groups, key=lambda g: g["group_id"])

    compare_keys = [
        "rule_id",
        "total_count",
        "representative_template",
        "representative_pattern",
        "merged_variants_count",
        "group_type",
        "original_logs",
    ]
    for g, c in zip(gca_sorted, cluster_sorted):
        for key in compare_keys:
            assert g[key] == c[key], f"Mismatch on {key}: {g[key]} != {c[key]}"


def test_main_gca_exit_code_on_parse_error(tmp_path: Path):
    """Empty/malformed file → exit 1 or 0 with 0 logs, but never a traceback."""
    bad_file = tmp_path / "bad.rpt"
    bad_file.write_text("", encoding="utf-8")

    process = _run_main(["gca", str(bad_file)], tmp_path)

    assert "Traceback" not in _output(process)


def test_cluster_defaults_unchanged():
    """Regression: cluster subparser defaults and choices are unchanged."""
    sys.path.insert(0, str(ROOT / "src"))
    from sanity_log_parser.cli import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["cluster", "LOG", "TEMPLATE"])

    assert args.out == "subutai_results.json"
    assert args.ai == "auto"
    assert args.json_indent == 2
    assert args.max_original_logs == 0
    assert args.embeddings_config is None
    assert args.rule_config is None
    assert args.no_color is False
    assert args.verbose is False

    # Verify ai choices
    for action in parser._subparsers._group_actions:
        for name, subparser in action.choices.items():
            if name == "cluster":
                ai_action = next(
                    a for a in subparser._actions if getattr(a, "dest", None) == "ai"
                )
                assert set(ai_action.choices) == {"auto", "on", "off"}
