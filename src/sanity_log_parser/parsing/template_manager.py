from __future__ import annotations

import hashlib
import logging
import os

from ..patterns import NUM_PATTERN, VAR_PATTERN

logger = logging.getLogger(__name__)


class RuleTemplateManager:
    def __init__(self, template_file: str | None) -> None:
        self.template_dict: dict[str, str] = {}
        self.exact_message_dict: dict[str, str] = {}
        self.ambiguous_templates: set[str] = set()

        if template_file:
            logger.info("Loading Rule Templates from: %s", template_file)
            self._load_templates(template_file)

    def get_pure_template(self, text: str) -> str:
        normalized_template = VAR_PATTERN.sub("'<VAR>'", text)
        normalized_template = NUM_PATTERN.sub("<NUM>", normalized_template)
        return normalized_template.strip()

    def _load_templates(self, file_path: str) -> None:
        if not os.path.exists(file_path):
            return
        with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith(("-", "Rule", "Severity")):
                    continue
                parts = line.split(maxsplit=3)
                if len(parts) < 4:
                    continue
                rule_id, message = parts[0], parts[3]
                message = message.strip()
                self.exact_message_dict[message] = rule_id
                pure_temp = self.get_pure_template(message)
                if pure_temp in self.ambiguous_templates:
                    continue
                existing = self.template_dict.get(pure_temp)
                if existing is None:
                    self.template_dict[pure_temp] = rule_id
                    continue
                if existing != rule_id:
                    self.ambiguous_templates.add(pure_temp)
                    self.template_dict.pop(pure_temp, None)

    def get_rule_id(self, log_template: str, raw_log: str | None = None) -> str:
        if raw_log is not None:
            exact_match = self.exact_message_dict.get(raw_log.strip())
            if exact_match is not None:
                return exact_match

        return self.template_dict.get(
            log_template,
            f"UNKNOWN_{hashlib.md5(log_template.encode()).hexdigest()[:6].upper()}",
        )
