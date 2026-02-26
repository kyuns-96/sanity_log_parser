from __future__ import annotations

from typing import Any, cast

from .log_parser import SubutaiParser
from .primetime_parser import PrimeTimeParser
from .template_manager import RuleTemplateManager


def parse_log_file(
    log_file: str,
    template_file: str | None = None,
) -> list[dict[str, Any]]:
    """Unified parser entry point.

    When *template_file* is falsy, parses *log_file* as a single-file
    PrimeTime constraint report. Otherwise uses the legacy two-file
    mode (SubutaiParser + RuleTemplateManager).
    """
    if not template_file:
        return PrimeTimeParser().parse_file(log_file)

    tm = RuleTemplateManager(template_file)
    parser = SubutaiParser(tm)
    parsed_logs: list[dict[str, Any]] = []

    with open(log_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith(("-", "=", "Rule", "Severity")):
                continue
            res = parser.parse_line(stripped)
            if res:
                parsed_logs.append(cast(dict[str, Any], res))

    return parsed_logs
