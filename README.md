# Sanity Log Parser

`sanity-log-parser` parses and clusters sanity logs, with first-class support for Synopsys PrimeTime constraint reports.

The pipeline has two stages:

1. logic clustering
2. optional AI clustering

For PrimeTime GCA reports, the recommended entrypoint is `sanity-log-parser gca`.

## What It Does

- parses PrimeTime constraint reports directly from the report structure
- groups identical logic patterns deterministically
- optionally merges similar logic groups with embeddings
- supports local sentence-transformers or remote OpenAI-compatible embeddings
- provides rule-level tuning utilities for `eps`, weights, levels, and adaptive eps trees

## Requirements

- Python 3.11+
- base install: logic clustering only
- local AI clustering: `sentence-transformers` + `scikit-learn`
- remote AI clustering: `scikit-learn`

If AI dependencies or embeddings config are missing, `--ai auto` falls back to logic-only output.

## Installation

```bash
pip install .
pip install ".[ai-local]"
pip install ".[ai-remote]"
pip install ".[dev]"
```

## Quick Start

Cluster a PrimeTime report:

```bash
sanity-log-parser gca REPORT.rpt
```

Write logic-only output:

```bash
sanity-log-parser gca REPORT.rpt --ai off --out logic.json
```

Render a saved result:

```bash
sanity-log-parser view subutai_results.json
```

## CLI Overview

### `gca`

```bash
sanity-log-parser gca REPORT.rpt [OPTIONS]
```

Use this for PrimeTime constraint reports. This command:

- parses the report with the built-in PrimeTime parser
- extracts `rule_id` and severity from the report itself
- loads GCA rule config
- runs logic clustering
- optionally runs weighted AI clustering

Common options:

- `--out PATH`
- `--ai {auto,on,off}`
- `--embeddings-config PATH`
- `--rule-config PATH`
- `--json-indent N`
- `--max-original-logs N`
- `--no-color`
- `-v, --verbose`

### `cluster`

```bash
sanity-log-parser cluster LOG_FILE [TEMPLATE_FILE] [OPTIONS]
```

Generic or legacy mode.

Use cases:

- legacy two-file parsing with `LOG_FILE + TEMPLATE_FILE`
- single-file parsing without GCA rule config

Important:

- if you need PrimeTime-specific rule tuning, use `gca`, not `cluster`
- `--rule-config` is only used by `gca`

### `view`

```bash
sanity-log-parser view [RESULTS_JSON] [OPTIONS]
```

Renders a saved results file in the terminal.

Useful options:

- `--top N`
- `--no-color`

## GCA Evaluation And Tuning Commands

### `gca-eval`

```bash
sanity-log-parser gca-eval \
  --logic logic.json \
  --ai ai.json \
  --ground-truth gt.json
```

Compares AI output against ground truth and reports precision, recall, and F1 per rule.

### `gca-distances`

```bash
sanity-log-parser gca-distances \
  --logic logic.json \
  --rule-id DES_0001 \
  --rule-config rule_clustering_config.json \
  --ground-truth gt.json
```

Shows pairwise base distances for one rule.

Use this to inspect the base weighted distance behavior before adaptive eps.

### `gca-fit-weights`

```bash
sanity-log-parser gca-fit-weights \
  --logic logic.json \
  --ground-truth gt.json \
  --rule-id DES_0001 \
  --rule-config rule_clustering_config.json \
  --out-rule-config tuned_base_config.json
```

Searches a **base** rule config automatically for one GCA rule.

It can search:

- `eps`
- `template_weight`
- variable `weight`
- variable `levels`
- variable `level_weights`
- variable `match_mode`

You can let it build a default search space, or provide one explicitly with `--search-spec`.

Important:

- this command writes a new config file
- it removes `pairwise_tree` and `adaptive_eps_tree` from the tuned rule in the output config
- use the written file directly instead of editing JSON by hand

### `gca-fit-adaptive-eps`

```bash
sanity-log-parser gca-fit-adaptive-eps \
  --logic logic.json \
  --ground-truth gt.json \
  --rule-id DES_0001 \
  --rule-config tuned_base_config.json \
  --out-rule-config tuned_final_config.json
```

Fits an `adaptive_eps_tree` for one GCA rule after the base rule config has been tuned.

### Full Air-Gapped Workflow

See [AGENT_TUNING.md](AGENT_TUNING.md) for the step-by-step tuning guide written for an air-gapped agent.

## Input Formats

### PrimeTime Constraint Report

This is the main GCA input format.

Expected structure:

```text
******************************************
Report : report_constraint_analysis
Version: U-2022.12-SP5-3
Date   : Tue Sep 2 17:40:49 2025
******************************************

 error                  62   0
  CGR_0018          46    0 Clock 'clk1' is generated from 'clk2'
       1 of 46          0 Clock 'GEN_A' is generated from 'MSTR'
       2 of 46          0 Clock 'GEN_B' is generated from 'MSTR2'

 warning               12   0
  CLK_0035           4    0 Clock 'x' is generated from 'y'
       1 of 4           0 ...
```

Hierarchy used by the parser:

1. severity section
2. rule parent line
3. instance line

Notes:

- instance lines inherit `rule_id` from the parent line
- severity words seen in practice: `error`, `warning`, `info`
- the report itself is the source of rule IDs and messages

### Legacy Two-File Mode

Use this only with `cluster LOG_FILE TEMPLATE_FILE`.

`LOG_FILE` line format:

```text
1 of 10 0 Signal 'u_top' not found
```

`TEMPLATE_FILE` line format:

```text
R001 HIGH INFO Signal 'u_top' not found
```

In legacy mode, the parser reads the message from field 4 onward.

## Embeddings Configuration

Config resolution order:

1. `--embeddings-config` or `--config`
2. `SANITY_LOG_PARSER_EMBEDDINGS_CONFIG`
3. `./config.json`
4. built-in defaults

Example remote config:

```json
{
  "embeddings_backend": "openai_compatible",
  "embed_batch_size": 512,
  "openai_compatible": {
    "base_url": "https://api.openai.com/v1",
    "model": "text-embedding-3-small",
    "api_key": "YOUR_API_KEY_HERE"
  }
}
```

Supported backends:

- `local`
- `openai_compatible`

Local backend:

- default model path is `nomic-ai/nomic-embed-text-v1.5`
- requires `sentence-transformers` and `scikit-learn`

Remote backend:

- requires `scikit-learn`
- reads `openai_compatible.api_key` or `OPENAI_API_KEY`

### `embed_batch_size`

`embed_batch_size` controls how many texts are sent in one embedding call.

- default: `512`
- smaller values: fewer timeout or memory issues
- larger values: fewer round-trips

## AI Clustering Model

When GCA rule config is loaded, the AI stage uses weighted distances.

At a high level:

```text
distance(A, B) =
  template_weight * template_distance(A, B)
  + sum(variable_weight_i * variable_distance_i(A, B))
```

Details:

- weights are renormalized per pair
- missing variable slots are masked out per pair
- `match_mode: "embedding"` uses cosine distance on embeddings
- `match_mode: "jaccard"` uses token-set Jaccard distance
- identical texts are deduplicated before embedding requests

Important for `DES_0001`:

- if only one variable slot is active and `template_weight = 0`, changing the scalar variable weight alone may not change the result
- in that case, `levels`, `match_mode`, and `eps` usually matter more than the raw weight number

## GCA Rule Config

Top-level keys:

- `default_eps`
- `default_template_weight`
- `default_variable_weight`
- `rules`

Per-rule keys:

- `eps`
- `template_weight`
- `variables`
- `pairwise_tree`
- `adaptive_eps_tree`

Per-variable keys:

- `weight`
- `levels`
- `level_weights`
- `match_mode`

Minimal example:

```json
{
  "default_eps": 0.2,
  "default_template_weight": 0.3,
  "default_variable_weight": 0.7,
  "rules": {
    "DES_0001": {
      "eps": 0.15,
      "template_weight": 0.0,
      "variables": {
        "0": {
          "weight": 1.0,
          "levels": [-3],
          "match_mode": "embedding"
        }
      }
    }
  }
}
```

## Results Format

Results are written to `subutai_results.json` unless `--out` is set.

Schema version:

- `2`

Top-level structure:

```json
{
  "schema_version": 2,
  "run": {
    "timestamp_utc": "2026-03-10T12:00:00Z",
    "log_file": "report.rpt",
    "sanity_item": "gca",
    "counts": {
      "parsed_logs": 100,
      "logic_groups": 25,
      "final_groups": 18
    },
    "ai": {
      "enabled": true,
      "backend": "openai_compatible",
      "warnings": []
    }
  },
  "groups": [
    {
      "group_type": "logic",
      "group_id": "DES_0001::logic::000001",
      "rule_id": "DES_0001",
      "representative_template": "Some template",
      "representative_pattern": "some/pattern",
      "total_count": 5,
      "merged_variants_count": 1,
      "original_logs": ["raw log line"]
    }
  ]
}
```

`run.ai.warnings` persists config and runtime warnings so downstream tooling can see why AI was disabled or degraded.

## Typical Tuning Loop

Generate logic groups:

```bash
sanity-log-parser gca REPORT.rpt --ai off --out logic.json --max-original-logs 0
```

Run baseline AI:

```bash
sanity-log-parser gca REPORT.rpt \
  --ai on \
  --rule-config rule_clustering_config.json \
  --out ai.json \
  --max-original-logs 0
```

Evaluate:

```bash
sanity-log-parser gca-eval --logic logic.json --ai ai.json --ground-truth gt.json
```

Tune base config:

```bash
sanity-log-parser gca-fit-weights \
  --logic logic.json \
  --ground-truth gt.json \
  --rule-id DES_0001 \
  --rule-config rule_clustering_config.json \
  --out-rule-config tuned_base_config.json
```

Fit adaptive eps:

```bash
sanity-log-parser gca-fit-adaptive-eps \
  --logic logic.json \
  --ground-truth gt.json \
  --rule-id DES_0001 \
  --rule-config tuned_base_config.json \
  --out-rule-config tuned_final_config.json
```

Run final evaluation:

```bash
sanity-log-parser gca REPORT.rpt \
  --ai on \
  --rule-config tuned_final_config.json \
  --out ai.json \
  --max-original-logs 0

sanity-log-parser gca-eval --logic logic.json --ai ai.json --ground-truth gt.json
```

## Development

Run tests:

```bash
pytest -q
```

Useful targeted test runs:

```bash
pytest -q tests/test_weight_tuning.py tests/test_cli_output.py
```

Install editable dev dependencies:

```bash
pip install -e ".[dev]"
```
