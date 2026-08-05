---
name: bom-validate
description: '对造价 BOM 执行质量门禁 G-01..G-13，回答"当前版本能晋升到哪一级、还差什么"。检查功能点类型分布、ILF 缺失、描述可判定性、跨系统重复计列、成熟度举证等。触发词："校验BOM", "BOM质量门禁", "检查功能点拆分", "/fund-review:bom-validate"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# bom-validate

门禁不是通过/不通过两态，而是**按目标状态分级**：一条 draft 条目允许没有 rationale，但它想升到 reviewed 就必须补上。报告因此回答的是"能升到哪一级、还差什么"，而不是笼统报一堆红字。

## 用法

```bash
PYTHONPATH=<plugin>/scripts python3 <plugin>/scripts/bom_validate.py \
  --bom bom [--previous bom-released] --json bom/gate-report.json --md bom/gate-report.md
```

退出码：`0` 可升 released ／ `1` 可升 reviewed ／ `2` 只能停 draft ／ `3` 结构性错误（必须先修）

## 门禁与阻断层级

| 编号 | 检查 | 阈值 | 阻断 |
|---|---|---|---|
| G-01 | id 唯一、格式合法 | — | draft |
| G-02a | `nesma.type` ∈ 五类 | — | draft |
| G-02b | `nesma.rationale` 非空 | — | reviewed |
| G-02c | `nesma.reviewed_by` 非空（双人复核） | — | released |
| G-03 | 单一功能点类型占比（按 system） | >80% | reviewed |
| G-04 | 无 ILF 的 system | 存在即 | reviewed |
| G-05 | 描述字数 / 有无动作词 | <15 字或无动词 | released |
| G-06 | 跨 system 描述相似度 | ≥85% | released |
| G-07 | class 与计数方式一致 | — | draft |
| G-08 | `existing`/`partial` 须有 maturity_evidence | — | reviewed |
| G-09 | 声明需 GPU 但 BOM 无硬件条目 | — | released |
| G-10 | 相对上一 released 的 FP 漂移 | >15% 且无 CHANGELOG | released |
| G-11 | 名称在 system 内唯一 | — | released |
| G-12 | 占位条目未确认 | 存在即 | reviewed |
| G-13a | 采购条目缺科目 / 举证字段 | — | draft |
| G-13b | 采购条目无单价 | 存在即 | released |
| G-13c | 采购条目授权/计费方式仍是 TODO | — | reviewed |

## 各门禁的判断依据

- **G-03 / G-04** 是财评最容易攻击的两条。单一类型占比过高说明是**批量打标**而非逐条识别；
  应用必然维护逻辑文件，零 ILF 是**系统性漏计**（方向上是少算，不是多算）。
- **G-06** 对应各地标准的重复计列禁令（柳州 三(一)2.7；山东同精神）。
  实现用字符 3-gram Jaccard + 倒排索引，避免 O(n²)。相似度 100% 的跨系统条目须逐对裁决：
  是真重复（删一条），还是同名不同边界（补 rationale 说明差异）。
- **G-08** 是最高危的一条：既有功能按新开发报价会被挑战；反过来，有复用事实却不在
  复用度因子上体现，一旦被比对到原始清单同样会被质疑。两个方向都要防。
- **G-07 的两栖判定**：`KB` / `DATASET` 既可按功能点计（治理加工的开发工作量），
  也可按购置计（外部数据的访问权）。同一 class 下两种条目并存是**正常的**，
  但单条只能有一个口径 —— 同时有 `nesma` 与 `spec.subject` 即构成重复计列，阻断 draft。
  筛「按功能点计的条目」一律用 `is_fp_counted()`，不要写 `cls in FP_COUNTED_CLASSES`。
- **G-13 分三档**是因为这三件事的补齐时机完全不同：科目与举证清单是**编制时**
  就该定的（定不了说明没想清楚该报哪一行）；单价等商务询价单；授权方式（买断/期限/
  按年订阅）决定该条进建设期还是运营期，得在共创环节裁定。
  无单价的条目由引擎列为「待询价」而非计 0 —— 报表上 ¥0 与「没有这一项」长得一样，
  这两种状态必须能区分。

## 解读报告

`highest_reachable_status` 是核心结论。典型推进路径：

1. `draft` → 先清 G-01/G-02a/G-07（结构性，通常导入即通过）
2. `reviewed` → 需补齐 rationale（G-02b）、重拆类型分布（G-03/G-04）、补成熟度举证（G-08）
3. `released` → 需双人复核（G-02c）、清理重复（G-06）、规范命名（G-11）

**只有 released 版本可用于正式报价。** draft 版本可用于内部估算与方案讨论，
但产出的任何金额都必须标注「基于 draft BOM，未经复核」。

## 注意

- G-02b/G-02c 在首次导入后必然全量触发（源表没有判定理由与复核记录）。
  这是正常的 —— 它们量化了"从导入到可用还差多少人工"，不是 bug
- 不要为了让门禁变绿而批量填充 rationale 模板文本。rationale 的价值在于逐条说明
  判定依据，模板化填充等于把门禁变成摆设
