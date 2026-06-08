# AGENT_TUNING

This file is for a low-intelligence air-gapped agent that must tune one GCA rule safely and mechanically.

Use this file as an operator runbook.

## 1. What You Are Tuning

You are tuning **Stage 2 AI clustering** for one rule.

Pipeline:

1. logic clustering
2. AI clustering

You only tune Stage 2.

## 2. Files You Need

- `REPORT.rpt` — PrimeTime report
- `logic.json` — output of `sanity-log-parser gca REPORT.rpt --ai off`
- `gt.json` — ground truth for one rule
- `rule_clustering_config.json` — current config
- `tuned_base_config.json` — output of `gca-fit-weights`
- `tuned_final_config.json` — output of `gca-fit-adaptive-eps`
- `ai.json` — AI output to evaluate

## 3. Air-Gapped Shell Rules

Assume `csh` or `tcsh`.

Rules:

- use `setenv`
- do not use `export`
- do not use `python -c`
- if scripting is needed, write a `.py` file and run `python3 file.py`

## 4. Ground Truth Rules

`gt.json` must use `logic.json` `group_id` values.

Rules:

- include only logic groups
- every logic group for the target rule must appear exactly once
- no duplicates
- no omissions

If GT is incomplete, STOP.

## 5. Key Truth About Tuning Order

There are two tuning layers:

- `gca-fit-weights` tunes the **base rule**
- `gca-fit-adaptive-eps` fits the **adaptive eps tree**

Current canonical order is:

1. baseline
2. direct `gca-fit-adaptive-eps`
3. if poor, run `gca-fit-weights`
4. then run `gca-fit-adaptive-eps` again from the tuned base config

Do not stop after `gca-fit-weights`.

Additional truth about `approx` mode:

- `approx` is for candidate screening only
- `approx` score is not final runtime truth
- only replayed `gca` + `gca-eval` results are valid for acceptance
- `approx` may still do post-search exact rerank unless you disable it
- if the environment looks hung after `Adaptive eps approx: completed ...`, rerun with `--rerank-top-k 0`

Additional truth about over-merging:

- if over-merging is unacceptable, set `MIN_PRECISION`
- `MIN_PRECISION` is a hard eligibility gate for candidate selection
- candidates below `MIN_PRECISION` must not be selected as the final result

Additional truth about clustering method:

- `clustering_method` controls how Stage 2 consumes the distance matrix
- `dbscan` can merge through bridge pairs
- `agglomerative_complete` requires complete-link agreement and is more conservative
- fitting commands use the method from the current rule config

## 6. Step 0: Set Paths

Edit these once:

```csh
setenv REPORT /absolute/path/to/REPORT.rpt
setenv TARGET_RULE_ID RULE_XXXX
setenv TARGET_VARIABLES 0
setenv BASE_CONFIG /absolute/path/to/rule_clustering_config.json
setenv LOGIC_JSON /absolute/path/to/logic.json
setenv GT_JSON /absolute/path/to/gt.json
setenv BASE_TUNED_CONFIG /absolute/path/to/tuned_base_config.json
setenv FINAL_TUNED_CONFIG /absolute/path/to/tuned_final_config.json
setenv AI_JSON /absolute/path/to/ai.json
setenv MIN_PRECISION 0.0
```

If the package is not installed, use:

```csh
python3 -m sanity_log_parser ...
```

Examples below use `sanity-log-parser`.

If false positives are unacceptable for the rule, override this immediately:

```csh
setenv MIN_PRECISION 1.0
```

## 7. Compressed Operator Flow

Use this exact flow.

1. generate `logic.json`
2. prepare complete `gt.json`
3. measure baseline
4. try direct adaptive fitting first
5. if direct adaptive fit still over-merges badly, treat it as a base-geometry problem
6. run base tuning
7. re-fit adaptive eps from the tuned base config
8. if needed, switch the final adaptive pass from `approx` to `exact`
9. replay the tuned config on the full report
10. accept only if full replay reproduces the claimed improvement

If over-merging is forbidden, add this rule:

11. set `MIN_PRECISION` to the required floor, for example `1.0` if false positives are not allowed

## 8. Step 1: Generate `logic.json`

```csh
sanity-log-parser gca $REPORT \
  --ai off \
  --out $LOGIC_JSON \
  --max-original-logs 0
```

Expected:

- command succeeds
- `logic.json` exists
- it contains logic groups

If this step fails, STOP.

## 9. Step 2: Prepare `gt.json`

To label logic groups, export them first:

```csh
sanity-log-parser export-labeling \
  --input $LOGIC_JSON \
  --output-dir /absolute/path/to/labeling_out
```

Expected:

- one JSON file per group
- files under `/absolute/path/to/labeling_out/$TARGET_RULE_ID/`
- each file keeps full payload including `original_logs`

Then create `gt.json` manually.

Format:

```json
{
  "RULE_XXXX": [
    ["RULE_XXXX::logic::000001", "RULE_XXXX::logic::000002"],
    ["RULE_XXXX::logic::000003"]
  ]
}
```

If GT is incomplete, STOP.

## 10. Step 3: Measure Baseline

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $BASE_CONFIG \
  --out $AI_JSON \
  --max-original-logs 0

sanity-log-parser gca-eval \
  --logic $LOGIC_JSON \
  --ai $AI_JSON \
  --ground-truth $GT_JSON
```

Record:

- precision
- recall
- F1
- AI cluster count

Also record these operator thresholds for the rest of the run:

- `F1_ACCEPT_DELTA = 0.05`
- `F1_MIN_IMPROVEMENT = 0.02`
- `PRECISION_MAX_DROP = 0.02`
- `MERGE_COUNT_RATIO = 0.70`
- `HIGH_RECALL_THRESHOLD = 0.90`
- `LOW_PRECISION_THRESHOLD = 0.75`

Interpretation for a low-intelligence agent:

- “good enough” means `new_F1 >= baseline_F1 + F1_ACCEPT_DELTA` and `new_precision >= baseline_precision - PRECISION_MAX_DROP`
- “material improvement” means `new_F1 >= baseline_F1 + F1_MIN_IMPROVEMENT`
- “cluster count much smaller than GT” means `AI_cluster_count <= GT_cluster_count * MERGE_COUNT_RATIO`

## 11. Step 4: Direct Adaptive Fit First

Use adaptive fitting first.

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --rerank-top-k 0 \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then verify it with full runtime:

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $FINAL_TUNED_CONFIG \
  --out $AI_JSON \
  --max-original-logs 0

sanity-log-parser gca-eval \
  --logic $LOGIC_JSON \
  --ai $AI_JSON \
  --ground-truth $GT_JSON
```

Important:

- the fit command may print a high `approx` F1
- ignore that number for final acceptance
- only the replayed `gca` + `gca-eval` score is valid

If `final_F1 >= baseline_F1 + F1_ACCEPT_DELTA` and `final_precision >= baseline_precision - PRECISION_MAX_DROP`, STOP.

## 12. Step 5: Decide Whether The Problem Is Base Geometry

Assume a **base-geometry problem** if any one of these is true:

- `adaptive_recall >= HIGH_RECALL_THRESHOLD` and `adaptive_precision <= LOW_PRECISION_THRESHOLD`
- `adaptive_AI_cluster_count <= GT_cluster_count * MERGE_COUNT_RATIO`
- `adaptive_F1 < baseline_F1 + F1_MIN_IMPROVEMENT`

If this pattern appears, do **not** keep trying random adaptive feature files first.
Fix the base rule.

## 13. Step 6: Base-Tuning Fallback

Run base tuning only after the direct adaptive path is poor.

```csh
sanity-log-parser gca-fit-weights \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_CONFIG \
  --variables $TARGET_VARIABLES \
  -v \
  --out-rule-config $BASE_TUNED_CONFIG
```

Then evaluate the base-tuned config directly:

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $BASE_TUNED_CONFIG \
  --out $AI_JSON \
  --max-original-logs 0

sanity-log-parser gca-eval \
  --logic $LOGIC_JSON \
  --ai $AI_JSON \
  --ground-truth $GT_JSON
```

If `base_tuned_F1 < baseline_F1 + F1_MIN_IMPROVEMENT`, STOP and return status `reject_base_tuning_no_gain`.

## 14. Step 7: Re-Fit Adaptive Eps From The Tuned Base

Never fit the second adaptive pass from `$BASE_CONFIG`.
Always fit from `$BASE_TUNED_CONFIG`.

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --min-precision $MIN_PRECISION \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then verify again with full runtime.

If `final_F1 >= baseline_F1 + F1_ACCEPT_DELTA` and `final_precision >= baseline_precision - PRECISION_MAX_DROP`, STOP.

## 15. Step 8: Switch To `exact` Only At The End

Use this rule:

- use `approx` first when screening
- use `exact` only when `base_tuned_F1 >= baseline_F1 + F1_MIN_IMPROVEMENT` and the approx adaptive replay still fails the acceptance rule
- if over-merging is forbidden, use the same `--min-precision $MIN_PRECISION` floor in exact mode

If `approx` looks hung after sparse search completes, use this fallback:

- rerun with `--rerank-top-k 0`
- this disables post-approx exact rerank
- use the result only for screening, not for final acceptance

Interpretation:

- `approx` = candidate ranking only
- `exact` = high-confidence final fitting mode
- do not return `approx` F1 as the final score

Final exact-fit form:

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode exact \
  --min-precision $MIN_PRECISION \
  --max-depth 5 \
  --max-min-samples-leaf 10 \
  --min-eps 0.001 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then verify again with full runtime.

If the exact-fit replay does not beat the previous best verified F1 by at least `F1_MIN_IMPROVEMENT`, reject it and keep the previous best config.

## 16. Step 9: Final Acceptance Rule

Only claim success if all are true:

- final config file exists
- full runtime replay reproduces the claimed score
- `final_F1 >= baseline_F1 + F1_ACCEPT_DELTA`
- `final_precision >= baseline_precision - PRECISION_MAX_DROP`
- if `MIN_PRECISION > 0.0`, then `final_precision >= MIN_PRECISION`

If multiple configs tie on verified score, keep the smaller tree.

Forbidden acceptance rule:

- do not accept because `approx` F1 is high
- do not compare one candidate's `approx` F1 to another candidate's replay F1
- do not report `approx` F1 as the final answer

## 17. Real-Report Representative-Subset Re-Tuning

Use this when:

- synthetic or curated results look good
- but the rule still fails on the real report
- or the user says the exact complaint examples are still wrong

Main rule:

> Do not trust synthetic-only success if the full real report disagrees.

### 17.1 Goal

1. build a representative subset from the real report
2. label that subset carefully
3. tune against the subset
4. replay the tuned config on the full real report
5. confirm the user complaint examples are fixed on the full report

### 17.2 Extra Paths

```csh
setenv LABEL_DIR /absolute/path/to/labeling_out
setenv SUBSET_DIR /absolute/path/to/representative_subset
setenv SUBSET_LOGIC_JSON /absolute/path/to/representative_subset/logic_subset.json
setenv SUBSET_GT_JSON /absolute/path/to/representative_subset/gt_subset.json
setenv SUBSET_BASE_TUNED_CONFIG /absolute/path/to/representative_subset/tuned_base_subset.json
setenv SUBSET_FINAL_TUNED_CONFIG /absolute/path/to/representative_subset/tuned_final_subset.json
setenv FULL_REPORT_AI_JSON /absolute/path/to/representative_subset/full_report_ai.json
```

### 17.3 Build Full Real-Report Logic First

```csh
sanity-log-parser gca $REPORT \
  --ai off \
  --out $LOGIC_JSON \
  --max-original-logs 0

sanity-log-parser export-labeling \
  --input $LOGIC_JSON \
  --output-dir $LABEL_DIR
```

### 17.4 Select A Representative Subset

The subset must include:

- user complaint examples
- easy same-family examples
- hard boundary examples
- long-tail / outlier examples
- current false merges and false splits

Do not pick only clean examples.

Recommended size:

- minimum: 20 logic groups
- preferred: 30 to 80 logic groups

### 17.5 Build `logic_subset.json`

There is no built-in CLI for this step.

Create `$SUBSET_DIR/build_logic_subset.py` with this logic:

- read full `logic.json`
- read selected per-group JSON files from `$SUBSET_DIR/groups/`
- validate rule_id and `group_type == "logic"`
- write a schema-v2 results file containing only the selected logic groups

If this step fails, STOP.

### 17.6 Build `gt_subset.json`

Rules:

- every subset logic group exactly once
- no duplicates
- no omissions
- no out-of-subset groups

### 17.7 Baseline On The Representative Subset

Run the current config on the **full real report**:

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $BASE_CONFIG \
  --out $FULL_REPORT_AI_JSON \
  --max-original-logs 0
```

Then evaluate only against the representative subset:

```csh
sanity-log-parser gca-eval \
  --logic $SUBSET_LOGIC_JSON \
  --ai $FULL_REPORT_AI_JSON \
  --ground-truth $SUBSET_GT_JSON
```

### 17.8 Tune Against The Representative Subset

```csh
sanity-log-parser gca-fit-weights \
  --logic $SUBSET_LOGIC_JSON \
  --ground-truth $SUBSET_GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_CONFIG \
  --variables $TARGET_VARIABLES \
  -v \
  --out-rule-config $SUBSET_BASE_TUNED_CONFIG

sanity-log-parser gca-fit-adaptive-eps \
  --logic $SUBSET_LOGIC_JSON \
  --ground-truth $SUBSET_GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $SUBSET_BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode exact \
  --min-precision $MIN_PRECISION \
  --max-depth 5 \
  --max-min-samples-leaf 10 \
  --min-eps 0.001 \
  -v \
  --out-rule-config $SUBSET_FINAL_TUNED_CONFIG
```

### 17.9 Replay The Tuned Rule On The Full Real Report

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $SUBSET_FINAL_TUNED_CONFIG \
  --out $FULL_REPORT_AI_JSON \
  --max-original-logs 0

sanity-log-parser gca-eval \
  --logic $SUBSET_LOGIC_JSON \
  --ai $FULL_REPORT_AI_JSON \
  --ground-truth $SUBSET_GT_JSON
```

### 17.10 Final Real-Report Acceptance Rule

Only accept if all are true:

- subset metric improved
- replay on the full real report still looks good
- user complaint examples are now clustered correctly on the full report
- no obvious catastrophic new merge was introduced

If subset metric improved but complaint examples are still wrong, do not claim success.

## 18. DES_0004 Example Status

DES_0004 is a worked example in this repo.

Facts to remember:

- `labeling/DES_0004/` contains synthetic/example artifacts
- `labeling/DES_0004/build_from_markdown.py` can build a synthetic DES_0004 report and GT
- the shipped DES_0004 config currently contains a compact adaptive tree
- the checked-in F1=1.0 result is valid for its checked-in example GT, but it is not automatic proof of perfect behavior on every real report

For other rules, copy the process, not the literal DES_0004 values.

## 19. What To Return

Return a short summary only.

Format:

```text
Rule: RULE_XXXX
Baseline F1: 0.82
Base tuned F1: 0.91
Final tuned F1: 0.97
Final config file: /absolute/path/to/tuned_final_config.json
Decision: accept / reject
Reason: one sentence
```

Do not paste large JSON blobs unless explicitly asked.

Do not report `approx` F1 as the final metric.
