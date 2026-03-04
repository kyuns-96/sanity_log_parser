# GCA Rule Clustering Config Tuning Guide

**Target agent**: Kimi-K2.5 (air-gapped environment)
**Embedding model**: nomic-embed-text-v1.5

You are optimizing `rule_clustering_config.json` for the `sanity-log-parser gca` subcommand.
Your goal is to adjust clustering parameters so that the tool's output matches a human-labeled ground truth.

## How the Tool Works

```
sanity-log-parser gca REPORT_FILE --rule-config rule_clustering_config.json --out results.json
```

The tool parses a PrimeTime Constraints report and clusters log messages in two stages:

1. **Logic Clustering** — groups identical templates (deterministic, not tunable)
2. **AI Clustering** — merges logic groups by semantic similarity using embeddings

You are tuning Stage 2.

## Distance Computation

For each rule_id, groups are clustered using **multi-embedding weighted distance**:

```
distance(A, B) = w_t * cosine(template_A, template_B)
               + Σ  w_i * cosine(var_i_A, var_i_B)
```

Each component (template and each variable position) is embedded **separately**.
Weights are **renormalized per pair** — only components where both groups have non-empty text are included.

Then DBSCAN clusters the resulting NxN distance matrix with `metric="precomputed"`.

### How Variables Are Extracted

Variables are extracted by splitting a logic group's `representative_pattern` on ` / ` (whitespace-slash-whitespace), 0-indexed left to right.

Example pattern: `clk_gen/pll/output / top/io/pad_ring`
- Variable 0: `clk_gen/pll/output`
- Variable 1: `top/io/pad_ring`

## Config Schema

```json
{
  "default_eps": 0.2,
  "default_template_weight": 0.3,
  "default_variable_weight": 0.7,
  "rules": {
    "CGR_0018": {
      "eps": 0.04,
      "template_weight": 0,
      "variables": {
        "0": { "weight": 0 },
        "1": { "weight": 1.0 }
      }
    }
  }
}
```

### Top-Level Defaults

| Field | Type | Constraint | Effect |
|---|---|---|---|
| `default_eps` | float | > 0 | DBSCAN distance threshold for rules without a per-rule `eps` |
| `default_template_weight` | float | >= 0 | Weight of template embedding for rules without a per-rule `template_weight` |
| `default_variable_weight` | float | >= 0 | Weight of each variable embedding when no per-variable config exists |

### Per-Rule Config (`rules.<RULE_ID>`)

| Field | Type | Constraint | Effect |
|---|---|---|---|
| `eps` | float | > 0 | DBSCAN distance threshold for this rule. Lower = tighter clusters. |
| `template_weight` | float | >= 0 | How much the template text matters for this rule |
| `variables` | dict | keys: `"0"`, `"1"`, ... | Per-variable-position tuning (0-indexed, matches extraction order) |

### Per-Variable Config (`rules.<RULE_ID>.variables.<INDEX>`)

| Field | Type | Constraint | Effect |
|---|---|---|---|
| `weight` | float | >= 0 | How much this variable matters (relative to other components) |
| `levels` | list[int] or null | Python-style indices | Which hierarchy levels of the `/`-separated path to embed |

### `levels` — Hierarchy Level Selection

Variables in VLSI logs are typically `/`-separated hierarchical paths like `top/cpu/alu/pipe/reg_bank`.

`levels` selects which parts to embed using **Python-style indexing** (negative indices count from the end):

| `levels` | Path `top/cpu/alu/pipe` | Selected | Use When |
|---|---|---|---|
| `null` | all | `"top cpu alu pipe"` | Entire path matters |
| `[0, 1]` | first two | `"top cpu"` | Top-level block is the discriminator |
| `[-2, -1]` | last two | `"alu pipe"` | Leaf module/instance matters |
| `[0, -1]` | first + last | `"top pipe"` | Both ends matter, middle is noise |
| `[2]` | third only | `"alu"` | One specific level is the key |

If all selected indices are out of bounds, the variable becomes empty and is **masked inactive** for that pair (its weight is excluded from renormalization).

### Allowed Keys

The config uses strict validation. Only these keys are accepted:

- **Top-level**: `default_eps`, `default_template_weight`, `default_variable_weight`, `rules`
- **Per-rule**: `eps`, `template_weight`, `variables`
- **Per-variable**: `weight`, `levels`

Any unknown key causes a validation error.

### Parameter Bounds

| Parameter | Min | Max (practical) | Notes |
|---|---|---|---|
| `eps` | 0.01 | 0.5 | 0.03-0.3 is typical. Below 0.03 clusters almost nothing. Above 0.4 merges too aggressively. |
| `template_weight` | 0.0 | 1.0 | 0.0 = ignore template. Higher = more influence from rule message text. |
| `variable weight` | 0.0 | 1.0 | 0.0 = ignore this variable. Weights are renormalized per pair, so ratios matter, not absolutes. |
| `levels` indices | any int | any int | Python indexing. Out-of-bounds silently skipped. |

## Baseline Commands

Generate two files: Stage-1 logic groups (AI off) and current Stage-2 results (AI on).

```bash
# Stage-1 only (logic groups — the input to AI clustering)
sanity-log-parser gca REPORT.rpt --ai off --out logic.json --max-original-logs 0

# Stage-2 with current config
sanity-log-parser gca REPORT.rpt --ai on --rule-config rule_clustering_config.json --out ai.json --max-original-logs 0
```

## Ground Truth Format

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

## Optimization Strategy

Work one rule at a time. For each rule, follow these three phases in order.

### Phase 1: Identify the Discriminator (Weights)

Before touching `eps`, determine what the distance should measure.

1. **Check templates.** Are all `representative_template` values identical for this rule?
   - If yes → `template_weight: 0` (templates carry no signal).
   - If no → templates may be the discriminator; keep `template_weight` > 0.

2. **Check variables.** Split each group's `representative_pattern` on ` / ` to extract variables (0-indexed). Compare variable values across ground truth clusters:
   - Variable whose values **vary within** a ground truth cluster → noise. Set `weight: 0`.
   - Variable whose values **align with** cluster boundaries → discriminator. Set `weight: 1.0`.

**Worked example — CGR_0018**:
```
Pattern: GEN_SECU_SCLK / P_CMU_SPLL_VP_CLK
         ^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^
         Variable 0        Variable 1 (source clock)
```
Ground truth has 5 clusters grouped by source clock (variable 1). Variable 0 (dest clock) varies within each cluster → noise.
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

## Evaluation Algorithm

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
     - Let L be the set of all logic group IDs for this rule.
     - Consider all pairs (a, b) where a, b in L and a < b.
     - **True Positive (TP)**: a, b are in the same cluster in both ground truth and AI results.
     - **False Positive (FP)**: a, b are in the same cluster in AI results but different in ground truth (over-clustering).
     - **False Negative (FN)**: a, b are in different clusters in AI results but the same in ground truth (under-clustering).
4. **Calculate Scores**:
   - **Precision** = TP / (TP + FP) — "when we merge, is it correct?"
   - **Recall** = TP / (TP + FN) — "do we find all merges?"
   - **F1** = 2 * Precision * Recall / (Precision + Recall)

Optimize for **F1 >= 0.97** per rule. If you must choose, **precision > recall**.

## Tuning Heuristics

1. **Identify the discriminator first, tune eps second.** Weight configuration determines *what* the distance measures. `eps` determines *where* to cut. Getting weights wrong makes `eps` tuning meaningless.

2. **Probe distances, don't guess.** Computing cosine distances between the discriminating variable's unique values gives you the exact gap to place `eps` in. This avoids blind trial-and-error.

3. **Weights are relative.** `template_weight=0.3, variable_0_weight=0.7` means "variables matter more than templates". Absolute values don't matter due to per-pair renormalization — only the ratios.

4. **Set weight to 0 for noise variables.** If a variable position contains values that vary within a ground truth cluster, it is noise. Set `"weight": 0.0` to exclude it entirely.

5. **Use `levels` for long paths.** If a variable like `top/subsys/block/subblock/leaf/instance` is 6 levels deep, probably only 2-3 levels are meaningful. Try `[0, 1]` (top-level block) or `[-2, -1]` (leaf) first.

6. **When all variables look the same**, the template is the discriminator. Set `template_weight` high (0.7-0.9) and variable weights low.

7. **When templates are identical**, variables are the only discriminator. Set `template_weight` to `0` and focus on variable weights and levels.

8. **Fewer rules in config is better.** Only add a per-rule entry when the defaults don't work for that rule. The `default_*` values apply to all unspecified rules.

9. **Precision over recall.** It is better to under-cluster (miss some merges) than to over-cluster (merge unrelated groups). When in doubt, use a lower `eps`.

## What to Return

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

## Preflight: Variable Tuning Risk

**Risk**: If the logic groups for a rule all have the same `representative_pattern` (or patterns with only one slot after splitting on ` / `), tuning individual variable weights or `levels` may be a **no-op**.

**Detection**:
- Check `logic.json` for the rule.
- Split each group's `representative_pattern` on ` / ` to see how many variable slots exist.
- If all patterns are identical, the AI only sees the `representative_template`.
- In this case, only `eps` and `template_weight` will have any effect.
- If patterns differ only in one slot, focus tuning on that slot's weight.

## Common Failure Modes

| Symptom | Likely Cause | Fix |
|---|---|---|
| Everything in one cluster | `eps` too high | Lower `eps` (try 0.1) |
| No merging at all | `eps` too low | Raise `eps` (try 0.3) |
| Unrelated paths merged | Noisy variable has high weight | Set `"weight": 0.0` for that variable |
| Same-block items split | Discriminating variable ignored | Add `levels` to focus on key hierarchy levels |
| Nearly identical logs split | Template dominates, tiny text diffs | Lower `template_weight`, raise variable weights |
| Different log types merged | Variables too similar after level selection | Try different `levels`, or raise `template_weight` |
