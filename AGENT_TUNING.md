# AGENT_TUNING

This file is for an air-gapped agent that must tune one GCA rule without hand-editing the config many times.

Use this workflow exactly.

## 1. What You Are Tuning

You are tuning **Stage 2 AI clustering** for one rule.

Pipeline:

1. `logic clustering`
2. `AI clustering`

You only tune Stage 2.

Files used in this workflow:

- `REPORT.rpt`
  PrimeTime constraint report
- `logic.json`
  output of `sanity-log-parser gca REPORT.rpt --ai off`
- `gt.json`
  ground truth clusters for one rule
- `rule_clustering_config.json`
  current config
- `tuned_base_config.json`
  output of `gca-fit-weights`
- `tuned_final_config.json`
  output of `gca-fit-adaptive-eps`
- `ai.json`
  final AI output to evaluate

## 2. Important Check: Base Tuning vs Adaptive Eps

There are two different tuning layers in this workflow.

- `gca-fit-weights` tunes the base rule:
  - `eps`
  - `template_weight`
  - `variables[*]`
- `gca-fit-adaptive-eps` fits an `adaptive_eps_tree` on top of the current base rule.

Do not mix them up.

- base tuning does not directly tune the adaptive tree
- adaptive-eps fitting does not search base `weight` candidates
- if you run `gca-fit-weights`, you must run `gca-fit-adaptive-eps` again afterward

Also, a scalar `weight` change by itself may do nothing for some rules.

This usually happens when:

- `template_weight` is `0`
- only one variable slot is active
- runtime renormalizes weights per pair

In that rule shape, useful search dimensions are usually:

- `levels`
- `eps`
- `template_weight`

Do not assume that changing `weight: 1.0` to `weight: 0.8` will change clustering.
Check the current rule config first, then decide whether weight magnitude is actually a meaningful search dimension.

## 3. Shell Rules

The air-gapped shell is `csh` or `tcsh`.

Use these rules:

- use `setenv NAME value`
- do not use `export`
- do not use bash arrays
- do not use `python -c`
- if you need a temporary script, write a `.py` file and run `python3 file.py`

## 4. Step 0: Set Paths

Run this first and edit the paths only once.

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
```

If the project is not installed as a package yet, run commands with:

```csh
python3 -m sanity_log_parser ...
```

If the console script is installed, use:

```csh
sanity-log-parser ...
```

The examples below use `sanity-log-parser`.

`TARGET_VARIABLES` should be the comma-separated variable slot indices you want `gca-fit-weights` to search.

Examples:

- `0`
- `0,1`

If you are not sure, inspect the current rule entry in `rule_clustering_config.json` and use the active variable keys from that rule.

## 5. Step 1: Generate `logic.json`

This is the Stage 1 output. It is required for tuning.

```csh
sanity-log-parser gca $REPORT \
  --ai off \
  --out $LOGIC_JSON \
  --max-original-logs 0
```

Expected result:

- command exits successfully
- `logic.json` exists
- it contains `group_type == "logic"` groups

If this step fails, stop and fix parsing first.

## 6. Step 2: Prepare `gt.json`

Before writing `gt.json`, export the logic groups into per-group labeling files:

```csh
sanity-log-parser export-labeling \
  --input $LOGIC_JSON \
  --output-dir /absolute/path/to/labeling_out
```

Expected result:

- one JSON file per logic group
- files written under `/absolute/path/to/labeling_out/<RULE_ID>/`
- each file keeps the full group payload, including all `original_logs`

Use those exported files for manual labeling, then convert the final labels into `gt.json`.

`gt.json` must contain complete ground truth for the target rule.

Format:

```json
{
  "RULE_XXXX": [
    ["RULE_XXXX::logic::000001", "RULE_XXXX::logic::000002"],
    ["RULE_XXXX::logic::000003"]
  ]
}
```

Rules:

- use `group_id` values from `logic.json`
- include only logic groups
- for the tuned rule, every logic group must appear exactly once
- do not omit any logic group for that rule
- do not duplicate any logic group

If ground truth is incomplete, fitting commands will fail. That is correct behavior.

## 7. Step 3: Measure Current Behavior

First, run the current config without changing anything.

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $BASE_CONFIG \
  --out $AI_JSON \
  --max-original-logs 0
```

Then evaluate:

```csh
sanity-log-parser gca-eval \
  --logic $LOGIC_JSON \
  --ai $AI_JSON \
  --ground-truth $GT_JSON
```

Save the current metrics. This is your baseline.

## 8. Step 4: Fit Adaptive Eps Directly

Do not hand-edit the config.

For ground-truth fitting, use `gca-fit-adaptive-eps` first.

This is the normal path for any rule.

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

What this command does:

1. loads `logic.json`
2. loads `gt.json`
3. uses the current rule config in `$BASE_CONFIG`
4. computes base distances for the rule
5. fits an `adaptive_eps_tree`
6. scores candidates with precision, recall, and F1
7. writes the final config to `$FINAL_TUNED_CONFIG`

Important:

- this command removes `pairwise_tree` from the tuned rule in the output config
- this is intentional
- the output is the final adaptive config for this fitting pass
- `--fit-mode approx --jobs 0` is the recommended large-rule path
- `-v` prints live candidate-search progress and new-best updates while the search runs

## 9. Step 5: Validate The Adaptive Config

The command prints:

- node count
- max depth
- min samples leaf
- precision
- recall
- F1

Run the pipeline with the adaptive config:

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $FINAL_TUNED_CONFIG \
  --out $AI_JSON \
  --max-original-logs 0
```

Then evaluate:

```csh
sanity-log-parser gca-eval \
  --logic $LOGIC_JSON \
  --ai $AI_JSON \
  --ground-truth $GT_JSON
```

Interpretation:

- if F1 is already good enough, you can stop here
- if F1 is still low, continue to the optional base-tuning fallback below

## 10. Step 6: Optional Base-Tuning Fallback

If direct adaptive fitting is still poor, try `gca-fit-weights` to search a better base rule.

Use this fallback only when at least one of these is true:

- the direct adaptive fit from `$BASE_CONFIG` still has poor F1
- the best adaptive result has obviously wrong merges and the base rule looks too coarse
- you want to fix the base path signal before fitting adaptive eps again

Do not jump into `gca-fit-weights` first unless the direct adaptive path already failed.

Run:

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

What this command does:

1. loads `logic.json`
2. loads `gt.json`
3. builds a search space for the target rule
4. tries many base configs automatically
5. scores each candidate with precision, recall, and F1
6. prints the best candidates
7. writes the best base config to `$BASE_TUNED_CONFIG`

Use this only when the direct adaptive fit from `$BASE_CONFIG` is not good enough.

After the command finishes, do this exact check:

1. read the top candidate summary
2. confirm the selected `levels` look structurally sensible
3. confirm precision did not collapse
4. use the written file directly
5. do not hand-edit the old config

If the top candidate still looks wrong, do not continue with `$BASE_TUNED_CONFIG` yet.
Instead, go to Step 14 and run a smaller explicit search spec.

## 11. Step 7: Re-fit Adaptive Eps On Top Of The Tuned Base Config

If you used the base-tuning fallback, run adaptive eps again from `$BASE_TUNED_CONFIG`:

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Always fit this second adaptive pass from `$BASE_TUNED_CONFIG`, not from the old base config.

This step is mandatory after weight tuning.

Do not stop after `gca-fit-weights`.
`gca-fit-weights` only gives you a better base rule.
The final GT-fitting result still comes from `gca-fit-adaptive-eps`.

## 12. Step 8: Run Final Evaluation

Run the final pipeline:

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $FINAL_TUNED_CONFIG \
  --out $AI_JSON \
  --max-original-logs 0
```

Then evaluate:

```csh
sanity-log-parser gca-eval \
  --logic $LOGIC_JSON \
  --ai $AI_JSON \
  --ground-truth $GT_JSON
```

This is the metric that matters.

## 13. Step 9: Decision Rules

Use these rules.

### Accept

Accept the new config if:

- F1 improved over baseline
- precision did not collapse
- the result is stable across repeated runs

### Reject

Reject the new config if:

- F1 did not improve
- precision became much worse
- the selected rule setting is obviously using noisy path levels

## 14. Step 10: If Final F1 Is Still Low

Do this in order.

1. inspect the best `gca-fit-weights` candidate summary
2. try a smaller explicit search spec
3. search `levels` first
4. then search `template_weight`
5. only after that, widen `eps`
6. fit adaptive eps again

If the poor result looks like a base-distance problem, prefer fixing the base path signal before growing a more complex adaptive tree.

## 15. Short Troubleshooting

### `gca-fit-weights` fails with missing ground truth groups

Cause:

- `gt.json` is incomplete

Fix:

- every logic group for the target rule must appear exactly once

### `gca-fit-weights` runs but best F1 is still poor

Cause:

- search space is wrong
- signal is in a different path level

Fix:

- provide a custom search spec
- keep the search small and explicit
- re-run `gca-fit-weights`
- if the new base config looks better, re-run `gca-fit-adaptive-eps` using `$BASE_TUNED_CONFIG`

### Base tuning looks good but final F1 is poor

Cause:

- adaptive eps was fit from the wrong config
- or adaptive eps was not re-fit after base changes

Fix:

- run `gca-fit-adaptive-eps` again using `$BASE_TUNED_CONFIG`

### New config seems ignored

Cause:

- old file was used by mistake

Fix:

- confirm the runtime command points to `$BASE_TUNED_CONFIG` or `$FINAL_TUNED_CONFIG`

## 16. What To Return To The User

Return only a short summary.

Use this format:

```text
Rule: RULE_XXXX
Baseline F1: 0.82
Base tuned F1: 0.91
Final tuned F1: 0.97
Best base config: eps=0.15, template_weight=0.0, var0=embedding,w=1.0@[-3]
Final config file: /absolute/path/to/tuned_final_config.json
```

Do not paste large JSON blobs unless the user explicitly asks for them.

## 17. Minimal Command Checklist

If you need the shortest possible recipe, use this exact order.

```csh
sanity-log-parser gca $REPORT --ai off --out $LOGIC_JSON --max-original-logs 0

sanity-log-parser gca $REPORT --ai on --rule-config $BASE_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON

sanity-log-parser gca-fit-adaptive-eps --logic $LOGIC_JSON --ground-truth $GT_JSON --rule-id $TARGET_RULE_ID --rule-config $BASE_CONFIG --out-rule-config $FINAL_TUNED_CONFIG --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json --fit-mode approx --jobs 0 -v

sanity-log-parser gca $REPORT --ai on --rule-config $FINAL_TUNED_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON
```

If the direct adaptive result is still poor, use this exact fallback sequence:

```csh
sanity-log-parser gca-fit-weights --logic $LOGIC_JSON --ground-truth $GT_JSON --rule-id $TARGET_RULE_ID --rule-config $BASE_CONFIG --variables $TARGET_VARIABLES -v --out-rule-config $BASE_TUNED_CONFIG

sanity-log-parser gca $REPORT --ai on --rule-config $BASE_TUNED_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON

sanity-log-parser gca-fit-adaptive-eps --logic $LOGIC_JSON --ground-truth $GT_JSON --rule-id $TARGET_RULE_ID --rule-config $BASE_TUNED_CONFIG --out-rule-config $FINAL_TUNED_CONFIG --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json --fit-mode approx --jobs 0 -v

sanity-log-parser gca $REPORT --ai on --rule-config $FINAL_TUNED_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON
```

## 18. High-Detail Hard-Rule Playbook For Air-Gapped Agents

This section is intentionally much more detailed than the minimal workflow above.

Use it when:

- the rule is hard
- the first adaptive fit is poor
- the rule obviously over-merges unrelated logic groups
- you want the agent to follow a known-good process step by step

This is a **general playbook for any hard rule**.

DES_0004 is only the worked example.

Use this section like this:

- the workflow is general
- the decision rules are general
- the stop conditions are general
- DES_0004 only shows one real case where the full recovery path was needed

Do **not** read this section as “only do this for DES_0004”.

The important lesson is:

1. do not hand-edit the rule many times
2. fix the **base geometry** first if adaptive fitting keeps collapsing groups
3. only then run a high-quality adaptive fit
4. verify with the real metric every time

### 18.1 What Happened In DES_0004

DES_0004 did **not** reach F1 1.0 by running one normal adaptive command and stopping.

It reached F1 1.0 because the process did this in order:

1. build complete tuning artifacts
2. measure baseline
3. try direct adaptive fitting
4. notice that adaptive fitting was collapsing too many clusters
5. improve the base rule first
6. re-run adaptive fitting from the improved base rule
7. use the exact fitting path when trying to maximize final F1
8. keep the compact exact-fit tree that still scored 1.0

If you skip the diagnosis and jump directly to hand-editing thresholds, you will waste time.

### 18.1A How To Apply This To Rules Other Than DES_0004

For any other rule, keep the same reasoning order even if the exact winning config is different.

Use this logic:

1. measure baseline first
2. try direct adaptive fitting first
3. if direct adaptive fit is poor, diagnose whether the failure is mostly:
   - bad base geometry
   - or insufficient adaptive separation
4. if the failure is mostly bad base geometry, improve the base rule first
5. re-fit adaptive eps from the improved base rule
6. if you are close but still want the best final score, switch the last pass to `--fit-mode exact`
7. keep the smallest verified tree that preserves the final metric

This means:

- do not copy DES_0004 values blindly into another rule
- do copy the DES_0004 **process** when another rule shows the same failure shape

The worked example below shows what that process looked like on one difficult rule.

### 18.2 If You Only Have A Markdown Labeling File And Not A Full Report

DES_0004 originally started from:

- `labeling/DES_0004/DES_0004.md`

That file grouped raw failing paths into human label buckets, but it was not a full PrimeTime report.

For this case, use the checked-in helper script:

- `labeling/DES_0004/build_from_markdown.py`

This script does three jobs:

1. reads the Markdown buckets
2. creates a synthetic DES_0004-only `REPORT.rpt`
3. can derive `gt.json` from the generated manifest plus `logic.json`

#### 18.2.1 Generate synthetic report and manifest

```csh
python3 labeling/DES_0004/build_from_markdown.py
```

Expected outputs:

- `labeling/DES_0004/generated/des_0004_synthetic_report.rpt`
- `labeling/DES_0004/generated/des_0004_md_manifest.json`

Then set paths for this synthetic workflow:

```csh
setenv REPORT /absolute/path/to/labeling/DES_0004/generated/des_0004_synthetic_report.rpt
setenv TARGET_RULE_ID DES_0004
setenv TARGET_VARIABLES 0
setenv BASE_CONFIG /absolute/path/to/src/sanity_log_parser/gca/rule_clustering_config.json
setenv LOGIC_JSON /absolute/path/to/labeling/DES_0004/generated/logic.json
setenv GT_JSON /absolute/path/to/labeling/DES_0004/generated/gt.json
setenv BASE_TUNED_CONFIG /absolute/path/to/labeling/DES_0004/generated/tuned_base_config.json
setenv FINAL_TUNED_CONFIG /absolute/path/to/labeling/DES_0004/generated/tuned_final_config.json
setenv AI_JSON /absolute/path/to/labeling/DES_0004/generated/ai.json
```

#### 18.2.2 Generate `logic.json`

```csh
sanity-log-parser gca $REPORT \
  --ai off \
  --out $LOGIC_JSON \
  --max-original-logs 0
```

#### 18.2.3 Generate `gt.json` from the helper script

```csh
python3 labeling/DES_0004/build_from_markdown.py \
  --logic-json $LOGIC_JSON \
  --gt-json $GT_JSON
```

Expected result:

- every DES_0004 logic group appears exactly once in `gt.json`
- no group is missing
- no group is duplicated

### 18.3 Always Measure Baseline First

Before tuning, always measure the rule exactly as it exists now.

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

For DES_0004, the baseline was poor.

That is not a reason to hand-edit the tree.
That is a reason to start the tuning workflow.

### 18.4 First Try Direct Adaptive Fitting

Do this first.

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then evaluate it.

If it is already good enough, stop.

If it is **not** good enough, do not guess.
Diagnose the failure pattern.

### 18.5 How To Recognize The Bad Base-Geometry Failure Mode

DES_0004 did not fail because the adaptive tree was too small.
It failed because the **base distances were wrong** before the tree was applied.

You are probably in the same failure mode if these are true:

- direct adaptive fit still produces very low F1
- output AI cluster count is much smaller than GT cluster count
- recall is high but precision is poor
- unrelated human clusters are being bridged together
- base-tuned config improves behavior, but adaptive-on-top collapses again

In plain language:

- the adaptive tree is trying to rescue a bad base rule
- if the base distance already says two unrelated groups are almost identical, adaptive scaling has very little room to fix it

When you see this pattern, stop trying random adaptive feature files.
Improve the base rule first.

### 18.6 Improve The Base Rule First

Use `gca-fit-weights`.

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

Then evaluate that base-tuned config directly:

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

For DES_0004, the successful base shape was not just a scalar weight change.
It changed the path-signal representation.

The important pattern was:

- include more discriminative signal near the register name
- do not rely only on a single old `levels` slice if it causes obvious bridges

For DES_0004, the successful base rule shape was:

```json
"variables": {
  "0": {
    "level_weights": {
      "-4": 0.25,
      "-3": 0.25,
      "-2": 0.5
    }
  }
}
```

This matters because it changed the base geometry from “bad over-merge” into a state that adaptive fitting could actually refine.

### 18.7 After Base Tuning, Re-Fit Adaptive Eps Again

Do **not** stop after `gca-fit-weights`.

Always re-fit adaptive eps from the improved base config:

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then evaluate again.

### 18.8 When To Switch From `approx` To `exact`

Use this rule:

- use `approx` first when you are screening candidates
- use `exact` when you already have a promising base rule and want the best final result

For DES_0004, `approx` was not enough for the final jump to 1.0.

Once the improved base geometry was in place, the final successful command was an **exact** adaptive fit:

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode exact \
  --max-depth 5 \
  --max-min-samples-leaf 10 \
  --min-eps 0.001 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Why this worked:

1. the base rule was already good enough to separate the worst bridges
2. exact fitting could search the remaining candidate trees precisely
3. the fitter now includes a general leaf-epsilon optimization pass
4. the fitter also includes a general score-preserving subtree compaction pass

Because of those general fitter improvements, the final exact-fit result for DES_0004 was both:

- perfect on the training ground truth
- much smaller than the first exact-fit tree

### 18.9 Verify The Final Result Every Time

Never trust the fit command summary alone.

Always run the full pipeline after writing the final config:

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

For DES_0004, the final verified result was:

```text
Rule ID                   P      R     F1   TP   FP   FN  GT#  AI# Status
-------------------------------------------------------------------------
DES_0004               1.00   1.00   1.00  958    0    0   10   10 PASS
```

If the full pipeline does not reproduce the fit summary, do not claim success.

### 18.10 How To Keep The Tree Compact And Avoid Overfitting

The goal is **not** “largest possible tree with F1 1.0”.
The goal is “smallest reasonable tree that still verifies at F1 1.0”.

The fitter now helps with this automatically.

Generic compaction rules now built into the fitter:

1. optimize leaf eps values after the tree structure is learned
2. try score-preserving subtree-to-leaf collapses
3. keep the compacted tree, not the raw sklearn tree
4. prefer smaller perfect-score trees when comparing candidates

For DES_0004 this reduced the tree from:

- `45 nodes / 23 leaves`

to:

- `19 nodes / 10 leaves`

while still preserving:

- `P = 1.0`
- `R = 1.0`
- `F1 = 1.0`

That is the correct stopping point.

Do not hand-prune the final tree unless the full metric stays identical after each change.

This compaction rule is also general.

For any rule:

- first get the best verified score you can
- then prefer the smaller verified tree over the larger verified tree
- if two trees have the same verified metric, keep the smaller one

### 18.11 Exact Decision Logic For A Lower-Intelligence Agent

Use this logic exactly.

#### Case A: direct adaptive fit is already strong

- keep the result
- verify with full pipeline
- stop

#### Case B: direct adaptive fit is poor and over-merges unrelated groups

- run base tuning
- evaluate base-tuned config directly
- if base-tuned config is better, re-fit adaptive from that base

#### Case C: base-tuned config is better, but adaptive-on-top is still weak

- switch the final adaptive pass to `--fit-mode exact`
- keep the same base-tuned config
- verify with full pipeline

#### Case D: exact fit reaches F1 1.0 but the tree is large

- keep the compacted tree written by the fitter
- verify the compacted tree with full pipeline
- stop if metric is still 1.0

#### Case E: exact fit is still poor

- do not hand-author a complex adaptive tree
- go back and check the base geometry again
- the problem is probably still in the base signal, not the tree size

### 18.11A Mechanical Execution Mode: IF / THEN / STOP

Use this section when the agent is weak and should behave like a deterministic operator.

Do not improvise.
Do not skip steps.
Do not jump ahead.

Follow this exact order.

#### Block 1: Build required files

IF `logic.json` does not exist,
THEN run:

```csh
sanity-log-parser gca $REPORT \
  --ai off \
  --out $LOGIC_JSON \
  --max-original-logs 0
```

IF this command fails,
THEN STOP.
Reason: parsing is broken and tuning is invalid.

IF `gt.json` does not exist or is incomplete,
THEN build or fix it before tuning.

IF every logic group for `$TARGET_RULE_ID` does not appear exactly once in `gt.json`,
THEN STOP.
Reason: fitting results are invalid with incomplete ground truth.

#### Block 2: Measure baseline

Always run baseline before fitting.

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

Record these values:

- baseline precision
- baseline recall
- baseline F1
- baseline AI cluster count

IF baseline is already good enough for the user,
THEN STOP.

#### Block 3: First adaptive attempt

Run direct adaptive fitting first.

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then verify it:

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

Record:

- direct-adaptive precision
- direct-adaptive recall
- direct-adaptive F1
- direct-adaptive AI cluster count

IF direct-adaptive F1 is already good enough,
THEN STOP and keep `$FINAL_TUNED_CONFIG`.

#### Block 4: Decide whether the problem is base geometry

IF all or most of these are true:

- recall is high but precision is poor
- AI cluster count is much smaller than GT cluster count
- unrelated groups are merged together
- adaptive fitting did not materially improve baseline

THEN assume the rule has a **base-geometry problem**.

IF the rule has a base-geometry problem,
THEN do **not** try many random adaptive feature files yet.

Go to Block 5.

IF the rule does **not** look like a base-geometry problem,
THEN you may continue experimenting with adaptive fitting,
BUT you must still verify every candidate with full `gca` + `gca-eval`.

#### Block 5: Improve the base rule

Run base tuning:

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

Record:

- base-tuned precision
- base-tuned recall
- base-tuned F1
- base-tuned AI cluster count

IF base-tuned F1 is not better than baseline,
THEN STOP and inspect the search space before continuing.

IF base-tuned F1 is better than baseline,
THEN continue.

#### Block 6: Re-fit adaptive from the improved base

Never fit the second adaptive pass from `$BASE_CONFIG`.
Always fit from `$BASE_TUNED_CONFIG`.

First try the normal second pass:

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode approx \
  --jobs 0 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then verify it:

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

IF this result is good enough,
THEN STOP.

#### Block 7: Switch to exact fitting only at the end

IF:

- the base-tuned rule is clearly better than baseline
- but the final adaptive result is still not good enough
- and you are trying to maximize the final score

THEN run the final pass with `--fit-mode exact`.

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id $TARGET_RULE_ID \
  --rule-config $BASE_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json \
  --fit-mode exact \
  --max-depth 5 \
  --max-min-samples-leaf 10 \
  --min-eps 0.001 \
  -v \
  --out-rule-config $FINAL_TUNED_CONFIG
```

Then verify it again with the full runtime pipeline.

IF exact fit still does not improve the verified result,
THEN STOP and go back to base-geometry diagnosis.

#### Block 8: Compact tree rule

IF the verified score is good and the resulting tree is large,
THEN keep the compacted tree written by the fitter.

Do not prefer a larger tree if:

- F1 is identical
- precision is identical
- recall is identical

Always prefer the smaller verified tree.

#### Block 9: Final acceptance rule

Only claim success if all are true:

- the final config file exists
- the full runtime pipeline reproduces the claimed score
- F1 improved over baseline
- precision did not collapse
- the tree is the smallest verified version you have

IF any of those are false,
THEN do not claim success.
STOP and report what failed.

### 18.12 What The Air-Gapped Agent Must Not Do

Do not do these things:

- do not hand-edit many candidate configs one by one
- do not skip the baseline measurement
- do not stop after `gca-fit-weights`
- do not run the second adaptive fit from the old base config
- do not assume `approx` is always enough for the final pass
- do not trust the fitter summary without a full `gca` + `gca-eval` verification
- do not keep a larger tree if a smaller verified tree has the same score

### 18.13 Minimal DES_0004 Success Recipe

This final subsection is **example-specific**.

Use it only when the target rule is actually DES_0004.

For all other rules, use the general decision logic above, not the literal DES_0004 values.

If you need the shortest high-quality DES_0004 recipe after artifacts already exist, use this order:

```csh
sanity-log-parser gca $REPORT --ai off --out $LOGIC_JSON --max-original-logs 0

sanity-log-parser gca $REPORT --ai on --rule-config $BASE_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON

sanity-log-parser gca-fit-weights --logic $LOGIC_JSON --ground-truth $GT_JSON --rule-id $TARGET_RULE_ID --rule-config $BASE_CONFIG --variables $TARGET_VARIABLES -v --out-rule-config $BASE_TUNED_CONFIG

sanity-log-parser gca $REPORT --ai on --rule-config $BASE_TUNED_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON

sanity-log-parser gca-fit-adaptive-eps --logic $LOGIC_JSON --ground-truth $GT_JSON --rule-id $TARGET_RULE_ID --rule-config $BASE_TUNED_CONFIG --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json --fit-mode exact --max-depth 5 --max-min-samples-leaf 10 --min-eps 0.001 -v --out-rule-config $FINAL_TUNED_CONFIG

sanity-log-parser gca $REPORT --ai on --rule-config $FINAL_TUNED_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON
```

If the final eval does not show `F1 = 1.00`, do not claim success.
