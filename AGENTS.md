# AGENTS

This file explains how the workstation agent and the air-gapped agent should work together for this repository.

For CLI usage, see `README.md`.
For rule tuning procedure, see `AGENT_TUNING.md`.

## 1. Purpose

This project is split across two environments:

- the **workstation agent** can read and edit code, run tests, commit, push, and update docs
- the **air-gapped agent** has access to secure PrimeTime reports and domain-specific report knowledge

The user manually relays information between those environments.

## 2. Roles

| Agent | Environment | Main job |
|---|---|---|
| Workstation agent | workstation | code, tests, docs, config, git, CLI workflow |
| Air-gapped agent | secure environment | report interpretation, real-data validation, representative labeling, tuning judgments |

## 3. Canonical Runtime Path

For PrimeTime constraint reports, the main entrypoint is:

```text
sanity-log-parser gca REPORT.rpt
```

Do not treat legacy `cluster LOG_FILE TEMPLATE_FILE` mode as the primary path for PrimeTime GCA work.

## 4. Canonical Tuning Truth

The pipeline has two stages:

1. logic clustering
2. AI clustering

Rule tuning only changes **Stage 2**.

Current tuning order:

1. generate `logic.json`
2. prepare complete `gt.json`
3. measure baseline with `gca` + `gca-eval`
4. try `gca-fit-adaptive-eps` first
5. if direct adaptive fitting is still poor, run `gca-fit-weights`
6. then re-run `gca-fit-adaptive-eps` from the tuned base config
7. validate with full `gca` + `gca-eval`

Important:

- `gca-fit-weights` tunes the **base rule only**
- `gca-fit-adaptive-eps` is the **final GT-fitting step**
- if weights are changed, adaptive eps must be fit again afterward

## 5. Current Project Facts To Preserve

### PrimeTime parsing

- the report itself contains the rule structure needed for GCA parsing
- hierarchy is `severity -> parent rule -> instance lines`
- instance lines inherit the active `rule_id`
- parent line examples include both full and count-only forms
- count-only parent lines such as `DES_0004                12206` are supported

### GCA commands

- `gca` runs PrimeTime parsing, logic clustering, and optional AI clustering
- `gca-eval` compares AI output against ground truth
- `gca-distances` shows rule-level base distance behavior
- `gca-fit-weights` searches a better base rule
- `gca-fit-adaptive-eps` fits an adaptive eps tree for one rule
- `export-labeling` exports per-group JSON files for labeling

### Ground truth rules

- ground truth uses `logic.json` `group_id` values
- for the tuned rule, every logic group must appear exactly once
- no duplicates
- no omissions

If GT is incomplete, tuning results are not trustworthy.

## 6. Air-Gapped Environment Rules

Assume the air-gapped shell is `csh` or `tcsh`.

Use these rules:

- use `setenv NAME value`
- do not use `export`
- do not use `python -c`
- if Python scripting is needed, write a temporary `.py` file and run `python3 file.py`

When sending commands to the air-gapped agent, prefer short, copy-paste-safe blocks.

## 7. Communication Rules

### Workstation agent → air-gapped agent

Good messages contain:

- exact command lines
- short config snippets
- one concrete question at a time
- compare-A-vs-B requests
- a request for one sample line or one short list of examples

Long questions are fine.

### Air-gapped agent → workstation agent

Preferred answer style:

- short
- concrete
- one recommendation at a time

Good outputs:

- one config direction
- one or two important observations
- one sample report line
- one short metric summary

Avoid large pasted logs or large JSON blobs unless absolutely necessary.

## 8. When To Ask The Air-Gapped Agent

Ask when the answer depends on real PrimeTime semantics or secure real-report examples.

Examples:

- whether two report variants are really the same rule family
- which path level carries real signal vs noise
- whether a suspicious real-report split or merge is correct
- whether a strange parent or instance line is a valid PrimeTime form
- whether a representative subset really covers the real failure pattern

Do not ask the air-gapped agent for things the workstation can determine locally, such as:

- Python syntax
- CLI behavior visible in code
- unit test failures
- git operations

## 9. Current DES_0004 Status

DES_0004 is a checked-in worked example, not a universal template.

Facts:

- `labeling/DES_0004/` contains a synthetic/example workflow
- `labeling/DES_0004/build_from_markdown.py` can generate a synthetic DES_0004 report and GT artifacts
- the shipped rule config currently contains a compact DES_0004 adaptive rule
- the checked-in DES_0004 F1=1.0 result is a **checked-in example result on its corresponding GT**, not proof that every real report will cluster perfectly

For other rules, copy the **process**, not the literal DES_0004 values.

## 10. What The Workstation Agent Should Return

The workstation agent should convert air-gapped guidance into repository changes such as:

- code updates
- config updates
- tests
- docs
- commits and pushes

The workstation side can also provide longer summaries back to the user.

## 11. Minimal Hand-Off Template

Use this format when asking the air-gapped agent for help:

```text
Rule: RULE_XXXX
Goal: improve real-report clustering quality
Current result: baseline F1 = 0.82
Question: which path level or family split is the real signal here?
Please answer briefly.
```

Use this format when summarizing air-gapped feedback back on the workstation side:

```text
Air-gapped guidance:
- use level [-3]
- avoid the noisy family merge
- re-fit adaptive eps after base tuning

Workstation action:
- update search direction
- rerun base tuning
- rerun adaptive eps
```

## 12. When To Update This File

Update `AGENTS.md` when any of these change:

- role split between workstation and air-gapped work
- canonical tuning order
- communication constraints
- known PrimeTime parsing assumptions
- the meaning of the current DES_0004 example status
