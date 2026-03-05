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
| `level_weights` | dict or null | keys: int strings, values: >= 0 | Per-level weights — each level gets its own embedding and weight |
| `match_mode` | string | `"embedding"` or `"jaccard"` | Distance metric for this variable (default: `"embedding"`) |

**`levels` vs `level_weights`**: These are mutually exclusive. Use `levels` when all selected levels should share one weight. Use `level_weights` when different hierarchy levels need different weights (e.g., block name matters more than register name).

### `levels` — Hierarchy Level Selection

Variables in VLSI logs are typically `/`-separated hierarchical paths like `top/cpu/alu/reg_bank/CK` where the leaf is often a **pin name** (CK, Q, D, SI, etc.).

`levels` selects which parts to embed using **Python-style indexing** (negative indices count from the end):

| `levels` | Path `top/cpu/alu/reg_bank/CK` | Selected | Use When |
|---|---|---|---|
| `null` | all | `"top cpu alu reg_bank CK"` | Entire path matters |
| `[-3, -2]` | block + register | `"alu reg_bank"` | Block/register is discriminator, pin is noise |
| `[-2]` | register only | `"reg_bank"` | Register name is the key discriminator |
| `[-3]` | block only | `"alu"` | Block-level grouping |
| `[-5, -4]` | top two | `"top cpu"` | Top-level subsystem is the discriminator |

**Always use negative indices.** They generalize across projects with different hierarchy depths. `[-2]` means "second from end" (register name) whether the path has 4 levels or 8.

**Common VLSI noise at the leaf level:** Pin names like `CK`, `Q`, `D`, `SI`, `SE`, `RN` appear on every register and vary within clusters. Always check if the leaf level (`-1`) is pin noise.

If all selected indices are out of bounds, the variable becomes empty and is **masked inactive** for that pair (its weight is excluded from renormalization).

### `level_weights` — Per-Level Weight Control

When different hierarchy levels have different discriminating power, use `level_weights` instead of `levels` to give each level its own embedding and weight.

```json
"0": { "level_weights": { "-3": 0.5, "-2": 1.0 } }
```

This expands variable 0 into **separate embedding slots** — one per level key. Each slot is embedded independently with its own weight. This is more expressive than `levels` which shares a single weight across all selected levels.

Example for path `top/cpu/alu/reg_bank/CK`:
- `"-3"` (alu) → embedded separately with weight 0.5
- `"-2"` (reg_bank) → embedded separately with weight 1.0
- Pin name `CK` at level `-1` is excluded (not in `level_weights`)

**When to use `level_weights` over `levels`:**
- When `gca-distances` level analysis shows some signal levels matter more than others
- When a coarse level (block) should have less influence than a fine level (register)
- When you want to zero out a specific level without losing others

**Keys must be integer strings** (negative recommended for portability). Values must be non-negative floats. `level_weights` and `levels` cannot be used together on the same variable.

### `match_mode` — Jaccard Distance for Structured Variables

By default, variables are compared using **cosine distance on embeddings** (`match_mode: "embedding"`). For structured identifiers like VLSI hierarchy paths, embeddings give noisy distances — `block_a` and `block_b` may have cosine distance 0.03, making eps tuning nearly impossible.

Set `match_mode: "jaccard"` to use **Jaccard distance on token sets** instead:

```json
"0": { "weight": 1.0, "levels": [-3, -2], "match_mode": "jaccard" }
```

How it works:
1. The variable text is split into tokens (whitespace-separated, quotes stripped)
2. Jaccard distance = 1 - |intersection| / |union| of the token sets
3. Identical token sets → distance **exactly 0**. Completely disjoint → distance **exactly 1**.

Example with `levels=[-3, -2]` on paths of depth 5:
| Group A | Group B | Tokens A | Tokens B | Jaccard |
|---------|---------|----------|----------|---------|
| `block_a/reg_1` | `block_a/reg_1` | {block_a, reg_1} | {block_a, reg_1} | **0.0** |
| `block_a/reg_1` | `block_a/reg_2` | {block_a, reg_1} | {block_a, reg_2} | **0.333** |
| `block_a/reg_1` | `block_b/reg_2` | {block_a, reg_1} | {block_b, reg_2} | **1.0** |

Distances are **discrete and predictable** — set eps between the "should merge" and "should not merge" values.

**When to use Jaccard:**
- Variables are structured identifiers (hierarchy paths, signal names, instance paths)
- `gca-distances` shows that embedding distances between different values are very small (< 0.1)
- eps tuning is stuck: can't find a threshold that separates same-cluster from different-cluster pairs

**When to keep embeddings:**
- Variables contain natural language (template text, error messages)
- You need fuzzy semantic matching (e.g., `clk_gen` ≈ `clock_generator`)

`match_mode` works with `levels`, `level_weights`, and `weight`. With `level_weights` + `match_mode: "jaccard"`, each level becomes a single token — Jaccard of single-token sets is exact match (0 or 1).

### Allowed Keys

The config uses strict validation. Only these keys are accepted:

- **Top-level**: `default_eps`, `default_template_weight`, `default_variable_weight`, `rules`
- **Per-rule**: `eps`, `template_weight`, `variables`
- **Per-variable**: `weight`, `levels`, `level_weights`, `match_mode`

Any unknown key causes a validation error.

### Parameter Bounds

| Parameter | Min | Max (practical) | Notes |
|---|---|---|---|
| `eps` | 0.01 | 0.5 | 0.03-0.3 is typical. Below 0.03 clusters almost nothing. Above 0.4 merges too aggressively. |
| `template_weight` | 0.0 | 1.0 | 0.0 = ignore template. Higher = more influence from rule message text. |
| `variable weight` | 0.0 | 1.0 | 0.0 = ignore this variable. Weights are renormalized per pair, so ratios matter, not absolutes. |
| `levels` indices | any int | any int | Python indexing. Out-of-bounds silently skipped. |
| `level_weights` keys | any int | any int | Negative indices recommended. Each gets its own embedding slot. |
| `level_weights` values | 0.0 | 1.0 | Weight for that specific hierarchy level. 0.0 = ignore. |

## Baseline Commands

Generate two files: Stage-1 logic groups (AI off) and current Stage-2 results (AI on).

```bash
# Stage-1 only (logic groups -- the input to AI clustering)
sanity-log-parser gca REPORT.rpt --ai off --out logic.json --max-original-logs 0

# Stage-2 with current config
sanity-log-parser gca REPORT.rpt --ai on --rule-config rule_clustering_config.json --out ai.json --max-original-logs 0
```

## Built-in Evaluation Command

**Do not implement your own evaluation script.** Use the built-in `gca-eval` subcommand:

```bash
sanity-log-parser gca-eval --logic logic.json --ai ai.json --ground-truth gt.json
```

Output:
```
Rule ID               P      R     F1   TP   FP   FN  GT#  AI# Status
----------------------------------------------------------------------
CGR_0018           1.00   1.00   1.00    6    0    0    5    5 PASS
CGR_0042           1.00   0.80   0.89    4    0    1    3    4 FAIL
```

The exit code is 0 if all rules PASS, 1 if any rule FAILs. Use `--f1-threshold` to change the threshold (default: 0.97).

## Built-in Distance Probing Command

**Do not compute distances manually with curl + Python.** Use the built-in `gca-distances` subcommand:

```bash
sanity-log-parser gca-distances --logic logic.json --rule-config rule_clustering_config.json --rule-id CGR_0018
```

Optional: add `--ground-truth gt.json` to annotate pairs with same-cluster info.

Output shows:
1. The config being used (eps, weights, levels)
2. All logic groups for the rule
3. All pairwise distances **sorted ascending**, with a marker showing where eps cuts

This gives you the exact distance gap to place eps in, without manual embedding + cosine computation.

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

### Phase 1: Identify the Discriminator (Weights AND Levels)

Before touching `eps`, determine what the distance should measure. This means setting **weights** (which variables matter) AND **levels** (which hierarchy levels within a variable matter).

**Use `gca-distances` with ground truth to get the analysis automatically:**
```bash
sanity-log-parser gca-distances --logic logic.json --rule-config rule_clustering_config.json --rule-id DES_0001 --ground-truth gt.json
```

The output includes a **Variable Level Analysis** section that shows, for each variable position:
- The unique values at each `/`-separated hierarchy level
- Whether each level is **SIGNAL** (discriminates GT clusters), **NOISE** (varies within GT clusters), or **constant**
- A **RECOMMENDATION** for which levels to use

#### Step 1: Set variable weights

- Variable whose values **vary within** a ground truth cluster -> noise. Set `weight: 0`.
- Variable whose values **align with** cluster boundaries -> discriminator. Set `weight: 1.0`.

#### Step 2: Check levels for each discriminating variable

This is critical. A variable like `top/subsys/block_a/reg_1` has 4 hierarchy levels. If `reg_1` (level 3) varies within GT clusters, it is noise **even though the variable as a whole is the discriminator**. You must set `levels` to exclude noisy levels.

Read the Level Analysis output:
- Levels marked **SIGNAL** -> keep them
- Levels marked **NOISE** -> exclude them
- Levels marked **constant** -> irrelevant (can keep or exclude)

Set `"levels"` to include only SIGNAL levels. **Always use negative indices** (count from the end) so the config works across projects with different hierarchy depths:
```json
"variables": {
  "0": { "weight": 1.0, "levels": [-3, -2] }
}
```

#### Step 3: Set template weight

- All `representative_template` values identical -> `template_weight: 0`.
- Templates differ -> keep `template_weight` > 0.

**Worked example -- CGR_0018**:
```
Pattern: GEN_SECU_SCLK / P_CMU_SPLL_VP_CLK
         ^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^
         Variable 0        Variable 1 (source clock)
```
Ground truth has 5 clusters grouped by source clock (variable 1). Variable 0 (dest clock) varies within each cluster -> noise.
Result: `template_weight: 0`, `var 0 weight: 0`, `var 1 weight: 1.0`.

**Worked example -- DES_0001 (levels needed)**:
```
Pattern: top/subsys/block_a/reg_bank/CK / clk_domain
         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^
         Variable 0 (hierarchy path)         Variable 1
```
The leaf level is a **pin name** (CK, Q, D, etc.) -- standard digital logic pins that appear on every register. These vary within clusters and are the real noise. The register name (reg_bank) is meaningful.

Level Analysis output:
```
Variable 0 Level Analysis (depth=5):
  Level 0 (-5): {top} (1 unique)                  -- constant, no signal
  Level 1 (-4): {subsys} (1 unique)               -- constant, no signal
  Level 2 (-3): {block_a, block_b} (2 unique)     << SIGNAL
  Level 3 (-2): {reg_bank, reg_ctrl, ...} (6 unique) << SIGNAL
  Level 4 (-1): {CK, Q, D, SI} (4 unique)         << NOISE (pin names)
  >> RECOMMENDATION: levels=[-3, -2] (signal only, strips noise at levels [-1])
```
Result: `var 0 weight: 1.0, levels: [-3, -2]`.
This keeps block + register name but strips the pin name. Negative indices generalize across projects -- `[-3, -2]` means "third and second from end" regardless of hierarchy depth.
Without `levels`, groups in the same cluster have non-zero distance (because `CK != Q`), and no `eps` value can fix it.

### Phase 2: Probe Distances (eps)

**Do not guess eps -- measure it.** Use `gca-distances` to see the exact pairwise distances:

```bash
sanity-log-parser gca-distances --logic logic.json --rule-config rule_clustering_config.json --rule-id CGR_0018 --ground-truth gt.json
```

The output shows all pairwise distances sorted ascending with a marker where eps cuts. Look for the gap between same-cluster pairs (distance ~0) and cross-cluster pairs.

**Key insight**: Groups with identical variable values have distance **0.0** -- any `eps > 0` merges them. The only constraint is that `eps` must stay below the smallest cross-cluster distance.

Set `eps` to roughly **80% of the smallest cross-cluster distance**:
```
Smallest cross-cluster distance = 0.0495
eps = 0.0495 * 0.8 = 0.04
```

### Phase 3: Validate and Iterate

Run the tool, then evaluate with `gca-eval`:
```bash
sanity-log-parser gca REPORT.rpt --rule-config rule_clustering_config.json --ai on --out ai.json --max-original-logs 0
sanity-log-parser gca-eval --logic logic.json --ai ai.json --ground-truth gt.json
```

Read the metrics table:
- **Too few groups (low precision)** -- `eps` is too high. A cross-cluster pair merged. Lower `eps`.
- **Too many groups (low recall)** -- three possible causes:
  1. `eps` too low -- raise it.
  2. Wrong variable weighted -- re-check Phase 1 Step 1.
  3. **Noisy hierarchy levels** -- the most common cause when eps tuning alone fails. Run `gca-distances --ground-truth gt.json` and check the Level Analysis. If any level is marked NOISE, add `"levels"` to exclude it. This is the fix for DES_0001-type rules.
- **Both P and R at 1.0** -- done. Move to next rule.

Repeat until all rules PASS. Prefer precision over recall (under-cluster rather than over-cluster).

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

5. **Use `levels` to strip hierarchy noise.** Run `gca-distances` with `--ground-truth` and read the Level Analysis. Levels marked NOISE vary within GT clusters and poison the distance -- no `eps` can fix this. Set `"levels"` to include only SIGNAL levels. If no ground truth, levels with high cardinality (many unique values) are likely noise.

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
| **eps tuning stuck** (no eps works) | Noisy hierarchy levels in discriminating variable | Run `gca-distances --ground-truth gt.json`, read Level Analysis, add `"levels"` to exclude NOISE levels. This is the #1 cause of eps-only tuning failure. |
