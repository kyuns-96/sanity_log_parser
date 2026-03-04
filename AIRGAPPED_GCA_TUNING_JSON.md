# Air-Gapped GCA Tuning Runbook (JSON Ground Truth)

**Target agent**: Kimi-K2.5 (air-gapped environment)

## 1. Goal
Tune `src/sanity_log_parser/gca/rule_clustering_config.json` so that AI-based semantic merging matches human-labeled ground truth.

### How Variables Are Extracted
Variables are extracted by splitting a logic group's `representative_pattern` on ` / ` (whitespace-slash-whitespace), 0-indexed left to right.

Example pattern: `clk_gen/pll/output / top/io/pad_ring`
- Variable 0: `clk_gen/pll/output`
- Variable 1: `top/io/pad_ring`

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

## 6. Optimization Strategy

Work one rule at a time. For each rule, follow these three phases in order.

### Phase 1: Identify the Discriminator (Weights)

Before touching `eps`, determine what the distance should measure.

1. **Check templates.** Are all `representative_template` values identical for this rule?
   - If yes → `template_weight: 0` (templates carry no signal).
   - If no → templates may be the discriminator; keep `template_weight` > 0.

2. **Check variables.** Split each group's `representative_pattern` on ` / ` to extract variables (0-indexed). Compare variable values across ground truth clusters:
   - Variable whose values **vary within** a ground truth cluster → noise. Set `weight: 0`.
   - Variable whose values **align with** cluster boundaries → discriminator. Set `weight: 1.0`.

**Example — CGR_0018**:
```
Pattern: GEN_SECU_SCLK / P_CMU_SPLL_VP_CLK
         ^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^
         Variable 0        Variable 1 (source clock)
```
Ground truth has 5 clusters grouped by source clock (variable 1). Variable 0 varies within clusters → noise.
Result: `template_weight: 0`, `var 0 weight: 0`, `var 1 weight: 1.0`.

### Phase 2: Probe Distances (eps)

**Do not guess eps — measure it.** Use the embedding API to compute cosine distances between the discriminating variable's unique values.

Send a request to the embeddings endpoint for each unique value of the discriminating variable:
```bash
curl -s http://EMBEDDING_SERVER/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input": ["P_CMU_SPLL_VP_CLK", "P_CMU_SPLL_OP_CLK", "GEN_SECU_SCLK", "GEN_SECU_AHB_CLK", "GEN_SENSOR_AHB_CLK"], "model": "nomic-embed-text-v1.5"}'
```

Then compute pairwise cosine distances between all embeddings. Build a table:

| Pair | Distance | Same cluster? |
|---|---|---|
| VP_CLK vs OP_CLK | 0.0495 | No |
| SECU_SCLK vs SECU_AHB_CLK | 0.1646 | No |
| SECU_AHB vs SENSOR_AHB | 0.1659 | No |

**Key insight**: Groups with identical variable values have distance **0.0** — any `eps > 0` merges them. The only constraint is that `eps` must stay below the smallest cross-cluster distance.

Set `eps` to roughly **80% of the smallest cross-cluster distance**:
```
Smallest cross-cluster distance = 0.0495
eps = 0.0495 × 0.8 ≈ 0.04
```

### Phase 3: Validate and Iterate

Run and compare group count to ground truth:
```bash
sanity-log-parser gca REPORT.rpt --rule-config rule_clustering_config.json --ai on --out result.json
```

- **Too few groups** → `eps` is too high. A cross-cluster pair merged. Lower `eps`.
- **Too many groups** → wrong variable weighted, or `eps` too low. Re-check Phase 1.

Repeat until all rules match. Prefer precision over recall (under-cluster rather than over-cluster).

## 7. What to Return
Do not return large JSON files. Return a compact summary:

### Per-Rule Overrides
| Rule ID | eps | template_weight | variable_weights/levels |
| :--- | :--- | :--- | :--- |
| CGR_0018 | 0.04 | 0 | var0: {weight: 0}, var1: {weight: 1.0} |

### Metrics Table
| Rule ID | Precision | Recall | F1 | Status |
| :--- | :--- | :--- | :--- | :--- |
| CGR_0018 | 1.00 | 1.00 | 1.00 | PASS |

### Distance Probe Table (per rule)
| Pair | Distance | Same cluster? |
| :--- | :--- | :--- |
| VP_CLK vs OP_CLK | 0.0495 | No |
| SECU_SCLK vs SECU_AHB_CLK | 0.1646 | No |

## 8. Preflight: Variable Tuning Risk
**Risk**: If the logic groups for a rule all have the same `representative_pattern` (or patterns with only one slot after splitting on ` / `), tuning individual variable weights or `levels` may be a **no-op**.

**Detection**:
- Check `logic.json` for the rule.
- Split each group's `representative_pattern` on ` / ` to see how many variable slots exist.
- If all patterns are identical, the AI only sees the `representative_template`.
- In this case, only `eps` and `template_weight` will have any effect.
- If patterns differ only in one slot, focus tuning on that slot's weight.
