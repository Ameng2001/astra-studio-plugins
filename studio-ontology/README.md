# studio-ontology

> **FDE pipeline** — 把业务分析编译成一个**可运行的数智本体**（clife-onto-engine 的五要素 OAG 插件）。
> astra-studio 的 DDD/事件风暴建模负责"业务→分析"，本插件负责"分析→可执行本体"，clife-onto-engine 负责"运行"。

这是 Palantir 那条 **Foundry/AIP 建模 → FDE → 本体运行时** 的中国版补位：astra-studio 是建模工作室，
clife-onto-engine 是运行时内核，`studio-ontology` 是夹在中间、把 DDD 模型编译成五要素本体的**FDE 工作台**。

## 定位：DDD ↔ 本体五要素

| DDD / 事件风暴（astra-studio 已产出） | 本体五要素（clife-onto-engine） |
|---|---|
| 聚合 / 实体 | **Object** |
| 聚合间关系 / 上下文集成 | **Link**（带 root_cause/hypothesis/derivation 边语义） |
| 命令 / 领域事件 | **Action**（受治理的写，带 guard/post-rule/HIL） |
| 读模型 / KPI | **Function**（只读派生量） |
| 不变式 / 策略 Policy | **Rule**（写后强制，带 source/citations） |
| 限界上下文 | **namespace / ontology_id** |

## 两个核心产物（编译脊梁）

| 文件 | 是什么 |
|---|---|
| [`references/ontology-map-schema.md`](references/ontology-map-schema.md) | **IR 五段式规范** —— 七段结构（对象/关系/函数/规则/动作 + 映射 + CQ）+ 每段的字段定义、上游映射规则、编译去向 |
| [`references/compile-rules.md`](references/compile-rules.md) | **编译规则** —— IR 行 → clife-onto-engine 插件（Python 注册 + 规则/动作 handler 骨架 + YAML），declarative 全自动 / function-backed 骨架 |

## 输入：两种入口（二选一）

| 入口 | 适用 | 来源 |
|---|---|---|
| **A. studio-planner 产物** | 已跑过事件风暴/领域建模 | behavior-matrix / domain-canvas / process-flow / event-storm |
| **B. 领域 Intake 标准 spec** | 没跑 planner，FDE 直接访谈/交业务方填 | [`templates/domain-intake.md.tmpl`](templates/domain-intake.md.tmpl) |

**Intake 标准 spec** 是给 FDE / 业务方 / 领域专家 / 规则工程师的**标准输入契约**：11 段（业务概述·角色·对象·关系·动作·指标·**规则**·决策点·数据源·能力问题·治理），每段标注谁来填、编译成哪个本体要素，带草业示例 + 完成度自检。填完即可喂给 `ontology-map`。两入口的 →IR 映射见 `skills/ontology-map/SKILL.md`。

## 用法

```bash
# 前置：studio-planner 已产出 behavior-matrix / domain-canvas / process-flow（或现场建模）
/studio-ontology:model {ontology_id}     # 流水线：map → 用户审 IR → compile
# 或分步：
/studio-ontology:map {ontology_id}       # 业务分析 → 五要素 IR（studio/changes/{id}/ontology-map.md）
/studio-ontology:compile {ontology_id}   # IR → clife-onto-engine 插件（{target_dir}/）
```

## 管线

```
业务分析(复用 studio-planner) → ontology-map(IR 五段) → 用户审 → ontology-compile(可运行插件骨架)
   → FDE 收尾(填 function规则体/Action回写/参考数据) → ontology-validate(结构/治理/CQ) → 闭环
```

三个 skill：`ontology-map`（建 IR）· `ontology-compile`（编译插件）· `ontology-validate`（校验闭环）。

## 专家 agent

- **ontology-architect**（FDE）：对象/关系/动作建模，OAG 纪律守门。
- **rule-engineer**：规则抽取与分类（hard/soft · declarative/function · 出处），把"专家经验→可执行不变式"。

## 诚实边界

编译只保证**结构对、五要素齐、可 import**。**function-backed 规则函数体、Action 回写逻辑、参考数据**是 FDE 的手艺活，生成骨架 + `TODO(FDE)`，由人在 clife-onto-engine 仓库内手填——这正是 FDE 不可替代之处，不假装全自动。

## 与 clife-onto-engine 的关系（上下游）

studio-ontology 是**建模端**，[**clife-onto-engine**](https://github.com/Ameng2001/clife-onto-engine) 是**运行时端**。

```
astra-studio (建模工作室)                          clife-onto-engine (OAG 运行时内核)
  studio-planner ──┐                                  ┌── plugins/<ontology_id>/  ← 编译产物落点
  domain-intake  ──┤→ studio-ontology ──编译──▶───────┤   五要素：Object/Link/Function/Rule/Action
                   │   (IR→插件→校验)                  └── Session.ask / HTTP API / Docker
```

- **下游运行时**：[github.com/Ameng2001/clife-onto-engine](https://github.com/Ameng2001/clife-onto-engine) —— 编译出的插件在其 `plugins/<ontology_id>/` 里跑成受治理的 OAG（做/查、本体兜底、审计、回滚），有 HTTP API / CLI / Docker 三种使用面。
- **端到端验证**：本插件已用真草业材料跑通，产物与 clife-onto-engine 手写的 `plugins/grass` **五要素同构**。

## 依赖

- 上游：`studio-core`（工作区）；消费 `studio-planner` / `studio-insight` 的分析产物，或直接吃 `domain-intake.md`。
- 下游：clife-onto-engine（运行时）。
