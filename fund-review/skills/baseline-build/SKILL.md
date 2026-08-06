---
name: baseline-build
description: '第二层：BOM × 区域标准包 → 区域基准。产出逐条目 UFP/AFP/因子、分系统汇总、条款引用索引与版本锁，供第三层出报价复用；也可直接 diff 两份基准做跨区域分析。触发词："建区域基准", "算这个省值多少钱", "跨区域对比", "diff 基准", "/fund-review:baseline-build"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# baseline-build

三层里的第二层。与 `bom-build` 对称 —— 一个建 BOM，一个建区域基准。

| 层 | 命令 | 回答什么 |
|---|---|---|
| 一 BOM | `bom_build` | 我们这套解决方案里有什么、每一项多大 |
| **二 区域计算** | **`baseline_build`** | **这套产品在这个省、按全定制口径值多少钱** |
| 三 模式定制 | `quote_generate` | 这次卖给谁、怎么交付、做多大范围 |

## 用法

```bash
PLUG=<plugin>/scripts
PYTHONPATH=$PLUG python3 $PLUG/baseline_build.py \
    --bom bom --pack standard-packs/shandong-2024
# → baselines/shandong-2024@bom-0.16.0/

# 跨区域对比 —— 不重新构建，直接 diff 两份已有的基准
PYTHONPATH=$PLUG python3 $PLUG/baseline_build.py --diff \
    baselines/shandong-2024@bom-0.16.0 baselines/guangdong-2019@bom-0.16.0
```

| 参数 | 说明 |
|---|---|
| `--out` | 默认 `baselines/<pack_id>@bom-<version>` —— 名字本身说清这份基准是什么的函数 |
| `--as-of <版本>` | 按该 BOM 版本时点重算。**它属第一层的坐标**，所以挂在这里而非第三层 |
| `--counting-method` | 默认估算功能点法 |
| `--diff A B` | 对比两份已生成的基准 |
| `--allow-deprecated-pack` | 加载已退役的标准包 —— 仅历史复算 |

## 产物

| 文件 | 进 git | 内容 |
|---|---|---|
| `baseline.json` | ✅ | 逐条目 UFP/AFP/因子 + 分系统汇总 + 采购标的 |
| `baseline.lock.json` | ✅ | BOM 版本 + 标准包版本 + **输入文件哈希** + 源路径 |
| `citations.md` | ✅ | 本包每个取值 → 页码/章节/原文 |
| `区域基准清单.xlsx` | ❌ | 渲染物，二进制不可 diff，重跑即得 |

## 三条设计约束

**第二层不含任何商机决策。** 不知道交付形态、不知道范围、不知道客户是谁。
`build()` 刻意**不接 `DeliveryContext`** —— 传了就不是基准了。

因此同一个 (BOM 版本 × 标准包) 只需算一次给所有商机复用；也因此两份基准
**天生可比**，`--diff` 就是跨区域分析，不需要另一条平行路径。

**基准含占位条目**（`include_placeholders=True`）。占位是「已知的未知」，
基准要完整；是否计入报价由第三层决定（默认不计）。所以基准 1763 条明细、
报价 1738 条 —— 这个差是**预期的**，不是 bug。

**哈希的是输入文件不是产物。** 产物可以重算，输入变了才需要重算。
第三层出报价前会重算哈希比对，对不上就拒绝：

```
基准已过期，拒绝出报价：
  标准包已变（基准 deadbeef… → 现 b7184ce5…）
  请先重建基准：baseline_build --bom bom --pack standard-packs/shandong-2024
```

只记 `pack_id` 不够 —— 标准包改一个系数、pack_id 不变，基准就悄悄过期而报价照出。

## 生产率三档：由标准包声明，不替标准编造

单点值等于假装精确。但**不能给每个省都硬造区间**：

| 标准包 | 生产率 | 有无浮动依据 | 产出 |
|---|---|---|---|
| `guangdong-2019` | 6.65 | 有，明文 ±20% | 三档（上限是下限的 1.50 倍） |
| `shandong-2024` | 6.71（CSBMK P50） | 无，全文无「浮动/区间/P25/P75」 | 单点 + 说明 |

`pack.productivity_band()` 有 `rates.productivity_hours_per_fp.adjustable_range`
才返回三档，否则 `None`。

## --diff 的输出

比原 `standard_ingest compare` 多一栏 **科目支持差异** —— 换省时最容易出事的正是它：

```
| 交付形态 | 山东 | 广东 |
| 云资源租赁 | forbidden | allowed |
| 数据资源与数据模型购置 | allowed | not_in_scope |
```

某形态在一地有科目、另一地 `not_in_scope`，**报价算得出来但落不了账**。

## 注意

- 选定标准包时就会报词表缺口（`missing` / 本次 BOM 用到的 `unmapped`），
  不用等生成到第 800 行才 `PackError`
- 基准里的采购标的**只列不定价、不判形态** —— 形态是第三层的事
