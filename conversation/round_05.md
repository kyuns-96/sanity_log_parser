# Round 5 — State Machine Behavior

**Questions from Claude Code:**

```
Q1: When a new severity section starts (e.g., "warning" after "error"),
    can the SAME rule_id appear again? Or does each rule_id appear
    exactly once in the entire file?

Q2: Can there be multiple parent lines in a row (no instance lines between them)?
    For example:
      CGR_0018   46  0  message1
      CGR_0002    4  0  message2
           1 of 4  0  instance...
    (where CGR_0018 has 0 instances listed)

Q3: Between the severity section line and the first parent line,
    are there any other lines? (column headers, blank lines, etc.)

Q4: Show the very first 5-10 lines of a real report file
    (the file header before the first severity section).
    Anonymize names if needed. I need to know what to skip at the top.
```

**Answers from Domain Agent:**

```
A1: No. Each rule_id appears exactly once in the entire file. Structure: severity -> rule -> instances. 
A2: Unknown. All seen parents have instances. If count=0, likely no instances follow.
A3: No. Immediate transition: severity line -> indented parent line.
A4:
******************************************
Report : report_constraint_analysis
Version: U-2022.12-SP5-3
Date   : Tue Sep   2 17:40:49 2025
******************************************
```
