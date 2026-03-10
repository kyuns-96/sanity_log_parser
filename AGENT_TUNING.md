# AGENT_TUNING

This file is for an air-gapped agent that must tune `DES_0001` or another GCA rule without hand-editing the config many times.

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

## 2. Important Rule For `DES_0001`

For the current `DES_0001` config, a scalar `weight` change by itself may do nothing.

Reason:

- `template_weight` is `0`
- only one variable slot is active
- runtime renormalizes weights per pair

So for `DES_0001`, useful search dimensions are usually:

- `levels`
- `match_mode`
- `eps`
- `template_weight`

Do not assume that changing `weight: 1.0` to `weight: 0.8` will change clustering.

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

`gt.json` must contain complete ground truth for the target rule.

Format:

```json
{
  "DES_0001": [
    ["DES_0001::logic::000001", "DES_0001::logic::000002"],
    ["DES_0001::logic::000003"]
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

## 8. Step 4: Tune The Base Rule Automatically

Do not hand-edit the config.

Use `gca-fit-weights` first.

### 8A. First Attempt: Default Search

For `DES_0001`, start with variable `0`.

```csh
sanity-log-parser gca-fit-weights \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id DES_0001 \
  --rule-config $BASE_CONFIG \
  --variables 0 \
  --out-rule-config $BASE_TUNED_CONFIG
```

What this command does:

1. loads `logic.json`
2. loads `gt.json`
3. builds a search space for the target rule
4. tries many base configs automatically
5. scores each candidate with precision, recall, and F1
6. prints the best candidates
7. writes the best **base** config to `$BASE_TUNED_CONFIG`

Important:

- this command removes `pairwise_tree` and `adaptive_eps_tree` from the tuned rule in the output config
- this is intentional
- the output is a **base config**, not the final adaptive config

### 8B. Read The Output

The command prints:

- total candidate count
- best precision
- best recall
- best F1
- best config summary
- top candidate list

Use the top line as the selected base config.

Do not manually merge the output into the old config.
Always use the written file.

## 9. Step 5: If Default Search Is Not Good Enough, Use A Search Spec

If the default search still gives poor F1, create a small explicit search spec.

Example for `DES_0001`:

```json
{
  "template_weight": [0.0, 0.2],
  "eps": [0.05, 0.1, 0.15, 0.2, 0.3],
  "variables": {
    "0": [
      { "weight": 0.0 },
      { "weight": 1.0, "levels": [-4] },
      { "weight": 1.0, "levels": [-3] },
      { "weight": 1.0, "levels": [-2] },
      { "weight": 1.0, "levels": [-3, -2] },
      { "weight": 1.0, "levels": [-2], "match_mode": "jaccard" },
      { "weight": 1.0, "levels": [-3], "match_mode": "jaccard" }
    ]
  }
}
```

Save it as `des_0001_search_spec.json`.

Then run:

```csh
sanity-log-parser gca-fit-weights \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id DES_0001 \
  --rule-config $BASE_CONFIG \
  --search-spec /absolute/path/to/des_0001_search_spec.json \
  --out-rule-config $BASE_TUNED_CONFIG
```

Search spec rules:

- `template_weight` is a list of candidate numbers
- `eps` is a list of candidate numbers
- `variables` maps variable index to a list of candidate variable configs
- each variable candidate can use:
  - `weight`
  - `levels`
  - `level_weights`
  - `match_mode`

Do not put both `levels` and `level_weights` in the same candidate.

## 10. Step 6: Validate The Tuned Base Config

Run the pipeline again with the tuned base config.

```csh
sanity-log-parser gca $REPORT \
  --ai on \
  --rule-config $BASE_TUNED_CONFIG \
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
- if F1 is still low and the rule mixes different structural regimes, continue to adaptive eps

## 11. Step 7: Fit Adaptive Eps On Top Of The Tuned Base Config

This is the normal path for `DES_0001`.

Run:

```csh
sanity-log-parser gca-fit-adaptive-eps \
  --logic $LOGIC_JSON \
  --ground-truth $GT_JSON \
  --rule-id DES_0001 \
  --rule-config $BASE_TUNED_CONFIG \
  --out-rule-config $FINAL_TUNED_CONFIG \
  --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json
```

What this command does:

1. uses the tuned base config as input
2. computes the base distance matrix
3. fits an `adaptive_eps_tree`
4. writes the final config to `$FINAL_TUNED_CONFIG`

Do not fit adaptive eps from the old config if you already changed the base rule.
Always fit adaptive eps from `$BASE_TUNED_CONFIG`.

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
4. then search `match_mode`
5. then search `template_weight`
6. only after that, widen `eps`
7. fit adaptive eps again

For `DES_0001`, prefer fixing the base path signal before growing a more complex adaptive tree.

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
- `match_mode` is wrong

Fix:

- provide a custom search spec

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
Rule: DES_0001
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

sanity-log-parser gca-fit-weights --logic $LOGIC_JSON --ground-truth $GT_JSON --rule-id DES_0001 --rule-config $BASE_CONFIG --variables 0 --out-rule-config $BASE_TUNED_CONFIG

sanity-log-parser gca-fit-adaptive-eps --logic $LOGIC_JSON --ground-truth $GT_JSON --rule-id DES_0001 --rule-config $BASE_TUNED_CONFIG --out-rule-config $FINAL_TUNED_CONFIG --features-json src/sanity_log_parser/gca/adaptive_eps_features_structural_v1.json

sanity-log-parser gca $REPORT --ai on --rule-config $FINAL_TUNED_CONFIG --out $AI_JSON --max-original-logs 0
sanity-log-parser gca-eval --logic $LOGIC_JSON --ai $AI_JSON --ground-truth $GT_JSON
```
