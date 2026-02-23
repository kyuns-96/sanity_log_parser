# Learnings — code-quality-refactor

## 2026-02-23 Session Init

### Source files
- `log_parser.py`: 522 lines (4 classes + main block)
- `view_log.py`: 81 lines

### Class structure in log_parser.py
- `RuleTemplateManager` (lines 23-54): Template loading and matching
- `SubutaiParser` (lines 58-141): Log line parsing with variable extraction
- `LogicClusterer` (lines 145-191): First-pass grouping by variables
- `AIClusterer` (lines 196-447): Second-pass semantic clustering with embeddings
- Main block (lines 452-522): CLI entry point

### Confirmed dead code
- `LogicClusterer.get_stem_signature` (lines 151-170) — never called
- `AIClusterer._apply_variable_value_weights` (lines 318-334) — never called
- `import time` (line 6) — unused
- `import numpy as np` (line 15) — unused

### Known issues
- Bare `except:` on line 202 (swallows KeyboardInterrupt, SystemExit)
- `AI_AVAILABLE` global mutated inside `AIClusterer.__init__`
- `var_pattern = re.compile(r"'(.*?)'")` duplicated 3 places (including inside a loop at line 379)
- `SubutaiParser` accesses `RuleTemplateManager._get_pure_template()` — private cross-boundary

### Import direction (strict, no circular imports)
`constants` ← `template_manager` ← `parser` ← `logic_clusterer` ← `ai_weights` ← `ai_clusterer` ← `main`

## 2026-02-23 Regression Test Baseline Notes

- `SubutaiParser.parse_line()` only accepts lines matching `\b\d+\s+of\s+\d+\b` and then drops the first 4 tokens before template extraction.
- `RuleTemplateManager._get_pure_template()` first masks quoted values to `'<VAR>'`, then masks standalone digits to `<NUM>`.
- `SubutaiParser.extract_variable_stems()` keeps tokens like `BLK_CPU` as a single stem because `str.isupper()` is `True` for uppercase strings containing `_`.
- `AIClusterer.extract_variable_tail()` docstring examples are accurate and can be used as deterministic non-ML regression tests.

## Task 2: Dead Code Removal - Completion Summary

**Status**: ✅ COMPLETE

**Removals Made**:
1. Removed `LogicClusterer.get_stem_signature()` method (lines 151-170) - was never called
2. Removed `AIClusterer._apply_variable_value_weights()` method (lines 318-334) - leftover dead code
3. Removed `import time` (line 6) - never used anywhere
4. Removed `import numpy as np` (line 15) - never referenced directly
5. Removed `variable_stems` and `stems_tuple` variable creation from `parse_line()` (lines 122-129)
6. Removed `"variable_stems": stems_tuple` from parse_line return dict (was line 137)
7. Updated test that was checking `variable_stems` key - removed assertion from `test_parse_line_matching_prefix_with_no_quotes_yields_no_var`

**Test Results**:
- All 17 tests pass after removal
- No broken functionality
- Code is cleaner and lighter

**Key Insight**: 
The `variable_stems` infrastructure was created but never actually used by any live code. The `get_stem_signature` method that consumed it was completely dead. This is a good example of why grep-based dead code detection needs to confirm zero call sites before removal.

**Evidence**: Saved to `.sisyphus/evidence/task-2-dead-code-removed.txt`


## Task 3: Symbol Renaming - Completion Summary

**Status**: ✅ COMPLETE

**Renames Applied in log_parser.py**:
1. `self.tm` → `self.template_manager` (all 3 references in SubutaiParser)
2. `_get_pure_template` → `get_pure_template` (made public method)
3. `var_pattern` inside AIClusterer.run() loop → moved to class-level `_VAR_PATTERN` (line 164)
4. All `lambda x:` → `lambda group:` (3 instances for clarity)
5. Loop variables:
   - `temp` → `normalized_template` in get_pure_template
   - `sigs` → `signatures` in LogicClusterer.get_logic_signature
   - `p` → `parsed_log` in LogicClusterer.run
   - `g` → `logic_group` throughout (14 instances)
   - `res` → `result` in main block

**Renames Applied in tests/test_template_manager.py**:
- Updated 3 test methods to use `get_pure_template` instead of `_get_pure_template` (lines 6, 12, 21)

**Test Results**:
- All 17 tests pass after renaming
- No functional changes - pure refactoring
- Code is significantly more readable

**Performance Improvement**:
- Moving `var_pattern = re.compile(r"'(.*?)')")` from inside the loop (line 328) to class attribute `_VAR_PATTERN` eliminates unnecessary regex recompilation on every iteration

**Key Insights**:
1. Making `_get_pure_template` public was justified - it's called from external class (SubutaiParser)
2. The `var_pattern` regex was being recompiled inside a hot loop - moving to class constant is both clearer and more efficient
3. Single-letter loop variables (`g`, `p`, `x`) were masking intent throughout the clustering logic
4. Lambda parameters named `x` are particularly cryptic in regex substitutions where `group` is more semantic

**Validation**:
- Grep confirmed 0 matches for old symbols (`self.tm[^a-z_]`, `_get_pure_template`)
- Full test suite passes

**Evidence**: Saved to `.sisyphus/evidence/task-3-renames-applied.txt`

**Commit**: `3a1d2fd - refactor: rename symbols for clarity across codebase`