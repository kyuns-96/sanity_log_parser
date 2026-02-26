# Agent Communication Protocol (For Air-Gapped Domain Agent)

## Setup

You are collaborating with **Claude Code** (a coding agent on the user's workstation) through the user, who relays messages between us.

- **You** have expertise on Synopsys PrimeTime Constraint reports and access to real report files.
- **Claude Code** writes the parser code but has never seen a real report file.

## How Communication Works

1. Claude Code sends you detailed questions (copy-pasted by the user).
2. You answer as **short as possible** — a few words, one example line, or a brief list.
3. The user relays your answer back to Claude Code.

## Answer Format Guidelines

- **Keep answers short.** The user types your answers manually.
- One example line is better than a paragraph of explanation.
- For format questions, just show the raw line.
- For yes/no questions, just say yes or no (with a short note if needed).
- Use numbered answers matching the question numbers.

## What Claude Code Already Knows

From our previous exchange, Claude Code now understands:

1. **Instance line format:** `<counter> of <total> <waived_count> <message>`
   - Example: `       1 of 46         0      Clock 'GEN_CLK_A' is generated from 'MASTER_CLK'.`
2. **Fields:** counter, "of", total, waived_count, then message (4 fields before message).
3. **Rule ID** (like `CGR_0018`) is on a **parent/header line above**, not on instance lines.
4. **No multiline messages.** Each message is one line.
5. **Variables** are single-quoted strings (e.g., `'GEN_CLK_A'`, `'MASTER_CLK'`).

## Conversation Template

Use this format for every exchange. Copy the questions block, paste your answers below each.

---

### Round N — [Topic]

**Questions from Claude Code:**

```
Q1: ...
Q2: ...
Q3: ...
```

**Your answers (keep short — one line each, example lines preferred):**

```
A1:
A2:
A3:
```

---

### Example

**Questions from Claude Code:**

```
Q1: How many fields before the message?
Q2: Is rule_id on the same line?
Q3: Are there multiline messages?
```

**Your answers:**

```
A1: 4 fields -> counter, "of", total, waived_count
A2: No, parent line above
A3: No, single line only
```

---

## Current Open Question (Round 2)

**Questions from Claude Code:**

```
Q1: What does the parent/header line (containing the rule ID) look like?
    Please show one parent line followed by one child instance line.
    Anonymize signal names if needed. Just raw lines, like:

    <the parent line with rule_id>
           1 of 46         0      Clock 'GEN_CLK_A' is generated from 'MASTER_CLK'.
```

**Your answers:**

```
A1:
```
