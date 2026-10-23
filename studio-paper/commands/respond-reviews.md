---
description: Decompose external reviews point by point, classify and triage them, and produce the response-to-reviewers table with evidence pointers; narrow claims when new data weakens them
argument-hint: [paper-id] <reviews.md> [--decision accept|major-revision|reject]
---

# /studio-paper:respond-reviews {paper-id} <reviews>

回应：逐条拆解分类（fatal / fixable / 误解 / 澄清）→ 按评审人可移动性排序 → 分诊哪些补实验（入账后才能引用）→ 逐条回应表（证据指针 + run_id + 修改承诺）→ 新数据削弱 claim 时强制收窄（闸门 D11）→ 字数预算 → 导师签字后由人提交。`--decision` 记录结果与有效回应类型。

Use skill: "respond-reviews"
