# Round 3 — Section Structure & Template File

**Questions from Claude Code:**

```
Q1: What do the severity section lines look like exactly?
    Just show one example line (e.g., does it say "error", "Error:", "Severity: error"?)

Q2: Can one rule_id (parent) have instances under multiple severity sections?
    (e.g., some instances under "error", others under "warning")

Q3: Is the template file a separate file, or is it the same report file?
    (The current CLI takes two args: LOG_FILE and TEMPLATE_FILE)

Q4: Show a complete block from the report — from one parent line to the
    next parent line — with 2-3 instance lines. Like:

    CGR_0018      46     0   Clock 'clk1' is generated from 'clk2'
    <severity line>
           1 of 46     0      Clock 'GEN_A' is generated from 'MSTR'
           2 of 46     0      Clock 'GEN_B' is generated from 'MSTR2'
    <next parent line starts here>
```

**Answers from Domain Agent:**

```
A1: error                  62   0
    (lowercase, indented, with count/waived numbers)
A2: No. Each rule_id appears once under one severity. Report groups: severity -> rule -> instances.
A3: Same file. Report contains both (parent lines = template, instance lines = violations). No separate template file needed.
A4: CGR_0018          46    0 Clock 'clk1' is generated from 'clk2'
         1 of 46          0    Clock 'GEN_A' is generated from 'MSTR'
         2 of 46          0    Clock 'GEN_B' is generated from 'MSTR2'
    CGR_0002            4     0     Clock 'x' is generated from 'y'
```
