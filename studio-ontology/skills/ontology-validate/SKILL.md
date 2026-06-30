---
name: ontology-validate
description: Validate a compiled clife-onto-engine ontology plugin — structural completeness (five-element refs resolve), governance audit (every rule has source/citations), remaining FDE TODOs (function-backed rules / action handlers still stubbed), and behavioral CQ (run the golden Competency Questions on the engine once FDE filled the skeletons). Closes the FDE loop after ontology-compile.
allowed-tools: Read, Glob, Grep, Bash
user-invocable: true
---

# Ontology Validate

校验 `ontology-compile` 产出的本体插件，闭合 FDE 环。两层校验：**结构/治理**（编译后即可跑，不需 FDE 填洞）+ **行为 CQ**（FDE 填完骨架后跑黄金问题集）。

## Pre-check

1. 定位被校验的插件目录 `{target_dir}/`（含 `__init__.py` + `mappings/` + `cq/golden.yaml` + `plugin.yaml`）。
2. 读 `studio/changes/{ontology_id}/ontology-map.md`（IR，作为期望基准）。
3. 确认 clife-onto-engine 可用（`{target_dir}` 应在引擎仓库的 `plugins/` 或可 import 到引擎）。

## Workflow

### A. 结构完整性（静态，always）
逐项检查（任一不过 → 报告为 BLOCKER）：
- 每个 Action 的 `guards` / `post_rules` 引用的规则都在 §4 已声明；`writes` 的对象都在 §1 已声明。
- 每条 Link 的 `from` / `to` 都是已声明 Object。
- 每个 Function 的 `reads` 都是已声明 Object。
- 五要素无悬空引用（orphan ref）。

### B. 治理审计（静态，always）—— 对齐 OKF 来源可查
- **每条 Rule 必须有 `source` + `citations`**。缺者列为"治理缺口"（`TODO(FDE): 补依据`），输出清单。
- 合规/对外凭证/定级定价类 Action 应设 `hil`；未设者告警。
- 这一步等价于 clife-onto-engine `export_okf` 的"无引用规则审计"，但在交付前就拦。

### C. 剩余 FDE TODO（静态，always）
- 扫描 `__init__.py`：仍 `raise NotImplementedError` 的 **function-backed 规则**与 **Action handler** → 列为"待 FDE 填"。
- `seed_reference_data` 仍为 `pass` → 告警（function-backed 规则查不到参考数据）。
- 输出：`declarative 全自动 X / function 待填 Y / Action 待填 A / seed 待填`。

### D. 行为 CQ（动态，FDE 填完后跑）
读 `cq/golden.yaml`，逐条在 clife-onto-engine 上验证（用 `Bash` 跑引擎仓库内的 import + execute）：
- `expect: query` → 该问应能编成/跑通一个 OQL 查询。
- `expect: action(X)` → `engine.execute(ontology, X, params, actor)` 应 `committed`。
- `expect: rejected(R)` → 应被规则 `R` 回滚（验证本体兜底）。
- 若插件仍有未填的 function-backed 规则/handler，相关 CQ 标记为 `SKIPPED (FDE TODO)` 而非 FAIL。

> 示例（在引擎仓库内）：
> ```bash
> python -c "import plugins.{ontology_id}; from clife_onto_engine import ActionEngine; ..."
> ```

## Report

```
本体插件校验：{target_dir}/
A. 结构完整性：✅ / ❌（列悬空引用）
B. 治理审计：规则 R 条，缺依据 Z 条 → [乡土合规 已补 / xxx 待补]
C. 剩余 FDE TODO：function 规则待填 Y、Action handler 待填 A、seed 待填
D. 行为 CQ：通过 P / 跳过 S(FDE TODO) / 失败 F → 逐条列出
结论：可交付 / 待 FDE 收尾（A、B 必须全过；D 在 FDE 填完后全绿才算闭环）
```

## Expected Inputs

编译产物 `{target_dir}/` + IR `ontology-map.md` + 可用的 clife-onto-engine。

## Expected Outputs

校验报告（结构/治理/TODO/CQ 四段）。不修改插件，只诊断 + 给修复指引。

## Out of Scope

- 不替 FDE 填 function-backed 规则体 / Action handler / 参考数据（只指出缺口）。
- 不改 clife-onto-engine 内核。
