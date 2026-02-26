# Round 1 — Basic Line Format

**Questions from Claude Code:**

```
Q1: Show one real line from the report (anonymize names). Just one complete line.
Q2: How many fields before the message?
Q3: Is rule_id (like CGR_0018) on the same line as the message, or in a separate file?
Q4: Are there multiline messages, or is each message exactly one line?
```

**Answers from Domain Agent:**

```
A1: Sample line: "       1 of 46         0      Clock 'GEN_CLK_A' is generated from 'MASTER_CLK'."
A2: 4 fields -> counter, "of", total, waived_count
A3: No, parent line above
A4: No, single line only
```
