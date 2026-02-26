from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def load_rule_config(config_file: str) -> dict[str, Any]:
    """Load rule-specific clustering parameters from a JSON config file."""
    if not os.path.exists(config_file):
        logger.debug("Rule config '%s' not found, using defaults.", config_file)
        return {}
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        logger.info("Loaded rule config from '%s'.", config_file)
        return config.get("rules", {})
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(
            "Error loading rule config '%s': %s. Using defaults.", config_file, exc
        )
        return {}
