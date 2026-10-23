---
name: freeze-results
description: Generate every RQ's result tables and figures from formal ledger rows only, following the figure plan; check that each RQ has a table, no window or seed was cherry-picked, excluded runs are disclosed, small-n results stay out of main tables; freeze at the advisor gate (freeze.lock) and emit the evidence pack that studio-docs' academic-paper blueprint consumes. Use when every RQ has at least one formal run and before any prose is written.
allowed-tools: Read, Write, Bash, Glob, Grep, Agent
user-invocable: true
---

# Freeze Results

写作的唯一入口。表和图只从台账的 formal 行生成，不手填任何数字；冻结后再改实验视为新一轮（iteration + 1，旧结果保留）。产物是证据包：论点、RQ、冻结图表、差异表、claim 登记表骨架，交 studio-docs 写正文。

## Intent
- 让每个 claim 都能回指一张从台账生成的表。
- 触发：每个 RQ 至少一条 formal 行、无未处置 drift；准备写初稿时。

## Expected Inputs
- `ledger/runs.jsonl`（只读有效行：每个 run_id 取最新一行，void 剔除）
- `proposal/rq.yaml`（`rqs: [{id: RQ-1, question, hypotheses: [{id: H-1a, statement}]}]`）
- `proposal/figure-plan.md`，表格式：`| RQ | id | 类型 | 内容 | 预期形状 |`，如 `| RQ-1 | T1 | 表 | 各层通过率随轮次 | 预期上升 |`（类型 ∈ 表 / 图）
- `ledger/drift.md`（须写明「无偏离」）；`config.yaml` 的 `thresholds.n_min / seeds_min`

## Expected Outputs
- `results/tables/{RQ-n}.md`：每个 RQ 一张表，列 run_id / kind / metric / value / ci / n / seeds / commit / owner；caption 脱离正文可读（「表 Tn：RQ-n …；n=…，seeds=…，区间=95% CI」）
- `results/figures/{RQ-n}.csv`：figure-plan 里有「图」的 RQ 的长表作图数据；`results/figures/{RQ-n}-{Fn}.pdf|png` + `captions.md` + `plot-manifest.json`：由 `scripts/plot_figures.py` 出图（SciencePlots `science,ieee,no-latex` 样式，缺则回退内置样式并标明），不筛行不平滑；脚本哈希与样式进 figures_hash，图表同冻结
- `results/exploratory.md`：探索性结果单列（附「为何不是 formal」），永不进主表
- `results/coverage.md`：RQ → 表号、formal / exploratory / failed 计数、被排除 run 与理由、阻塞项
- `results/build.json`（生成时台账哈希）· `results/freeze.lock`（tables_hash / figures_hash / ledger_hash / ts / by / iteration）
- `results/evidence-pack/`：thesis.md · nearest-diff.md · rq.yaml · tables/ · figures/ · coverage.md · exploratory.md · freeze.lock · claims-skeleton.md

## Preconditions
- `gates.criteria == approved`；每个 RQ ≥ 1 formal 行；drift.md 无未处置项。

## Workflow
脚本在 `${CLAUDE_SKILL_DIR}/scripts/`，在项目根（`.studio-paper/` 所在目录）执行。

1. 生成表：`python3 ${CLAUDE_SKILL_DIR}/scripts/build_tables.py <paper-id>`
   - 只取 `status == "formal"` 行，按 rq 分组（一条 run 挂多个 RQ 时进多张表）；每次全量重建。
   - 本轮已冻结（freeze.lock.iteration == status.iteration）时拒绝重建。
2. 覆盖检查：`python3 ${CLAUDE_SKILL_DIR}/scripts/coverage_check.py <paper-id>`；exit 2 = 有阻塞项。检查：
   - 每个 RQ ≥ 1 formal 行，且 figure-plan 为它规划了表 / 图（有 RQ 无表 → 阻塞）
   - formal 行 n ≥ n_min；main / ablation 的 `fairness.seeds` ≥ seeds_min；formal 行无 `confounded`
   - 同一 RQ 的 formal 行共用一个 metric 名（多个 → 疑似挑窗 / 挑指标）
   - 每个被作废的 run 最新一行有 `void_reason`；drift.md 含「无偏离」；表由当前台账生成
3. 有阻塞项：逐条告诉学生怎么改——台账只追加，修正 = `python3 ${CLAUDE_SKILL_DIR}/../../scripts/ledger_io.py <paper-id> void <run_id> --reason …` 再经 record-run 重登；然后回到第 1 步。不得手改表。
4. 无阻塞项：调 `research-advisor` 预审，输入 coverage.md + tables/ + exploratory.md，对照 `${CLAUDE_SKILL_DIR}/../../references/validity-checklist.md` 的「实验 → 写作」一行（只有成功 run？有 RQ 无表？挑窗挑种子？）。
5. 走审批闸门（下节）。导师选「确认」并给出姓名后：
   `python3 ${CLAUDE_SKILL_DIR}/scripts/coverage_check.py <paper-id> --freeze --by <导师姓名>`
   → 写 freeze.lock、`gates.results = approved`（hash = tables_hash）、stage → writing、打包 evidence-pack。
6. 提示下一步：`/studio-docs:generate`（academic-paper blueprint，输入 = `results/evidence-pack/`）。

## Approval Gate

本技能含一个导师签字闸门（D7 结果冻结）。产出草稿后、写入最终产物前：

1. 用一页摘要呈现草稿，并附 `research-advisor` 的预审意见（放行 / 不放行 + 理由）。
2. 请导师明确选择：

> **审批检查点：结果冻结**
>
> {产物摘要 + 预审意见}
>
> 请选择：
> - ✅ **确认** — 签字放行，写入产物并记录到 status.json（approved_at / approved_by / hash）
> - ✏️ **修改** — 需要调整（说明哪里改），改后重新呈现
> - ❌ **拒绝** — 重做（说明原因），草稿作废

3. 签字是人的动作，插件不得代签；`status.json` 的 `gates.results` 未到 `approved` 时，下游技能拒绝以它为前置。
## Out of Scope
- 不写正文；不手填数字；冻结后不改表（改 = 新一轮）。

## Example

```
$ python3 …/build_tables.py demo
✓ 表 2 张 → .studio-paper/demo/results/tables
✓ 作图数据 1 份 → …/results/figures
$ python3 ${CLAUDE_SKILL_DIR}/scripts/plot_figures.py evo            # 出图：pdf+png + captions.md + plot-manifest.json
$ python3 …/coverage_check.py demo
RQ-1: formal=3 exploratory=0 failed=0 表=T1/F1
RQ-2: formal=2 exploratory=0 failed=1 表=T2
❌ 3 个阻塞项：
  - RQ-2：formal 行用了多个指标 ['C_tok', 'T_tier3'] —— 评测协议不一致，疑似挑窗/挑指标
  - r-2026-10-20-0002：ablation 的 fairness.seeds=2 < seeds_min=3
  - r-2026-10-20-0003：void 行缺 void_reason
# void + 重登 → 重跑 build_tables → 无阻塞 → 预审 → 王老师在检查点选「确认」
$ python3 …/coverage_check.py demo --freeze --by 王老师
🔒 已冻结 → …/results/freeze.lock   gates.results=approved by 王老师
📦 证据包 → …/results/evidence-pack
```

表 caption 示例：**表 T1：RQ-1 各层通过率随轮次（预期上升）；n=60，seeds=3，区间=95% CI；仅含 formal 行（3 条），来源 ledger/runs.jsonl。**

