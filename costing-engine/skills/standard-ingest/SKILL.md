---
name: standard-ingest
description: '把地方财评标准 PDF 摄取为结构化标准包（pack.yaml），每个参数带页码级 citation，并用标准自带算例与既有工作簿双重回归验证。支持多区域并存。触发词："摄取标准", "建标准包", "解析预算标准", "/fund-review:standard-ingest"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# standard-ingest

标准包只放**「换个省会变」**的东西：系数、费率、科目、举证要求、负面清单。
功能点清单在 `bom/`，交付决策在 `deals/` —— 三者正交，换省只改这一层。

## 硬约束：每个数值都要有 citation

`StandardPack.validate()` 在加载时递归检查 `fp_counting` / `factors` / `rates` /
`other_fees` / `procurement_evidence` / `fp_exclusions` 下的每个数值节点，
够不到 `citation` 就直接报错。

财评答辩要逐条可查。一个查不到出处的系数会拖垮整份报价的可信度 ——
所以宁可加载失败，也不接受降级运行。

`--verify-citations` 更进一步：把 `quote` 拿回 PDF 对应页比对（容忍跨页折行）。
页码写错或抄漏字时，现场翻到那页找不到原话，比没写 citation 更糟。

## 参数不做全自动抽取

一位小数点错了，全盘皆错。LLM 可以提候选，但必须**逐条人工确认**后写进
`pack.yaml`。本 skill 负责的是「写完之后能不能自证」，不是「替你写」。

## 用法

```bash
PLUG=<plugin>/scripts
export PYTHONPATH=$PLUG

# 1. PDF → 章节树 + 页锚点（供 citation 页码核验）
python3 $PLUG/standard_ingest.py parse --pdf <标准.pdf> --out standard-packs/<pack-id>

# 2. 人工编写 pack.yaml（参考 shandong-2024）

# 3. 校验：citation 完整性 + quote 可查 + 标准自带回归用例
python3 $PLUG/standard_ingest.py validate --pack standard-packs/<pack-id> --verify-citations

# 4. 全链路复算（有既有工作簿时）
python3 $PLUG/standard_ingest.py replay --pack standard-packs/<pack-id> --workbook <xlsx>
```

退出码：`0` 全过 ／ `1` 回归不通过 ／ `2` 结构性错误（缺 citation 等）

## 两层回归，缺一不可

| 层 | 来源 | 覆盖 |
|---|---|---|
| 标准自带算例 | `pack.yaml` 的 `regression` 段 | 通常只有费率类，如山东 p.11「800万 → 设计费 13.04万」 |
| **全链路复算** | 既有工作簿 `replay` | 功能点 → 调整后功能点 → 工作量 → 人月费率 → 费用 整条链 |

山东标准全文只有**一个**算例，覆盖不到主链。所以第二层是必需的 ——
拿一份已交叉验证过的工作簿回放，能一次性验证系数、费率、舍入口径全部正确。

## 公式档案不可合并

**不要**做「万能公式 + 系数置 1」。各地差的不只是取值，是**链路顺序**：

| | 山东 | 广东 |
|---|---|---|
| 规模变更因子 | 有（1.39/1.21） | **无此因子** |
| 复用系数作用位置 | 调整后功能点层 | **UFP 层** |
| 人月费率 | 基准 × 开发类别系数（0.8–1.1） | 固定 24000，**无系数** |
| 直接非人力成本 | 未单列 | **公式内显式加项** |

强行统一会让广东报价虚高 21%。每个 profile 是一段显式、可审计的计算步骤，
放在 `scripts/formula_profiles/<name>.py`，参数一律从 `StandardPack` 取，
不硬编码。

## 舍入

全链路走 `nesma_weights.xlround` —— 复刻 Excel 的 ROUND（half-up + 先规整到
15 位有效数字），不是 Python 内置的银行家舍入。财评会拿 Excel 复核，
口径不一致即被质疑。实测依据见 `p0-baseline/README.md`。

## 新增区域

1. `parse` 出 `standard.parsed.json`
2. 人工写 `pack.yaml`（每个值带 citation）
3. 判断能否复用现有 `formula_profile`；链路顺序不同就新写一个
4. `validate --verify-citations` 通过
5. 有工作簿就 `replay`

**BOM 零改动。** 这是三层分离的意义。
