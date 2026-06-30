---
name: ontology-map
description: Compile business analysis artifacts into the five-element ontology IR (ontology-map.md) — Object / Link / Function / Rule / Action + namespace + mapping + CQ. The FDE modeling step that turns DDD/event-storm analysis into a structured ontology intermediate representation ready to compile into a clife-onto-engine plugin. Use after studio-planner produces behavior-matrix / domain-canvas / process-flow, or run standalone on any domain analysis.
allowed-tools: Read, Write, Glob, Grep, Agent
user-invocable: true
---

# Ontology Map

把业务分析产物归一成 **五要素本体 IR**（`ontology-map.md`）——clife-onto-engine 的 Object / Link / Function / Rule / Action。这是 FDE（Forward Deployed Engineer）的建模动作：把散在事件风暴/行为矩阵/流程图里的对象、关系、动作、规则，抽成可编译的结构化中间表示。

完整字段规范、上游映射规则、完整性自检 → **必读 `${CLAUDE_SKILL_DIR}/../../references/ontology-map-schema.md`**。

## Pre-check

1. 确认 `studio/` 存在。
2. 读取上游分析产物（有则用，缺则用专家现场补）：
   - `studio/changes/{domain}/behavior-matrix.md` —— **首要来源**（Data Entity Map→Object，Action→Action，Data Out→Function）
   - `studio/changes/{domain}/domain-canvas.md` —— Owns→Object、Relationship Map→Link、core/supporting/generic 分层
   - `studio/changes/{domain}/processes/*.md` —— Decision Point 条件→Rule、状态流转→lifecycle
   - `studio/changes/{domain}/event-storm.md` —— Events→Action/事件、Hotspots→Rule、Decision Points
   - 若全缺：要求用户先跑 `/studio-planner:plan`，或仅凭一句业务描述启动（退化为现场建模）。
3. 确定 `ontology_id`（= 限界上下文 / domain-model 的插件候选名）与 `target_dir`（clife-onto-engine 仓库内落点，如 `plugins/{ontology_id}`）。

## Workflow

1. **加载专家**：用 Agent 工具调 `ontology-architect`（FDE，主导对象/关系/动作建模）+ `rule-engineer`（主导规则抽取与分类）+ 动态发现的领域专家（复核口径）。
2. **逐段抽取**（严格按 `ontology-map-schema.md` 的字段与上游映射规则）：
   - §1 Objects ← behavior-matrix Data Entity Map + domain-canvas Owns（判 Object vs 派生量）
   - §2 Links ← domain-canvas Relationship Map + 因果链（判 edge_semantics：root_cause/hypothesis/derivation）
   - §3 Functions ← 计算型 Data Out + KPI（只读派生量）
   - §4 Rules ← Decision Point 条件 + Hotspot + 标准/SOP（**rule-engineer 主导**：逐条问"hard/soft？declarative/function？依据是谁？"，挂 source/citations，function-backed 留 `TODO(FDE)` check）
   - §5 Actions ← behavior-matrix Action + Command（params/guards/post_rules/writes/hil）
   - §6 Mapping ← 来源系统 → store/table/key/columns/materialization
   - §7 CQ ← Decision Points + persona 高频问（至少含 1 query + 1 action + 1 rejected）
3. **专家复核**：让 ontology-architect 复核"对象边界与动作完整性"，rule-engineer 复核"规则分类与出处"，领域专家复核"口径是否符合现实约束"（Agent 子调用，多视角）。
4. **写出** `studio/changes/{ontology_id}/ontology-map.md`（按 schema 的七段结构）。
5. **完整性自检**：逐项核对 schema 末尾的 checklist，未达标的标 `TODO(FDE)`。
6. **报告**：五要素计数 + TODO 清单 + 下一步（`/studio-ontology:compile {ontology_id}`）。

## Expected Inputs

上游分析产物（behavior-matrix / domain-canvas / process-flow / event-storm），或一句业务描述（退化模式，靠专家现场补）。

## Expected Outputs

`studio/changes/{ontology_id}/ontology-map.md` —— 七段式五要素 IR，可被 `ontology-compile` 机械编译。

## Out of Scope

- 不写可执行代码（那是 `ontology-compile`）。
- 不实现 function-backed 规则函数体 / Action 回写逻辑（FDE 在编译后手填——本体建模的不可替代部分）。
- 不做业务分析本身（事件风暴/限界上下文是 studio-planner 的活，本 skill 消费其产物）。
