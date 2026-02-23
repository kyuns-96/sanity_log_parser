from __future__ import annotations

from typing import Any
import re
from collections import defaultdict


# ==============================================================================
# Logic Clusterer
# ==============================================================================
class LogicClusterer:
    def get_logic_signature(self, var_tuple: tuple[str, ...]) -> str:
        if not var_tuple or var_tuple == ("NO_VAR",): return "NO_VAR"
        signatures = [re.sub(r"\d+", "*", str(v)) for v in var_tuple]
        return " / ".join(signatures)
    
    def run(self, parsed_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups = defaultdict(list)
        for parsed_log in parsed_logs:
            # [1st grouping] Original method: using variables only (ignoring stem)
            full_sig = self.get_logic_signature(parsed_log['variables'])
            key = (parsed_log['rule_id'], full_sig, parsed_log['template'])
            groups[key].append(parsed_log)

        results = []
        for (rule_id, full_sig, temp), members in groups.items():
            results.append({
                "type": "LogicGroup",
                "rule_id": rule_id,
                "pattern": full_sig,  # Original method: variable-based pattern
                "template": temp,
                "count": len(members),
                "members": members  # Parsed objects with raw_log included here
            })
        results.sort(key=lambda group: group['count'], reverse=True)
        return results
