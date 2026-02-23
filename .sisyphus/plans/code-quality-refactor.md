# Code Quality Refactoring — Sanity Log Parser

## TL;DR

> **Quick Summary**: Comprehensive code quality overhaul of log_parser.py (523 lines) and view_log.py — type hints, dead code removal, aggressive renaming, English comments, error handling, and modular file splitting.
> 
> **Deliverables**:
> - 6 modular files: `template_manager.py`, `parser.py`, `logic_clusterer.py`, `ai_clusterer.py`, `ai_weights.py`, `main.py`
> - Improved `view_log.py`
> - pytest infrastructure with regression tests
> - All Korean comments translated to English
> - Full type annotations on all public/private functions
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 4 waves
> **Critical Path**: T1 (pytest setup) → T2 (dead code) → T3 (rename) → T4 (type hints) → T5 (english) → T6 (error handling) → T7-T10 (file split) → F1-F4 (verification)

---

## Context

### Original Request
Comprehensive code quality refactoring: type hints everywhere, dead code removal, aggressive renaming, English docstrings/comments, error handling improvements, file splitting to stay under 300 lines.

### Interview Summary
**Key Discussions**:
- **Module structure**: One class per file (user chose this over fewer-files option)
- **Naming scope**: Aggressive — rename classes, methods, variables, everything
- **Testing**: Basic pytest to verify behavior (not TDD, but tests-before-refactor for safety)
- **Comments**: Translate all Korean to English
- **Scope**: log_parser.py + view_log.py only, no config changes, no behavior changes

**Research Findings**:
- `SubutaiParser` accesses `RuleTemplateManager._get_pure_template()` (private cross-boundary access)
- `AI_AVAILABLE` global is mutated inside `AIClusterer.__init__`
- Two confirmed dead methods: `get_stem_signature`, `_apply_variable_value_weights`
- Unused imports: `time`, `numpy as np`
- Bare `except:` on line 202 swallows all exceptions
- `var_pattern = re.compile(r"'(.*?)'")` duplicated in 3 places (including inside a loop)
- 13+ LSP diagnostics from missing type hints and dynamic patterns
- `test_run.log` has 1 sample line — usable for basic regression test

### Metis Review
**Identified Gaps** (addressed):
- **Missing template file**: No template file in repo — will use synthetic test data for unit tests
- **AIClusterer will exceed 300 lines**: Extract weight/tail methods into `ai_weights.py`
- **Private cross-access**: Make `_get_pure_template` public before file split
- **AI_AVAILABLE global mutation**: Convert to instance-level `self.ai_available` attribute
- **Korean in user-facing print strings**: Translate alongside comments (acceptable behavior change for i18n)
- **Dead code may be WIP**: Confirmed dead via grep — removing with comment noting removal
- **Circular import risk**: Define strict import direction in plan

---

## Work Objectives

### Core Objective
Refactor the sanity log parser for maintainability without changing functional behavior — splitting the monolithic file, adding types, cleaning naming, and establishing test coverage.

### Concrete Deliverables
- `template_manager.py` — renamed RuleTemplateManager class
- `parser.py` — renamed SubutaiParser class
- `logic_clusterer.py` — renamed LogicClusterer class
- `ai_clusterer.py` — renamed AIClusterer class (core logic)
- `ai_weights.py` — extracted weight/tail helper methods
- `main.py` — CLI entry point
- `view_log.py` — improved viewer
- `conftest.py` + test files — pytest infrastructure
- All files under 300 lines, fully typed, English-only

### Definition of Done
- [ ] `python main.py test_run.log <template_file>` produces identical JSON output to current `log_parser.py`
- [ ] `grep -rn '[가-힣]' *.py` returns 0 matches
- [ ] `wc -l < <file>` ≤ 300 for every .py file
- [ ] All pytest tests pass
- [ ] Zero bare `except:` in codebase

### Must Have
- Regression tests capturing current behavior BEFORE any code changes
- Type hints on ALL function signatures (params + return)
- All Korean → English translation
- Files split, each ≤ 300 lines
- Bare except replaced with specific exception handling

### Must NOT Have (Guardrails)
- NO behavior changes to JSON output (structure, field names, ordering, values)
- NO argparse, pyproject.toml, or packaging infrastructure
- NO `logging` module retrofitting (only fix bare except location)
- NO dataclass conversions for existing dict return types
- NO `defaultdict` pattern replacements
- NO changes to `rule_clustering_config.json`
- NO over-abstraction — keep existing architecture patterns
- NO strict mypy compliance chase (type hints on signatures only, not full strict mode)

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO — will be created in Task 1
- **Automated tests**: YES (tests-before, then refactor)
- **Framework**: pytest
- **Strategy**: Create unit-level regression tests with synthetic data first, then refactor

### QA Policy
Every task MUST include agent-executed QA scenarios.
Evidence saved to `.sisyphus/evidence/task-{N}-{scenario-slug}.{ext}`.

- **Module imports**: Use Bash (`python -c "from module import Class"`)
- **Regression**: Use Bash (`pytest` + `diff` against baseline)
- **Korean removal**: Use Bash (`grep -rn '[가-힣]' *.py`)
- **File size**: Use Bash (`wc -l`)

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation — sequential, must complete before anything else):
├── Task 1: Set up pytest + create regression tests [deep]

Wave 2 (Code cleaning — sequential within wave, depends on T1):
├── Task 2: Remove dead code + unused imports [quick]
├── Task 3: Aggressive symbol renaming [unspecified-high]
├── Task 4: Add type hints to all functions [unspecified-high]
├── Task 5: Translate Korean → English [quick]
├── Task 6: Fix error handling + AI_AVAILABLE pattern [unspecified-high]

Wave 3 (File splitting — parallel within wave, depends on T2-T6):
├── Task 7: Extract template_manager.py + parser.py [quick]
├── Task 8: Extract logic_clusterer.py [quick]
├── Task 9: Extract ai_clusterer.py + ai_weights.py [unspecified-high]
├── Task 10: Create main.py + update view_log.py [quick]

Wave FINAL (Verification — parallel, depends on ALL):
├── Task F1: Plan compliance audit [oracle]
├── Task F2: Code quality review [unspecified-high]
├── Task F3: Regression QA [unspecified-high]
├── Task F4: Scope fidelity check [deep]

Critical Path: T1 → T2 → T3 → T4 → T5 → T6 → T7/T8/T9/T10 → F1-F4
```

### Dependency Matrix

| Task | Depends On | Blocks |
|------|-----------|--------|
| T1 | — | T2-T10 |
| T2 | T1 | T3 |
| T3 | T2 | T4 |
| T4 | T3 | T5 |
| T5 | T4 | T6 |
| T6 | T5 | T7, T8, T9, T10 |
| T7 | T6 | F1-F4 |
| T8 | T6 | F1-F4 |
| T9 | T6 | F1-F4 |
| T10 | T6 | F1-F4 |
| F1-F4 | T7-T10 | — |

### Agent Dispatch Summary

- **Wave 1**: 1 task — T1 → `deep`
- **Wave 2**: 5 tasks — T2 → `quick`, T3 → `unspecified-high`, T4 → `unspecified-high`, T5 → `quick`, T6 → `unspecified-high`
- **Wave 3**: 4 tasks (parallel) — T7 → `quick`, T8 → `quick`, T9 → `unspecified-high`, T10 → `quick`
- **FINAL**: 4 tasks (parallel) — F1 → oracle, F2-F3 → `unspecified-high`, F4 → `deep`

---

## TODOs

- [ ] 1. Set up pytest infrastructure + regression baseline tests

  **What to do**:
  - Install pytest (`pip install pytest`)
  - Create `tests/` directory with `__init__.py`
  - Create `tests/conftest.py` with shared fixtures (synthetic log lines, synthetic template data)
  - Create `tests/test_template_manager.py` — test `_get_pure_template()` and `get_rule_id()` with known inputs/outputs
  - Create `tests/test_parser.py` — test `parse_line()` with sample lines matching `\b\d+\s+of\s+\d+\b` pattern
  - Create `tests/test_logic_clusterer.py` — test `get_logic_signature()` and `run()` with synthetic parsed logs
  - Create `tests/test_ai_clusterer.py` — test `extract_variable_tail()`, `_apply_variable_position_weights()`, `get_rule_config()` (methods that DON'T require ML models)
  - Create baseline snapshot: run `python log_parser.py test_run.log /dev/null` and capture output structure for regression comparison
  - All tests must pass before proceeding

  **Must NOT do**:
  - Do NOT modify any source code in this task
  - Do NOT require ML model loading for tests (test non-AI methods only)
  - Do NOT add pyproject.toml or complex packaging

  **Recommended Agent Profile**:
  - **Category**: `deep`
    - Reason: Requires understanding all class interfaces to write meaningful tests with synthetic data
  - **Skills**: []
  - **Skills Evaluated but Omitted**:
    - `playwright`: No browser work needed

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 1 (solo)
  - **Blocks**: T2, T3, T4, T5, T6, T7, T8, T9, T10
  - **Blocked By**: None (can start immediately)

  **References**:

  **Pattern References**:
  - `log_parser.py:23-54` — RuleTemplateManager class (all methods to test)
  - `log_parser.py:58-141` — SubutaiParser class (parse_line + extract_variable_stems)
  - `log_parser.py:145-191` — LogicClusterer class (get_logic_signature + run)
  - `log_parser.py:196-335` — AIClusterer non-ML methods (extract_variable_tail, weight methods)
  - `log_parser.py:112` — The `\b\d+\s+of\s+\d+\b` filter pattern that parse_line requires

  **Test References**:
  - `test_run.log` — Single sample log line: `4 of 4 Signal 'top/u_cpu/decode/pipe_4' float Signal 'top/u_cpu/decode/pipe_5' float 'top/u_cpu/decode/pipe_5' signal conflicted`

  **WHY Each Reference Matters**:
  - `log_parser.py:32-37` — `_get_pure_template` is the core normalization logic — tests must verify variable masking and number masking work correctly
  - `log_parser.py:64-106` — `extract_variable_stems` has complex delimiter priority logic — test with examples from docstring
  - `log_parser.py:112-113` — This regex filter is the ONLY entry gate for parse_line — lines without `N of N` are rejected
  - `log_parser.py:244-298` — `extract_variable_tail` has detailed docstring with worked examples — use those as test cases

  **Acceptance Criteria**:
  - [ ] `tests/` directory exists with `__init__.py`
  - [ ] `tests/conftest.py` exists with fixtures
  - [ ] `pytest tests/ -v` → all tests PASS
  - [ ] Minimum 15 test cases across all test files
  - [ ] Baseline output captured from running parser with test_run.log

  **QA Scenarios:**

  ```
  Scenario: Happy path — all tests pass
    Tool: Bash
    Preconditions: pytest installed, tests/ directory created
    Steps:
      1. Run `pytest tests/ -v --tb=short`
      2. Check exit code is 0
      3. Count total test cases in output
    Expected Result: Exit code 0, ≥15 tests collected, 0 failures
    Failure Indicators: Non-zero exit code, any FAILED lines in output
    Evidence: .sisyphus/evidence/task-1-pytest-pass.txt

  Scenario: Baseline snapshot captured
    Tool: Bash
    Preconditions: log_parser.py unmodified
    Steps:
      1. Run `python log_parser.py test_run.log /dev/null 2>&1`
      2. Capture stdout output
      3. Verify output contains expected structure (rule_id, pattern, count)
    Expected Result: Parser runs without error, output structure captured
    Failure Indicators: Python traceback, empty output
    Evidence: .sisyphus/evidence/task-1-baseline-snapshot.txt
  ```

  **Commit**: YES
  - Message: `test: add pytest infrastructure and regression baseline tests`
  - Files: `tests/__init__.py`, `tests/conftest.py`, `tests/test_*.py`
  - Pre-commit: `pytest tests/ -v`

- [ ] 2. Remove dead code and unused imports

  **What to do**:
  - Remove `LogicClusterer.get_stem_signature()` method (lines 151-170) — confirmed never called
  - Remove `AIClusterer._apply_variable_value_weights()` method (lines 318-334) — confirmed never called
  - Remove `import time` (line 6) — never used
  - Remove `import numpy as np` (line 15) — never referenced directly (numpy is a transitive dependency of sentence_transformers, not needed as direct import)
  - Remove `variable_stems` and `stems_tuple` handling from `SubutaiParser.parse_line()` (lines 122-129) — only used by dead `get_stem_signature`
  - Remove `stem_tuple` field from parse_line return dict (line 137) — dead data
  - Verify all existing tests still pass after removal

  **Must NOT do**:
  - Do NOT remove any code that is actually called (verify with grep first)
  - Do NOT remove `variable_stems` / `extract_variable_stems` — only remove their usage in `parse_line` if they feed exclusively into dead code paths. Actually, check if `extract_variable_stems` itself is called anywhere else before removing.
  - Do NOT change any method signatures of live code

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward deletions of confirmed dead code
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (sequential start)
  - **Blocks**: T3
  - **Blocked By**: T1

  **References**:

  **Pattern References**:
  - `log_parser.py:151-170` — `get_stem_signature` method to remove (confirmed dead: only defined, never called)
  - `log_parser.py:318-334` — `_apply_variable_value_weights` method to remove (confirmed dead: only defined, never called)
  - `log_parser.py:6` — `import time` to remove
  - `log_parser.py:15` — `import numpy as np` to remove
  - `log_parser.py:122-129` — `variable_stems` / `stems_tuple` creation in parse_line (feeds dead `get_stem_signature`)
  - `log_parser.py:137` — `"variable_stems": stems_tuple` in return dict (dead field)

  **WHY Each Reference Matters**:
  - Lines 151-170: `get_stem_signature` was part of a stem-based grouping approach that was replaced by the current variable-based approach. It's dead WIP code.
  - Lines 318-334: `_apply_variable_value_weights` was a feature that was removed per commit `c7e695f` ("REFACTOR: Remove variable_value_weights") but the method body was left behind.
  - Lines 122-129, 137: These create `stems_tuple` data that only `get_stem_signature` would consume. With that method gone, this data creation is pure waste.

  **Acceptance Criteria**:
  - [ ] `grep -n 'get_stem_signature' log_parser.py` → 0 matches
  - [ ] `grep -n '_apply_variable_value_weights' log_parser.py` → 0 matches
  - [ ] `grep -n 'import time' log_parser.py` → 0 matches
  - [ ] `grep -n 'import numpy' log_parser.py` → 0 matches
  - [ ] `pytest tests/ -v` → all tests still PASS

  **QA Scenarios:**

  ```
  Scenario: Dead code fully removed
    Tool: Bash
    Preconditions: Task 1 tests in place
    Steps:
      1. Run `grep -c 'get_stem_signature\|_apply_variable_value_weights\|import time\|import numpy' log_parser.py`
      2. Assert count is 0
      3. Run `pytest tests/ -v --tb=short`
      4. Assert all tests pass
    Expected Result: 0 grep matches, all tests pass
    Failure Indicators: Non-zero grep count, any test failures
    Evidence: .sisyphus/evidence/task-2-dead-code-removed.txt

  Scenario: No remaining references to removed code
    Tool: Bash
    Preconditions: Dead code removed
    Steps:
      1. Run `grep -rn 'stems_tuple\|variable_stems' log_parser.py`
      2. Verify only `extract_variable_stems` method definition remains (if kept) or zero matches
    Expected Result: Zero references to removed data fields
    Failure Indicators: References to stems_tuple in parse_line return dict
    Evidence: .sisyphus/evidence/task-2-no-orphan-refs.txt
  ```

  **Commit**: YES
  - Message: `refactor: remove dead code and unused imports`
  - Files: `log_parser.py`
  - Pre-commit: `pytest tests/ -v`

- [ ] 3. Aggressive symbol renaming

  **What to do**:
  - Rename cryptic variables across both files (log_parser.py + view_log.py):
    - `tm` → `template_manager` (in SubutaiParser.__init__ and main)
    - `p` → `parsed_log` (in LogicClusterer.run loop)
    - `g` → `logic_group` (in AIClusterer.run and main loops)
    - `res` → `result` (in main output loop)
    - `temp` → `normalized_template` (in _get_pure_template)
    - `sigs` → `signatures` (in get_logic_signature)
    - `x` in lambdas → `group` (e.g., `key=lambda group: group['count']`)
  - Rename `_get_pure_template` → `get_pure_template` (make public — called cross-class by SubutaiParser)
  - Rename duplicated `var_pattern` inside AIClusterer.run loop (line 379) to `variable_regex` and move outside loop
  - Consider renaming classes if significantly better names exist (user approved aggressive renaming)
  - Update ALL references after each rename
  - Run tests after completion

  **Must NOT do**:
  - Do NOT rename JSON output field names (rule_id, pattern, template, count, etc.) — these are output contract
  - Do NOT rename config file keys (they match rule_clustering_config.json)
  - Do NOT change variable names that appear in string formatting or regex patterns

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Many renames across multiple locations, need careful tracking to avoid missed references
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after T2)
  - **Blocks**: T4
  - **Blocked By**: T2

  **References**:

  **Pattern References**:
  - `log_parser.py:59-61` — SubutaiParser.__init__ where `self.tm` is set
  - `log_parser.py:131` — `self.tm._get_pure_template(line)` — private cross-class access to rename
  - `log_parser.py:174` — `for p in parsed_logs` in LogicClusterer.run
  - `log_parser.py:379` — `var_pattern = re.compile(...)` inside a for loop (inefficient + cryptic name)
  - `log_parser.py:190,446` — `key=lambda x: x['count']` sort lambdas
  - `view_log.py:46` — `for i, group in enumerate(data)` — already well-named in view_log

  **WHY Each Reference Matters**:
  - Line 131: This is the most important rename — `_get_pure_template` → `get_pure_template` fixes a private-cross-class violation that will break conventions when files are split
  - Line 379: `var_pattern` is compiled inside a `for` loop on every iteration — should be moved out as a module-level constant or class attribute

  **Acceptance Criteria**:
  - [ ] `grep -n 'self\.tm ' log_parser.py` → 0 matches (renamed to template_manager)
  - [ ] `grep -n '_get_pure_template' log_parser.py` → 0 matches (now public: get_pure_template)
  - [ ] `pytest tests/ -v` → all tests PASS (update tests to match new names if needed)

  **QA Scenarios:**

  ```
  Scenario: All renames applied correctly
    Tool: Bash
    Preconditions: Dead code removed (T2 complete)
    Steps:
      1. Run `grep -c 'self\.tm[^a-z]' log_parser.py` — assert 0 (tm renamed)
      2. Run `grep -c '_get_pure_template' log_parser.py` — assert 0 (now public)
      3. Run `pytest tests/ -v --tb=short`
      4. Assert all tests pass
    Expected Result: Zero old names remaining, all tests pass
    Failure Indicators: Old variable names still present, test failures
    Evidence: .sisyphus/evidence/task-3-renames-applied.txt

  Scenario: var_pattern moved out of loop
    Tool: Bash
    Preconditions: Renaming complete
    Steps:
      1. Search for `re.compile` calls inside the `run` method of AIClusterer
      2. Verify no regex compilation inside for loops
    Expected Result: All regex patterns compiled at class/module level, not in loops
    Failure Indicators: re.compile found inside indented loop body
    Evidence: .sisyphus/evidence/task-3-no-loop-compile.txt
  ```

  **Commit**: YES
  - Message: `refactor: rename symbols for clarity across codebase`
  - Files: `log_parser.py`, `view_log.py`, `tests/test_*.py`
  - Pre-commit: `pytest tests/ -v`

- [ ] 4. Add type hints to all functions

  **What to do**:
  - Add parameter type hints and return type annotations to every function/method in log_parser.py:
    - `RuleTemplateManager.__init__(self, template_file: str | None) -> None`
    - `_get_pure_template(self, text: str) -> str` (now `get_pure_template`)
    - `_load_templates(self, file_path: str) -> None`
    - `get_rule_id(self, log_template: str) -> str`
    - `SubutaiParser.__init__(self, template_manager: RuleTemplateManager) -> None`
    - `extract_variable_stems(self, variable: str) -> list[str]`
    - `parse_line(self, line: str) -> dict[str, Any] | None`
    - `LogicClusterer.get_logic_signature(self, var_tuple: tuple[str, ...]) -> str`
    - `LogicClusterer.run(self, parsed_logs: list[dict[str, Any]]) -> list[dict[str, Any]]`
    - `AIClusterer.__init__(self, model_path: str = ..., config_file: str = ...) -> None`
    - All AIClusterer methods with appropriate types
  - Add type hints to view_log.py:
    - `print_pretty_report(json_file_path: str) -> None`
  - Add `from __future__ import annotations` at top of files for modern union syntax
  - Add `from typing import Any` where needed for dict value types
  - DO NOT chase full mypy strict compliance — focus on function signatures only

  **Must NOT do**:
  - Do NOT add TypedDict or dataclass for existing dict return types
  - Do NOT add inline variable type annotations everywhere — only function signatures
  - Do NOT make the code unreadable with overly complex generic types

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Many methods need typed, must understand data flow to pick correct types
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after T3)
  - **Blocks**: T5
  - **Blocked By**: T3

  **References**:

  **Pattern References**:
  - `log_parser.py:23-53` — RuleTemplateManager method signatures to annotate
  - `log_parser.py:58-140` — SubutaiParser method signatures to annotate
  - `log_parser.py:145-191` — LogicClusterer method signatures to annotate
  - `log_parser.py:196-447` — AIClusterer method signatures to annotate (most complex)
  - `log_parser.py:134-140` — parse_line return dict shape (rule_id, variables, template, raw_log)
  - `view_log.py:19` — print_pretty_report signature

  **WHY Each Reference Matters**:
  - Lines 134-140: parse_line's return dict flows through LogicClusterer.run and AIClusterer.run — the type must be consistent across all three
  - Lines 244-298: extract_variable_tail has complex optional parameters (tail_weights, variable_position_weights) — types clarify the API

  **Acceptance Criteria**:
  - [ ] Every function/method has parameter types and return type
  - [ ] `from __future__ import annotations` present in both .py files
  - [ ] `pytest tests/ -v` → all tests PASS

  **QA Scenarios:**

  ```
  Scenario: All functions have type annotations
    Tool: Bash
    Preconditions: T3 complete
    Steps:
      1. Run `python -c "import ast; tree=ast.parse(open('log_parser.py').read()); funcs=[n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]; missing=[f.name for f in funcs if f.returns is None and f.name != '__init__']; print(f'Missing return types: {missing}')" `
      2. Assert missing list is empty (or only __init__)
      3. Run `pytest tests/ -v --tb=short`
    Expected Result: All non-__init__ functions have return type annotations, tests pass
    Failure Indicators: Functions without return annotations listed
    Evidence: .sisyphus/evidence/task-4-type-hints.txt
  ```

  **Commit**: YES
  - Message: `refactor: add type hints to all function signatures`
  - Files: `log_parser.py`, `view_log.py`
  - Pre-commit: `pytest tests/ -v`

- [ ] 5. Translate all Korean comments and strings to English

  **What to do**:
  - Find all Korean text: `grep -rn '[가-힣]' log_parser.py view_log.py`
  - Translate each Korean comment to English equivalent
  - Translate Korean docstrings to English
  - Translate Korean user-facing print strings (error messages in view_log.py lines 21, 30; status messages throughout)
  - Keep emoji in print strings (📂, 🤖, ✅, ⚠️, 💾) — they're visual aids, not Korean text
  - Verify zero Korean characters remain after

  **Must NOT do**:
  - Do NOT change the meaning of any comment — translate faithfully
  - Do NOT add new comments beyond what exists in Korean
  - Do NOT remove the emoji from print statements

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Straightforward translation task, no logic changes
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after T4)
  - **Blocks**: T6
  - **Blocked By**: T4

  **References**:

  **Pattern References**:
  - `log_parser.py:33-36` — Korean comments in _get_pure_template
  - `log_parser.py:175-176` — Korean comments in LogicClusterer.run
  - `log_parser.py:185-188` — Korean comments in results building
  - `log_parser.py:205-210` — Korean comments in AIClusterer.__init__
  - `log_parser.py:248-272` — Korean docstring in extract_variable_tail (25 lines)
  - `log_parser.py:278-298` — Korean comments in tail extraction logic
  - `log_parser.py:300-316` — Korean docstring in _apply_variable_position_weights
  - `log_parser.py:341,346,375-376,425,460,472,479,502,507,523` — scattered Korean comments
  - `view_log.py:21,30` — Korean error messages in print statements

  **WHY Each Reference Matters**:
  - Lines 248-272: This is the largest Korean docstring — must be carefully translated to preserve the technical meaning of VLSI variable tail extraction
  - Lines 21, 30 in view_log.py: These are runtime output — translating changes what users see, but the user approved this

  **Acceptance Criteria**:
  - [ ] `grep -rn '[가-힣]' log_parser.py view_log.py | wc -l` → 0
  - [ ] `pytest tests/ -v` → all tests PASS

  **QA Scenarios:**

  ```
  Scenario: Zero Korean characters remaining
    Tool: Bash
    Preconditions: T4 complete
    Steps:
      1. Run `grep -rn '[가-힣]' log_parser.py view_log.py`
      2. Assert output is empty
      3. Run `pytest tests/ -v --tb=short`
    Expected Result: No Korean characters found, all tests pass
    Failure Indicators: Any grep output, test failures
    Evidence: .sisyphus/evidence/task-5-korean-removed.txt
  ```

  **Commit**: YES
  - Message: `refactor: translate all Korean comments and strings to English`
  - Files: `log_parser.py`, `view_log.py`
  - Pre-commit: `pytest tests/ -v`

- [ ] 6. Fix error handling and AI_AVAILABLE pattern

  **What to do**:
  - Replace bare `except:` on line 202 with specific exception:
    ```python
    except (ImportError, OSError, RuntimeError) as exc:
        print(f"⚠️  Failed to load SentenceTransformer model: {exc}")
        self.ai_available = False
    ```
  - Convert `AI_AVAILABLE` global to instance attribute:
    - Remove `global AI_AVAILABLE` from AIClusterer.__init__
    - Add `self.ai_available: bool` attribute set in __init__
    - AIClusterer.run() checks `self.ai_available` instead of global
    - Main block checks `ai_clusterer.ai_available` instead of global `AI_AVAILABLE`
  - Keep the module-level try/except for import (lines 12-18) — this is fine, just don't mutate it
  - Add `self.model: SentenceTransformer | None = None` default in __init__ to prevent AttributeError when AI unavailable
  - Fix the duplicated `var_pattern` creation in AIClusterer.run (line 379) — should already be at class level from T3 rename

  **Must NOT do**:
  - Do NOT add the `logging` module
  - Do NOT change how the import try/except at module level works
  - Do NOT change the behavior when AI is available — only improve the failure path

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Touches initialization flow and conditional logic — needs careful handling
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Wave 2 (after T5)
  - **Blocks**: T7, T8, T9, T10
  - **Blocked By**: T5

  **References**:

  **Pattern References**:
  - `log_parser.py:12-18` — Module-level import try/except (keep as-is)
  - `log_parser.py:196-210` — AIClusterer.__init__ with bare except and global mutation
  - `log_parser.py:337` — AIClusterer.run checks `AI_AVAILABLE` global
  - `log_parser.py:482-488` — Main block checks `AI_AVAILABLE` to decide whether to run AI clustering

  **WHY Each Reference Matters**:
  - Line 202: The bare `except:` catches SystemExit, KeyboardInterrupt — dangerous. Must be narrowed.
  - Line 203: `AI_AVAILABLE = False` mutates a module-level constant from inside a class — anti-pattern that becomes cross-module state when split
  - Lines 337, 482: These are the consumers of AI_AVAILABLE — must update to use instance attribute

  **Acceptance Criteria**:
  - [ ] `grep -n 'except:' log_parser.py` → 0 matches (no bare except)
  - [ ] `grep -n 'global AI_AVAILABLE' log_parser.py` → 0 matches
  - [ ] `grep -n 'self.ai_available' log_parser.py` → matches in __init__ and run
  - [ ] `grep -n 'self.model' log_parser.py` includes `None` default initialization
  - [ ] `pytest tests/ -v` → all tests PASS

  **QA Scenarios:**

  ```
  Scenario: Bare except eliminated, AI_AVAILABLE refactored
    Tool: Bash
    Preconditions: T5 complete
    Steps:
      1. Run `grep -c 'except:' log_parser.py` — assert 0
      2. Run `grep -c 'global AI_AVAILABLE' log_parser.py` — assert 0
      3. Run `grep -c 'self\.ai_available' log_parser.py` — assert ≥2
      4. Run `pytest tests/ -v --tb=short`
    Expected Result: No bare except, no global mutation, instance attribute used, tests pass
    Failure Indicators: Bare except remains, global mutation remains, test failures
    Evidence: .sisyphus/evidence/task-6-error-handling.txt

  Scenario: Model attribute safely initialized
    Tool: Bash
    Preconditions: Error handling refactored
    Steps:
      1. Run `python -c "from log_parser import AIClusterer; a = AIClusterer.__new__(AIClusterer); print(hasattr(a, 'model'))"` — verify model attribute existence handling
      2. Check that __init__ sets self.model = None before attempting load
    Expected Result: No AttributeError risk for self.model
    Failure Indicators: AttributeError when accessing self.model
    Evidence: .sisyphus/evidence/task-6-model-safe-init.txt
  ```

  **Commit**: YES
  - Message: `refactor: fix bare except and convert AI_AVAILABLE to instance attribute`
  - Files: `log_parser.py`
  - Pre-commit: `pytest tests/ -v`

- [ ] 7. Extract template_manager.py and parser.py from log_parser.py

  **What to do**:
  - Create `template_manager.py` containing the (renamed) `RuleTemplateManager` class:
    - Move class definition with all methods: `__init__`, `get_pure_template` (formerly `_get_pure_template`), `_load_templates`, `get_rule_id`
    - Include necessary imports at top: `os`, `re`, `hashlib`
    - Move the `var_pattern = re.compile(r"'(.*?)'")` to class attribute
    - File must be ≤ 300 lines
  - Create `parser.py` containing the (renamed) `SubutaiParser` class:
    - Move class definition with all methods: `__init__`, `extract_variable_stems`, `parse_line`
    - Import `RuleTemplateManager` from `template_manager` module
    - Include necessary imports: `re`
    - The `self.var_pattern` stays as class attribute here too (it's independently needed)
    - Remove `variable_stems` / `stems_tuple` references from parse_line if not already removed in T2
    - File must be ≤ 300 lines
  - Remove these classes from `log_parser.py`
  - Verify strict import direction: `template_manager` ← `parser` (parser imports from template_manager, never reverse)
  - Run tests after — update test imports if needed

  **Must NOT do**:
  - Do NOT change any method logic — only move code between files
  - Do NOT add `__all__` exports or barrel patterns
  - Do NOT create an `__init__.py` in root — these are standalone scripts

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Mechanical file extraction — cut and paste with import fixups
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T8, T9, T10)
  - **Blocks**: F1, F2, F3, F4
  - **Blocked By**: T6

  **References**:

  **Pattern References**:
  - `log_parser.py:23-54` — RuleTemplateManager class (exact code to extract)
  - `log_parser.py:58-141` — SubutaiParser class (exact code to extract)
  - `log_parser.py:131` — `self.tm._get_pure_template(line)` — cross-class call (now public after T3)
  - `log_parser.py:1-7` — imports needed: `os`, `re`, `hashlib` for template_manager; `re` for parser

  **WHY Each Reference Matters**:
  - Lines 23-54: This is the EXACT code block to extract into template_manager.py — copy verbatim (with T2-T6 changes applied)
  - Lines 58-141: This is the EXACT code block to extract into parser.py
  - Line 131: After T3 rename, this is `self.template_manager.get_pure_template(line)` — verify import resolves

  **Acceptance Criteria**:
  - [ ] `template_manager.py` exists and is ≤ 300 lines
  - [ ] `parser.py` exists and is ≤ 300 lines
  - [ ] `python -c "from template_manager import RuleTemplateManager; print('OK')"` → OK
  - [ ] `python -c "from parser import SubutaiParser; print('OK')"` → OK
  - [ ] `pytest tests/ -v` → all tests PASS (update imports in tests)

  **QA Scenarios:**

  ```
  Scenario: Happy path — modules import and classes are accessible
    Tool: Bash
    Preconditions: T6 complete, classes extracted
    Steps:
      1. Run `python -c "from template_manager import RuleTemplateManager; print('OK')"` — assert prints OK
      2. Run `python -c "from parser import SubutaiParser; from template_manager import RuleTemplateManager; tm = RuleTemplateManager(None); p = SubutaiParser(tm); print('OK')"` — assert prints OK
      3. Run `wc -l template_manager.py parser.py` — assert both ≤ 300
      4. Run `pytest tests/ -v --tb=short` — assert all pass
    Expected Result: Both modules import cleanly, both ≤ 300 lines, all tests pass
    Failure Indicators: ImportError, ModuleNotFoundError, file > 300 lines, test failures
    Evidence: .sisyphus/evidence/task-7-module-imports.txt

  Scenario: No circular imports between template_manager and parser
    Tool: Bash
    Preconditions: Both files created
    Steps:
      1. Run `python -c "import template_manager; import parser; print('No circular imports')"` — assert no error
      2. Run `grep -n 'from parser' template_manager.py` — assert 0 matches (template_manager must not import from parser)
    Expected Result: Clean imports, no circular dependency
    Failure Indicators: ImportError mentioning circular import, grep matches
    Evidence: .sisyphus/evidence/task-7-no-circular.txt
  ```

  **Commit**: NO (groups with T8, T9, T10)

- [ ] 8. Extract logic_clusterer.py from log_parser.py

  **What to do**:
  - Create `logic_clusterer.py` containing the (renamed) `LogicClusterer` class:
    - Move class definition with methods: `get_logic_signature`, `run`
    - `get_stem_signature` should already be removed by T2 (dead code). If still present, do NOT include it.
    - Include necessary imports: `re`, `from collections import defaultdict`
    - No imports from other project modules needed — LogicClusterer is self-contained
    - File must be ≤ 300 lines
  - Remove this class from `log_parser.py`
  - Run tests after — update test imports if needed

  **Must NOT do**:
  - Do NOT change any method logic — only move code
  - Do NOT include the dead `get_stem_signature` method (should be gone after T2)

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Single class extraction, self-contained with no cross-module dependencies
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T7, T9, T10)
  - **Blocks**: F1, F2, F3, F4
  - **Blocked By**: T6

  **References**:

  **Pattern References**:
  - `log_parser.py:145-191` — LogicClusterer class (exact code to extract)
  - `log_parser.py:7` — `from collections import defaultdict` import needed

  **WHY Each Reference Matters**:
  - Lines 145-191: This is the EXACT code block to extract — after T2 removes `get_stem_signature` (lines 151-170), this will be ~25 lines
  - Line 7: LogicClusterer.run uses `defaultdict(list)` — must import it

  **Acceptance Criteria**:
  - [ ] `logic_clusterer.py` exists and is ≤ 300 lines
  - [ ] `python -c "from logic_clusterer import LogicClusterer; print('OK')"` → OK
  - [ ] `grep -n 'get_stem_signature' logic_clusterer.py` → 0 matches (dead code not carried over)
  - [ ] `pytest tests/ -v` → all tests PASS

  **QA Scenarios:**

  ```
  Scenario: Happy path — LogicClusterer module works standalone
    Tool: Bash
    Preconditions: T6 complete, class extracted
    Steps:
      1. Run `python -c "from logic_clusterer import LogicClusterer; lc = LogicClusterer(); sig = lc.get_logic_signature(('test/var',)); print(sig)"` — assert prints a signature string
      2. Run `wc -l logic_clusterer.py` — assert ≤ 300
      3. Run `pytest tests/ -v --tb=short` — assert all pass
    Expected Result: Module imports, method works, file small, tests pass
    Failure Indicators: ImportError, unexpected output, file > 300 lines
    Evidence: .sisyphus/evidence/task-8-logic-clusterer.txt
  ```

  **Commit**: NO (groups with T7, T9, T10)

- [ ] 9. Extract ai_clusterer.py and ai_weights.py from log_parser.py

  **What to do**:
  - Create `ai_weights.py` containing extracted helper methods from AIClusterer:
    - Move `extract_variable_tail()` method as a standalone function
    - Move `_apply_variable_position_weights()` method as a standalone function
    - Include necessary imports: `re` (if needed)
    - These become module-level functions (not class methods) — they don't use `self` for anything besides calling each other
    - Actually check: `extract_variable_tail` calls `self._apply_variable_position_weights` — after extraction, it should call the function directly
    - File must be ≤ 300 lines
  - Create `ai_clusterer.py` containing the (renamed) `AIClusterer` class:
    - Move class definition with remaining methods: `__init__`, `_load_config`, `get_rule_config`, `run`
    - Import extracted functions: `from ai_weights import extract_variable_tail, apply_variable_position_weights`
    - Import other project modules: none needed (AIClusterer receives logic_groups as parameter)
    - Import external deps: `os`, `json`, `re`, `from collections import defaultdict`
    - Conditional imports for ML: `try: from sentence_transformers import SentenceTransformer; from sklearn.cluster import DBSCAN; except ImportError: ...`
    - The `AI_AVAILABLE` module-level flag stays in this file (for import-time check)
    - `self.ai_available` instance attribute (from T6) stays in the class
    - Update `run()` to call `extract_variable_tail(...)` instead of `self.extract_variable_tail(...)`
    - Update `run()` to call `apply_variable_position_weights(...)` instead of `self._apply_variable_position_weights(...)`
    - File must be ≤ 300 lines (this is the critical one — verify after extraction)
  - Remove AIClusterer class from `log_parser.py`
  - Run tests after — update test imports if needed

  **Must NOT do**:
  - Do NOT change the DBSCAN clustering logic — only move code
  - Do NOT change the embedding input construction — only update method calls to function calls
  - Do NOT remove the module-level `AI_AVAILABLE` flag — it's needed for import-time check
  - Do NOT create circular imports between ai_clusterer and ai_weights

  **Recommended Agent Profile**:
  - **Category**: `unspecified-high`
    - Reason: Most complex extraction — must split class into two files while maintaining call relationships
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T7, T8, T10)
  - **Blocks**: F1, F2, F3, F4
  - **Blocked By**: T6

  **References**:

  **Pattern References**:
  - `log_parser.py:196-447` — Full AIClusterer class (code to split)
  - `log_parser.py:244-298` — `extract_variable_tail` method (move to ai_weights.py)
  - `log_parser.py:300-316` — `_apply_variable_position_weights` method (move to ai_weights.py)
  - `log_parser.py:336-447` — `run()` method (stays in ai_clusterer.py, calls updated)
  - `log_parser.py:376-412` — Inside `run()`: calls to `self.extract_variable_tail()` and `self._apply_variable_position_weights()` — must update to function calls
  - `log_parser.py:12-18` — Module-level ML import try/except (duplicate in ai_clusterer.py)

  **WHY Each Reference Matters**:
  - Lines 244-316: These two methods are the extraction targets for ai_weights.py — they're pure functions that don't need class state (only `self` is used to call the other method)
  - Lines 376-412: Inside `run()`, the calls to `self.extract_variable_tail(...)` and `self._apply_variable_position_weights(...)` must become plain function calls after extraction
  - Lines 12-18: The ML import guard must be duplicated in ai_clusterer.py since it needs SentenceTransformer and DBSCAN

  **Acceptance Criteria**:
  - [ ] `ai_clusterer.py` exists and is ≤ 300 lines
  - [ ] `ai_weights.py` exists and is ≤ 300 lines
  - [ ] `python -c "from ai_clusterer import AIClusterer; print('OK')"` → OK
  - [ ] `python -c "from ai_weights import extract_variable_tail; print('OK')"` → OK
  - [ ] `grep -n 'self\.extract_variable_tail\|self\._apply_variable_position_weights' ai_clusterer.py` → 0 matches (now function calls, not self.method)
  - [ ] `pytest tests/ -v` → all tests PASS

  **QA Scenarios:**

  ```
  Scenario: Happy path — both modules import and work
    Tool: Bash
    Preconditions: T6 complete, class split into two files
    Steps:
      1. Run `python -c "from ai_weights import extract_variable_tail, apply_variable_position_weights; print('OK')"` — assert prints OK
      2. Run `python -c "from ai_clusterer import AIClusterer; print('OK')"` — assert prints OK
      3. Run `python -c "from ai_weights import extract_variable_tail; result = extract_variable_tail('A / B / C', tail_levels=1); print(result)"` — assert prints 'C'
      4. Run `wc -l ai_clusterer.py ai_weights.py` — assert both ≤ 300
      5. Run `pytest tests/ -v --tb=short` — assert all pass
    Expected Result: Both modules work, functions callable, sizes within limit, tests pass
    Failure Indicators: ImportError, wrong output, file > 300 lines, test failures
    Evidence: .sisyphus/evidence/task-9-ai-split.txt

  Scenario: No self.method calls remain for extracted functions
    Tool: Bash
    Preconditions: Split complete
    Steps:
      1. Run `grep -n 'self\.extract_variable_tail\|self\._apply_variable_position_weights' ai_clusterer.py`
      2. Assert 0 matches — these should now be plain function calls
    Expected Result: Zero self.method references to extracted functions
    Failure Indicators: Any grep matches
    Evidence: .sisyphus/evidence/task-9-no-self-calls.txt

  Scenario: Import direction is correct (no circular imports)
    Tool: Bash
    Preconditions: Both files created
    Steps:
      1. Run `grep -n 'from ai_clusterer' ai_weights.py` — assert 0 matches
      2. Run `python -c "import ai_weights; import ai_clusterer; print('No circular')"` — assert no error
    Expected Result: ai_weights does NOT import from ai_clusterer
    Failure Indicators: Circular import error, grep matches
    Evidence: .sisyphus/evidence/task-9-no-circular.txt
  ```

  **Commit**: NO (groups with T7, T8, T10)

- [ ] 10. Create main.py entry point and update view_log.py

  **What to do**:
  - Create `main.py` containing the CLI entry point (currently `if __name__ == '__main__':` block, lines 452-523):
    - Import from all modules: `from template_manager import RuleTemplateManager`, `from parser import SubutaiParser`, `from logic_clusterer import LogicClusterer`, `from ai_clusterer import AIClusterer`
    - Import stdlib: `sys`, `json`
    - Move the main execution logic into a `def main() -> None:` function
    - Add `if __name__ == '__main__': main()` at bottom
    - Replace `AI_AVAILABLE` global check with `ai_clusterer_instance.ai_available` (from T6)
    - File must be ≤ 300 lines
  - Update `view_log.py`:
    - Ensure all type hints are applied (from T4)
    - Ensure all Korean is translated (from T5)
    - No import changes needed (view_log.py is standalone, reads JSON file)
  - Delete `log_parser.py` — all code has been moved to the new modules
    - **CRITICAL**: Only delete AFTER verifying all classes and main block have been extracted
    - Run `git diff --stat` to confirm log_parser.py is fully replaced
  - Run full test suite after deletion to confirm nothing breaks

  **Must NOT do**:
  - Do NOT change the JSON output structure — `main.py` must produce identical output
  - Do NOT add argparse — keep the simple `sys.argv` pattern
  - Do NOT add a `__init__.py` — these are scripts, not a package
  - Do NOT delete `log_parser.py` until ALL classes are confirmed extracted and tests pass

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: Main block extraction is straightforward, view_log.py changes are minimal
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES
  - **Parallel Group**: Wave 3 (with T7, T8, T9)
  - **Blocks**: F1, F2, F3, F4
  - **Blocked By**: T6

  **References**:

  **Pattern References**:
  - `log_parser.py:452-523` — Main execution block (exact code to extract)
  - `log_parser.py:461-462` — `tm = RuleTemplateManager(rule_file)` / `parser = SubutaiParser(tm)` — update to use new module imports
  - `log_parser.py:482-488` — `AI_AVAILABLE` check → change to `ai_clusterer.ai_available`
  - `log_parser.py:490-500` — Fallback logic when AI unavailable (move as-is)
  - `log_parser.py:518-523` — JSON output writing + final print message (translate Korean in line 523)
  - `view_log.py` — Full file, minimal changes needed beyond T4/T5 work

  **WHY Each Reference Matters**:
  - Lines 452-523: This is the EXACT code to move into `main()` function — it wires all four classes together
  - Line 482: `if AI_AVAILABLE:` must become `if ai_clusterer.ai_available:` after T6 refactoring
  - Line 523: `print(f"\n💾 모든 결과(원본 로그 포함)가 '{output_filename}'에 저장되었습니다.")` — Korean, must be translated already from T5

  **Acceptance Criteria**:
  - [ ] `main.py` exists and is ≤ 300 lines
  - [ ] `python main.py test_run.log /dev/null` runs without error (produces same output structure)
  - [ ] `log_parser.py` no longer exists (deleted)
  - [ ] `wc -l *.py | sort -n` — all files ≤ 300
  - [ ] `pytest tests/ -v` → all tests PASS (update all test imports from `log_parser` to new module names)

  **QA Scenarios:**

  ```
  Scenario: Happy path — main.py runs end-to-end
    Tool: Bash
    Preconditions: All modules extracted (T7, T8, T9 complete)
    Steps:
      1. Run `python main.py test_run.log /dev/null 2>&1` — assert no Python traceback
      2. Check output contains 'Final Results' and group count
      3. Check `subutai_results.json` is created
      4. Run `wc -l main.py` — assert ≤ 300
    Expected Result: Parser runs to completion, JSON output created, file under limit
    Failure Indicators: Python traceback, missing output file, file > 300 lines
    Evidence: .sisyphus/evidence/task-10-main-runs.txt

  Scenario: log_parser.py is fully replaced
    Tool: Bash
    Preconditions: main.py created and verified
    Steps:
      1. Run `test -f log_parser.py && echo 'STILL EXISTS' || echo 'DELETED'` — assert DELETED
      2. Run `wc -l *.py | sort -n` — assert all files ≤ 300
      3. Run `pytest tests/ -v --tb=short` — assert all pass
    Expected Result: log_parser.py gone, all new modules present, all tests pass
    Failure Indicators: log_parser.py still exists, test failures, files over 300 lines
    Evidence: .sisyphus/evidence/task-10-monolith-deleted.txt

  Scenario: Output matches baseline (regression check)
    Tool: Bash
    Preconditions: main.py working, baseline from T1 available
    Steps:
      1. Run `python main.py test_run.log /dev/null 2>&1 | grep -v '^[[:space:]]*$' > /tmp/new_output.txt`
      2. Compare structure against baseline from .sisyphus/evidence/task-1-baseline-snapshot.txt
      3. Verify JSON keys match: rule_id, representative_pattern, total_count, original_logs
    Expected Result: Same output structure as original log_parser.py
    Failure Indicators: Missing fields, different structure, different counts
    Evidence: .sisyphus/evidence/task-10-regression.txt
  ```

  **Commit**: YES (combined commit for T7, T8, T9, T10)
  - Message: `refactor: split monolith into modular files`
  - Files: `template_manager.py`, `parser.py`, `logic_clusterer.py`, `ai_clusterer.py`, `ai_weights.py`, `main.py`, `view_log.py`, deleted `log_parser.py`, `tests/test_*.py`
  - Pre-commit: `pytest tests/ -v`
---

## Final Verification Wave

- [ ] F1. **Plan Compliance Audit** — `oracle`
  Read the plan end-to-end. For each "Must Have": verify implementation exists. For each "Must NOT Have": search codebase for forbidden patterns — reject with file:line if found. Check evidence files exist in .sisyphus/evidence/. Compare deliverables against plan.
  Output: `Must Have [N/N] | Must NOT Have [N/N] | Tasks [N/N] | VERDICT: APPROVE/REJECT`

- [ ] F2. **Code Quality Review** — `unspecified-high`
  Run `pytest` + check all files for: bare except, `as any`, empty catches, console.log in prod, commented-out code, unused imports. Check AI slop: excessive comments, over-abstraction, generic names. Verify all files ≤ 300 lines.
  Output: `Tests [N pass/N fail] | Files [N clean/N issues] | VERDICT`

- [ ] F3. **Regression QA** — `unspecified-high`
  Run the full parser pipeline with test_run.log and verify output matches baseline snapshot from Task 1. Test edge cases: empty input, missing template file, AI unavailable. Save evidence.
  Output: `Scenarios [N/N pass] | Integration [N/N] | VERDICT`

- [ ] F4. **Scope Fidelity Check** — `deep`
  For each task: read "What to do", read actual diff. Verify 1:1 — everything in spec was built, nothing beyond spec was built. Check "Must NOT do" compliance. Flag unaccounted changes.
  Output: `Tasks [N/N compliant] | Unaccounted [CLEAN/N files] | VERDICT`

---

## Commit Strategy

| Phase | Commit Message | Files |
|-------|---------------|-------|
| T1 | `test: add pytest infrastructure and regression tests` | conftest.py, tests/*.py |
| T2 | `refactor: remove dead code and unused imports` | log_parser.py |
| T3 | `refactor: rename symbols for clarity` | log_parser.py, view_log.py |
| T4 | `refactor: add type hints to all functions` | log_parser.py, view_log.py |
| T5 | `refactor: translate Korean comments to English` | log_parser.py, view_log.py |
| T6 | `refactor: improve error handling and AI availability pattern` | log_parser.py |
| T7-T10 | `refactor: split monolith into modular files` | template_manager.py, parser.py, logic_clusterer.py, ai_clusterer.py, ai_weights.py, main.py, view_log.py |

---

## Success Criteria

### Verification Commands
```bash
pytest                                    # All tests pass
grep -rn '[가-힣]' *.py | wc -l          # Expected: 0
wc -l *.py | sort -n                      # All files ≤ 300 lines
python -c "from template_manager import RuleTemplateManager; print('OK')"  # OK
python -c "from ai_clusterer import AIClusterer; print('OK')"              # OK
```

### Final Checklist
- [ ] All "Must Have" present
- [ ] All "Must NOT Have" absent
- [ ] All tests pass
- [ ] Zero Korean characters in .py files
- [ ] All files ≤ 300 lines
- [ ] JSON output identical to pre-refactor baseline
