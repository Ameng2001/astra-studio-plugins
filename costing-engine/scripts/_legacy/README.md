# 已归档的脚本

**归档 ≠ 删除。** 与标准包退役同一纪律：文件留着，但不在任何入口的可达路径上。

删掉的代价是万一要复现旧交付物就没法复现了；留在 `scripts/` 又会让人以为它还活着 ——
移到这里两个问题都解决。`scripts/` 在 PYTHONPATH 上，`_legacy/` 是子目录，
不会被当成顶层模块 import，所以不存在"归档了却还在被用"的中间状态。

## 归档依据

从真实入口（`skills/` + `commands/` + `agents/` + `tests/`）做可达性分析，
本目录下的脚本**没有任何入口能到达**，且只被彼此引用：

```
finalize_delivery  ← 入口零引用
  ├ fix_references     仅被 finalize_delivery 引用
  ├ md_to_docx         同上
  ├ naming_scheme      同上
  └ sanitize           同上
verify_closure     ← 入口零引用
  └ generate_project_summary   仅被 verify_closure 引用
auto_approve / verify_plan / generate_defense_kit /
generate_evidence_doc / generate_optimize_report / generate_todo_checklist
                   ← 均入口零引用、无引用者
```

全部为 2026-07-27 建插件时的初始提交，此后未改动过。

## 后续归档

**`excel_styler`**（2026-08-06 归档）—— 被 `gov_sheet` 取代。

它做的是**事后美化**：把写好的工作簿重新遍历一遍，猜出表头行、改字体、
描边、按列名关键字猜金额列。这套办法有个盖不住的盲区 —— 它只改长相，
看不见内容。实测同一批表里，`类型分布` 列的值是 Python dict 的 repr
（`{'EI': 66, 'EQ': 48}`）、`产品成熟度` 列是没翻译的 `existing`，
`excel_styler` 把它们排得整整齐齐地印进了要报财评的公文。

`gov_sheet` 把约束前移到**写入时**：容器进单元格报错、裸英文枚举报错、
明细行缺条款出处报错、合计行的活公式与引擎值对不上报错。
样式仍在（常量直接沿用自这里），但不再是唯一的职责。

`finalize_delivery.py` 里还有一行 `from excel_styler import style_workbook` ——
两个都在本目录，一起归档，不构成活引用。

## 要复活怎么办

`git mv scripts/_legacy/<name>.py scripts/`，然后**给它加入口** ——
技能文档或命令里引用它。没有入口的脚本迟早再次变成这里的住户。

## 不在此列的

- `run_optimize` / `run_review` / 12 个 `scan_*` / `expert_reviewer` /
  `derive_fp` / `mode_config` / `nesma_classifier` —— 同样入口不可达，但
  `quote-optimize` / `quote-review` 两个技能仍在（技能用散文描述扫描逻辑，
  不调这套确定性实现）。**是接回去还是一起归档，是产品决策，待定。**
- `bom_p2_proposal` / `bom_rationale` / `bom_restore_desc` —— BOM 建设期的
  一次性迁移工具，用过且有效，缺的是文档不是去留。
