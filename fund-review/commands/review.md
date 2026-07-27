---
description: Score a quote against the budget standard from a financial-reviewer perspective; every finding cites a specific clause and page.
---

Run financial review pre-audit.

Usage:
```
/fund-review:review [--session-id ID] [--target original|final]
```

`--target` defaults to `final` if `quote-final-*.xlsx` exist, else `original`.

Invoke the `quote-review` skill. On return:
- Print the report summary (score, counts, top risks).
- Report the path to `review-report.md`.
- If the report was on the original quote, suggest running `/fund-review:optimize` → `rewrite` to address findings.
