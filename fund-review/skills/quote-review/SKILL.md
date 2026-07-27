---
name: quote-review
description: 'Score a quote against the local budget standard from a financial-reviewer perspective. Every conclusion is paired with a citation to the standard (PDF page + section). Use after quote-rewrite (against `quote-final.xlsx`) or directly on the original (`quote.json`) for an initial diagnostic. Trigger phrases: "财评打分", "评审报价", "/fund-review:review".'
allowed-tools: Read, Write, Bash, Glob, Agent
user-invocable: true
---

# quote-review

Produce a `review-report.md` styled like a pre-audit memo. Every row of conclusion must include at least one standard citation with page anchor.

## Preconditions
- `.fund-review/{session-id}/standard.json` + `quote.json` exist
- If `quote-final-*.xlsx` exist, parse them as the review target (call `quote-parser` internally); otherwise review the original quote

## Steps

### 1. Deterministic compliance pass
For each row of the target quote:
- Look up applicable clauses via `mapping.json`
- Run rule checks via `formula-engine`:
  - Unit price within range?
  - Function-point formula satisfied (where applicable)?
  - Category factor + reuse factor consistent?
  - OA/website/CA per-user caps respected?
  - Direct non-labor cost zeroed unless justified?
- Assign per-row score in `{pass, warn, fail}` with a machine-readable reason code

### 2. Aggregate to sections
Group findings under the standard's section tree (信息化建设项目预算支出标准 → 软件 / 网络 / 设备 / ...).

### 3. Expert commentary (LLM, via agent)
Invoke `financial-reviewer` agent for any `warn`/`fail` items:
- Question: "如何作为财评专家挑战这一项？"
- Expected output: 2-4 sentences of professional challenge + suggested remediation

### 4. Compose `review-report.md`

Template:
```markdown
# 报价财评评审报告
*会话*: {session_id}
*被评对象*: {filenames}
*评审基准*: 柳财审〔2020〕16号《柳州市本级信息化建设项目预算支出标准（试行）》

## 总评
- 综合得分：85/100
- 通过：120 项 / 警告：18 项 / 否决：4 项
- 主要风险：人力单价未按功能点法核算；平台-大模型存在描述重复；设备清单未汇总
- 估算合计金额：¥xx,xxx,xxx（与申报合计差异 ¥xxx）

## 一、定制软件开发
### 1.1 平台软件——智慧管理模块（成本单价 ¥1200）
- **评分**：⚠️ warn
- **评审意见**：单价未按 1.7 万/人月折算；未提供功能点（FP）估算，无法验证 AI 类别调整因子 1.0-1.5 的取值合理性。建议补 FP 估算，并按业务处理（0.8-1.0）类别核算，差额约 ¥xxx。
- **标准出处**：
  - 《柳财审〔2020〕16号》p.13 第三章 (一) 2.1 — 定制软件开发费用公式
  - 同文 p.14 表3 — 软件类别调整因子取值
- **影响金额**：约 ¥-23,000

### 1.2 ...

## 二、人工智能（大模型）
...

## 三、专用设备
...

## 附录 A — 全部条款引用索引
| 条款 | 页码 | 引用次数 |
| ... | ... | ... |
```

### 5. Self-check before persist
- Assert every section's items[] has non-empty `standard_refs`
- Assert every `page` in refs is in `[1, standard.json.page_count]`
- If any item fails the check, downgrade to a warning and continue (do not block report)

## Output contract
- `.fund-review/{session-id}/review-report.md`
- `.fund-review/{session-id}/review-findings.json` (machine-readable mirror, for downstream tools)

## Notes
- The report is a **pre-audit**, explicitly labeled as such. It models how an outside reviewer would likely challenge the quote; it is not itself a regulatory audit.
- When operating on `quote-final.xlsx`, also produce a delta section "相对原始报价改动了什么" by diffing against `quote.json`.
