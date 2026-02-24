# Sanity Log Parser

A tool for clustering log files using a two-stage workflow: logic-based grouping followed by semantic AI merging. It compresses large log files into manageable groups of similar events.

## Requirements

- Python 3.8+
- Optional: `sentence-transformers`, `scikit-learn` (required for AI-based semantic clustering)

The tool works without AI dependencies by falling back to logic-only clustering.

## Quickstart

1. Run the parser on your log file and template file:
   ```bash
   python main.py LOG_FILE TEMPLATE_FILE
   ```
   This generates `subutai_results.json` in the current directory.

2. View the results in a pretty report:
   ```bash
   python view_log.py [subutai_results.json]
   ```

## CLI Usage

- `python main.py --help`: Show usage and options.
- `python main.py --no-color ...`: Disable colored output.
- `NO_COLOR=1 python main.py ...`: Also disables colored output.
- `python main.py --config /path/to/config.json ...`: Use a specific embeddings config file.

## Input Formats

### LOG_FILE
A standard text log file. The parser skips empty lines and lines starting with `-`, `=`, `Rule`, or `Severity`.

Parsing note: the current parser only considers lines that contain a counter like `N of M`, and it uses the text after the first 4 whitespace-separated fields as the message.

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

The tool writes results to `subutai_results.json`.

### Schema Overview
- `rule_id`: The ID from the template file or a generated hash.
- `representative_pattern`: A sample log line representing the group.
- `total_count`: Number of logs in this group.
- `original_logs`: List of all raw log lines belonging to this group.
- `merged_variants_count`: (AI only) Number of logic groups merged into this super group.

## AI Clustering

If `sentence-transformers` and `scikit-learn` are installed, the tool performs a second stage of clustering to semantically merge similar groups. It uses cosine similarity on embeddings generated from log templates and variables.

### Local Model Usage

The AIClusterer uses all-MiniLM-L6-v2 by default. This model downloads and caches automatically via sentence-transformers.

To use a local model instead, point model_path at a directory with your model files.

1. Place your model files in a directory. The my_local_model/ folder is git-ignored, so it's a good spot for these files.
2. Since there's no CLI flag for the model path yet, you'll need to edit main.py to pass the path to AIClusterer.

Update this line in main.py:
```python
ai_clusterer = AIClusterer(console=console)
```
To:
```python
ai_clusterer = AIClusterer(model_path="my_local_model/", console=console)
```

### OpenAI-Compatible Embeddings

You can use an OpenAI-compatible API for embeddings by creating a `config.json` file in your current working directory. The tool automatically reads this file if it exists.

#### How config is loaded at runtime

1. `main.py` creates `AIClusterer`.
2. `AIClusterer` calls `load_embeddings_config(config_path=...)`.
3. By default this is `./config.json`, but you can override it with `--config`.
4. If backend is `openai_compatible`, embeddings are requested from `{base_url}/embeddings`.
5. If config is missing or invalid, backend falls back to `local`.

Use `--config` to choose a different config file path.

Example `config.json`:
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
- `embeddings_backend`: Set to `openai_compatible` to use a remote API, or `local` (default) for local models.
- `openai_compatible.base_url`: The base URL of the OpenAI-compatible API.
- `openai_compatible.model`: The model name to use for embeddings.
- `openai_compatible.api_key`: Your API key. If omitted, the tool looks for the `OPENAI_API_KEY` environment variable.

#### Loading precedence
- `embeddings_backend`: default `local` -> overridden by `config.json`.
- `openai_compatible.api_key`: `config.json` value first, otherwise `OPENAI_API_KEY` environment variable.

#### Run examples (how to pass config through program)

If `config.json` is next to your input files:
```bash
cd /path/to/workdir
python /path/to/sanity_log_parser/main.py LOG_FILE TEMPLATE_FILE
```

If your config is in another location, pass it explicitly:
```bash
python /path/to/sanity_log_parser/main.py --config /path/to/my-config.json LOG_FILE TEMPLATE_FILE
```

#### Fallback and warnings
- Missing `config.json`: uses local embeddings backend.
- Invalid `embeddings_backend`: warning, then fallback to local backend.
- `embeddings_backend=openai_compatible` but missing `base_url`: warning, then fallback to local backend.
- Invalid JSON in `config.json`: warning, then fallback to defaults.

Tip: `--config` is the safest way to avoid current-directory confusion.

## Running Tests

Run the test suite using pytest:
```bash
python -m pytest -q
```

## Notes

- Output is always saved to `subutai_results.json` in the current working directory.
- `view_log.py` provides a colorized summary of the analysis.
- The tool handles variable regions (quoted text) and standalone numbers during normalization.
