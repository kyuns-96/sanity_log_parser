# Sanity Log Parser

A tool for clustering log files using a two-stage workflow: logic-based grouping followed by semantic AI merging. It compresses large log files into manageable groups of similar events.

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

1. Cluster a log file:
   ```bash
   sanity-log-parser cluster LOG_FILE TEMPLATE_FILE
   ```
   This writes `subutai_results.json` to the current directory.

2. View the results:
   ```bash
   sanity-log-parser view [subutai_results.json]
   ```

## CLI Usage

The CLI has two subcommands: `cluster` and `view`.

### `cluster`

```
sanity-log-parser cluster LOG_FILE TEMPLATE_FILE [OPTIONS]
```

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

### LOG_FILE

A standard text log file. The parser skips empty lines and lines starting with `-`, `=`, `Rule`, or `Severity`.

Only lines containing a counter like `N of M` are parsed. The text after the first 4 whitespace-separated fields is used as the message.

Example line:
```
1 of 10 foo bar Signal 'u_top' not found
```

### TEMPLATE_FILE

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
- `rule_id`: The ID from the template file or a generated hash.
- `representative_pattern`: A sample log line representing the group.
- `total_count`: Number of logs in this group.
- `original_logs`: List of all raw log lines belonging to this group.
- `merged_variants_count`: (AI only) Number of logic groups merged into this super group.

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
    results/            # Output schema and writing
    embeddings/         # Embedding backends
    view.py             # Report rendering
tests/
pyproject.toml
```
