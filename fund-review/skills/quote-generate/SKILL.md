---
name: quote-generate
description: '三层合成生成报价输出物 —— BOM × 区域标准包 × 交付配置 → 建设期采购清单、功能点测算表、编制说明、版本锁。支持按历史 BOM 版本时点精确重算。触发词："生成报价", "出造价清单", "算一版报价", "/fund-review:quote-generate"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# quote-generate

把三层正交的输入合成为报价。引擎**不硬编码任何系数** —— 全部经 `StandardPack` 取，
每个取值都能追到页码级 citation。

| 层 | 来源 | 提供什么 |
|---|---|---|
| L0 | `bom/` | 有什么、多大（功能点类型、`app_type` 分类名、`dev_category` 分类名） |
| L1 | `standard-packs/<region>/` | 这个省怎么算（权重、系数取值、费率、费用科目、负面清单） |
| L2 | 命令行参数 / `deal.yaml` | 这次怎么卖（计数方法、项目类型、复用度、范围） |

## 用法

```bash
PLUG=<plugin>/scripts
PYTHONPATH=$PLUG python3 $PLUG/quote_generate.py \
  --bom bom --pack standard-packs/shandong-2024 \
  --out deals/<deal-id> --deal-id "<项目名称>"
```

| 参数 | 说明 |
|---|---|
| `--as-of <版本>` | 按该 BOM 版本时点重算 —— **历史报价复现** |
| `--include-placeholders` | 把占位条目计入报价（默认不计） |
| `--project-type` / `--reuse-level` | 新建 / 升级改造 |

## 输出

| 文件 | 内容 |
|---|---|
| `01-建设期采购清单.xlsx` | 按标准科目树组织，每行带计算依据与条款出处 |
| `02-功能点测算表.xlsx` | 参数页（带出处）+ 逐条目明细 + 测算汇总 |
| `05-编制说明.md` | 方法、参数取值与出处、项目类型、假设与边界、不覆盖范围 |
| `deal.lock.json` | 版本锁：BOM 版本 + 标准包 + 交付配置 + 合计 |
| `costing-result.json` | 机器可读全量结果 |

> 运营期清单与 TCO 属交付方式（P5）范围，本 skill 不产出。

## 三个设计约束

**Excel 只是渲染产物。** 所有数值由引擎算定后写入**静态值**，不依赖单元格公式。
这是从 P0 那个教训来的 —— 合并单元格击穿 `SUMIF` 少算了 ¥421.6 万。
测算表另附「复算公式」文本列供人工核验，但它不参与计算。

**按 (系统, 开发类别) 分组，不按系统取众数。** 同一系统内条目的 `dev_category`
可能不同，取众数会把「数据分析及加工」(0.8) 的条目按「大型行业软件开发」(1.1) 计价。

**占位条目默认不计入报价。** `tags: [placeholder]` 表示「已知的未知」，
把未确认的东西算进报价是虚报。编制说明会显式写明排除了多少条及原因。

## 时光回溯

`--as-of` 重建任意历史版本时点的 BOM 状态：条目在 `since` 引入、
在 `deprecated_in` 废弃，有效集合 = `since ≤ v` 且 `(未废弃 或 deprecated_in > v)`。

这是「条目永不物理删除」的兑现 —— 任何历史报价凭 `deal.lock.json` 都能精确重算。

已验证：`--as-of 0.9.0` 复现 ¥9,138,437.85，与 P0 基准逐位一致
（该数字此前已由 LibreOffice、独立 Python 复算、标准包全链路复算三路验证）。

## 编制说明里必须写清的三件事

生成时自动带出，不要删：

1. **项目类型与复用度** —— 引用标准原文说明 0.66 只适用于升级改造项目；
   BOM 的 `maturity` 是供应商产品成熟度，不参与造价
2. **排除了什么** —— 占位条目数量与原因、BOM 状态（draft/reviewed/released）
3. **本标准不覆盖的范围** —— 从 `delivery_mode_support` 自动列出 `not_in_scope` /
   `forbidden` 的交付形态。山东没有运维与租赁科目，这必须正面写明而非回避

## 注意

- 报价前先跑 `bom-validate`。基于 `draft` BOM 出的金额必须标注「未经复核」
- `--include-placeholders` 出的报价只能用于内部上限估算，不得对外
- 费用项的资格门槛由标准包声明（如山东系统集成费仅限大型行业/统一平台类项目），
  引擎自动判定并在清单中写明未计列原因
