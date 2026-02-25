from __future__ import annotations

import re
from typing import Any

from .template_manager import RuleTemplateManager


class SubutaiParser:
    def __init__(self, template_manager: RuleTemplateManager) -> None:
        self.var_pattern = re.compile(r"'(.*?)'")
        self.template_manager = template_manager
        self.delimiters = [("/", 1), ("_", 2), ("-", 3)]

    def extract_variable_stems(self, variable: str) -> list[str]:
        if not variable:
            return []

        parts = variable.split("/")
        stems = []

        for part in parts:
            if not part:
                continue
            if len(part) <= 3 or part.isupper():
                stems.append(part)
            else:
                sub_parts = part.split("_")
                for sub_part in sub_parts:
                    if sub_part:
                        final_parts = sub_part.split("-")
                        stems.extend([p for p in final_parts if p])

        return stems

    def parse_line(self, line: str) -> dict[str, Any] | None:
        line = line.strip()
        if not line:
            return None

        if not re.search(r"\b\d+\s+of\s+\d+\b", line):
            return None

        line = " ".join(line.split()[4:])

        variables = self.var_pattern.findall(line)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)

        template = self.template_manager.get_pure_template(line)
        rule_id = self.template_manager.get_rule_id(template)

        return {
            "rule_id": rule_id,
            "variables": var_tuple,
            "template": template,
            "raw_log": line,
        }
