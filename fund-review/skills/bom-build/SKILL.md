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

## 注意

- **不要**在导入时"顺手修正"看起来不合理的类型标注。忠实导入 + 门禁暴露，比静默修改可追溯得多
- 源数据异常（如层级列的 `合并`、`待定` 等编辑残留）**如实导入并记入 `anomalies`**，不静默清洗
- `legacy_quote`（人天、报价）仅供交叉校验，**不得用于定价**。定价必须从功能点正向推导
