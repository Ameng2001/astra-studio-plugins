---
name: evolve-attribute
description: Mine one window of runtime trajectories into atomic ontology edit proposals — label each trajectory from user feedback, split correct vs wrong, distil commonalities from correct runs, locate the divergence point of wrong runs and attribute it to one of seven five-element deficit types, then write a single-hypothesis proposal per deficit. The root-cause-attribution step of the self-evolving pipeline (RCA on the agent, blamed on the ontology). Use at the end of every evolution window, before evolve-verify.
allowed-tools: Read, Write, Glob, Grep, Bash, Agent
user-invocable: true
---

# Evolve Attribute

把一个进化窗口里的运行轨迹变成**原子提案**。思路是根因分析：症状在智能体的某一步，根因在本体缺了什么。本 skill 只负责找到分歧点、归到缺陷类型、写出提案；提案对不对由 `evolve-verify` 在沙箱里裁决，本 skill 不下结论。

规范必读：`${CLAUDE_SKILL_DIR}/../../references/proposal-schema.md`（缺陷类型、提案格式、失败签名）。

## Pre-check

1. 定位轨迹来源，二选一：
   - **A. 引擎审计日志**（首选）：clife-onto-engine 对每次 `execute` / `ask` 记录的动作、触发的规则、回滚、HIL 事件。路径从 `.studio-evolve/{id}/config.yaml` 的 `trajectory_source` 读。
   - **B. 会话轨迹**：智能体会话的工具调用序列。只有最终结果、没有逐步记录时用这个，报告标 `COARSE_TRAJECTORY`，归因精度降级。
2. 读 `.studio-evolve/{id}/trajectories/{window}/` 的反馈标签（`feedback.yaml`：每条轨迹 `correct / partial / wrong` + 真实答案或真实根因）。没有反馈的轨迹不参与归因（不猜标签）。
3. 读 `growth-ledger.md` 的拒绝签名表。
4. 读当前 IR 与 §8 Evidence，和 `cases/real/` 现有 family 清单。

## Workflow

### 1. 轨迹切块与标注
- 以一次 `ask` / `execute` 为一个块，块外的噪声丢弃。
- 给每块附标签 `(a, c)`：`a` 来自反馈，`c` 是成本（步数、token、秒）。
- `D⁺ = {a = correct}`，`D⁻ = {a = wrong}`；`partial` 的轨迹只用于成本统计，不进 `D⁺` 也不进 `D⁻`。
- **顺手把每条有反馈的轨迹落成真实案例**：`cases/real/{family}-{n}.yaml`，`origin: real`，标签即反馈给的真实答案。这是外关卡留出集的唯一来源，所以这一步不能省。

### 2. 从 D⁺ 学共性
- 同一 family 内多条 correct 轨迹反复出现的动作序列、派生量算法、观察 → 候选 `add FUNCTION` / `update ACTION` / 正向 `add RULE`。
- 固化阈值 `ρ`（默认 0.6）：出现比例低于阈值的不提，比例写进提案 `hypothesis`。

### 3. 从 D⁻ 学对比修正
对每条 wrong 轨迹：
- 逐步比对「这一步的意图」与「真实答案要求的步骤」，第一个不一致的步骤 = **分歧点**，填进提案的 Divergence 表。
- 有同 family 的 correct 轨迹 → 成对比对，分歧点之后被跳过的步骤就是修复内容；没有 → 标 `UNPAIRED`，只从分歧点反推缺陷类型。
- 归到七类缺陷之一（`proposal-schema.md` §3），取该类的默认编辑。
- 多条 wrong 轨迹指向同一缺陷 → 合并成一份提案，`source_cases` 列全。
- 本体已有元素与真实根因矛盾 → `update` / `delete`，不另 `add` 一条打架的。

### 4. 写提案
- 一份提案一个假设；编辑写成 IR 行；`add OBJECT / MAPPING / RULE(function)` 必带 `add EVIDENCE`（用 Bash 跑只读探测取样）或标 `TODO(FDE): 待探测`。
- **核对失败签名**：与 growth-ledger 拒绝表签名相同的，不写，报告里列为 `SUPPRESSED`。
- 写到 `studio/changes/{id}/proposals/{n}.md`；修订版（verify 退回的）写 `{n}.r{attempt}.md`，并把失败案例并入 `source_cases`。

### 5. 调专家复核
`ontology-evolver`（本插件 agent）审分歧点是否真是第一个不一致步骤、缺陷类型是否归对；`rule-engineer` 审新规则的极性、severity 与出处。

### 6. 报告
```
归因：{ontology_id} · 窗口 {window}
轨迹 N：correct {p} / wrong {w} / partial {x} / 无反馈 {u}（未参与）
新增真实案例 {k} 条 → cases/real/
提案 {m} 份：
  {n}: {deficit_type} · {一句话假设} · 来源案例 {…} · 编辑 {k} 个 · 证据 {有/TODO}
SUPPRESSED {s} 份（命中拒绝签名：…）
UNPAIRED {q} 份（无同 family 正例，精度降级）
轨迹来源：AUDIT_LOG / COARSE_TRAJECTORY
下一步：用户看提案 → /studio-evolve:verify {ontology_id} {n}
```

## Expected Inputs

一个窗口的轨迹 + 反馈标签 + 当前 IR + growth-ledger。

## Expected Outputs

`studio/changes/{id}/proposals/{n}.md`（若干）；`cases/real/` 新增案例；`.studio-evolve/{id}/attribute-report-{window}.md`。

## Out of Scope

- 不裁决提案对错（那是 `evolve-verify`）。
- 不改生产 IR、不编译。
- 不给没有反馈的轨迹猜标签。
