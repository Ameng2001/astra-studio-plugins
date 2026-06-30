---
name: ontology-compile
description: Compile the five-element ontology IR (ontology-map.md) into a runnable clife-onto-engine plugin — schema registration + rule/action handler skeletons + mapping YAML + CQ + manifest. Fills the 7 SPI slots. Declarative rules are fully generated; function-backed rules and Action write-back become skeletons with TODO(FDE). Use after ontology-map.
allowed-tools: Read, Write, Glob, Grep
user-invocable: true
---

# Ontology Compile

读 `ontology-map.md`（五要素 IR），机械编译成一个 **clife-onto-engine 插件**（Python 注册 + 规则/动作 handler 骨架 + 映射 YAML + CQ + 清单）。纯输出，无头脑风暴。

完整逐要素编译规则（IR 行 → Python/YAML，含 declarative 全自动 / function-backed 骨架 / Action 骨架 / HIL predicate / 映射 / CQ）→ **必读 `${CLAUDE_SKILL_DIR}/../../references/compile-rules.md`**。

## Pre-check

1. 读 `studio/changes/$ARGUMENTS/ontology-map.md` —— 必需。缺则让用户先跑 `/studio-ontology:map`。
2. 读 IR 第 0 段，取 `ontology_id`、`bounded_context`、`target_dir`。
3. 读 `${CLAUDE_SKILL_DIR}/../../references/compile-rules.md`（编译规则）。
4. 读模板 `${CLAUDE_SKILL_DIR}/../../templates/` 下的 `plugin.yaml.tmpl`、`cq.yaml.tmpl`。

## Workflow（严格按 compile-rules.md）

1. **建目录** `{target_dir}/`（及 `mappings/`、`cq/`），不删任何已有文件。
2. **生成 `__init__.py`**：固定头部 → §1 Objects 注册 → §2 Links 注册 → §3 Functions（骨架+TODO）→ §4 Rules（declarative 全自动 / function 骨架+source/citations）→ §5 Actions（@spi.action + handler 骨架）→ seed 桩 → `spi.load_mappings(...)`。
3. **生成 `mappings/objects.yaml`** ← IR §6。
4. **生成 `cq/golden.yaml`** ← IR §7。
5. **生成 `plugin.yaml`** ← 清单。
6. **幂等**：目标文件已存在 → 不覆盖，告警 + 给 diff 建议；`modify` 模式只重生成本轮变更要素。
7. **报告**（按 compile-rules.md 末尾格式）：五要素计数 + TODO 清单（待填的 function-backed 规则 / Action handler / 无依据规则 / seed）+ 验证下一步。

## Key Rules（摘自 compile-rules.md，详见该文）

- **只 import `clife_onto_engine.sdk`**（红线，绝不碰内核内部）。
- **declarative 规则**：从 IR 的 `check` 直接生成完整函数体。
- **function-backed 规则**：生成骨架 + `raise NotImplementedError` + docstring(intent) + **保留 source/citations**（供 OKF 导出）+ `TODO(FDE): 查…`。
- **Action handler**：为每个 `writes` 对象生成 `ctx.stage_write(...)` 骨架注释 + `set_confidence` + TODO。
- **HIL `when`**：`confidence<X` → `predicate=lambda confidence, touched_hard: confidence < X`；`severity_touched==hard` → `lambda c,h: h`。
- **无 source 的规则**：docstring 标 `# TODO(FDE): 补依据`。

## Expected Inputs

`studio/changes/{ontology_id}/ontology-map.md`。

## Expected Outputs

`{target_dir}/`（clife-onto-engine 插件）：`__init__.py` + `mappings/objects.yaml` + `cq/golden.yaml` + `plugin.yaml`。

## Out of Scope

- 不实现 function-backed 规则函数体、Action 回写逻辑、参考数据 —— 生成骨架与 `TODO(FDE)`，由 FDE 在 clife-onto-engine 仓库内手填。
- 不改 clife-onto-engine 内核（只产插件）。
- 不跑 CQ 验证（那是 ontology-validate / 在引擎仓库内跑）。
