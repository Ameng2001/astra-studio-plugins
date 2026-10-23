---
description: Show every paper's stage, gate status and ledger counts — the advisor's dashboard
argument-hint: [paper-id]
---

# /studio-paper:status [paper-id]

读 `.studio-paper/*/status.json`，列每篇论文：当前阶段、六个闸门的状态与签字人、formal / exploratory run 数、RQ 覆盖数、内审次数、录用决定。给一个 paper-id 时展开该篇的待办（未处置 drift、待补实验、未签字闸门）。
