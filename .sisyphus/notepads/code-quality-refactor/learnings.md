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
