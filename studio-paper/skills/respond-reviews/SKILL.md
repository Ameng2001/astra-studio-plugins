---
name: respond-reviews
description: Turn external reviews into a response-to-reviewers table — decompose every reviewer point (none merged, none skipped), classify fatal / fixable / misunderstanding / clarification, rank by reviewer movability, triage which points deserve new experiments within the compute and time budget, require those runs to be in the ledger before they are cited, write each response with an evidence pointer, run_id and revision commitment, force the claim-narrowing branch when new data weakens a claim, check the word/page budget, and record the outcome after the decision. Use the day reviews arrive and after the decision.
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, Agent
user-invocable: true
---

# Respond Reviews

rebuttal 只能补证据，不能换研究问题；无逐条回应表不得提交。表的每一行是一条评审人要点：ID / 原话 / 分类 / 回应（中性，一到三句）/ 证据指针 / run_id / 修改承诺 / 是否新 claim（是 → 必须有数据）。新数据削弱原 claim 时强制收窄（闸门 D11），不隐瞒。

## Intent
- 几天内把意见变成有证据指针的回应，并把补实验纳入台账。
- 触发：外审到达；`--decision` 在结果公布后记录。

## Expected Inputs
- 外审原文；`ledger/runs.jsonl`；`review/claims.md`；`config.yaml.rebuttal`（`limit`：整数 = 词，`"5000 chars"` = 字符，`"1page"` ≈ 500 词；`rounds`）
- `submission/snapshot.json`（投稿版本）

## Expected Outputs
- `rebuttal/reviews.md`（按 `${CLAUDE_SKILL_DIR}/templates/reviews.md.tmpl`：每位评审人的可移动性 + 逐条要点 R1-W2 / R2-Q1 … + 分类）
- `rebuttal/todo-runs.md`（分诊出的补实验 + 算力 / 时间预算）
- `rebuttal/responses.md`（按 `${CLAUDE_SKILL_DIR}/templates/responses.md.tmpl`：ID / 原话 / 分类 / 回应 / 证据指针 / run_id / 修改承诺 / 是否新 claim / priority）
- `rebuttal/tables/T-R<n>.md`（回应里引用的新表）
- `rebuttal/outcome.md`（分数移动、有效回应类型、决定；E26 组内经验）

## Preconditions
- snapshot.json 存在；回应表引用的 run_id 全部在台账且为 formal 或 rebuttal 补跑（check_refs.py 校验，否则阻塞）；`rebuttal.limit` 已按当年 CFP 填数。

## Workflow

脚本根：`S=${CLAUDE_SKILL_DIR}/scripts`；工作目录 = 项目根；路径参数相对 `.studio-paper/<paper-id>/`。

1. 调 `position-paper --rescan`（评审人可能就是近邻作者）。
2. **拆解**：复制 `reviews.md.tmpl` 为 `rebuttal/reviews.md`，逐条拆（不合并不遗漏），ID 用 `R{评审人}-{W|Q|C}{序号}`；分类 fatal / fixable / 误解 / 澄清（按 `${CLAUDE_SKILL_DIR}/../../references/review-forms/{family}-conference.md` 的 fatal 与 fixable 表）；给每位评审人标可移动性，资源投给摇摆者。
3. **分诊**：一周内能补的才算 fixable，写进 `rebuttal/todo-runs.md`（每条：实验、预算、对应 ID）。补实验由 `record-run` 以 `kind: rebuttal` 入账，本技能不跑。
4. **回应表**：复制 `responses.md.tmpl` 为 `rebuttal/responses.md`，每条要点一行；回应中性一到三句；新表写 `rebuttal/tables/T-R<n>.md`；priority 1 = 最该保留。然后核引用：
   ```bash
   python3 $S/check_refs.py <paper-id> rebuttal/responses.md
   ```
   退出码 2：run_id 不在台账 / 已作废 / failed / 非 formal 且非 rebuttal 补跑，`T-R<n>` 没文件，或「是否新 claim = 是」却无 run_id → 先补齐再继续。
5. **收窄分支**：补出来的数据若削弱原 claim（区间跨 0、不显著、反例），该行改为收窄措辞，同步 `review/claims.md` 与 `submission/revision-checklist.md`；在审批摘要里单列。
6. **预算**：
   ```bash
   python3 $S/budget_check.py <paper-id> rebuttal/responses.md [--limit "5000 chars"]
   ```
   退出码 1 = 超限，按输出的 priority 裁剪候选从数字最大的开始删 / 压缩，重跑到 0；退出码 2 = limit 未配置。
7. **审批闸门（D11）**：派 `research-advisor` 预审 responses.md（「我们认为」回应数据质疑、补实验没入账、削弱未收窄 → 不放行），按下方格式呈现。导师确认后由其本人签字，然后用户自己去会议系统提交：
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../../scripts/paper_ws.py <paper-id> rebuttal approved --by <导师> --hash "sha256:$(shasum -a 256 .studio-paper/<paper-id>/rebuttal/responses.md | cut -d' ' -f1)"
   ```
8. **`--decision`**（结果公布后）：用 `python3 ${CLAUDE_SKILL_DIR}/../../scripts/paper_ws.py {paper-id} decision accept|major-revision|reject` 记入 status.json；写 `rebuttal/outcome.md`（每位评审人分数前后、哪类回应有效）。accept / major-revision → `check-submission --camera-ready`；reject → `position-paper --rescan` 准备改投。

## Approval Gate

本技能含一个导师签字闸门（D11 收窄 claim 与回应表提交）。产出草稿后、写入最终产物前：

1. 用一页摘要呈现草稿，并附 `research-advisor` 的预审意见（放行 / 不放行 + 理由）。
2. 请导师明确选择：

> **审批检查点：回应表提交**
>
> {产物摘要 + 预审意见}
>
> 请选择：
> - ✅ **确认** — 签字放行，写入产物并记录到 status.json（approved_at / approved_by / hash）
> - ✏️ **修改** — 需要调整（说明哪里改），改后重新呈现
> - ❌ **拒绝** — 重做（说明原因），草稿作废

3. 签字是人的动作，插件不得代签；`status.json` 的 `gates.rebuttal` 未到 `approved` 时，下游技能拒绝以它为前置。
## Out of Scope
- 不跑补实验（record-run）；不改 RQ；不引入无数据的新 claim；不替人提交。
