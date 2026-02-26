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
