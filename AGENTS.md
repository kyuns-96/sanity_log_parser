# Agent Communication Protocol

This file defines how the workstation agent and the air-gapped domain agent should collaborate on this repository.

Use this as the coordination document.

For end-user usage and CLI details, see:

- `README.md`
- `AGENT_TUNING.md`

## 1. Purpose

This project has two different strengths:

- the workstation agent can read and edit code, run tests, commit, and push
- the air-gapped domain agent has PrimeTime report expertise and access to real secure report data

The goal is to combine both without assuming direct network or file transfer between the two environments.

## 2. Roles

| Agent | Environment | Main responsibilities |
|---|---|---|
| Workstation agent | user workstation | code changes, tests, CLI design, docs, git |
| Domain agent | air-gapped secure environment | PrimeTime report interpretation, real-data validation, tuning decisions |

## 3. Canonical Current Workflow

The current recommended runtime path for PrimeTime constraint reports is:

```text
sanity-log-parser gca REPORT.rpt
```

The current recommended tuning path for one rule is:

1. generate `logic.json`
2. prepare `gt.json`
3. run `gca-fit-weights`
4. run `gca-fit-adaptive-eps` if needed
5. run `gca-eval`

Do not treat legacy `cluster LOG_FILE TEMPLATE_FILE` mode as the primary path for GCA tuning.

## 4. Communication Rules

The user relays messages manually between environments.

### Workstation agent → Domain agent

Questions may be long and detailed.

Good content:

- exact command lines
- short config snippets
- one concrete question at a time
- “compare A vs B” requests
- requests for one sample line or one short list of examples

### Domain agent → Workstation agent

Answers must be short.

Preferred answer styles:

- a few words
- a short list
- one sample line
- one config choice
- one “use A, not B” instruction

Avoid returning large pasted logs or large JSON blobs unless absolutely necessary.

## 5. Current Project Facts

These are the important current facts the domain agent should assume.

### PrimeTime report parsing

- the report itself contains the data needed for GCA parsing
- parent lines contain the `rule_id`
- instance lines inherit the active `rule_id`
- severity structure is `severity -> parent rule -> instance lines`
- instance line format is `N of M WAIVED MESSAGE`
- parent line format is `RULE_ID COUNT WAIVED MESSAGE`

### GCA runtime

- `gca` is the main PrimeTime/GCA entrypoint
- `gca` loads rule config and can run weighted AI clustering
- `gca-eval` compares AI output to ground truth
- `gca-distances` shows the base weighted distance behavior for one rule
- `gca-fit-weights` searches base rule settings automatically
- `gca-fit-adaptive-eps` fits an adaptive eps tree

### Legacy mode

- `cluster LOG_FILE TEMPLATE_FILE` still exists
- it is not the preferred path for GCA tuning

## 6. Current Tuning Guidance

For the latest step-by-step tuning procedure, use `AGENT_TUNING.md`.

The most important current rules are:

1. tune the base rule before adaptive eps
2. use `gca-fit-weights` instead of hand-editing many weight candidates
3. use `gca-fit-adaptive-eps` only after the base rule is sensible
4. validate with `gca-eval`

### `DES_0001` special note

For `DES_0001`, changing only a scalar variable `weight` may not change clustering.

Reason:

- `template_weight` may be `0`
- only one variable slot may be active
- runtime renormalizes weights per pair

So for `DES_0001`, the domain agent should pay attention to:

- `levels`
- `match_mode`
- `eps`
- `template_weight`

not only the raw `weight` number.

## 7. Ground Truth Rules

Ground truth format:

```json
{
  "DES_0001": [
    ["DES_0001::logic::000001", "DES_0001::logic::000002"],
    ["DES_0001::logic::000003"]
  ]
}
```

Rules:

- use logic `group_id` values from `logic.json`
- for the tuned rule, every logic group must appear exactly once
- no duplicates
- no omissions

If ground truth is incomplete, fitting results are not trustworthy.

## 8. Air-Gapped Environment Rules

Assume the air-gapped shell is `csh` or `tcsh`.

Use these conventions:

- use `setenv NAME value`
- do not use `export`
- do not use `python -c`
- if Python scripting is needed, write a temporary `.py` file and run it

When the workstation agent sends commands for the air-gapped environment, prefer short, copy-paste-safe command blocks.

## 9. What The Domain Agent Should Return

The domain agent should usually return only:

- one recommended config direction
- one or two important observations
- one sample report line if format clarification is needed
- one short metric summary

Good examples:

```text
Use var0 levels [-3], not [-2].
```

```text
DES_0001: jaccard worse than embedding.
```

```text
Parent line example:
CGR_0018 46 0 Clock 'clk1' is generated from 'clk2'
```

```text
Baseline F1 0.82, tuned F1 0.96.
```

## 10. What The Workstation Agent Should Return

The workstation agent should translate domain guidance into repository changes such as:

- code updates
- config updates
- test updates
- docs updates
- git commits and pushes

The workstation agent can send longer, more explicit summaries back to the user because copy-out is easy on the workstation side.

## 11. When To Ask The Domain Agent

Ask the domain agent when the question depends on real PrimeTime semantics or real secure report examples.

Examples:

- whether two report variants are semantically the same rule family
- which path level is meaningful vs noisy
- whether a specific `DES_0001` split is correct
- whether a strange parent or instance line is a valid PrimeTime form

Do not ask the domain agent for things the workstation agent can determine locally, such as:

- Python syntax
- CLI behavior visible in code
- test failures from local unit tests
- git operations

## 12. When To Update This File

Update `AGENTS.md` when any of these change:

- the primary tuning workflow
- the preferred command sequence
- the role split between agents
- the communication constraints
- the known PrimeTime report structure assumptions

If the detailed tuning procedure changes, update `AGENT_TUNING.md` too.

## 13. Minimal Hand-Off Template

Use this template when sending a task to the domain agent:

```text
Rule: DES_0001
Goal: improve clustering quality
Current result: baseline F1 = 0.82
Question: which path level carries the real signal, [-4], [-3], or [-2]?
Please answer briefly.
```

Use this template when summarizing domain feedback back on the workstation side:

```text
Domain guidance:
- use level [-3]
- avoid jaccard
- adaptive eps still needed

Workstation action:
- update search spec
- rerun base tuning
- refit adaptive eps
```
