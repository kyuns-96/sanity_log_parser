import hashlib


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
