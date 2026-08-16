---
name: quote-selfreview
description: '对自己生成的报价做财评预审（红队预演）—— 预演财评专家会怎么问、检查我方能否作答，产出财评自审报告。条款引证按 pack_id 动态取，不硬编码地方标准。触发词："财评自审", "自己评一遍", "送审前检查", "/fund-review:quote-selfreview"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# quote-selfreview

与 `quote-review` 的区别：那个评审**外部**报价，这个评审**我们自己刚生成的**。
立场是替财评专家提问，然后检查我方能不能答上来。

## 每条发现三段式

```
reviewer_question   财评专家会怎么问 —— 用他们的语气和关注点
our_answer          我方准备的回答
answerable          现在答不答得上来
```

**`answerable=false` 才是真正的产出。** 报告里那些答得上来的条目是「已就位」，
答不上来的是「送审前必须补的工作」，报告开头单独列一节。

得分只是摘要，不要拿它当结论 —— 40 分和 60 分的差别远不如「7 项答不上来」重要。

## 用法

```bash
PYTHONPATH=$PLUG python3 $PLUG/quote_selfreview.py \
  --deal deals/<id> --bom bom --pack standard-packs/<region> \
  --modes delivery-modes/modes.yaml \
  --delivery-plan deals/<id>/delivery-plan.yaml
```

产出 `06-财评自审报告.md` + `review-findings.json`。

前置：`quote-generate` 已产出 `costing-result.json`。

## 检查族

| 族 | 检查什么 | 数据来源 |
|---|---|---|
| C1 计数规范 | BOM 门禁结论：状态、零 ILF、单一类型、跨系统重复 | `nesma_rules.run()` |
| C2 方法论 | 功能点是正向数的还是从人天反推的；rationale 覆盖率 | BOM `legacy_quote` / `nesma.rationale` |
| C3 范围 | 占位排除、待核价、本标准不覆盖的科目 | `costing-result.json` |
| C4 交付一致性 | R-1..R-8 | `delivery_matrix.check()` |
| C5 举证 | 询价门槛、数据模型人月举证、质保期 | `pack.procurement_evidence` |
| C6 负面清单 | 硬件条目是否命中 | `pack.check_negative_list()` |
| C7 费用科目 | 未计列的费用是否说明了原因 | `costing-result.json` |

## 条款引证按 pack_id 动态取

所有 citation 来自 `StandardPack`，**不硬编码任何地方标准**。
同一份报价切到广东包重跑，问题和引证会跟着换 —— 例如山东下「运维无科目」
在广东下不成立（广东有运维分册），C3-03 的措辞会随之变化。

## 几条重要的预设回答

这些是设计里已经想清楚、报告会自动带出的：

**「先有价还是先有功能点？」**
先数功能点。BOM 的 `legacy_quote` 是历史人天报价，明确标注「仅供交叉校验，
不得用于定价」。本次金额由功能点法正向推导。

**「这部分功能到底做不做？」**（占位条目）
未确认，故**未计入金额**。列而不计，不虚报。

**「运维怎么办？」**
本标准仅覆盖建设期。已在编制说明显式列出并说明需另行立项 —— **列而不计**，
而非遗漏。漏列才会被认为方案不完整。

## 注意

- 报告开头有免责声明：**这是预审，不是监管审计**。它模拟外部挑战，
  不构成合规结论。不要在对外材料里去掉这句
- 不要为了提分而修改判定阈值。得分低说明工作没做完，不说明检查太严
- 送审前应把 `answerable=false` 清零；清不完的要在商务侧有明确的应对口径
