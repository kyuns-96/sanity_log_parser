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

You are tuning Stage 2. The embedding model is **Qwen-Embedding-4B** (last-token pooling).

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

Run the tool with current config and compare to ground truth:

```bash
sanity-log-parser gca REPORT --rule-config rule_clustering_config.json --out baseline.json
```

### Step 2: Identify Problem Rules

For each rule_id in the ground truth, compare:
- **Under-clustering**: groups that should be merged are in separate clusters → **increase `eps`** or **decrease weights** on noisy variables
- **Over-clustering**: groups that should be separate are merged → **decrease `eps`** or **increase weights** on discriminating variables

### Step 3: Analyze Variables

For a problem rule, examine its log patterns. Variables are extracted by `'...'` quoting in the pattern text (regex: `'(.*?)'`), 0-indexed left to right.

Example pattern: `Clock 'clk_gen/pll/output' from source 'top/io/pad_ring'`
- Variable 0: `clk_gen/pll/output`
- Variable 1: `top/io/pad_ring`

Ask:
- Which variable distinguishes groups that should be separate? → **increase its weight**
- Which variable is noise (same across different groups or irrelevant)? → **decrease its weight** or set to 0
- Which hierarchy levels of a variable carry the signal? → set `levels` accordingly

### Step 4: Adjust and Re-run

Edit the config, re-run, compare. One rule at a time.

### Step 5: Iterate

Repeat Steps 2-4 until the output matches ground truth for all rules.

## Tuning Heuristics

1. **Start with `eps`**. If all groups for a rule are separate when they should merge, `eps` is too low. If unrelated groups merge, `eps` is too high. Adjust in increments of 0.02-0.05.

2. **Weights are relative**. `template_weight=0.3, variable_0_weight=0.7` means "variables matter more than templates". The absolute values don't matter because of per-pair renormalization — only the ratios.

3. **Set weight to 0 for noise variables**. If a variable position contains random instance numbers or timestamps, set `"weight": 0.0` to exclude it entirely.

4. **Use `levels` for long paths**. If a variable like `top/subsys/block/subblock/leaf/instance` is 6 levels deep, probably only 2-3 levels are meaningful. Try `[0, 1]` (top-level block) or `[-2, -1]` (leaf) first.

5. **When all variables look the same**, the template is the discriminator. Set `template_weight` high (0.7-0.9) and variable weights low.

6. **When templates are identical**, variables are the only discriminator. Set `template_weight` low (0.0-0.1) and focus on variable weights and levels.

7. **Fewer rules in config is better**. Only add a per-rule entry when the defaults don't work for that rule. The `default_*` values apply to all unspecified rules.

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
