from __future__ import annotations

from typing import Any
import re
from template_manager import RuleTemplateManager

# ==============================================================================
# 2. Parser
# ==============================================================================
class SubutaiParser:
    def __init__(self, template_manager: RuleTemplateManager) -> None:
        self.var_pattern = re.compile(r"'(.*?)'")
        self.template_manager = template_manager
        self.delimiters = [('/', 1), ('_', 2), ('-', 3)]  # (delimiter, priority)
    
    def extract_variable_stems(self, variable: str) -> list[str]:
        """
        Extract semantic stems from variable respecting delimiter priority.
        Priority: '/' (highest) > '_' > '-' (lowest)
        
        Strategy: Split by priority delimiters but keep meaningful components.
        - '/' is hierarchy separator: splits into distinct components
        - '_' is compound separator within a component: may keep together or split
        - '-' is sub-component separator: splits into atoms
        
        Example: 'BLK_CPU/A/B/C/mem_top_ABC' -> ['BLK_CPU', 'A', 'B', 'C', 'mem_top', 'ABC']
        Example: 'mem_top_ABC' -> ['mem_top', 'ABC']
        
        Returns list of stem components in hierarchical order.
        """
        if not variable:
            return []
        
        # Step 1: Split by highest priority delimiter ('/')
        parts = variable.split('/')
        stems = []
        
        for part in parts:
            if not part:
                continue
            
            # Step 2: For each part, decide whether to split by '_' or '-'
            # Strategy: If the part is a known hierarchy marker (A, B, C, X, Y, etc.) or very short, keep it
            # Otherwise split by '_' (compound names like mem_top), then by '-'
            
            if len(part) <= 3 or part.isupper():
                # Single letters or uppercase markers like BLK, CPU, SENSOR - keep as one stem
                stems.append(part)
            else:
                # Compound names: split by '_' first, then '-'
                sub_parts = part.split('_')
                for sub_part in sub_parts:
                    if sub_part:
                        # Final split by '-' for components like 'ABC', '123-456'
                        final_parts = sub_part.split('-')
                        stems.extend([p for p in final_parts if p])
        
        return stems

    def parse_line(self, line: str) -> dict[str, Any] | None:
        line = line.strip()
        if not line: return None
        
        if re.search(r'\b\d+\s+of\s+\d+\b', line):
            pass 
        else:
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
            "raw_log": line  # Original log stored here
        }
