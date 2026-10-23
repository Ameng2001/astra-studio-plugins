---
name: propose-experiments
description: Turn a signed thesis into numbered research questions and hypotheses with pre-registered pass/fail criteria, sample-size and interval requirements, data-contamination statement, baseline list with fairness budget, and a figure plan per RQ — then freeze them (hash + timestamp + git commit) at the advisor gate. --amend changes criteria with a logged reason while keeping old versions. Use right after the thesis is signed, and whenever pilot results force an RQ change.
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
user-invocable: true
---

# Propose Experiments

开题的产物不是 PPT，是一份能被哈希的判据文件。RQ-n / H-n 编号是全链路外键（台账、评审、回应表都靠它）；判据写下并冻结之前不许跑正式实验（record-run 据 `criteria.lock` 拒收 formal）。改判据走 `--amend`：旧版本保留，谁 / 何时 / 为何留痕。

## Intent
- 堵住「事后改判据」：判据与冻结哈希绑定，正式 run 只认冻结后的版本。
- 触发：论点句签字后；pilot 或基线复现推翻初版 RQ 时（`--amend`）。

## Expected Inputs
- `positioning/thesis.md`（已签字）
- 学生草稿：RQ、假设、数据集与划分、基线、评测协议与种子数

## Expected Outputs
- `proposal/rq.yaml` —— `rqs.<RQ-n>.{question, thesis_part, hypotheses.<H-na>.{statement, direction, metric}}`（样例 `${CLAUDE_SKILL_DIR}/templates/rq.yaml.example`）
- `proposal/criteria.yaml` —— `criteria.<RQ-n>.{fail, n_min|n, seeds, contamination}` 必填，`success / metric / interval / protocol / baselines / figure / contamination_note` 建议（样例 `templates/criteria.yaml.example`）
- `proposal/figure-plan.md`（五列固定格式，见 `templates/figure-plan.md.example`）与 `proposal/baselines.md`（见 `templates/baselines.md.example`）
- `proposal/baselines.md`（基线清单 + 调参预算 / 重试次数 / 种子 / 硬件，与本方法并排）
- `proposal/criteria.lock`（脚本写：JSON `{version, hash, rq_hash, rqs, ts, commit, dirty, by}`）
- `proposal/history/v{N}/`（脚本写：每次冻结的签字原文 criteria.yaml + rq.yaml + criteria.lock）
- `proposal/changes.md`（脚本写：每次 amend 一行 —— 时间 / git 用户 / 解冻版本 / 旧哈希 / 原因）

YAML 保持「按 id 嵌套的 map + 行内列表 `[a, b]`」，值单行、不含 ` #`，这样无 PyYAML 时 `read_yaml_lite` 也能读。

## Preconditions
- `status.json.gates.thesis == approved`；否则 `freeze` 以 exit 2 拒绝立项。

## Workflow
1. 读 `positioning/thesis.md`，从论点句拆 RQ，编号 RQ-1…、假设 H-1a…，写 `proposal/rq.yaml`（`thesis_part` 指回论点句原文）。
2. 每个 RQ 写 `proposal/criteria.yaml` 一节：先写 `fail`（什么结果算失败）—— 写不出的 RQ 不立项；再补 `n_min`、`seeds`、`interval`、`protocol`、`contamination`（excluded | known_overlap | unknown）。对照 `${CLAUDE_SKILL_DIR}/../../references/validity-checklist.md` 第 1、3、5、6、7 条逐条核。
3. figure-first：写 `proposal/figure-plan.md`（先定图，再倒推实验）和 `proposal/baselines.md`（预算比不得超过 `config.thresholds.baseline_budget_ratio_max`）。
4. 调 `research-advisor` 预审（D3：每个 RQ 有失败判据、n 算得出区间、污染关系已说明、基线预算并排），拿到「放行 / 不放行 + 理由」。
5. 走下方审批闸门。导师「确认」后，用导师本人的名字运行：
   `python3 ${CLAUDE_SKILL_DIR}/scripts/freeze_criteria.py <paper-id> freeze --by <导师>`
   - 脚本先 `gate_require(thesis)`，再校验每个 RQ 的 fail / n / seeds / contamination；缺项列为阻塞，exit 2，不写任何文件。
   - n / seeds 低于 config 阈值只告警（结果将进不了主表），由导师决定是否接受。
   - 成功：写 `criteria.lock` 与 `history/v{N}/`，`gates.criteria = approved`（hash = criteria.yaml 的 sha256），`counts.rqs` 更新，`stage = experiments`。
   - 工作树 dirty 时提示先提交 `proposal/`。
6. 校验（record-run 也调它）：`freeze_criteria.py <paper-id> check [--json]` —— lock 存在且 criteria.yaml、rq.yaml 哈希都与 lock 一致 → 0，否则 1。
7. `--amend <reason>`（pilot / 基线复现推翻初版时）：
   `python3 ${CLAUDE_SKILL_DIR}/scripts/freeze_criteria.py <paper-id> amend --reason "..."`
   移除 lock、`changes.md` 追加一行、`gates.criteria` 回 `pending`、`stage = proposal`；签字原文留在 `history/v{N}/`。然后改 rq.yaml / criteria.yaml，重走 4–5（产生 v{N+1}）。冻结前已产生的 formal run 仍属旧版本，不自动迁移。

## Example
```bash
P=${CLAUDE_SKILL_DIR}/scripts/freeze_criteria.py
cp ${CLAUDE_SKILL_DIR}/templates/rq.yaml.example       .studio-paper/evolving-foundation/proposal/rq.yaml
cp ${CLAUDE_SKILL_DIR}/templates/criteria.yaml.example .studio-paper/evolving-foundation/proposal/criteria.yaml
python3 $P evolving-foundation freeze --by 王老师
# ✓ 判据已冻结 v1  sha256:37657e687e9c3740…  RQ: RQ-1   签字: 王老师
python3 $P evolving-foundation check            # ✓ 判据冻结有效 v1 → exit 0
python3 $P evolving-foundation amend --reason "pilot 显示每轮窗口只能稳定拿到 40 例"
python3 $P evolving-foundation check            # ✗ 判据未冻结（无 criteria.lock）→ exit 1
# 改 criteria.yaml（n_min: 40）→ 预审 → 导师确认 →
python3 $P evolving-foundation freeze --by 王老师   # ✓ 判据已冻结 v2
```

## Approval Gate

本技能含一个导师签字闸门（D3 判据冻结）。产出草稿后、写入最终产物前：

1. 用一页摘要呈现草稿，并附 `research-advisor` 的预审意见（放行 / 不放行 + 理由）。
2. 请导师明确选择：

> **审批检查点：判据冻结**
>
> {产物摘要 + 预审意见}
>
> 请选择：
> - ✅ **确认** — 签字放行，写入产物并记录到 status.json（approved_at / approved_by / hash）
> - ✏️ **修改** — 需要调整（说明哪里改），改后重新呈现
> - ❌ **拒绝** — 重做（说明原因），草稿作废

3. 签字是人的动作，插件不得代签；`status.json` 的 `gates.criteria` 未到 `approved` 时，下游技能拒绝以它为前置。
## Out of Scope
- 不跑实验；不复现基线（record-run 的 `--kind baseline`）；不生成图。
