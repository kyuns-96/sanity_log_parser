# Mandatory Rules for Air-Gapped Agents (csh Environment)

## Shell: csh / tcsh

The air-gapped environment runs **csh** (C shell), NOT bash/zsh.
Every command you generate MUST be csh-compatible.

---

## CRITICAL: Never Use `python -c` — It WILL Break

### The Problem

When an AI agent (opencode, aider, etc.) executes a `python -c` command in csh,
the shell **parses the Python code as shell commands**. The multi-line content
leaks out of the `-c` argument and each line runs as a separate shell command.

**What happens in practice:**

```
# Agent runs this:
python3 -c "
import json
print('hello')
"

# csh actually executes:
python3 -c ""        ← empty string, python exits immediately
import json           ← shell runs `import` (ImageMagick screenshot tool)
print 'hello'         ← shell runs `print` (csh built-in or lpr alias)
```

The `import` command is a real binary (`/usr/bin/import` from ImageMagick).
In X11/Wayland environments it **launches a screenshot capture tool** and hangs.
In headless environments it errors. Either way, your Python code never runs.

The `print` command in csh is a built-in that writes to a printer or acts as
`echo`. Your Python logic is silently lost.

### Why This Happens

1. **csh does not support multi-line double-quoted arguments.** A newline inside
   `"..."` terminates the string. Each subsequent line becomes a standalone
   shell command.
2. **Agent tools may strip or mangle quoting.** Even if you craft correct
   quoting, the tool layer between the agent and the shell may reformat the
   command, breaking the quoting boundary.
3. **`python -c '...'` with single quotes** also fails if the Python code
   contains any single quote, `!`, `$`, or backtick — all of which csh
   interprets.

### Root Cause Summary

| What you write | What csh sees | Result |
|---------------|---------------|--------|
| `python -c "import json; ..."` (one line) | Works sometimes | Fragile — breaks on `!`, `$` |
| `python -c "` + newline + `import json` + newline + `"` | `python -c ""` then `import json` as shell command | **`import` binary runs instead** |
| `python -c 'import json; ...'` with `'` inside | Broken quoting | Syntax error or command leak |

---

## Mandatory Rule: ALWAYS Write a .py File, Then Execute It

**Do not use `python -c` at all. No exceptions. No one-liners.**

The ONLY safe pattern in csh under an agent tool:

### Step 1: Write the script to a file

```csh
cat << 'PYEOF' > /tmp/_task.py
import json
with open('file.json') as f:
    data = json.load(f)
for item in data:
    print(repr(item))
PYEOF
```

### Step 2: Run the file

```csh
python3 /tmp/_task.py
```

### With arguments

```csh
cat << 'PYEOF' > /tmp/_check.py
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
for g in data['groups']:
    print(g['group_id'], len(g.get('original_logs', [])))
PYEOF
python3 /tmp/_check.py results.json
```

### Why this is the only safe option

| Method | csh safe? | Agent-tool safe? | Verdict |
|--------|-----------|------------------|---------|
| `python -c "..."` (multi-line) | No | No | **BANNED** |
| `python -c '...'` (one-liner) | Fragile | Fragile | **BANNED** |
| `python3 << 'EOF'` (heredoc to stdin) | Yes | Depends on tool | Risky |
| Write `.py` file + run it | Yes | Yes | **REQUIRED** |

Heredocs piped to `python3` stdin can work in a real csh terminal, but agent
tools may split the heredoc into separate commands. The only method that
survives both csh quoting AND agent tool command splitting is a physical file.

---

## CRITICAL: Never Use `export` — It Does Not Exist in csh

### The Problem

`export VAR=value` is a **bash/sh command**. It does not exist in csh.
AI agents trained mostly on bash will generate `export` by default.

```
# BROKEN in csh — DO NOT USE
export PYTHONPATH=src
export MODEL_NAME="gpt-4"
export PATH=$PATH:/opt/tools/bin
```

csh will return: `export: Command not found.`

### Mandatory Fix: Use `setenv`

```csh
# csh equivalent of export
setenv PYTHONPATH src
setenv MODEL_NAME "gpt-4"
setenv PATH "${PATH}:/opt/tools/bin"
```

### Syntax Comparison

| bash (BROKEN in csh) | csh (CORRECT) | Notes |
|----------------------|---------------|-------|
| `export VAR=value` | `setenv VAR value` | No `=` sign in setenv |
| `export VAR="val"` | `setenv VAR "val"` | Quoting works the same |
| `export PATH=$PATH:/new` | `setenv PATH "${PATH}:/new"` | Brace-quote `$PATH` in csh |
| `VAR=value command` | `env VAR=value command` | Inline env vars for one command |
| `unset VAR` | `unsetenv VAR` | Removing env vars |
| `set -x` | `set echo` | Debug tracing |

### Also Banned: Other bash-isms

| bash command | csh equivalent | Notes |
|-------------|---------------|-------|
| `export VAR=val` | `setenv VAR val` | See above |
| `source file.sh` | `source file.csh` | Same keyword, but file must be csh syntax |
| `[[ condition ]]` | `if ( condition ) then` | csh uses different conditionals |
| `$(command)` | `` `command` `` | csh uses backticks for command substitution |
| `cmd1 && cmd2` | `cmd1 && cmd2` | Works in tcsh, not all csh |
| `function fname {}` | N/A | csh has no functions; use alias or script file |

---

## Additional csh Pitfalls (Inside heredoc/cat blocks)

Even inside `cat << 'PYEOF'`, be aware of these csh behaviors:

| Pattern | csh behavior | Safe alternative |
|---------|-------------|-----------------|
| `f'{x!r}'` | `!r` triggers history expansion | `repr(x)` |
| `f'{x!s}'` | `!s` triggers history expansion | `str(x)` |
| `f"${name}"` | `$name` is shell-expanded | Quote heredoc delimiter: `<< 'PYEOF'` |
| Backticks `` `cmd` `` | Executed as shell command | Irrelevant in Python, but avoid in comments |

**Single-quoting the heredoc delimiter (`<< 'PYEOF'`) disables all csh
expansion inside the block.** Always quote it.

---

## Checklist — MUST Pass Before Executing Any Command

1. [ ] **No `python -c` anywhere.** Not in single quotes, not in double quotes, not as a one-liner.
2. [ ] Python code is written to a `.py` file first (via `cat << 'PYEOF' > /tmp/script.py`).
3. [ ] Heredoc delimiter is single-quoted (`<< 'PYEOF'` not `<< PYEOF`).
4. [ ] Closing delimiter (`PYEOF`) is on its own line with no leading spaces.
5. [ ] Python code does not use `!r` or `!s` format specs (use `repr()`/`str()`).
6. [ ] `python3 /tmp/script.py [args]` is a separate command after the file is written.
7. [ ] **No `export` anywhere.** Use `setenv VAR value` (no `=` sign).
8. [ ] **No bash-isms:** no `$(...)`, no `[[ ]]`, no `function` keyword.

**If ANY check fails, rewrite before executing.**

---

## Common Task Templates

### Read and inspect JSON

```csh
cat << 'PYEOF' > /tmp/_inspect.py
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
print(json.dumps(data, indent=2)[:2000])
PYEOF
python3 /tmp/_inspect.py INPUT.json
```

### Run pytest

```csh
setenv PYTHONPATH src
python3 -m pytest --tb=short -q
```

(This is safe — no `-c`, no Python code in the shell argument.)

### Filter JSON output

```csh
cat << 'PYEOF' > /tmp/_filter.py
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
for g in data.get('groups', []):
    if g.get('group_type') == sys.argv[2]:
        print(repr(g.get('group_id')), len(g.get('original_logs', [])))
PYEOF
python3 /tmp/_filter.py results.json ai_super
```
