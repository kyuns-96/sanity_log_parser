# Round 4 — Edge Cases

**Questions from Claude Code:**

```
Q1: Does the rule_id always match the pattern XXX_NNNN (3 uppercase letters,
    underscore, 4 digits)? Or can it vary (e.g., ABCD_001, XX_00018)?

Q2: Are there any lines between instances that are NOT: severity, parent,
    instance, dashes, equals, empty, "Rule", or "Severity"?
    (Any other line types I should know about?)

Q3: Is the first field of the severity line always one of these exact words:
    error, warning, info? Or are there others?

Q4: The "waived" field on instance lines — can it be non-zero?
    Should waived instances be treated differently?
```

**Answers from Domain Agent:**

```
A1: Yes, always XXX_NNNN pattern. Seen: CGR, CLK, UNT, HIER, DEX, DRV, OPN, UNC, EXD.
A2: Yes - "Design:" line (shows design name), constraint statistics table headers, scenario headers.
A3: Yes - only error, warning, info seen.
A4: No, always  in this report. Waived is tracked on parent lines, not instances
```
