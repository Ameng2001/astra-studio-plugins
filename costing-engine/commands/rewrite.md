---
description: Apply an approved optimization plan — rewrite the quote xlsx with corrected subjects, semantic split, recomputed labor pricing, and restructured sheets.
---

Apply the approved optimization plan.

Usage:
```
/fund-review:rewrite [--session-id ID]
```

Refuse to run if `approved-plan.json` is missing — instruct user to run `/fund-review:optimize` first.

Invoke the `quote-rewrite` skill. On return:
- Print the changelog summary (applied / skipped / warnings).
- Report the new file paths.
- Suggest `/fund-review:review` as the next step.
