---
name: review-internally
description: Internal review of a draft the way a program committee would — scan claim words (first / novel / significantly / state-of-the-art / outperforms), link every quantitative claim to a ledger run and a table, have three reviewer roles (nearest-work author, methodology/statistics, adjacent-field reader) fill the venue's current review form with scores and confidence, produce the claim-to-evidence table and the top-3 rejection reasons tagged fatal or fixable, and get fatal items dispositioned at the advisor gate. Run once while writing is unfinished and again before the submission freeze.
allowed-tools: Read, Write, Bash, Glob, Grep, Agent
user-invocable: true
---

# Review Internally

内审不是「读一遍」，是按目标会议当年评审表打分的模拟审稿。产物三件：三份评审、claim↔证据对照表、Top-3 拒稿理由。fatal 由导师签字处置（收窄 claim 或回到设计），fixable 指派到台账待补队列。写作未完时跑第一次（改结构代价低），投稿冻结前第二次。

## Intent
- 让「我会在第几分钟拒这篇」在投稿前被回答两次。
- 触发：有初稿（或只有证据包时跑 claims 扫描）；`check-submission` 要求两次通过。

## Expected Inputs
- 初稿 markdown（studio-docs 产出）；`results/evidence-pack/`；`ledger/runs.jsonl`
- `config.yaml.venue`（family / name / year / cfp_url：读当年 CFP 的评审表字段；缺则用 `${CLAUDE_SKILL_DIR}/../../references/review-forms/{family}-conference.md`）
- `config.yaml.review.roles`（默认三个角色）

## Expected Outputs
- `review/claims.md`（# / 行 / 原句 / 强度词 / 表号 / run_id / n 与区间 / 最强反例或缺失基线 / 判定：支持 · 过强 · 无证据）
- `review/internal-{N}/{reviewer-nearest-author,reviewer-methodology,reviewer-adjacent}.md`
- `review/internal-{N}/verdict.md`（审稿人分数 + Top-3 拒稿理由 + 全量弱点清单 + 空白「处置」列）
- `ledger/todo-runs.md`（fixable 追加，不重复）

## Preconditions
- `gates.results == approved`（compose_verdict.py 会校验）；初稿路径可读。
- 只有证据包、还没初稿时：只跑第 1 步（scan_claims.py 对 evidence-pack 里的 md），不派审稿人。

## Workflow

脚本根：`S=${CLAUDE_SKILL_DIR}/scripts`；工作目录 = 项目根（含 `.studio-paper/`）。`N` = 第几次内审（写作中 1，冻结前 2）。

1. **声称词扫描 + 回指**
   ```bash
   python3 $S/scan_claims.py <paper-id> <draft.md>            # → review/claims.md
   ```
   - 词典硬编码自 `${CLAUDE_SKILL_DIR}/../../references/claim-words.md`（中英文，大小写不敏感）；量化句（数字 + % / 倍 / x、`n = `、小数、run_id）无论有无声称词都抽出。
   - 回指：表号（`表 T2` / `Table 2` / `Fig 1` / `图 1`）、台账 run_id（须在 effective_runs）、或句中数值与某条 formal run 的 `metric.value` 相差 ≤ 0.005。
   - 判定：无表号且回指不到 formal run → **无证据**；有证据但声称词缺比较对象词（than / vs / over / 比 / 相对 / 相比）或缺区间 / 检验 → **过强**；其余 **支持**。
   - 重跑会保留同一句已手填的「最强反例或缺失基线」。`--strict` 时有无证据即退出码 2。

2. **派三个审稿 agent（Agent 工具，一个角色一次调用，三次调用放在同一条消息里并行）**
   - 角色定义：`${CLAUDE_SKILL_DIR}/../../agents/reviewer-nearest-author.md`、`reviewer-methodology.md`、`reviewer-adjacent.md`（列表以 `config.yaml.review.roles` 为准）。
   - 每次调用的 prompt 必须给齐：
     - 角色文件路径（让 agent 先读并按其视角审）
     - 初稿绝对路径、`review/claims.md` 路径（要求逐条看「过强 / 无证据」，并补「最强反例或缺失基线」建议）
     - `venue.family`（se / ml）、name、year、cfp_url；评审表回退结构 `${CLAUDE_SKILL_DIR}/../../references/review-forms/{family}-conference.md`
     - 模板 `${CLAUDE_SKILL_DIR}/templates/review-form.md.tmpl`：照它填 summary / strengths / weaknesses / questions / limitations / scores / confidence / top-1 拒稿理由
     - 输出路径：`.studio-paper/<paper-id>/review/internal-{N}/{agent-name}.md`（如 `reviewer-methodology.md`）
     - 硬约定：`## Weaknesses` 下一条一行，行尾标 `[fatal]` 或 `[fixable]`，可选 `→ 稿中位置`；fatal / fixable 分级按评审表文件末尾「fatal 与 fixable」表；fixable 写明一周内能补的实验或修订
     - 只读稿件与台账，不改稿、不改 claims.md
   - 三份都写出后再进下一步；缺哪份就补派哪个角色。

3. **合成结论**
   ```bash
   python3 $S/compose_verdict.py <paper-id> --pass N
   ```
   - 收集三份 Weaknesses → 关键词重合度聚类 → fatal 优先、同级按「几位审稿人提到」排序 → `verdict.md`（Top-3 + 全量清单，「处置」列留空）。
   - fixable 追加进 `ledger/todo-runs.md`（带 `<!-- internal-N:Wk -->` 标记，重跑不重复）。
   - `status.counts.internal_reviews = max(原值, N)`。有「未标级」条目时让对应 agent 补标后重跑。
   - 人工补两项检查写进 verdict.md 末尾：每个 RQ 的答案在哪个表；每个 caption 脱离正文是否可读。

4. **审批闸门（D8）**：先派 `research-advisor` 对 verdict.md + claims.md 预审，再按下方格式呈现。
   - 导师为每条 fatal 选处置：`收窄 claim`（写明改哪一句 → 学生在 studio-docs 改稿后重跑第 1 步）或 `回到设计`（→ `propose-experiments --amend`）；fixable → `todo`。
   - 处置填进 verdict.md 的「处置」列后，由导师本人签字：
     ```bash
     python3 ${CLAUDE_SKILL_DIR}/../../scripts/paper_ws.py <paper-id> review approved --by <导师> --hash "sha256:$(shasum -a 256 .studio-paper/<paper-id>/review/internal-N/verdict.md | cut -d' ' -f1)"
     ```

## Approval Gate

本技能含一个导师签字闸门（D8 fatal 处置）。产出草稿后、写入最终产物前：

1. 用一页摘要呈现草稿，并附 `research-advisor` 的预审意见（放行 / 不放行 + 理由）。
2. 请导师明确选择：

> **审批检查点：fatal 项处置**
>
> {产物摘要 + 预审意见}
>
> 请选择：
> - ✅ **确认** — 签字放行，写入产物并记录到 status.json（approved_at / approved_by / hash）
> - ✏️ **修改** — 需要调整（说明哪里改），改后重新呈现
> - ❌ **拒绝** — 重做（说明原因），草稿作废

3. 签字是人的动作，插件不得代签；`status.json` 的 `gates.review` 未到 `approved` 时，下游技能拒绝以它为前置。
## Out of Scope
- 不改稿（回 studio-docs）；不补实验（record-run）；不替导师决定收窄还是重做。
