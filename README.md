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

## Running Tests

Run the test suite using pytest:
```bash
python -m pytest -q
```

## Notes

- Output is always saved to `subutai_results.json` in the current working directory.
- `view_log.py` provides a colorized summary of the analysis.
- The tool handles variable regions (quoted text) and standalone numbers during normalization.
