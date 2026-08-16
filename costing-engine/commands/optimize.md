---
description: Scan a quote and produce a list of optimization suggestions with standard citations. Waits for human approval before any rewrite.
---

Run quote optimization.

Usage:
```
/fund-review:optimize [--session-id ID]
```

If no session exists, prompt to run `/fund-review:init` first (or auto-initialize from cwd if confirmed).

Invoke the `quote-optimize` skill. On return:
- Display suggestions grouped by category and severity.
- Wait for the user's natural-language approval decisions.
- Write `approved-plan.json` and confirm before persisting.
- Suggest `/fund-review:rewrite` as the next step.
