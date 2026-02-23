import pytest

from log_parser import AIClusterer, RuleTemplateManager, SubutaiParser


@pytest.fixture
def synthetic_template_lines():
    return [
        "Rule Severity Header Message",
        "R001 HIGH INFO Signal 'top/u_cpu/decode/pipe_4' float Signal 'top/u_cpu/decode/pipe_5' float 'top/u_cpu/decode/pipe_5' signal conflicted",
        "R002 LOW INFO Path 'foo/bar_1' count 12 exceeds 3",
    ]


@pytest.fixture
def synthetic_template_file(tmp_path, synthetic_template_lines):
    path = tmp_path / "synthetic_templates.log"
    path.write_text("\n".join(synthetic_template_lines), encoding="utf-8")
    return str(path)


@pytest.fixture
def template_manager(synthetic_template_file):
    return RuleTemplateManager(synthetic_template_file)


@pytest.fixture
def parser(template_manager):
    return SubutaiParser(template_manager)


@pytest.fixture
def sample_matching_line():
    return "4 of 4 Signal 'top/u_cpu/decode/pipe_4' float Signal 'top/u_cpu/decode/pipe_5' float 'top/u_cpu/decode/pipe_5' signal conflicted"


@pytest.fixture
def sample_non_matching_line():
    return "Signal 'top/u_cpu/decode/pipe_4' float signal conflicted"


@pytest.fixture
def ai_clusterer_no_init():
    return AIClusterer.__new__(AIClusterer)
