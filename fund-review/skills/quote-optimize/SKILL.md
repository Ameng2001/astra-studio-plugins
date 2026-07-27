---
name: quote-optimize
description: 'Analyze a quote against the local budget standard + global pricing guideline and produce a structured list of optimization suggestions. Targets the three known pain points — subject misalignment, platform/LLM semantic overlap, and coarse labor pricing. Outputs `optimize-suggestions.json` and waits for human approval before any rewrite. Trigger phrases: "优化报价方案", "给报价提优化建议", "/fund-review:optimize".'
allowed-tools: Read, Write, Bash, Glob, Agent
user-invocable: true
---

# quote-optimize

Produce evidence-backed optimization suggestions; **does not modify the original xlsx**. Always followed by a human-in-the-loop approval step before rewrite.

## Preconditions
- Session workspace exists at `.fund-review/{session-id}/` with `standard.json` + `quote.json` + `mapping.json`. If missing, **first invoke `init-session`** and re-run.

## Steps

### 1. Deterministic compliance scan (no LLM)
Three scanners write findings to a working buffer:

- **subject-alignment**: for every quote row, look up matching standard clause via `mapping.json`. Unmatched rows → suggestion `subject-mapping`.
- **semantic-overlap**: cross-sheet text similarity (TF-IDF over normalized 建设详情 columns) between 平台 sheets and 大模型 sheets; matches above threshold → suggestion `semantic-split` with proposed terminology shift (平台 → 功能特性 / 大模型 → 模型能力).
- **labor-pricing**: for every row with `人/天` or `人天` column, recompute using `formula-engine` (see `${CLAUDE_SKILL_DIR}/../../references/formula-engine.md`):
  - Convert standard 1.7 万/人月 × category factor × reuse factor → per-day rate floor/ceiling
  - For software-development rows, optionally propose function-point recalculation if FP estimates are missing
  - Deviations → suggestion `labor-pricing`

Additional scanners (lighter):
- **sheet-restructure**: detect cases like the 6-园所 device sheets without a summary; propose adding a summary sheet
- **overlap-attribution**: rows that legitimately exist in both 平台 and 大模型 — propose splitting cost across the two with explicit attribution

### 2. Expert review (LLM, via agent)
Invoke `quote-optimizer` agent with:
- All deterministic findings
- `guideline.md` content (if present)
- A summarized view of `standard.json` (top-level clause index, not full text — full text retrieved on demand)

The agent's job is to:
- Filter low-value findings, prioritize by impact
- Add `rationale` and `standard_refs[]` to each kept suggestion
- Estimate `delta_amount` where computable

### 3. Emit `optimize-suggestions.json`

Schema:
```jsonc
{
  "session_id": "...",
  "generated_at": "...",
  "summary": {
    "total": 42,
    "by_severity": { "high": 7, "medium": 22, "low": 13 },
    "by_category": { "subject-mapping": 14, "semantic-split": 9, "labor-pricing": 12, "sheet-restructure": 4, "overlap-attribution": 3 },
    "estimated_impact_amount": -1234567
  },
  "suggestions": [
    {
      "id": "S001",
      "category": "labor-pricing",
      "severity": "high",
      "target": { "workbook": "...xlsx", "sheet": "幼教综合服务平台", "row": 17, "col": "H", "current_value": 1200 },
      "proposed_change": { "type": "set_value", "new_value": 782, "also_recompute": ["成本总价","对外总价"] },
      "rationale": "...",
      "standard_refs": [ { "section": "三.(一).2", "page": 14, "snippet": "..." } ],
      "estimated_delta_amount": -23000,
      "auto_applicable": true
    }
  ]
}
```

### 4. Render to user + HIL gate
- Print suggestions grouped by category, sorted by severity, each with `id`, `severity`, target, proposed change, page anchor.
- End with the HIL prompt:
  ```
  下一步：在自然语言里告诉我你的决策，例如：
    "采纳 S001-S010、S015；S012 改为 800；其他全部拒绝"
  我会把你的决策落到 approved-plan.json，然后你就可以 /fund-review:rewrite
  ```
- When the user replies, **parse decisions and write `approved-plan.json`**:
  ```jsonc
  {
    "session_id": "...",
    "approved_at": "...",
    "decisions": [
      { "id": "S001", "decision": "accept" },
      { "id": "S012", "decision": "edit", "custom_change": {...} },
      { "id": "S099", "decision": "reject", "reason": "..." }
    ]
  }
  ```
- **Echo back** a one-line confirmation per decision and ask for final ✓ before persisting.

## Output contract
- `.fund-review/{session-id}/optimize-suggestions.json` (always)
- `.fund-review/{session-id}/approved-plan.json` (after HIL)

## Non-goals
- Do not touch the original xlsx files.
- Do not invent suggestions outside the deterministic scanners + expert filter; every suggestion must trace to a finding or guideline rule.
