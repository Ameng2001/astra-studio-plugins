---
name: bom-build
description: '从人工梳理的 xlsx 建设清单构建造价 BOM（唯一事实源 / TOS）—— 区域无关、交付无关的功能点与产品事实。产出 `bom/items/*.yaml` + 构建报告。触发词："构建BOM", "生成造价清单事实源", "导入建设清单", "/fund-review:bom-build"。'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# bom-build

把 Excel 清单转成结构化 BOM。**Excel 从此只是渲染产物，不再是事实源** —— 公式依赖单元格布局，合并/留空即失效且无校验（实测案例见 `p0-baseline/README.md`：合并单元格击穿 SUMIF，少算 ¥421.6 万）。

## 输入

| 参数 | 说明 |
|---|---|
| `--fp <xlsx>` | **条目主源**：功能点测算工作簿。已拆到功能点粒度，提供层级、名称、描述、NESMA 类型 |
| `--list <xlsx>` | **成熟度来源**：建设清单工作簿。提供「是否已有 / 当前开发情况 / 复用%」及人天报价 |
| `--out <dir>` | BOM 输出目录 |
| `--version` | 语义化版本，默认 `0.9.0` |

⚠️ `--fp` 必须传**已修复合并单元格的版本**。若某 sheet 的「系统/子系统」列存在合并区，
脚本会在遇到空值时直接报错并提示 —— 不要绕过，先修表。

## 前置检查

1. 确认 `--fp` 工作簿每张清单 sheet 的「系统/子系统」列逐行有值（无合并单元格）
2. 确认 `--list` 各 sheet 第 2 行是表头（脚本按表头名定位列，不依赖列号 —— 各 sheet 列结构并不统一）

## 步骤

```bash
PYTHONPATH=<plugin>/scripts python3 <plugin>/scripts/bom_build.py \
  --fp <功能点测算-修正版.xlsx> --list <建设清单.xlsx> --out bom --version 0.9.0
```

脚本内部：

1. **建立成熟度索引** — 扫 `--list` 各 sheet，层级列向下填充，登记全深度键与各级前缀键
2. **逐行导入条目** — 从 `--fp` 读 NESMA 类型、层级、描述
3. **关联成熟度** — 按 `(sheet, 分节, L1, L2, L3)` 逐级回退匹配。
   关联键须归一化（去空白 + 统一全半角括号）—— 两表同名层级写法并不一致
4. **名称兜底** — 源表约 45% 的「功能点名称」是描述的机器截断，识别后退回最深层级名
5. **运营期触发器** — 按文本证据标记 `needs_inference_gpu` / `calls_external_llm`，
   记录命中关键词到 `runtime.evidence`，`reviewed=False`。**这只是候选，需人工复核**
6. **硬件条目** — 硬件 sheet 转 `class: HARDWARE`，保留询价规格，不走功能点法
7. **写出分片 + `build-report.json`**

## 输出契约

- `bom/VERSION` / `bom/taxonomy.yaml` / `bom/items/*.yaml`（按 system 分片）
- `bom/build-report.json` — 关联覆盖率、名称兜底数、运营期触发器数、源数据异常

## 验收

必须逐项核对，任一不满足即返工：

| 项 | 标准 |
|---|---|
| 条目数 | 等于源表功能点行数 + 硬件行数 |
| **UFP 对账** | BOM 的 UFP 合计与逐系统值须与源表**逐系统零差异** |
| 成熟度关联覆盖率 | ≥95%；未命中样本须逐条能解释 |
| 结构校验 | `Bom.validate()` 无异常 |

UFP 对账是最关键的一条 —— 它证明这是**忠实导入**而非重新判定。
导入阶段不改变任何功能点类型；类型的修正属 `bom-validate` 暴露、P2 重拆解决。


## BOM 演进工具（导入之后才用得上）

`bom_build` 只做**忠实导入**。导入之后的每一次结构性演进都由下面这组工具完成，
每次产出一个新版本号并记 CHANGELOG —— **没有一个会就地改 BOM 而不留痕**。

| 工具 | 做什么 | 何时用 |
|---|---|---|
| `bom_p2_proposal` | 依标准识别规则**测算**重拆方案，产出提案与裁决清单 | `bom-validate` 报出 G-03/G-04（批量打标、零 ILF）之后 |
| `bom_apply` | 把调整落成一个新版本（A/ADJ/E/F 四类） | 提案经人工裁决之后 |
| `bom_rationale` | 用规则引擎独立重判，为一致的条目补 `rationale` | G-02b 大面积缺判定理由时 |
| `bom_restore_desc` | 从源工作簿**回源**恢复被机械切分的描述 | G-05 报出大量过短描述时 |

### 它们共同的一条纪律：不替产品做判断

```bash
PLUG=<plugin>/scripts

# 1) 测算重拆方案 —— **只提案不改 BOM**，每项带标准条款、置信度、方向（增/减）
PYTHONPATH=$PLUG python3 $PLUG/bom_p2_proposal.py --bom bom --out bom/p2-proposal
#    A 数据功能折叠(减) B 数据功能完整性(增) C 补齐缺失 ILF(增)
#    D 机械碎片合并(减) E AI 资产重拆(增)

# 2) 人工裁决后落版本
PYTHONPATH=$PLUG python3 $PLUG/bom_apply.py --bom bom --adjustment F     --spec bom/p2-proposal/F-adjudicated.csv --new-version 0.13.0 [--dry-run]

# 3) 补 rationale —— 只在规则引擎重判与现有类型**一致且无竞争规则**时生成
PYTHONPATH=$PLUG python3 $PLUG/bom_rationale.py --bom bom --version 0.14.0 [--dry-run]

# 4) 回源恢复描述 —— 需要源工作簿
PYTHONPATH=$PLUG python3 $PLUG/bom_restore_desc.py --bom bom \
    --source raw-input/<建设清单>.xlsx --version 0.15.0 [--dry-run]
```

**每一个都支持 `--dry-run`，先看再落。**

> `--spec` 曾经在传给 A/F 时被**静默丢弃** —— 命令照跑，结果与不传完全一致，
> 于是「按裁决结果落库」实际是「按规则重跑」，输出还看不出差别。
> 现在四条路径都会硬报错：F 传 spec 拒绝、E 缺 spec 拒绝、spec 文件不存在拒绝。

### 三条容易被绕过的边界

**`bom_p2_proposal` 只提案。** 它给出的每项调整都带置信度（高＝标准条款可直接判定 /
中＝规则推导需实体确认 / 低＝需领域判断）和方向。低置信度的必须人工裁决，
不要因为"看起来对"就整批 apply —— 提案的价值在于它把判断暴露出来给人做。

**`bom_rationale` 是印证，不是编理由。** 它只在规则引擎**独立重判**后与现有类型
一致、且无竞争规则时才生成，生成的 rationale 自带出身声明、`counted_by` 记
`rule-corroborated@<版本>`，与人工撰写的可区分。不一致或有竞争规则的一律不生成，
导出待人工清单。

给既有结论倒着编一个理由，等于把 G-02b 变成摆设。而且 G-02c（双人复核）不受影响，
仍全量阻断 released —— **印证回答"有没有理由"，人工复核回答"理由对不对"**，两件事。

**`bom_restore_desc` 是回源，不是重写需求。** 那些残片（「支持待办事项。」）不是
描述写得简，是源表把「建设详情」单元格机械切碎的产物，**原文还在源表里**。
所以是按层级键定位父级原文、切成语义段、用字符重合度把残片对回所属段落。

对不齐任何分段的（如从「男女人数」中间切断的「男」「女」两片）判为机械碎片、
标记待合并，**不硬补描述** —— 给一个本不该独立存在的条目补描述，
等于把切分错误固化下来。

## 注意

- **不要**在导入时"顺手修正"看起来不合理的类型标注。忠实导入 + 门禁暴露，比静默修改可追溯得多
- 源数据异常（如层级列的 `合并`、`待定` 等编辑残留）**如实导入并记入 `anomalies`**，不静默清洗
- `legacy_quote`（人天、报价）仅供交叉校验，**不得用于定价**。定价必须从功能点正向推导
