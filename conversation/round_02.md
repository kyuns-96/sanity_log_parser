# Round 2 — Parent/Header Line Format

**Questions from Claude Code:**

```
Q1: What does the parent/header line (containing the rule ID) look like?
    Please show one parent line followed by one child instance line.
    Anonymize signal names if needed. Just paste the raw lines, like:

    <the parent line with rule_id here>
           1 of 46         0      Clock 'GEN_CLK_A' is generated from 'MASTER_CLK'.

Q2: Does the parent line also have the "N of M" pattern, or is it a different format?

Q3: Are there other header lines between the parent line and the instance lines?
    (e.g., column headers, dashes, severity line, etc.)
```

**Answers from Domain Agent:**

```
A1: CGR_0018      46     0   Clock 'clk1' is generated from 'clk2'
         1 of 46            0      Clock 'GEN_A' is generated from 'MSTR'
A2: No. Parent has count/waived fields (not N of M). Instance has N of M pattern.
A3: Yes. Section lines like error, warning between parent and instances. No column headers.
```
