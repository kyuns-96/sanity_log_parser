from __future__ import annotations

from typing import Any
import os
import re
import hashlib

# ==============================================================================
# 1. Template Manager
# ==============================================================================
class RuleTemplateManager:
    def __init__(self, template_file: str | None) -> None:
        self.template_dict = {} 
        self.var_pattern = re.compile(r"'(.*?)'")
        
        if template_file:
            print(f"📂 Loading Rule Templates from: {template_file}")
            self._load_templates(template_file)

    def get_pure_template(self, text: str) -> str:
        # 1. Protect variable regions
        normalized_template = self.var_pattern.sub("'<VAR>'", text)
        # 2. Mask only standalone numbers
        normalized_template = re.sub(r"\b\d+\b", "<NUM>", normalized_template)
        return normalized_template.strip()

    def _load_templates(self, file_path: str) -> None:
        if not os.path.exists(file_path):
            return
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(('-', 'Rule', 'Severity')): continue
                parts = line.split(maxsplit=3)
                if len(parts) < 4: continue
                rule_id, message = parts[0], parts[3]
                pure_temp = self.get_pure_template(message)
                self.template_dict[pure_temp] = rule_id

    def get_rule_id(self, log_template: str) -> str:
        return self.template_dict.get(log_template, f"UNKNOWN_{hashlib.md5(log_template.encode()).hexdigest()[:6].upper()}")
