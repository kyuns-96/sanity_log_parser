def test_parse_line_returns_none_for_non_matching_prefix(parser, sample_non_matching_line):
    assert parser.parse_line(sample_non_matching_line) is None


def test_parse_line_parses_matching_line_and_extracts_variables(parser, sample_matching_line):
    parsed = parser.parse_line(sample_matching_line)

    assert parsed is not None
    assert parsed["variables"] == (
        "top/u_cpu/decode/pipe_4",
        "top/u_cpu/decode/pipe_5",
        "top/u_cpu/decode/pipe_5",
    )
    assert parsed["rule_id"].startswith("UNKNOWN_")
    assert parsed["template"] == "'<VAR>' float Signal '<VAR>' float '<VAR>' signal conflicted"


def test_parse_line_matching_prefix_with_no_quotes_yields_no_var(parser):
    line = "1 of 1 A B no_quoted_variables_here"
    parsed = parser.parse_line(line)

    assert parsed is not None
    assert parsed["variables"] == ("NO_VAR",)


