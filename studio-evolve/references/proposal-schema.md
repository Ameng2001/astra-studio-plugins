# Proposal Schema — 原子提案格式、缺陷类型、分歧点记录

> 一切进化都归约为「对一类本体元素施加一种动作」的有限组合。表征先被约束，学习才可验证：
> 因为提案只能是这些原子编辑，每次变更才是原子的、可回滚的、可以在沙箱里对照检验的。
>
> 写到：`studio/changes/{ontology_id}/proposals/{n}.md`（`n` 自增；修订版写 `{n}.r{attempt}.md`）。

---

## 1. 原子编辑

```
ω ∈ {add, update, delete} × {OBJECT, LINK, FUNCTION, RULE, ACTION, MAPPING, CQ, EVIDENCE}
```

一份提案 `P = {ω₁, …, ω_k}`，`k` 通常 1–5。约束：

- **单一假设**：一份提案里的所有编辑服务于同一个缺陷假设（见 §3）。两个不相关的缺陷开两份提案，分别过闸门。
- **引用闭合**：`add ACTION` 若引用了新规则，同一提案里必须 `add RULE`；`delete OBJECT` 必须同时处理引用它的 LINK / MAPPING / ACTION.writes，否则 `ontology-validate` 的结构段会拦。
- **证据随行**：`add OBJECT` / `add MAPPING` / `add RULE(function)` 必须带 `add EVIDENCE`，或显式标 `TODO(FDE): 待探测`。
- **编辑写成 IR 行**：每个 ω 的 payload 就是 `ontology-map-schema.md` 对应段的一行表格（update 给前后两行，delete 给原行）。

---

## 2. 文件结构

```markdown
# Proposal {n}: {一句话假设}

- ontology_id: {id}
- round: {进化轮次}
- attempt: {1..3}
- window: {轨迹窗口标识}
- source_cases: [{case_id}, …]            # 本提案据以归因的案例（进内关卡的 Dᵢ）
- deficit_type: {§3 的一种}
- hypothesis: {把缺陷修掉后，哪些案例应从 fail 变 pass，为什么}
- expected_effect: { T: 哪一层会升, S: 哪些变体会一致, W: 证据率 / 场景覆盖怎么变 }
- failure_signature_checked: {是否核对过 growth-ledger 里的已拒签名，命中则不得提}

## Divergence（分歧点）
| case | step | agent_intent | ground_truth | deviation |
|------|------|--------------|--------------|-----------|
| … | 第几步 | 这一步智能体想做什么 | 真实答案 / 真实根因要求做什么 | 差在哪 |

## Edits（原子编辑）
### ω1 · add RULE
| name | severity | backing | evaluation | on_actions | intent | source | citations | check |
|…（IR §4 的一行）|

### ω2 · add EVIDENCE
| element | probe | sample | verified_at |
|…（IR §8 的一行）|

## Risk
- 可能伤到哪些 family / tier：…
- 是否改变已通过案例的路径：…

## Verdict（由 evolve-verify 回填）
- inner: PASS / FAIL（读数见 growth-ledger round {r}）
- outer: PASS / FAIL
- decision: ACCEPT / REVISE / REJECT
```

---

## 3. 缺陷类型（本体归因的落点）

归因的目标不是「智能体哪一步错了」，是「本体缺了什么让它错」。七类，每类对应的默认编辑：

| deficit_type | 症状（在轨迹里看到什么） | 默认编辑 |
|---|---|---|
| `MISSING_OBJECT` | 智能体反复探查原始数据找一个本体里没有的实体 | add OBJECT + add MAPPING + add EVIDENCE |
| `WRONG_LINK` | 多跳推理走错边、停早了或停不下来 | update LINK（改 edge_semantics / cardinality）或 add LINK |
| `MISSING_FUNCTION` | 智能体现场手算一个派生量，且多条轨迹算法一致 | add FUNCTION（intent 写口径） |
| `STALE_RULE` | 规则把正确动作拦了，或没拦住错误动作，且与真实根因矛盾 | update RULE（改 check / severity）或 delete RULE |
| `MISSING_GUARD` | 动作在不该执行的状态下被执行 | update ACTION（加 guards / post_rules）+ add RULE |
| `WRONG_MAPPING` | 查询编对了但落到了错的表 / 列 / 键 | update MAPPING + add EVIDENCE |
| `MISLEADING_SIGNAL` | 智能体被某个量的绝对幅度误导（噪声大的计数器、随负载波动的会话数） | add RULE（severity=soft，极性为负：不该单独作为依据）|

`MISLEADING_SIGNAL` 产出的规则是**负向规则**（反模式）：IR §4 的 `intent` 以「不应」开头，`severity` 取 `soft`，`evaluation` 取 `pre`。这是 OpsHarness 规则极性在五要素里的表达方式。

---

## 4. 从哪里学（两个方向）

**从判对的轨迹学共性**：同一 family 内多条 correct 轨迹反复出现的动作序列 → `add FUNCTION` 或 `update ACTION`（固化路径）；反复出现的观察 → 正向规则。固化阈值：出现在该 family 正轨迹的比例 ≥ `ρ`（默认 0.6），比例写进提案的 `hypothesis`。

**从判错的轨迹学对比修正**：对每条 wrong 轨迹，逐步比对「这一步的意图」与「真实答案要求的步骤」，第一个不一致的步骤即分歧点；分歧点之后被跳过的、正轨迹里有的步骤就是修复内容。有同 family 的 correct 轨迹时做成对比对，没有时只能从分歧点反推缺陷类型并标 `UNPAIRED`。

**由过时知识导致的失败**：本体里已有的元素与真实根因矛盾 → `update` 或 `delete`，不是再 `add` 一条打架的。

---

## 5. 失败签名

提案被 REJECT 时记录：

```
signature = (deficit_type, 涉及的本体元素名集合, 回退的维度, 回退的 family)
```

后续轮次的归因在写提案前必须核对 `growth-ledger.md` 的拒绝签名表；签名相同的提案不得重复提出，除非来源案例集合有新增。这是 EvoOntology 「记录被拒候选避免重复无效更新」的做法。
