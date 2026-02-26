from __future__ import annotations

import logging
import re
from typing import Any

from ..patterns import (
    INSTANCE_LINE_PATTERN,
    RULE_ID_LINE_PATTERN,
    SEPARATOR_PATTERN,
    SEVERITY_LINE_PATTERN,
    VAR_PATTERN,
)
from .template_manager import RuleTemplateManager

logger = logging.getLogger(__name__)


class PrimeTimeParser:
    """Stateful single-file PrimeTime constraint report parser.

    Walks each line of a report, tracking current_severity and
    current_rule_id from structural context lines. Emits one parsed
    dict per instance line (lines matching "N of M").
    """

    def __init__(self) -> None:
        self._template_manager = RuleTemplateManager(template_file=None)
        self.current_severity: str = "unknown"
        self.current_rule_id: str = "UNKNOWN"

    def parse_file(self, path: str) -> list[dict[str, Any]]:
        """Parse a single PrimeTime report file."""
        self.current_severity = "unknown"
        self.current_rule_id = "UNKNOWN"
        results: list[dict[str, Any]] = []

        counts = {"instances": 0, "severity": 0, "parents": 0, "skipped": 0}

        with open(path, encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                parsed = self._process_line(line.rstrip("\n"), counts)
                if parsed is not None:
                    results.append(parsed)

        logger.info(
            "PrimeTimeParser: %d instances, %d severity sections, "
            "%d parent rules, %d skipped from %s",
            counts["instances"],
            counts["severity"],
            counts["parents"],
            counts["skipped"],
            path,
        )
        return results

    def _process_line(
        self,
        line: str,
        counts: dict[str, int],
    ) -> dict[str, Any] | None:
        stripped = line.strip()

        # Step 1: Empty / separator
        if not stripped or SEPARATOR_PATTERN.match(line):
            counts["skipped"] += 1
            return None

        # Step 2: Severity section
        m = SEVERITY_LINE_PATTERN.match(line)
        if m:
            self.current_severity = m.group(1).lower()
            self.current_rule_id = "UNKNOWN"
            counts["severity"] += 1
            return None

        # Step 3: Parent line
        m = RULE_ID_LINE_PATTERN.match(line)
        if m:
            self.current_rule_id = m.group(1)
            counts["parents"] += 1
            return None

        # Step 4: Instance line
        m = INSTANCE_LINE_PATTERN.match(line)
        if m:
            counts["instances"] += 1
            return self._parse_instance_line(m)

        # Step 5: Skip everything else
        logger.debug("Skipped unrecognized line: %s", stripped[:80])
        counts["skipped"] += 1
        return None

    def _parse_instance_line(self, match: re.Match[str]) -> dict[str, Any]:
        message = match.group(4)

        variables = VAR_PATTERN.findall(message)
        var_tuple = tuple(variables) if variables else ("NO_VAR",)
        template = self._template_manager.get_pure_template(message)

        return {
            "rule_id": self.current_rule_id,
            "variables": var_tuple,
            "template": template,
            "raw_log": message,
            "severity": self.current_severity,
        }
