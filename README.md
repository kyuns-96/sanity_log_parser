# Sanity Log Parser

A tool for parsing and clustering Synopsys PrimeTime constraint reports (and similar log files) using a two-stage workflow: logic-based grouping followed by semantic AI merging. It compresses large reports into manageable groups of similar events.

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

1. Cluster a PrimeTime constraint report (single-file mode):
   ```bash
   sanity-log-parser cluster REPORT_FILE
   ```

2. Or use legacy two-file mode with a separate template:
   ```bash
   sanity-log-parser cluster LOG_FILE TEMPLATE_FILE
   ```

3. View the results:
   ```bash
   sanity-log-parser view [subutai_results.json]
   ```

## CLI Usage

The CLI has two subcommands: `cluster` and `view`.

### `cluster`

```
sanity-log-parser cluster LOG_FILE [TEMPLATE_FILE] [OPTIONS]
```

When `TEMPLATE_FILE` is omitted, the tool uses the built-in PrimeTime parser which extracts rule IDs and severity from the report's own structure (severity sections and parent/rule lines). When provided, it falls back to the legacy two-file parsing mode.

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

`NO_COLOR=1` also disables colored output.

### `view`

```
sanity-log-parser view [RESULTS_JSON] [OPTIONS]
```

| Option | Default | Description |
|---|---|---|
| `--top N` | `50` | Maximum number of groups to show |
| `--no-color` | | Disable ANSI color output |

## Input Formats

### Single-File Mode (PrimeTime Reports)

A Synopsys PrimeTime constraint report (`.rpt`) containing severity sections, rule/parent lines, and instance lines. The parser uses the report's own structure to extract rule IDs and severity.

Report structure:
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

### Legacy Two-File Mode

#### LOG_FILE

A text log file where lines containing `N of M` are parsed. The text after the first 4 whitespace-separated fields is used as the message.

Example line:
```
1 of 10 foo bar Signal 'u_top' not found
```

#### TEMPLATE_FILE

A whitespace-separated file where each line defines a rule.
- Field 1: `rule_id`
- Field 4+: Message template

Example line:
```
R001 HIGH INFO Signal 'u_top' not found
```

## Output

Results are written to `subutai_results.json` (or the path given by `--out`).

### Schema Overview
- `rule_id`: The ID from the report structure (single-file) or template file (legacy), or a generated hash.
- `representative_pattern`: A sample log line representing the group.
- `total_count`: Number of logs in this group.
- `original_logs`: List of all raw log lines belonging to this group.
- `merged_variants_count`: (AI only) Number of logic groups merged into this super group.

In single-file mode, `template_file` is omitted from the run metadata.

## AI Clustering

If `sentence-transformers` and `scikit-learn` are installed, the tool performs a second stage of clustering to semantically merge similar groups using cosine similarity on embeddings.

Control AI behavior with `--ai`:
- `auto` (default): use AI if dependencies are available
- `on`: require AI (fails if dependencies are missing)
- `off`: skip AI stage entirely

### Local Model Usage

The AIClusterer uses `all-MiniLM-L6-v2` by default, downloaded and cached automatically via sentence-transformers.

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
- `embeddings_backend`: `openai_compatible` for a remote API, or `local` (default) for local models.
- `openai_compatible.base_url`: The base URL of the OpenAI-compatible API.
- `openai_compatible.model`: The model name to use for embeddings.
- `openai_compatible.api_key`: Your API key. Falls back to the `OPENAI_API_KEY` environment variable if omitted.

#### Config Resolution

1. If `--config` is provided, that path is used.
2. Otherwise, the tool looks for `config.json` next to the package, then in the current directory.
3. If no config is found or `embeddings_backend` is invalid, the tool falls back to local embeddings.

Pass `--config` explicitly to avoid current-directory ambiguity:
```bash
sanity-log-parser cluster LOG_FILE TEMPLATE_FILE --config /path/to/config.json
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
    cli.py              # CLI entry point
    clustering/         # Logic and AI clustering
    config/             # Config loading and resolution
    parsing/            # Log and template parsing
      __init__.py       # Shared parse_log_file() entry point
      primetime_parser.py  # Single-file PrimeTime report parser
      subutai_parser.py    # Legacy two-file parser
      template_manager.py  # Rule template handling
    results/            # Output schema and writing
    embeddings/         # Embedding backends
    patterns.py         # Shared regex patterns
    view.py             # Report rendering
tests/
pyproject.toml
```
