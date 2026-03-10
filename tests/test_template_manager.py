import hashlib

from sanity_log_parser.parsing.template_manager import RuleTemplateManager

from sanity_log_parser.parsing.template_manager import RuleTemplateManager


def test_get_pure_template_replaces_quoted_vars_and_numbers(template_manager):
    text = "Signal 'signal_name' has value 42 and count 7"
    pure = template_manager.get_pure_template(text)
    assert pure == "Signal '<VAR>' has value <NUM> and count <NUM>"


def test_get_pure_template_strips_outer_whitespace(template_manager):
    text = "   Path 'foo/bar_1' count 12 exceeds 3   "
    pure = template_manager.get_pure_template(text)
    assert pure == "Path '<VAR>' count <NUM> exceeds <NUM>"


def test_get_rule_id_returns_loaded_rule_for_known_template(template_manager):
    known_message = (
        "Signal 'top/u_cpu/decode/pipe_4' float Signal "
        "'top/u_cpu/decode/pipe_5' float 'top/u_cpu/decode/pipe_5' signal conflicted"
    )
    known_template = template_manager.get_pure_template(known_message)
    assert template_manager.get_rule_id(known_template) == "R001"


def test_get_rule_id_returns_unknown_hash_prefix_for_missing_template(template_manager):
    missing_template = "Completely unseen '<VAR>' with count <NUM>"
    expected = (
        f"UNKNOWN_{hashlib.md5(missing_template.encode()).hexdigest()[:6].upper()}"
    )
    assert template_manager.get_rule_id(missing_template) == expected


def test_get_rule_id_prefers_exact_message_when_template_is_ambiguous(tmp_path):
    path = tmp_path / "rules.log"
    path.write_text(
        "\n".join(
            [
                "R001 HIGH INFO Signal 'u_top' not found",
                "R002 HIGH INFO Signal 'u_cpu' not found",
            ]
        ),
        encoding="utf-8",
    )
    manager = RuleTemplateManager(str(path))
    template = manager.get_pure_template("Signal 'u_top' not found")

    assert manager.get_rule_id(template, raw_log="Signal 'u_top' not found") == "R001"
    assert manager.get_rule_id(template, raw_log="Signal 'u_cpu' not found") == "R002"


def test_get_rule_id_returns_unknown_for_ambiguous_template_without_exact_match(tmp_path):
    path = tmp_path / "rules.log"
    path.write_text(
        "\n".join(
            [
                "R001 HIGH INFO Signal 'u_top' not found",
                "R002 HIGH INFO Signal 'u_cpu' not found",
            ]
        ),
        encoding="utf-8",
    )
    manager = RuleTemplateManager(str(path))
    template = manager.get_pure_template("Signal 'u_any' not found")
    expected = (
        f"UNKNOWN_{hashlib.md5(template.encode()).hexdigest()[:6].upper()}"
    )

    assert manager.get_rule_id(template, raw_log="Signal 'u_any' not found") == expected
