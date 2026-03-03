# Air-Gapped GCA Tuning Runbook (JSON Ground Truth)

## 1. Goal
Tune `src/sanity_log_parser/gca/rule_clustering_config.json` so that AI-based semantic merging matches human-labeled ground truth.

## 2. Inputs/Outputs
- **Input**: PrimeTime Constraints report (`REPORT.rpt`).
- **Config**: `src/sanity_log_parser/gca/rule_clustering_config.json`.
- **Output**: A set of per-rule overrides for the config file.

## 3. Commands to Generate Baseline Data
Generate two files to compare: one with AI disabled (Stage-1 logic groups) and one with current AI settings.

```bash
# 1. Generate logic.json (Stage-1 baseline)
sanity-log-parser gca REPORT.rpt --ai off --out logic.json --max-original-logs 0

# 2. Generate ai.json (Current Stage-2 results)
sanity-log-parser gca REPORT.rpt --ai on --rule-config src/sanity_log_parser/gca/rule_clustering_config.json --out ai.json --max-original-logs 0
```

## 4. Ground Truth JSON Format
The human expert provides ground truth as a JSON object mapping `rule_id` to a list of clusters. Each cluster is a list of `logic_group_id` strings from `logic.json`.

### Schema
```json
{
  "rule_id": [
    ["logic_group_id_1", "logic_group_id_2"],
    ["logic_group_id_3"]
  ]
}
```

### Example
```json
{
  "CGR_0018": [
    ["CGR_0018::logic::000001", "CGR_0018::logic::000002"],
    ["CGR_0018::logic::000003"]
  ]
}
```

### Labeling Rules
- Use `group_id` from `logic.json` where `group_type == "logic"`.
- For any labeled `rule_id`, **every** logic `group_id` for that rule in `logic.json` must appear exactly once across the clusters for that rule.

## 5. Evaluation Algorithm
To measure accuracy, compare `ai.json` against the ground truth JSON.

1. **Map Raw Logs to Logic Groups**:
   - From `logic.json`, build a map: `raw_log_text -> logic_group_id`.
2. **Convert AI Clusters to Logic Group Sets**:
   - For each `ai_super` group in `ai.json`:
     - Map its `original_logs` back to `logic_group_id` using the map from step 1.
     - The result is a set of `logic_group_ids` that the AI merged.
   - **Fail** if any AI raw log cannot map back to a logic group.
3. **Compute Pairwise Metrics (Per Rule)**:
   - For each `rule_id` in ground truth:
     - Let $L$ be the set of all logic group IDs for this rule.
     - Consider all pairs $(a, b)$ where $a, b \in L$ and $a < b$.
     - **True Positive (TP)**: $a, b$ are in the same cluster in both ground truth and AI results.
     - **False Positive (FP)**: $a, b$ are in the same cluster in AI results but different in ground truth (over-clustering).
     - **False Negative (FN)**: $a, b$ are in different clusters in AI results but the same in ground truth (under-clustering).
4. **Calculate Scores**:
   - **Precision** = TP / (TP + FP)
   - **Recall** = TP / (TP + FN)
   - **F1** = 2 * Precision * Recall / (Precision + Recall)

## 6. Optimization Loop
1. **Tune `eps` first**:
   - If Precision is low (over-clustering), decrease `eps`.
   - If Recall is low (under-clustering), increase `eps`.
2. **Tune weights/levels**:
   - Only perform this if a preflight (Section 8) confirms variable extraction is active for the rule.
   - Increase weight for variables that distinguish groups that should be separate.
   - Decrease weight (or set to 0) for noisy variables.
   - Use `levels` to focus on specific hierarchy parts of a path.

## 7. What to Return
Do not return large JSON files. Return a compact summary:

### Per-Rule Overrides
| Rule ID | eps | template_weight | variable_weights/levels |
| :--- | :--- | :--- | :--- |
| CGR_0018 | 0.15 | 0.1 | var0: {weight: 0.7, levels: [0, 1]} |

### Metrics Table
| Rule ID | Precision | Recall | F1 | Status |
| :--- | :--- | :--- | :--- | :--- |
| CGR_0018 | 1.00 | 0.95 | 0.97 | PASS |

## 8. Preflight: Variable Tuning Risk
**Risk**: If the logic groups for a rule all have the same `representative_pattern` (or no variables extracted), tuning `variable_weight` or `levels` is a **no-op**.

**Detection**:
- Check `logic.json` for the rule.
- If all `representative_pattern` values are identical, the AI only sees the `representative_template`.
- In this case, only `eps` and `template_weight` will have any effect.
- If you need to separate these groups, the parser's logic-clustering stage must be improved first (out of scope for this runbook).
