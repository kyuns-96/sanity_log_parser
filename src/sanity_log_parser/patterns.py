from __future__ import annotations

import re

VAR_PATTERN = re.compile(r"'(.*?)'")
NUM_PATTERN = re.compile(r"\b\d+\b")
LINE_COUNTER_PATTERN = re.compile(r"\b\d+\s+of\s+\d+\b")

# PrimeTime single-file report patterns
SEPARATOR_PATTERN = re.compile(r"^\s*[*=\-]+\s*$")
SEVERITY_LINE_PATTERN = re.compile(
    r"^\s+(error|warning|info)\s+\d+\s+\d+\s*$", re.IGNORECASE
)
RULE_ID_LINE_PATTERN = re.compile(r"^\s{0,6}([A-Z]{2,4}_\d{4})\s+\d+\s+\d+\s+\S")
INSTANCE_LINE_PATTERN = re.compile(r"^\s*(\d+)\s+of\s+(\d+)\s+(\d+)\s+(.*\S)")
