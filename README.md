# Sanity Log Parser

A tool for parsing and clustering Synopsys PrimeTime constraint reports (and similar log files) using a two-stage workflow: logic-based grouping followed by optional semantic AI merging. It compresses large reports into manageable groups of similar events.

## Requirements

- Python 3.11+
- Optional: `sentence-transformers`, `scikit-learn` (for local AI-based semantic clustering)
- Optional: `scikit-learn` (for remote OpenAI-compatible embeddings)

The tool works without AI dependencies by falling back to logic-only clustering.

## Installation

```bash
pip install .                  # base (logic clustering only)
pip install ".[ai-local]"     # + local sentence-transformers model
pip install ".[ai-remote]"    # + remote OpenAI-compatible embeddings
pip install ".[dev]"          # + pytest
```

## Quickstart

1. Cluster a PrimeTime Constraints (GCA) report:
   ```bash
   sanity-log-parser gca REPORT_FILE
   ```

2. View the results:
   ```bash
   sanity-log-parser view
   ```

3. Or use the generic `cluster` subcommand with a separate template:
   ```bash
   sanity-log-parser cluster LOG_FILE TEMPLATE_FILE
   ```

## CLI Usage

The CLI provides item-specific subcommands (e.g. `gca`) for each sanity item type, plus a generic `cluster` subcommand and a `view` subcommand for rendering results.

### `gca`

```
sanity-log-parser gca REPORT_FILE [OPTIONS]
```

Parses and clusters a PrimeTime Constraints ("GCA") report. Uses the built-in PrimeTime parser to extract rule IDs and severity from the report's own structure. The output metadata includes `"sanity_item": "gca"`.

| Option | Default | Description |
|---|---|---|
| `--out PATH` | `subutai_results.json` | Output JSON path |
| `--config PATH` | auto-detected | Embeddings config file path |
| `--rule-config PATH` | auto-detected | Rule clustering config path |
| `--ai {auto,on,off}` | `auto` | AI clustering mode |
| `--json-indent N` | `2` | JSON output indentation |
| `--max-original-logs N` | `0` (all) | Max original logs stored per group |
| `--no-color` | | Disable ANSI color output |
| `-v, --verbose` | | Enable INFO-level logging |

### `cluster`

```
sanity-log-parser cluster LOG_FILE [TEMPLATE_FILE] [OPTIONS]
```

Generic/legacy clustering subcommand. When `TEMPLATE_FILE` is omitted, the tool uses the built-in PrimeTime parser (same as `gca`). When provided, it uses the legacy two-file parsing mode.

Accepts the same options as `gca`, plus an optional `TEMPLATE_FILE` positional argument.

### `view`

```
sanity-log-parser view [RESULTS_JSON] [OPTIONS]
```

Renders a human-readable report from a results JSON file.

| Option | Default | Description |
|---|---|---|
| `--top N` | `50` | Maximum number of groups to show |
| `--no-color` | | Disable ANSI color output |

### Environment Variables

- `NO_COLOR=1` disables colored output (equivalent to `--no-color`).

## Input Formats

### PrimeTime Constraint Reports (used by `gca` and single-file `cluster`)

A Synopsys PrimeTime constraint report (`.rpt`) containing severity sections, rule/parent lines, and instance lines. The parser extracts rule IDs, severity, and instance messages from the report's own structure.

```
****************************************
  Report : ...
  Version: ...
  Date   : ...
****************************************

  error    5   0
    CGR_0018    3   0   Some rule description
        1 of 3   0   Instance message for 'signal_a'
        2 of 3   0   Instance message for 'signal_b'
        3 of 3   0   Instance message for 'signal_c'

  warning    12   0
    CLK_0042    4   0   Another rule description
        1 of 4   0   Clock 'clk_main' has issue
        ...
```

### Legacy Two-File Mode (used by `cluster` with `TEMPLATE_FILE`)

#### LOG_FILE

A text log file where lines containing `N of M` are parsed. The text after the first 4 whitespace-separated fields is used as the message.

```
1 of 10 foo bar Signal 'u_top' not found
```

#### TEMPLATE_FILE

A whitespace-separated file where each line defines a rule.
- Field 1: `rule_id`
- Field 4+: Message template

```
R001 HIGH INFO Signal 'u_top' not found
```

## Output

Results are written to `subutai_results.json` (or the path given by `--out`).

### Schema (v2)

The output JSON has this structure:

```json
{
  "schema_version": 2,
  "run": {
    "timestamp_utc": "2026-02-26T12:00:00Z",
    "log_file": "report.rpt",
    "sanity_item": "gca",
    "counts": { "parsed_logs": 100, "logic_groups": 25, "final_groups": 18 },
    "ai": { "enabled": true, "backend": "local", "warnings": [] }
  },
  "groups": [
    {
      "group_type": "logic",
      "group_id": "CGR_0018::logic::000001",
      "rule_id": "CGR_0018",
      "representative_template": "Clock 'clk1' from 'clk2'",
      "representative_pattern": "'clk1'",
      "total_count": 3,
      "merged_variants_count": 1,
      "original_logs": ["..."]
    }
  ]
}
```

### Run Metadata Fields

| Field | Presence | Description |
|---|---|---|
| `timestamp_utc` | Always | ISO 8601 timestamp |
| `log_file` | Always | Path to the input file |
| `template_file` | Legacy two-file mode only | Path to the template file |
| `sanity_item` | Item-specific subcommands only | Sanity item type (e.g. `"gca"`) |
| `counts` | Always | Parsed logs, logic groups, and final groups |
| `ai` | Always | AI clustering status, backend, and warnings |

### Group Fields

| Field | Description |
|---|---|
| `group_type` | `"logic"` or `"ai_super"` |
| `group_id` | Unique ID: `{rule_id}::{type}::{seq}` |
| `rule_id` | Rule ID from the report or template, or a generated hash |
| `representative_template` | Representative message template for the group |
| `representative_pattern` | A sample log line representing the group |
| `total_count` | Number of logs in this group |
| `merged_variants_count` | Number of logic groups merged (AI only; 1 for logic groups) |
| `original_logs` | All raw log lines belonging to this group |

## AI Clustering

If `sentence-transformers` and `scikit-learn` are installed, the tool performs a second stage of clustering to semantically merge similar groups using cosine similarity on embeddings.

Control AI behavior with `--ai`:
- `auto` (default): use AI if dependencies are available
- `on`: require AI (fails if dependencies are missing)
- `off`: skip AI stage entirely

### Local Model

The AI clusterer uses `all-MiniLM-L6-v2` by default, downloaded and cached automatically via sentence-transformers.

### OpenAI-Compatible Embeddings

Use a remote API for embeddings by creating a `config.json` (see `config.json.example`):

```json
{
  "embeddings_backend": "openai_compatible",
  "openai_compatible": {
    "base_url": "https://api.openai.com/v1",
    "model": "text-embedding-3-small",
    "api_key": "YOUR_API_KEY_HERE"
  }
}
```

#### Configuration Keys

| Key | Description |
|---|---|
| `embeddings_backend` | `"openai_compatible"` for remote API, or `"local"` (default) |
| `openai_compatible.base_url` | Base URL of the OpenAI-compatible API |
| `openai_compatible.model` | Model name for embeddings |
| `openai_compatible.api_key` | API key (falls back to `OPENAI_API_KEY` env var) |

#### Config Resolution

1. If `--config` is provided, that path is used.
2. Otherwise, the tool looks for `config.json` next to the package, then in the current directory.
3. If no config is found or `embeddings_backend` is invalid, the tool falls back to local embeddings.

```bash
sanity-log-parser gca REPORT_FILE --config /path/to/config.json
```

## Running Tests

```bash
pip install ".[dev]"
python -m pytest -q
```

## Project Structure

```
src/
  sanity_log_parser/
    cli.py                 # CLI entry point, subcommand routing, pipeline
    console.py             # Colored terminal output
    patterns.py            # Shared regex patterns
    view.py                # Report rendering
    clustering/
      logic.py             # Logic-based clustering (stage 1)
      ai/
        clusterer.py       # AI semantic clustering (stage 2)
    config/
      resolution.py        # Config loading and resolution
    parsing/
      __init__.py          # parse_log_file() entry point
      primetime_parser.py  # Single-file PrimeTime report parser
      subutai_parser.py    # Legacy two-file parser
      template_manager.py  # Rule template handling
    results/
      schema_v2.py         # Output schema, TypedDicts, read/write
    embeddings/            # Embedding backends (local, OpenAI-compatible)
    data/                  # Bundled config files
tests/
pyproject.toml
```
