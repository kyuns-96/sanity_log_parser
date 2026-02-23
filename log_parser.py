from __future__ import annotations

from typing import Any
import os
import sys
import re
import json
import hashlib
from collections import defaultdict

# ==============================================================================
# [Dependency Check]
# ==============================================================================
try:
    from sentence_transformers import SentenceTransformer
    from sklearn.cluster import DBSCAN
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

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

# ==============================================================================
# 3. Logic Clusterer
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

# ==============================================================================
# 4. AI Clusterer
# ==============================================================================
class AIClusterer:
    _VAR_PATTERN = re.compile(r"'(.*?)'")
    def __init__(self, model_path: str = 'all-MiniLM-L6-v2', config_file: str = 'rule_clustering_config.json') -> None:
        # Initialize instance attributes
        self.model: SentenceTransformer | None = None
        self.ai_available: bool = False
        if AI_AVAILABLE:
            try:
                self.model = SentenceTransformer(model_path)
                self.ai_available = True
            except (ImportError, OSError, RuntimeError) as exc:
                print(f"⚠️  Failed to load SentenceTransformer model: {exc}")
                self.ai_available = False
        # Load rule-specific eps and tail_weight from config file
        self.rule_config = self._load_config(config_file)
        self.default_eps = 0.2
        self.default_tail_weight = 2

    def _load_config(self, config_file: str) -> dict[str, Any]:
        """Load rule-specific parameters from config file"""
        if not os.path.exists(config_file):
            print(f"   ⚠️  Config file '{config_file}' not found. Using default settings.")
            return {}
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            print(f"   ✅ Loaded rule config from '{config_file}'")
            return config.get('rules', {})
        except Exception as e:
            print(f"   ⚠️  Error loading config: {e}. Using default settings.")
            return {}

    def get_rule_config(self, rule_id: str) -> dict[str, Any]:
        """Get rule config, return default if not found"""
        if rule_id in self.rule_config:
            config = self.rule_config[rule_id].copy()
            if 'eps' not in config:
                config['eps'] = self.default_eps
            if 'variable_position_weights' not in config:
                config['variable_position_weights'] = None
            if 'variable_tail_configs' not in config:
                config['variable_tail_configs'] = None
            return config
        return {
            'eps': self.default_eps,
            'variable_position_weights': None,
            'variable_tail_configs': None
        }

    def extract_variable_tail(self, full_pattern: str, tail_levels: int = 1, tail_weights: list[int] | None = None, variable_position_weights: list[int] | None = None) -> str:
        """
        Extract tail part of VLSI variables (tail part is more important)
        Also supports position-based weighting of variables
        
        Args:
            full_pattern: Form like 'BLK_CPU/A/B/C/mem_top_ABC'
            tail_levels: How many levels from the tail to extract (default 1)
            tail_weights: Weight list for each level
                          Example: [2, 3] → last is 2x, previous is 3x
            variable_position_weights: Position-based weights for variables
                          Example: [3, 2, 1] → 1st variable 3x, 2nd 2x, 3rd 1x
        
        Returns:
            Tail string with weights applied
        
        Example:
            full_pattern = 'BLK_CPU/A/B/C/mem_top_ABC'
            
            tail_levels=1, tail_weights=[2]
            → 'ABC ABC'
            
            tail_levels=2, tail_weights=[3, 2]
            → 'mem_top mem_top mem_top ABC ABC'
            
            If variable tuple is ('var1', 'var2', 'var3') and
            variable_position_weights=[3, 2, 1], then
            → 'var1 var1 var1 var2 var2 var3'
        """
        if ' / ' not in full_pattern:
            return full_pattern
        
        parts = full_pattern.split(' / ')
        
        # Extract tail levels
        tail_parts = parts[-tail_levels:] if tail_levels <= len(parts) else parts
        
        # Set weights (default: all 1)
        if tail_weights is None:
            tail_weights = [1] * len(tail_parts)
        else:
            # Pad tail_weights with last value if insufficient
            while len(tail_weights) < len(tail_parts):
                tail_weights.append(tail_weights[-1] if tail_weights else 1)
        
        # Repeat each part by its weight
        result = []
        for part, weight in zip(tail_parts, tail_weights):
            result.extend([part] * weight)
        
        # Apply variable position weights if provided
        if variable_position_weights:
            result = self._apply_variable_position_weights(result, variable_position_weights)
        
        return ' '.join(result)
    
    def _apply_variable_position_weights(self, parts: list[str], variable_position_weights: list[int]) -> list[str]:
        """
        Apply position-based weights to variable substrings
        Example: parts=['mem_top', 'ABC'], variable_position_weights=[3, 2]
        → ['mem_top', 'mem_top', 'mem_top', 'ABC', 'ABC']
        """
        if not parts or not variable_position_weights:
            return parts
        
        result = []
        for i, part in enumerate(parts):
            # Get position weight (use last if insufficient)
            weight_idx = min(i, len(variable_position_weights) - 1)
            weight = variable_position_weights[weight_idx]
            result.extend([part] * weight)
        
        return result
    
    def run(self, logic_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.ai_available or not logic_groups: return []

        print(f"🤖 Stage 2 - AI Clustering: analyzing {len(logic_groups)} logic groups...")
        
        # Classify groups by rule_id
        groups_by_rule = defaultdict(list)
        for logic_group in logic_groups:
            groups_by_rule[logic_group['rule_id']].append(logic_group)
        
        print(f"   Grouping by rule_id: {len(groups_by_rule)} different rules")
        
        final_output = []
        ai_group_counter = 0
        
        # Perform AI Clustering separately for each rule
        for rule_id, rule_groups in groups_by_rule.items():
            config = self.get_rule_config(rule_id)
            eps = config['eps']
            variable_position_weights = config.get('variable_position_weights', None)
            variable_tail_configs = config.get('variable_tail_configs', None)
            
            if len(rule_groups) < 2:
                # No merging needed if only 1 group
                for logic_group in rule_groups:
                    ai_group_counter += 1
                    all_raw_logs = [m['raw_log'] for m in logic_group['members']]
                    final_output.append({
                        "type": "AISuperGroup",
                        "super_group_id": f"{rule_id}_SG_{ai_group_counter}",
                        "rule_id": rule_id,
                        "representative_template": logic_group['template'],
                        "representative_pattern": logic_group['pattern'],
                        "total_count": logic_group['count'],
                        "merged_variants_count": 1,
                        "original_logs": all_raw_logs
                    })
                continue
            
            # Perform embedding and clustering only within same rule_id
            embedding_inputs = []
            for logic_group in rule_groups:
                # Extract variables from pattern

                pattern_text = logic_group['pattern'].replace(' / ', ' ')
                variables = self._VAR_PATTERN.findall(pattern_text)
                
                # Handle position-based tail config if present
                if variable_tail_configs:
                    var_texts = []
                    for idx, var in enumerate(variables):
                        var_config = variable_tail_configs.get(str(idx), None)
                        if var_config:
                            tail_levels = var_config.get('tail_levels', 1)
                            tail_weights = var_config.get('tail_weights', [1])
                            # Restore variable in " / " format
                            var_with_sep = var.replace('/', ' / ')
                            tail_text = self.extract_variable_tail(var_with_sep, tail_levels, tail_weights, None)
                            var_texts.append(tail_text)
                        else:
                            # Use variable as-is if no config
                            var_texts.append(var)
                    
                    # Apply position-based variable weights
                    if variable_position_weights:
                        var_texts = self._apply_variable_position_weights(var_texts, variable_position_weights)
                    
                    embedding_input = f"{logic_group['template']} {' '.join(var_texts)}"
                else:
                    # Use variables as-is without tail config
                    if variable_position_weights:
                        var_texts = self._apply_variable_position_weights(variables, variable_position_weights)
                        embedding_input = f"{logic_group['template']} {' '.join(var_texts)}"
                    else:
                        embedding_input = f"{logic_group['template']} {' '.join(variables)}"
                
                embedding_inputs.append(embedding_input)
            
            embeddings = self.model.encode(embedding_inputs, batch_size=128, show_progress_bar=False)
            
            # Perform clustering with rule-specific eps
            clustering = DBSCAN(eps=eps, min_samples=1, metric='cosine').fit(embeddings)
            
            ai_grouped = defaultdict(lambda: {"total_count": 0, "logic_subgroups": []})
            for label, logic_group in zip(clustering.labels_, rule_groups):
                cluster_key = f"{rule_id}_SG_{label}"
                ai_grouped[cluster_key]["total_count"] += logic_group['count']
                ai_grouped[cluster_key]["logic_subgroups"].append(logic_group)

            # Generate results
            for key, data in ai_grouped.items():
                ai_group_counter += 1
                main = max(data["logic_subgroups"], key=lambda group: group['count'])
                
                all_raw_logs = []
                for sub in data["logic_subgroups"]:
                    for member in sub["members"]:
                        all_raw_logs.append(member["raw_log"])

                final_output.append({
                    "type": "AISuperGroup",
                    "super_group_id": key,
                    "rule_id": rule_id,
                    "representative_template": main['template'],
                    "representative_pattern": main['pattern'],
                    "total_count": data["total_count"],
                    "merged_variants_count": len(data["logic_subgroups"]),
                    "original_logs": all_raw_logs
                })
        
        final_output.sort(key=lambda group: group['total_count'], reverse=True)
        return final_output

# ==============================================================================
# 5. Main Execution
# ==============================================================================
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python subutai_reviewer.py <LOG_FILE> <TEMPLATE_FILE>")
        sys.exit(1)

    log_file = sys.argv[1]
    rule_file = sys.argv[2]

    # 1. Parsing
    tm = RuleTemplateManager(rule_file)
    parser = SubutaiParser(tm)
    parsed_logs = []
    
    with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith(('-', '=', 'Rule', 'Severity')): continue
            res = parser.parse_line(stripped)
            if res: parsed_logs.append(res)

    # 2. Logic Clustering [1st grouping: original method]
    logic_results = LogicClusterer().run(parsed_logs)
    print(f"\n📊 Stage 1 - Logic Clustering (Original Method - Variables Only):")
    print(f"   Input logs: {len(parsed_logs):,}")
    print(f"   Output groups: {len(logic_results):,}")
    print(f"   Compression ratio: {len(parsed_logs) / len(logic_results):.2f}x")

    # 3. AI Clustering [2nd grouping: semantic merging]
    results = [] # Store all results here

    ai_clusterer = AIClusterer()
    if ai_clusterer.ai_available:
        # 2nd grouping: semantically re-merge 1st logic groups using AI
        results = ai_clusterer.run(logic_results)
        print(f"\n🤖 Stage 2 - AI Clustering (Semantic Merging of 1st-Groups):")
        print(f"   Input 1st-groups: {len(logic_results):,}")
        print(f"   Output 2nd-groups: {len(results):,}")
        print(f"   Final compression ratio: {len(parsed_logs) / len(results):.2f}x")
    else:
        # Return only Logic results if AI unavailable
        for logic_group in logic_results:
            # Recover original logs from Logic group
            raw_logs = [m['raw_log'] for m in logic_group['members']]
            results.append({
                "type": "LogicGroup",
                "rule_id": logic_group['rule_id'],
                "representative_pattern": logic_group['pattern'],
                "total_count": logic_group['count'],
                "original_logs": raw_logs
            })

    # 4. Output results and save to file
    print("\n" + "="*80)
    print(f"✅ Final Results: {len(results)} Groups Created.")
    print("="*80)
    
    # Display on screen (sample)
    for i, result in enumerate(results[:5]):
        pattern_display = result.get('representative_pattern', 'N/A')
        merged_info = f" (merged {result.get('merged_variants_count', 1)} groups)" if result.get('merged_variants_count', 1) > 1 else ""
        print(f"{i+1:02d}. [{result['rule_id']}] {pattern_display}{merged_info}")
        print(f"    Count: {result['total_count']:,}")
        print(f"    Original Logs Sample (Top 2):")
        for log in result['original_logs'][:2]:
            print(f"      - {log}")
        print("-" * 60)

    # Save results to file (JSON)
    output_filename = "subutai_results.json"
    with open(output_filename, "w", encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n💾 All results (including original logs) saved to '{output_filename}'.")