# GCA Rule Clustering Tuning Guide

**Target agent**: air-gapped domain agent  
**Embedding model**: `nomic-embed-text-v1.5`

This guide explains how to tune `rule_clustering_config.json` for the `sanity-log-parser gca` pipeline, including the new adaptive-eps workflow used for `DES_0001`.

## Goal

Tune Stage 2 AI clustering so the tool output matches a human-labeled ground truth.

## Pipeline Overview

```bash
sanity-log-parser gca REPORT.rpt --rule-config rule_clustering_config.json --out ai.json
```

The tool has two stages:

1. `Logic clustering`
   - deterministic
   - groups identical logic patterns
   - output becomes the input to Stage 2
2. `AI clustering`
   - tunable
   - merges logic groups using weighted distances and optional rule-specific trees

You are tuning Stage 2.

## Inputs and Outputs

Typical files used during tuning:

- `logic.json`
  - produced by `sanity-log-parser gca REPORT.rpt --ai off`
  - contains the logic groups that AI clustering will merge
- `ai.json`
  - produced by `sanity-log-parser gca REPORT.rpt --ai on`
  - contains the final AI clustering result
- `gt.json`
  - human-labeled ground truth
  - maps `rule_id` to clusters of logic group IDs from `logic.json`
- `rule_clustering_config.json`
  - current clustering config
- `tuned_rule_clustering_config.json`
  - new output config written by the fitter

## Air-Gapped Shell Notes

The air-gapped environment uses `csh` / `tcsh`.

Follow these rules when generating commands there:

- do not use `python -c`
- write Python code to a temporary `.py` file, then run `python3 /tmp/script.py`
- use `setenv VAR value`, not `export VAR=value`
- quote heredoc delimiters as `<< 'EOF'` when writing temporary scripts

## Runtime Precedence Per Rule

For each `rule_id`, runtime uses the first applicable path below:

1. `pairwise_tree`
   - builds a direct `0/1` distance matrix
   - `eps` from the rule is then used by DBSCAN
2. `adaptive_eps_tree`
   - computes the normal embedding/jaccard base distance first
   - computes a pair-specific `eps` from the tree
   - uses `normalized_distance = base_distance / pair_eps`
   - DBSCAN then runs with fixed `eps = 1.0`
3. plain weighted distance
   - uses the normal weighted base distance only
   - DBSCAN uses rule `eps`

Important:
- If `pairwise_tree` exists, it takes precedence over `adaptive_eps_tree`.
- The `gca-fit-adaptive-eps` command removes `pairwise_tree` from the fitted rule in the output config, so the new adaptive tree actually takes effect.

## Base Distance Computation

When a rule does not use `pairwise_tree`, the base distance is:

```text
distance(A, B) = w_t * template_distance(A, B)
               + sum(w_i * variable_distance_i(A, B))
```

Notes:
- template and each variable slot are compared separately
- weights are renormalized per pair
- empty slots are masked out for that pair
- `match_mode: "embedding"` uses embedding cosine distance
- `match_mode: "jaccard"` uses token-set Jaccard distance

## Variable Extraction

Variables are extracted by splitting `representative_pattern` on ` / `.
The slots are 0-indexed from left to right.

Example:

```text
clk_gen/pll/output / top/io/pad_ring
```

- variable 0: `clk_gen/pll/output`
- variable 1: `top/io/pad_ring`

## Config Schema

```json
{
  "default_eps": 0.2,
  "default_template_weight": 0.3,
  "default_variable_weight": 0.7,
  "rules": {
    "DES_0001": {
      "eps": 1.0,
      "template_weight": 0,
      "variables": {
        "0": {
          "weight": 1.0,
          "levels": [-2],
          "match_mode": "embedding"
        }
      },
      "adaptive_eps_tree": {
        "features": [
          { "kind": "path_tfidf_char_wb", "ngram_range": [3, 6] },
          { "kind": "suffix_similarity", "max_shift": 3, "decay": 0.65 },
          { "kind": "level_jaccard", "levels": [-4, -3] },
          { "kind": "path_length_diff" }
        ],
        "nodes": [
          { "feature": 0, "threshold": 0.369, "left": 1, "right": 6 },
          { "value": 0.001 }
        ]
      }
    }
  }
}
```

### Allowed Top-Level Keys

- `default_eps`
- `default_template_weight`
- `default_variable_weight`
- `rules`

### Allowed Per-Rule Keys

- `eps`
- `template_weight`
- `variables`
- `pairwise_tree`
- `adaptive_eps_tree`

### Allowed Per-Variable Keys

- `weight`
- `levels`
- `level_weights`
- `match_mode`

### Allowed Tree Feature Kinds

For `pairwise_tree.features[]` and `adaptive_eps_tree.features[]`:

- `path_tfidf_char_wb`
- `suffix_similarity`
- `level_jaccard`
- `level_exact`
- `path_length_equal`
- `path_length_diff`

Feature-specific keys:

- `kind`
- `levels`
- `ngram_range`
- `max_shift`
- `decay`

### Tree Nodes

A non-leaf node has:

```json
{ "feature": 0, "threshold": 0.369, "left": 1, "right": 6 }
```

A leaf node has:

- for `pairwise_tree`

```json
{ "value": 0 }
```

or

```json
{ "value": 1 }
```

- for `adaptive_eps_tree`

```json
{ "value": 0.147 }
```

Meaning:
- evaluation starts at `nodes[0]`
- if `features[feature] <= threshold`, go to `left`
- otherwise go to `right`
- `left` and `right` are child node indices in the same `nodes` array

### Precision Constraints

Tree numeric values are strict:

- `threshold` values: at most `3` decimal places
- `adaptive_eps_tree` leaf `value`: positive finite float, at most `3` decimal places
- `pairwise_tree` leaf `value`: must be `0` or `1`

## Ground Truth Format

Ground truth is a JSON object:

```json
{
  "DES_0001": [
    ["DES_0001::logic::000001", "DES_0001::logic::000002"],
    ["DES_0001::logic::000003"]
  ]
}
```

Rules:
- use `group_id` from `logic.json` where `group_type == "logic"`
- for a labeled `rule_id`, every logic group for that rule must appear exactly once

## Baseline Commands

Generate Stage 1 logic groups and Stage 2 AI output:

```bash
sanity-log-parser gca REPORT.rpt --ai off --out logic.json --max-original-logs 0
sanity-log-parser gca REPORT.rpt --ai on --rule-config rule_clustering_config.json --out ai.json --max-original-logs 0
```

Evaluate against ground truth:

```bash
sanity-log-parser gca-eval --logic logic.json --ai ai.json --ground-truth gt.json
```

Probe base distances for one rule:

```bash
sanity-log-parser gca-distances --logic logic.json --rule-config rule_clustering_config.json --rule-id DES_0001 --ground-truth gt.json
```

Important:
- `gca-distances` shows the base weighted distance behavior
- it does **not** show the final adaptive normalization from `adaptive_eps_tree`
- if a rule uses adaptive eps, validate with `gca-eval`, not only `gca-distances`

## Recommended Workflow

Work one rule at a time.

### Phase 1: Tune the Base Distance First

Before using adaptive eps, make the base distance as sensible as possible.

1. identify which variables matter
2. set noise variables to `weight: 0`
3. if a variable is a hierarchy path, use `levels` or `level_weights` to strip noisy levels
4. set `template_weight` to `0` when templates are identical
5. use `gca-distances --ground-truth` to inspect same-cluster vs cross-cluster pairs

If a simple global `eps` works, stop there.

### Phase 2: Use Adaptive Eps When One Global `eps` Cannot Fit the Rule

Use adaptive eps when:
- the rule still needs embeddings
- one global `eps` cannot separate all same-cluster and different-cluster pairs
- the rule mixes several structural regimes in one family

This is the path used for `DES_0001`.

## Adaptive Eps Fitting Command

Use the built-in fitter instead of hand-editing the tree:

```bash
sanity-log-parser gca-fit-adaptive-eps \
  --logic logic.json \
  --ground-truth gt.json \
  --rule-id DES_0001 \
  --rule-config rule_clustering_config.json \
  --out-rule-config tuned_rule_clustering_config.json \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json
```

What the fitter does:

1. loads logic groups for `rule_id` from `logic.json`
2. loads ground truth cluster labels from `gt.json`
3. computes the current rule's base distance matrix using the current config and embeddings
4. builds generic structural pairwise features
5. fits a compact decision tree over same-cluster vs different-cluster pairs
6. converts each tree leaf into a positive `eps` value from observed base distances
7. writes an updated config with `adaptive_eps_tree`
8. removes any existing `pairwise_tree` for that rule in the output config

Default feature file:

- `src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json`

Current default feature set:

```json
[
  { "kind": "path_tfidf_char_wb", "ngram_range": [3, 6] },
  { "kind": "suffix_similarity", "max_shift": 3, "decay": 0.65 },
  { "kind": "level_jaccard", "levels": [-4, -3] },
  { "kind": "path_length_diff" }
]
```

Useful options:

- `--max-depth`
  - maximum decision-tree depth to search
- `--max-min-samples-leaf`
  - largest `min_samples_leaf` to search
- `--round-decimals`
  - decimal rounding for thresholds and adaptive leaf values
  - keep this at `3` if you need the same precision as the current DES workflow
- `--min-eps`
  - minimum positive adaptive leaf eps

## Typical Adaptive Eps Loop

```bash
sanity-log-parser gca REPORT.rpt --ai off --out logic.json --max-original-logs 0

sanity-log-parser gca-fit-adaptive-eps \
  --logic logic.json \
  --ground-truth gt.json \
  --rule-id DES_0001 \
  --rule-config rule_clustering_config.json \
  --out-rule-config tuned_rule_clustering_config.json \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json

sanity-log-parser gca REPORT.rpt \
  --ai on \
  --rule-config tuned_rule_clustering_config.json \
  --out ai.json \
  --max-original-logs 0

sanity-log-parser gca-eval --logic logic.json --ai ai.json --ground-truth gt.json
```

If F1 is still low:
- revisit the base variable settings first
- then try a different feature set or search range
- do not start by hand-editing tree thresholds unless the fitter is clearly missing a known structural boundary

## Heuristics

1. Tune weights and levels before fitting adaptive eps.
2. Prefer excluding noisy hierarchy levels instead of trying to fix them with larger trees.
3. Keep `template_weight` at `0` when the template text is identical across the rule.
4. Prefer precision over recall if you must choose.
5. Keep the feature set small. More features make the tree harder to read and easier to overfit.
6. If a rule has no meaningful path structure, adaptive eps may not help.

## What to Return

Do not return large JSON blobs. Return a compact summary like this:

### Per-Rule Override

| Rule ID | Base config | Adaptive eps | F1 |
| :--- | :--- | :--- | :--- |
| DES_0001 | var0=`embedding@[-2]`, template=`0` | fitted tree, nodes=`11` | `1.00` |

### Metrics

| Rule ID | Precision | Recall | F1 | Status |
| :--- | :--- | :--- | :--- | :--- |
| DES_0001 | 1.00 | 1.00 | 1.00 | PASS |

### Files Produced

- `logic.json`
- `tuned_rule_clustering_config.json`
- `ai.json`

## Common Failure Modes

| Symptom | Likely Cause | Fix |
|---|---|---|
| `gca-fit-adaptive-eps` fails before fitting | bad `logic.json` or incomplete `gt.json` | ensure every logic group for the rule appears exactly once in ground truth |
| Fitted config has no effect | rule still has `pairwise_tree` in runtime config | use the output config from the fitter, which removes `pairwise_tree` |
| `gca-distances` looks good but final F1 is poor | rule uses adaptive eps and only base distance was inspected | evaluate with `gca-eval` |
| Tree rejected by config loader | threshold or leaf value has more than 3 decimals | round to 3 decimals or rerun fitter with `--round-decimals 3` |
| F1 stays low after fitting | base distance is wrong, not just eps | revisit weights, levels, template weight, and feature choice |
