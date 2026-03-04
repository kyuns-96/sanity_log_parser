# GCA Rule Clustering Config Tuning Guide

You are optimizing the `rule_clustering_config.json` file for the `sanity-log-parser gca` subcommand.
Your goal is to adjust clustering parameters so that the tool's output matches a human-labeled ground truth.

## How the Tool Works

```
sanity-log-parser gca REPORT_FILE --rule-config rule_clustering_config.json --out results.json
```

The tool parses a PrimeTime Constraints report and clusters log messages in two stages:

1. **Logic Clustering** — groups identical templates (deterministic, not tunable)
2. **AI Clustering** — merges logic groups by semantic similarity using embeddings

You are tuning Stage 2. The embedding model is **nomic-embed-text-v1.5**.

## Distance Computation

For each rule_id, groups are clustered using **multi-embedding weighted distance**:

```
distance(A, B) = w_t * cosine(template_A, template_B)
               + Σ  w_i * cosine(var_i_A, var_i_B)
```

Each component (template and each variable position) is embedded **separately**.
Weights are **renormalized per pair** — only components where both groups have non-empty text are included.

Then DBSCAN clusters the resulting NxN distance matrix with `metric="precomputed"`.

## Config Schema

```json
{
  "default_eps": 0.2,
  "default_template_weight": 0.3,
  "default_variable_weight": 0.7,
  "rules": {
    "CGR_0018": {
      "eps": 0.15,
      "template_weight": 0.1,
      "variables": {
        "0": { "weight": 0.7, "levels": [0, 1] },
        "1": { "weight": 0.2, "levels": [-2, -1] }
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

## Allowed Keys

The config uses strict validation. Only these keys are accepted:

- **Top-level**: `default_eps`, `default_template_weight`, `default_variable_weight`, `rules`
- **Per-rule**: `eps`, `template_weight`, `variables`
- **Per-variable**: `weight`, `levels`

Any unknown key causes a validation error.

## Parameter Bounds

| Parameter | Min | Max (practical) | Notes |
|---|---|---|---|
| `eps` | 0.01 | 0.5 | 0.05-0.3 is typical. Below 0.05 clusters almost nothing. Above 0.4 merges too aggressively. |
| `template_weight` | 0.0 | 1.0 | 0.0 = ignore template. Higher = more influence from rule message text. |
| `variable weight` | 0.0 | 1.0 | 0.0 = ignore this variable. Weights are renormalized per pair, so ratios matter, not absolutes. |
| `levels` indices | any int | any int | Python indexing. Out-of-bounds silently skipped. |

## Optimization Workflow

### Step 1: Establish Baseline

Run the tool with AI off and AI on to get both stages:

```bash
# Stage-1 only (logic groups — the input to AI clustering)
sanity-log-parser gca REPORT --ai off --out logic.json

# Stage-2 with current config
sanity-log-parser gca REPORT --rule-config rule_clustering_config.json --ai on --out ai.json
```

### Step 2: Identify the Discriminator

For each rule_id in the ground truth, examine the logic groups and ask:

1. **Are all templates identical?** Check `representative_template` across groups for the same rule_id.
   - If yes → set `template_weight: 0`. Templates carry zero signal.
   - If no → templates may be the discriminator. Keep `template_weight` > 0.

2. **Which variable separates the ground truth clusters?** Split each group's `representative_pattern` on ` / ` to extract variables (0-indexed). Compare variable values across ground truth clusters:
   - If a variable has the **same value** across groups that belong to **different** ground truth clusters → it is noise. Set `weight: 0`.
   - If a variable has **different values** that align with ground truth cluster boundaries → it is the discriminator. Set `weight: 1.0`.

**Worked example — CGR_0018**:
```
Pattern: GEN_SECU_SCLK / P_CMU_SPLL_VP_CLK
         ^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^
         Variable 0        Variable 1 (source clock)
```
Ground truth has 5 clusters grouped by source clock (variable 1). Variable 0 (dest clock) varies within each cluster → noise.
Result: `template_weight: 0`, `variable 0 weight: 0`, `variable 1 weight: 1.0`.

### Step 3: Probe Pairwise Distances

**This is the critical step.** Do not guess `eps` — measure it.

Collect the unique values of the discriminating variable from each ground truth cluster. Compute pairwise cosine distances using the embedding model (nomic-embed-text-v1.5).

```python
from sentence_transformers import SentenceTransformer
from scipy.spatial.distance import cosine

model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
values = ["P_CMU_SPLL_VP_CLK", "P_CMU_SPLL_OP_CLK", "GEN_SECU_SCLK",
          "GEN_SECU_AHB_CLK", "GEN_SENSOR_AHB_CLK"]
embs = model.encode(values)

for i in range(len(values)):
    for j in range(i + 1, len(values)):
        print(f"{values[i]} vs {values[j]}: {cosine(embs[i], embs[j]):.4f}")
```

This produces a distance table:

| Pair | Distance | Same cluster? |
|---|---|---|
| VP_CLK vs OP_CLK | 0.0495 | No |
| SECU_SCLK vs SECU_AHB_CLK | 0.1646 | No |
| SECU_AHB_CLK vs SENSOR_AHB_CLK | 0.1659 | No |
| VP_CLK vs SECU_SCLK | 0.3724 | No |

### Step 4: Set eps from the Distance Gap

Groups with **identical** variable values (same source clock) have distance **0.0** — any `eps > 0` merges them. The constraint is that `eps` must stay **below** the smallest cross-cluster distance.

From the table above, the smallest cross-cluster distance is **0.0495** (VP vs OP). So:

```
eps < 0.0495  →  set eps = 0.04
```

This keeps all 5 clusters separate while merging all groups within each cluster (distance 0).

**General rule**: Set `eps` to roughly 80% of the smallest cross-cluster distance to leave margin.

### Step 5: Validate and Iterate

Run with the computed `eps`, compare output group count to ground truth:

```bash
sanity-log-parser gca REPORT --rule-config rule_clustering_config.json --ai on --out result.json
```

If the count doesn't match:
- **Too few groups** (over-clustering) → `eps` is too high. A cross-cluster pair is below `eps`. Re-probe distances and lower `eps`.
- **Too many groups** (under-clustering) → `eps` is too low, or the wrong variable is weighted. Check that the discriminating variable is correct.

Repeat Steps 3-5 one rule at a time until all rules match.

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

## Ground Truth Format

The human provides ground truth as groups of log messages that should cluster together. Compare by examining the `groups` array in the output JSON:

```json
{
  "groups": [
    {
      "group_type": "ai_super",
      "group_id": "CGR_0018::ai::000001",
      "rule_id": "CGR_0018",
      "representative_template": "Clock ... from source ...",
      "representative_pattern": "Clock 'clk_gen/pll/output' from source 'top/io/pad'",
      "total_count": 42,
      "merged_variants_count": 3,
      "original_logs": ["raw log line 1", "raw log line 2", ...]
    }
  ]
}
```

- `merged_variants_count > 1` means multiple logic groups were merged by AI clustering
- `original_logs` contains the actual log lines in each cluster
- Compare these against ground truth clusters to measure accuracy

## Evaluation Metrics

For each rule_id, count:
- **True Positive (TP)**: pair of messages correctly in the same cluster
- **False Positive (FP)**: pair incorrectly in the same cluster (over-clustering)
- **False Negative (FN)**: pair incorrectly in different clusters (under-clustering)

Then:
- **Precision** = TP / (TP + FP) — "when we merge, is it correct?"
- **Recall** = TP / (TP + FN) — "do we find all merges?"
- **F1** = 2 * Precision * Recall / (Precision + Recall)

Optimize for **F1 >= 0.9** per rule. If you must choose, **precision > recall** — it is better to under-cluster (miss some merges) than to over-cluster (merge unrelated groups).

## Common Failure Modes

| Symptom | Likely Cause | Fix |
|---|---|---|
| Everything in one cluster | `eps` too high | Lower `eps` (try 0.1) |
| No merging at all | `eps` too low | Raise `eps` (try 0.3) |
| Unrelated paths merged | Noisy variable has high weight | Set `"weight": 0.0` for that variable |
| Same-block items split | Discriminating variable ignored | Add `levels` to focus on key hierarchy levels |
| Nearly identical logs split | Template dominates, tiny text diffs | Lower `template_weight`, raise variable weights |
| Different log types merged | Variables too similar after level selection | Try different `levels`, or raise `template_weight` |
