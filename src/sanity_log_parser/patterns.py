from __future__ import annotations

import re

VAR_PATTERN = re.compile(r"'(.*?)'")
NUM_PATTERN = re.compile(r"\b\d+\b")
LINE_COUNTER_PATTERN = re.compile(r"\b\d+\s+of\s+\d+\b")
