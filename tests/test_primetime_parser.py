from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from sanity_log_parser.parsing.primetime_parser import PrimeTimeParser


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REAL_REPORT = textwrap.dedent("""\
    ******************************************
    Report : report_constraint_analysis
    Version: U-2022.12-SP5-3
    Date   : Tue Sep   2 17:40:49 2025
    ******************************************

    Severity  Rule  Count  Waived  Message

    -----------------------------------------
     error                  62   0
      CGR_0018          46    0 Clock 'clk1' is generated from 'clk2'
           1 of 46          0    Clock 'GEN_A' is generated from 'MSTR'
           2 of 46          0    Clock 'GEN_B' is generated from 'MSTR2'
           3 of 46          0    Clock 'GEN_C' is generated from 'MSTR3'
      CGR_0002            4     0 Clock 'x' is generated from 'y'
           1 of 4           0    Clock 'aclk' is generated from 'bclk'
           2 of 4           0    No clocks defined
     warning               12   0
      CLK_0035           12    0 Path 'foo' violates constraint
           1 of 12          0    Path 'bar_1' violates constraint
           2 of 12          0    Path 'bar_2' violates constraint
""")


@pytest.fixture()
def real_rpt(tmp_path: Path) -> str:
    p = tmp_path / "real.rpt"
    p.write_text(REAL_REPORT, encoding="utf-8")
    return str(p)


@pytest.fixture()
def empty_rpt(tmp_path: Path) -> str:
    p = tmp_path / "empty.rpt"
    p.write_text("", encoding="utf-8")
    return str(p)


def _write_rpt(tmp_path: Path, content: str) -> str:
    p = tmp_path / "test.rpt"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Instance-only output
# ---------------------------------------------------------------------------


def test_parse_file_returns_instance_lines_only(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)
    assert len(results) == 7


# ---------------------------------------------------------------------------
# Rule ID inheritance
# ---------------------------------------------------------------------------


def test_rule_id_inherited_from_parent(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)
    assert results[0]["rule_id"] == "CGR_0018"
    assert results[1]["rule_id"] == "CGR_0018"
    assert results[2]["rule_id"] == "CGR_0018"
    assert results[3]["rule_id"] == "CGR_0002"
    assert results[4]["rule_id"] == "CGR_0002"
    assert results[5]["rule_id"] == "CLK_0035"
    assert results[6]["rule_id"] == "CLK_0035"


# ---------------------------------------------------------------------------
# Severity propagation and reset
# ---------------------------------------------------------------------------


def test_severity_propagated_from_section(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)
    for r in results[:5]:
        assert r["severity"] == "error"
    for r in results[5:]:
        assert r["severity"] == "warning"


def test_severity_reset_on_new_section(tmp_path: Path) -> None:
    content = (
        " error    10   0\n"
        "  CGR_0001    5   0 Msg 'a'\n"
        "       1 of 5   0   Msg 'x'\n"
        " warning   5   0\n"
        "  CLK_0001    3   0 Msg 'b'\n"
        "       1 of 3   0   Msg 'y'\n"
    )
    rpt = tmp_path / "test.rpt"
    rpt.write_text(content, encoding="utf-8")
    rpt = str(rpt)
    results = PrimeTimeParser().parse_file(rpt)
    assert results[0]["severity"] == "error"
    assert results[0]["rule_id"] == "CGR_0001"
    assert results[1]["severity"] == "warning"
    assert results[1]["rule_id"] == "CLK_0001"


# ---------------------------------------------------------------------------
# Orphan fallbacks
# ---------------------------------------------------------------------------


def test_orphan_instance_before_any_context(tmp_path: Path) -> None:
    rpt = _write_rpt(
        tmp_path,
        """\
             1 of 10   0   Orphan 'sig'
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert len(results) == 1
    assert results[0]["rule_id"] == "UNKNOWN"
    assert results[0]["severity"] == "unknown"


def test_orphan_after_severity_but_before_parent(tmp_path: Path) -> None:
    content = " error    5   0\n     1 of 5   0   Orphan 'sig'\n"
    rpt = tmp_path / "test.rpt"
    rpt.write_text(content, encoding="utf-8")
    rpt = str(rpt)
    results = PrimeTimeParser().parse_file(rpt)
    assert results[0]["rule_id"] == "UNKNOWN"
    assert results[0]["severity"] == "error"


# ---------------------------------------------------------------------------
# Variable extraction
# ---------------------------------------------------------------------------


def test_variables_extracted(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)
    assert results[0]["variables"] == ("GEN_A", "MSTR")


def test_no_var_when_no_quotes(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)
    # "No clocks defined" has no quoted vars
    assert results[4]["variables"] == ("NO_VAR",)


# ---------------------------------------------------------------------------
# Template normalization
# ---------------------------------------------------------------------------


def test_template_normalizes_vars(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)
    assert "'<VAR>'" in results[0]["template"]
    assert "GEN_A" not in results[0]["template"]


def test_template_normalizes_numbers(tmp_path: Path) -> None:
    rpt = _write_rpt(
        tmp_path,
        """\
         error    1   0
          CGR_0001    1   0 Signal has 42 errors
               1 of 1   0   Signal has 99 errors
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert "<NUM>" in results[0]["template"]
    assert "99" not in results[0]["template"]


# ---------------------------------------------------------------------------
# raw_log
# ---------------------------------------------------------------------------


def test_raw_log_is_message_without_prefix(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)
    assert "GEN_A" in results[0]["raw_log"]
    assert "of" not in results[0]["raw_log"].split()[0:2]


# ---------------------------------------------------------------------------
# Skip lines
# ---------------------------------------------------------------------------


def test_skip_file_header(tmp_path: Path) -> None:
    rpt = _write_rpt(
        tmp_path,
        """\
        ******************************************
        Report : report_constraint_analysis
        Version: U-2022.12-SP5-3
        Date   : Tue Sep   2 17:40:49 2025
        ******************************************
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert results == []


def test_skip_dashes_and_equals(tmp_path: Path) -> None:
    rpt = _write_rpt(
        tmp_path,
        """\
        ============================
        ----------------------------
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert results == []


def test_empty_file(empty_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(empty_rpt)
    assert results == []


# ---------------------------------------------------------------------------
# State transition regression: "Rule" and "Severity" headers skipped
# ---------------------------------------------------------------------------


def test_rule_and_severity_headers_skipped(tmp_path: Path) -> None:
    rpt = _write_rpt(
        tmp_path,
        """\
        Severity  Rule  Count  Waived  Message
        Rule  Severity  Count
         error    5   0
          CGR_0001    3   0 Msg 'a'
               1 of 3   0   Msg 'x'
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert len(results) == 1
    assert results[0]["rule_id"] == "CGR_0001"
    assert results[0]["severity"] == "error"


# ---------------------------------------------------------------------------
# Substring trap negatives
# ---------------------------------------------------------------------------


def test_parent_pattern_not_triggered_by_message_content(tmp_path: Path) -> None:
    """A message body containing 'CGR_0018' should not trigger parent detection."""
    rpt = _write_rpt(
        tmp_path,
        """\
         error    1   0
          CGR_0001    1   0 Rule CGR_0018 referenced
               1 of 1   0   Rule CGR_0018 mentioned in 'sig'
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert len(results) == 1
    assert results[0]["rule_id"] == "CGR_0001"


def test_instance_pattern_not_triggered_by_prose(tmp_path: Path) -> None:
    """Prose like 'Total: 4 of 46 rules violated' should not be parsed."""
    rpt = _write_rpt(
        tmp_path,
        """\
        Total: 4 of 46 rules violated
        Summary 1 of 10 checks passed
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert results == []


def test_severity_pattern_not_triggered_by_prose(tmp_path: Path) -> None:
    """Prose like 'error handling is important' should not trigger severity."""
    rpt = _write_rpt(
        tmp_path,
        """\
        error handling is important
        warning about this feature
    """,
    )
    parser = PrimeTimeParser()
    parser.parse_file(rpt)
    assert parser.current_severity == "unknown"


# ---------------------------------------------------------------------------
# Parent with zero instances
# ---------------------------------------------------------------------------


def test_parent_with_zero_instances_followed_by_next(tmp_path: Path) -> None:
    rpt = _write_rpt(
        tmp_path,
        """\
         error    3   0
          CGR_0001    0   0 Msg 'a'
          CGR_0002    3   0 Msg 'b'
               1 of 3   0   Msg 'x'
    """,
    )
    results = PrimeTimeParser().parse_file(rpt)
    assert len(results) == 1
    assert results[0]["rule_id"] == "CGR_0002"


# ---------------------------------------------------------------------------
# RuleTemplateManager(None) safety
# ---------------------------------------------------------------------------


def test_template_manager_none_works() -> None:
    from sanity_log_parser.parsing.template_manager import RuleTemplateManager

    tm = RuleTemplateManager(None)
    result = tm.get_pure_template("Clock 'clk1' has 42 errors")
    assert "'<VAR>'" in result
    assert "<NUM>" in result


# ---------------------------------------------------------------------------
# Anonymized real fixture with golden expected output
# ---------------------------------------------------------------------------


def test_real_fixture_golden_output(real_rpt: str) -> None:
    results = PrimeTimeParser().parse_file(real_rpt)

    assert len(results) == 7

    # CGR_0018 instances
    assert results[0] == {
        "rule_id": "CGR_0018",
        "variables": ("GEN_A", "MSTR"),
        "template": "Clock '<VAR>' is generated from '<VAR>'",
        "raw_log": "Clock 'GEN_A' is generated from 'MSTR'",
        "severity": "error",
    }
    assert results[2]["variables"] == ("GEN_C", "MSTR3")

    # CGR_0002 instances
    assert results[3]["rule_id"] == "CGR_0002"
    assert results[4]["variables"] == ("NO_VAR",)

    # CLK_0035 instances
    assert results[5]["rule_id"] == "CLK_0035"
    assert results[5]["severity"] == "warning"
    assert results[5]["variables"] == ("bar_1",)


# ---------------------------------------------------------------------------
# RULE_ID_LINE_PATTERN negative test (no end anchor)
# ---------------------------------------------------------------------------


def test_non_parent_line_not_misclassified(tmp_path: Path) -> None:
    """Lines that partially look like parent lines should not match."""
    rpt = _write_rpt(
        tmp_path,
        """\
        Design: CGR_0018 top module
        Scenario CGR_0018 timing
    """,
    )
    parser = PrimeTimeParser()
    parser.parse_file(rpt)
    # These lines should NOT update current_rule_id
    assert parser.current_rule_id == "UNKNOWN"
