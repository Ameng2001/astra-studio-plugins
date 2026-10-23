---
name: check-submission
description: Pre-submission or camera-ready readiness check — forced nearest-work rescan, claim-word rescan, double-blind scan (author names, repo links, acknowledgements, self-citation wording), artifact dry run in a clean environment, revision-checklist tick-off and paper-number-vs-artifact consistency in --camera-ready mode, then a version snapshot (code / data / ledger hashes) at the advisor gate. The submit click itself stays with the human. Use after two internal reviews, and again at camera-ready.
allowed-tools: Read, Bash, Glob, Grep, Write
user-invocable: true
---

# Check Submission

把「仓库链接删了吗」从记忆变成清单。全部通过后生成版本快照，导师放行（闸门 D9）；提交动作由人在会议系统完成，插件只给放行 / 阻塞结论与理由。`--camera-ready` 模式加修订清单逐条勾销与论文数字 ↔ artifact 一致。

## Intent
- 投稿与 camera-ready 的最后一道机械检查，不靠人记。
- 触发：两次内审完成且无未处置 fatal；录用后准备 camera-ready。

## Expected Inputs
- 稿件 markdown、artifact 目录、`review/claims.md`、`review/internal-2/verdict.md`（fatal 已处置）
- 作者名（中英文）与单位名：向用户要，用于匿名扫描
- `--camera-ready`：`submission/revision-checklist.md`（major revision / shepherd 修订清单，`- [ ]` / `- [x]` 逐条）、投稿版 `submission/snapshot.json`、`rebuttal/responses.md`

## Expected Outputs
- `submission/checklist-{submit|camera-ready}.md`（按 `${CLAUDE_SKILL_DIR}/templates/checklist.md.tmpl` 填：每项 通过 / 阻塞 + 证据）
- `submission/rescan-{date}.md`（position-paper --rescan 产出）· `submission/claims-rescan-{date}.md`
- `submission/anonymity-report.md`
- `submission/snapshot.json`（投稿）/ `submission/snapshot-camera-ready.json`（camera-ready；投稿版保留给 respond-reviews）

## Preconditions
- `counts.internal_reviews ≥ 2` 且 `gates.review == approved`；camera-ready 模式另要求 `decision ∈ {accept, major-revision}`。snapshot.py 会逐项校验，不满足即退出码 2。

## Workflow

脚本根：`S=${CLAUDE_SKILL_DIR}/scripts`；工作目录 = 项目根。先把清单模板复制成 `submission/checklist-{mode}.md`，每步结果回填。

1. **近邻重扫**：调 `position-paper --rescan`，产出 `submission/rescan-{今天}.md`；有新增近邻且稿中未对比 → 阻塞（回 studio-docs 补对比）。
2. **声称词再扫**（与 claims.md 对账，不覆盖它）：
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/../review-internally/scripts/scan_claims.py <paper-id> <draft.md> \
       --compare review/claims.md --report submission/claims-rescan-$(date +%F).md
   ```
   退出码 2 = 有「新增且无证据」的句子 → 阻塞（回 review-internally 第 1 步或改稿）。
3. **双盲扫描**（投稿模式；camera-ready 跳过）：
   ```bash
   python3 $S/anonymity_scan.py <paper-id> <draft.md> --artifact <dir> \
       --authors "张三,Zhang San" --orgs "某大学,Foo Lab" [--allow "github.com/<第三方基线仓库>"]
   ```
   查作者名（含 linwei / lin.wei 变体）、单位、邮箱、带用户名的仓库链接、本机家目录路径、自引措辞、致谢节、基金号；退出码 2 = 有泄露 → 改稿 / 改 artifact 后重扫。PDF 元数据、压缩包、图片水印人工核对，记进清单。
4. **artifact 干净环境跑通**（Bash）：`mktemp -d` 新目录（或容器）里按 artifact README 从零装依赖、一键跑；把主表数字与稿件逐个对照，命令与输出摘要写进清单。只在作者机器能跑 → 阻塞。
5. **camera-ready**：`revision-checklist.md` 逐条勾销（每条写明改在哪）；对照 `rebuttal/responses.md` 的「修改承诺」列逐条落地；论文每个数字可由 artifact 命令复现；去匿名信息补回。
6. **快照预览 → 审批闸门（D9）**：
   ```bash
   python3 $S/snapshot.py <paper-id> <draft.md> --artifact <dir> [--camera-ready] --dry-run
   ```
   有阻塞项（gates.review、internal_reviews、匿名报告结论 / 是否针对当前稿件、decision、修订清单未勾项）就停在这里修。通过后派 `research-advisor` 预审，按下方格式呈现清单 + 快照预览。
7. **导师选择「确认」后**，用导师给出的名字落快照并记录放行：
   ```bash
   python3 $S/snapshot.py <paper-id> <draft.md> --artifact <dir> [--camera-ready] --by <导师>
   ```
   写 snapshot 文件（mode · ts · by · git commit/dirty · draft_hash · artifact_hash · ledger_hash · rescan · anonymity_report_hash · revision_checklist_hash）并 `gates.submission = approved`。然后提示用户去会议系统**自己**提交。

## Approval Gate

本技能含一个导师签字闸门（D9 投稿放行）。产出草稿后、写入最终产物前：

1. 用一页摘要呈现草稿，并附 `research-advisor` 的预审意见（放行 / 不放行 + 理由）。
2. 请导师明确选择：

> **审批检查点：投稿放行**
>
> {产物摘要 + 预审意见}
>
> 请选择：
> - ✅ **确认** — 签字放行，写入产物并记录到 status.json（approved_at / approved_by / hash）
> - ✏️ **修改** — 需要调整（说明哪里改），改后重新呈现
> - ❌ **拒绝** — 重做（说明原因），草稿作废

3. 签字是人的动作，插件不得代签；`status.json` 的 `gates.submission` 未到 `approved` 时，下游技能拒绝以它为前置。
## Out of Scope
- 不替人点提交；不做会议模板 / LaTeX（studio-docs）；不修改稿件。
