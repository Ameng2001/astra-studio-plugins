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

### 1. 确定性扫描 —— 跑脚本，不要自己算

```bash
PLUG=${CLAUDE_SKILL_DIR}/../../scripts
PYTHONPATH=$PLUG python3 $PLUG/run_optimize.py .fund-review/{session-id}
# → optimize-suggestions.json + feasibility-fp-table.xlsx
```

`run_optimize` 编排 12 个扫描器并写出 `optimize-suggestions.json`
（它的 docstring 就写着 "orchestrator for quote-optimize skill"）：

| 扫描器 | 查什么 |
|---|---|
| `scan_subject` | 逐行对 `mapping.json` 找标准条款；对不上 → `subject-mapping` |
| `scan_labor` | 有 `人/天`/`人天` 的行按 `formula_engine` 重算人天档位区间 |
| `scan_semantic` | 平台 sheet 与大模型 sheet 的描述相似度 → `semantic-split` |
| `scan_completeness` / `scan_redundancy` / `scan_workdays` | 漏项、重复计列、人天离群 |
| `scan_summary_sheets` | 多 sheet 无汇总（P0 那类结构问题） |
| `scan_ops` / `scan_commercial` / `scan_brand_models` | 运维、商务条款、品牌型号 |
| `scan_evidence` / `scan_caps` | **最后跑** —— 它们要看到前面所有结论 |

**不要用 LLM 复现这些扫描。** 相似度、人天区间、预算带这类计算必须是
确定性的、可复算的；交给模型执行会得到每次都不一样、且无法向财评解释的结果。
本步骤的价值恰恰在于它不是模型算的。

#### 认不出就报错，不静默出 0

表结构解析统一走 `quote_shape` 模块（`COLUMN_ROLES` 按**语义角色**归类候选列名，
`is_priceable()` 用排除法而非白名单做 kind 门控）。遇到下面两种情形
**先修输入或代码，不要跳过**：

- **某个语义角色全表零命中** → `QuoteShapeError`，列出期望与实际列名。
  修法：把本表的列名加进 `quote_shape.COLUMN_ROLES` 的对应角色 ——
  **不要改成静默跳过**。
- **反推功能点合计为 0** → stderr 告警。纯硬件报价确实可能是 0，
  但绝大多数情况是列名或 `kind` 没认出来。

这类「认不出就当合格」的 bug 在这两个脚本里出现过**五次**，全都不报错：
实测一份 1215 行的真实报价，原总额 0、反推 FP 0、逐行检查 0 findings、
综合得分 0/100，而命令正常退出 —— 读的人只会以为报价烂透了。
修好后是 ¥34,756,235.24 / 41,103 FP / 1131 项通过 / 100 分。

### 2. 专家复核（LLM，经 agent）—— 这一步才轮到模型

模型的职责是**筛选与解释**，不是计算：过滤低价值发现、按影响排序、
补 `rationale` 与 `standard_refs[]`。金额与判定来自第 1 步，模型不改数。

Invoke `quote-optimizer` agent with:
- All deterministic findings（来自 `optimize-suggestions.json`）
- `guideline.md` content (if present)
- A summarized view of `standard.json` (top-level clause index, not full text — full text retrieved on demand)

The agent's job is to:
- Filter low-value findings, prioritize by impact
- Add `rationale` and `standard_refs[]` to each kept suggestion
- Estimate `delta_amount` where computable

### 3. 产物：`optimize-suggestions.json`（第 1 步已写出，第 2 步就地补 rationale）

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
