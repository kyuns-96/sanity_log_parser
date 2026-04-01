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
