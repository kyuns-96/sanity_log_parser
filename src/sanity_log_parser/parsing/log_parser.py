from __future__ import annotations

from typing import Any

from ..patterns import LINE_COUNTER_PATTERN, VAR_PATTERN
from .template_manager import RuleTemplateManager


class SubutaiParser:
    def __init__(self, template_manager: RuleTemplateManager) -> None:
        self.template_manager = template_manager

    def parse_line(self, line: str) -> dict[str, Any] | None:
        line = line.strip()
        if not line:
            return None

        if not LINE_COUNTER_PATTERN.search(line):
            return None

        line = " ".join(line.split()[4:])

        variables = VAR_PATTERN.findall(line)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)

        template = self.template_manager.get_pure_template(line)
        rule_id = self.template_manager.get_rule_id(template)

        return {
            "rule_id": rule_id,
            "variables": var_tuple,
            "template": template,
            "raw_log": line,
        }
