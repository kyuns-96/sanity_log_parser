# Agent Communication Protocol

## Setup

Two agents collaborate on this project:

| Agent | Environment | Role |
|-------|-------------|------|
| **Claude Code** | User workstation | Code implementation, testing, git |
| **Domain Agent** | Air-gapped secure env | Synopsys PrimeTime expertise, real report access |

## Communication Method

The user relays messages between agents manually.

- **Questions (Claude Code → Domain Agent):** Can be long and detailed. User can copy-paste from this environment into the air-gapped env.
- **Answers (Domain Agent → Claude Code):** Must be short. The domain agent cannot easily copy-paste out, so answers should be a few words, a short example line, or a brief list.

## Findings Log

### 2026-02-26: PrimeTime Constraint Report Format

**Sample instance line:**
```
       1 of 46         0      Clock 'GEN_CLK_A' is generated from 'MASTER_CLK'.
```

**Field layout (4 fields before message):**

| Position | Field | Example |
|----------|-------|---------|
| 0 | counter | `1` |
| 1 | "of" | `of` |
| 2 | total | `46` |
| 3 | waived_count | `0` |
| 4+ | message | `Clock 'GEN_CLK_A' is generated from 'MASTER_CLK'.` |

**Rule ID location:** On a parent/header line above, NOT on instance lines. Instance lines inherit the rule ID from their parent.

**Multiline:** No. Each message is a single line.

### 2026-02-26: Parent/Header Line Format (Round 2)

**Report structure (top to bottom):**

```
CGR_0018      46     0   Clock 'clk1' is generated from 'clk2'        ← parent line (rule_id, count, waived, message)
<section lines like "error", "warning">                                ← severity section markers
       1 of 46            0      Clock 'GEN_A' is generated from 'MSTR'  ← instance line (N of M, waived, message)
       2 of 46            0      Clock 'GEN_B' is generated from 'MSTR2'
       ...
```

**Parent line fields:** `RULE_ID  COUNT  WAIVED  MESSAGE` (no "N of M" pattern)

**Instance line fields:** `N  of  M  WAIVED  MESSAGE` (has "N of M" pattern)

**Key insight:** The rule ID is only on the parent line. Instance lines inherit it. There are severity section lines (error, warning) between parent and instances — no column headers.

### 2026-02-26: Full Report Structure (Round 3)

**Complete report hierarchy:**

```
Severity: ...                                          ← top-level header (skipped)
 error                  62   0                         ← severity section (lowercase, indented, count, waived)
  CGR_0018          46    0 Clock 'clk1' is generated from 'clk2'   ← parent (rule_id, count, waived, message)
       1 of 46          0    Clock 'GEN_A' is generated from 'MSTR'  ← instance (N of M, waived, message)
       2 of 46          0    Clock 'GEN_B' is generated from 'MSTR2' ← instance
  CGR_0002            4     0     Clock 'x' is generated from 'y'   ← next parent
       1 of 4           0    ...                                     ← its instances
 warning               12   0                         ← next severity section
  CLK_0035          ...                                ← parents under warning
```

**Key findings:**
- **No separate template file needed.** The report IS the data — parent lines serve as templates, instance lines are violations.
- **Hierarchy:** severity → rule (parent) → instances
- **Each rule_id appears once** under one severity section.
- **Severity section line format:** lowercase word + count + waived (e.g., ` error                  62   0`)
- **Parent line format:** `RULE_ID  COUNT  WAIVED  MESSAGE` — matches `XXX_NNNN` pattern, no "N of M"
- **Instance line format:** `N  of  M  WAIVED  MESSAGE` — has "N of M" pattern

### 2026-02-26: Edge Cases (Round 4)

- **Rule ID pattern:** Always `XXX_NNNN` (3 uppercase letters + underscore + 4 digits). Known prefixes: CGR, CLK, UNT, HIER, DEX, DRV, OPN, UNC, EXD.
- **Extra line types to skip:** `"Design:"` line, constraint statistics table headers, scenario headers.
- **Severity words:** Only `error`, `warning`, `info` seen.
- **Waived field on instances:** Always 0. Waived count is tracked on parent lines only.

### 2026-02-26: State Machine & File Header (Round 5)

- **Each rule_id appears exactly once** in the entire file. No duplicates across severity sections.
- **Parents always have instances** (all observed). If count=0, likely no instance lines follow.
- **No lines between severity and first parent** — immediate transition.
- **File header format:**
  ```
  ******************************************
  Report : report_constraint_analysis
  Version: U-2022.12-SP5-3
  Date   : Tue Sep   2 17:40:49 2025
  ******************************************
  ```
  Stars (`*`) are the header delimiter.

### Impact on Current Parser

The current CLI requires `LOG_FILE` + `TEMPLATE_FILE` as two separate args. In reality:
1. Template file argument is unnecessary — rule IDs come from parent lines in the same file.
2. Parser must track **current rule_id** state while iterating lines (parent sets it, instances inherit it).
3. Severity section lines need to be recognized and either skipped or used as metadata.
