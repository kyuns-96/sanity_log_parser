from sanity_log_parser.clustering.logic import LogicClusterer


def test_get_logic_signature_replaces_digits_with_wildcards():
    clusterer = LogicClusterer()
    sig = clusterer.get_logic_signature(("pipe_4", "pipe_55"))
    assert sig == "pipe_* / pipe_*"


def test_get_logic_signature_handles_no_var_cases():
    clusterer = LogicClusterer()
    assert clusterer.get_logic_signature(("NO_VAR",)) == "NO_VAR"
    assert clusterer.get_logic_signature(tuple()) == "NO_VAR"


def test_run_groups_logs_with_same_rule_signature_and_template():
    clusterer = LogicClusterer()
    parsed_logs = [
        {"rule_id": "R1", "variables": ("pipe_1",), "template": "T1", "raw_log": "a"},
        {"rule_id": "R1", "variables": ("pipe_2",), "template": "T1", "raw_log": "b"},
    ]

    results = clusterer.run(parsed_logs)
    assert len(results) == 1
    assert results[0]["count"] == 2
    assert results[0]["pattern"] == "pipe_*"


def test_run_sorts_groups_by_count_descending():
    clusterer = LogicClusterer()
    parsed_logs = [
        {"rule_id": "R1", "variables": ("pipe_1",), "template": "T1", "raw_log": "a"},
        {"rule_id": "R1", "variables": ("pipe_2",), "template": "T1", "raw_log": "b"},
        {"rule_id": "R2", "variables": ("lane_9",), "template": "T2", "raw_log": "c"},
    ]

    results = clusterer.run(parsed_logs)
    assert len(results) == 2
    assert results[0]["count"] == 2
    assert results[1]["count"] == 1
