---
description: Initialize a fund-review session — parse the budget standard PDF, quote xlsx, and optional pricing guideline into a structured session workspace.
---

Initialize a fund-review session workspace.

Usage:
```
/fund-review:init [--standard PDF] [--quotes XLSX...] [--guideline MD] [--session-id ID]
```

If arguments are omitted, the skill auto-detects files in the current directory (glob `*预算支出标准*.pdf` and `*报价清单*.xlsx`). Confirms ambiguous matches before proceeding.

Invoke the `init-session` skill with the resolved arguments. After it returns, suggest `/fund-review:optimize`.
