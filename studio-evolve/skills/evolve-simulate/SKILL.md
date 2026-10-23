---
name: evolve-simulate
description: Derive labelled competency cases from a grounded ontology — one or more cases per Rule (rejected), Action (committed), Link (multi-hop query) and Function (derived value), each with a difficulty tier, a scenario family, a private label and perturbation variants. Writes them to the sim case bank; sim cases feed the inner gate only. The simulation-augmentation step of the self-evolving pipeline. Use after evolve-ground, and again when the ontology structure changes.
allowed-tools: Read, Write, Glob, Grep, Bash, Agent
user-invocable: true
---

# Evolve Simulate

真实轨迹里的失败用完之后，进化会在第三四轮停下（EvoOntology 的观察）。仿真的作用是从本体结构本身派生出**带标签的案例**，让「长高」在真实失败耗尽后还有素材；同时这些案例是沙箱内关卡的测试集。

硬规则：**仿真案例只进内关卡**，`origin: sim` 的案例不得进入留出集。原因写在 `${CLAUDE_SKILL_DIR}/../../references/gate-rules.md` §2。

## Pre-check

1. 读 `studio/changes/$ARGUMENTS/ontology-map.md`，要求已有 §8 Evidence（没有则提示先跑 `/studio-evolve:ground`；允许跳过但报告标 `UNGROUNDED`，此时派生的案例标签可信度降级）。
2. 读 `{target_dir}/cq/golden.yaml`，已有的黄金问题不重复生成。
3. 读 `${CLAUDE_SKILL_DIR}/../../references/growth-metrics.md` 的「案例的扩展字段」与难度层级判法，和 `templates/case.yaml.tmpl`。
4. 读 `.studio-evolve/{id}/config.yaml`，取每类元素的派生数量上限（默认每条规则 2、每个动作 2、每条关系 1、每个函数 1）。

## Workflow

1. **按要素派生**，每条案例填齐 `q / expect / label / tier / family / origin / variants`：
   - **Rule（hard）→ `rejected(R)` 案例，tier 3**：构造一个违反规则的动作调用，标签是「被 R 回滚」。用 §8 的采样值造实参（真实存在的 site_id、真实不在名录里的草种），不用编的。
   - **Rule（soft / 负向）→ `query` 案例，tier 2**：构造一个会被误导信号引诱的问题，标签是正确答案不等于那个信号指向的答案。
   - **Action → `action(X)` 案例，tier 3；带 HIL 的 → tier 4**：合法实参、应 committed；带 HIL 的标签含「应触发复核」。
   - **Link（edge_semantics=root_cause / hypothesis）→ 多跳 `query` 案例，tier 2**：从边的 `from` 问到 `to`，多跳链超过两条边的标 tier 2 并在 family 里记链长。
   - **Function → `query` 案例，tier 1 或 2**：按 `intent` 的口径，用 §8 采样值算出标签；算不出的（function-backed 未填）标 `SKIPPED (FDE TODO)` 不生成。
2. **定 family**：取 process-flow 的决策点名或 event-storm 的 Hotspot 名；都没有时用「要素名 + 动作」。family 是留出集分层维度之一，粒度要和真实案例一致（看 `cases/real/` 已有的 family 取名）。
3. **生成扰动变体**（每条 2–3 个）：改写问法（同义）、换实体（§8 采样里的另一个真实实例）、注入无关字段（多给一个与规则无关的参数）。变体与原题共用标签。
4. **去重**：与 `golden.yaml` 和 `cases/sim/` 已有案例按 `expect + 涉及元素` 去重。
5. **写出** `.studio-evolve/{id}/cases/sim/{family}-{n}.yaml`。标签字段只在案例文件里，不出现在任何提示词模板中。
6. **调专家抽检**：领域专家（动态发现）抽 10% 看标签是否符合现实口径；不符的删，不改标签迁就本体。
7. **报告**：

   ```
   仿真案例：{ontology_id}
   新增 N 条（tier 1/2/3/4 = a/b/c/d；family 数 F）· 变体 V 个 · 去重丢弃 X · SKIPPED(FDE TODO) Y
   专家抽检：抽 S 条，删 R 条（原因：…）
   案例库现状：real {m} · sim {n}
   提醒：sim 只进内关卡。
   下一步：/studio-evolve:cycle {ontology_id}
   ```

## Expected Inputs

接地后的 IR + `cq/golden.yaml` + `config.yaml`。

## Expected Outputs

`.studio-evolve/{id}/cases/sim/*.yaml`；`.studio-evolve/{id}/simulate-report.md`。

## Out of Scope

- 不生成 `origin: real` 的案例（那只能来自真实轨迹 + 反馈）。
- 不跑案例（跑是 `evolve-verify` 的事）。
- 不为了凑数生成标签算不出的案例。
