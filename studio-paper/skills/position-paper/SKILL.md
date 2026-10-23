---
name: position-paper
description: Position a paper — build or update the versioned literature matrix, diff the three nearest works line by line with source quotes, draft the one-sentence thesis and take it to the advisor gate; --rescan re-sweeps nearest work (incl. recent arXiv) and diffs against the previous matrix version. Use at topic selection, whenever new related work appears, and mandatorily before submission and before rebuttal.
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, Agent
user-invocable: true
---

# Position Paper

把「没人做过这个」变成一张可核对的表：文献矩阵（有版本）、最近邻三篇差异表（每条差异附原文出处）、一句话论点句。论点句是后面所有 RQ 的锚，导师签字（闸门 D2）后才解锁开题。`--rescan` 是同一技能的重扫模式：投稿前和 rebuttal 前强制跑，和上一版矩阵比对出新增近邻。

## Intent
- 让新颖性在开题时就被三篇最近邻检验，而不是在内审或外审时暴露。
- 触发：选题、读到新相关工作、`check-submission` 与 `respond-reviews` 的前置重扫。

## Expected Inputs
- 选题描述；用户给的 PDF / BibTeX / URL
- 检索 MCP（`.mcp.json` 的 paper-search、arxiv；见下节）；两者不可达时才用 WebFetch 查 Semantic Scholar / Crossref 公开 API，再不行降级为手工输入，不猜引用
- `--rescan`：上一版 `positioning/literature-matrix.md`

## Expected Outputs
- `positioning/literature-matrix.md`：版本头 `> version: N` + 表 `| key | 题目 | 年份 | 问题 | 方法 | 数据 | 结论 | 与本题关系 | 来源 | 核查 |`（核查 ∈ verified / UNVERIFIED）；旧版存 `positioning/history/matrix-v{N}.md`
- `positioning/nearest-diff.md`（模板 `${CLAUDE_SKILL_DIR}/templates/nearest-diff.md.tmpl`：三篇 × 他们做了什么 / 我们多做了什么 / 原文出处 / 差异是否值得一篇论文）
- `positioning/thesis.md`（模板 `thesis.md.tmpl`：一句话 + 预审 + 签字记录）
- rescan 模式：`positioning/rescan-{date}.md`（新增近邻、移除、核查变化、「论点句是否需改：待导师判断」）

## Preconditions
- 工作区存在。rescan 模式要求已有 matrix v1。

## 检索工具（随插件配置的 MCP，`.mcp.json`）

- **paper-search**（`mcp__paper-search__search_papers` 等）：多源检索与去重（arXiv / Semantic Scholar / OpenAlex / Crossref / dblp / OpenReview），用于初筛候选与补 DOI；全文只走 `download_with_fallback` 的开放获取链。
- **arxiv**（`mcp__arxiv__*`）：按章节读 arXiv 原始 LaTeX（`paper → outline → section`）、从元数据出 BibTeX。差异表每条「原文出处」要落到它读出的章节，不凭记忆写。
- 两者不可达时降级为用户贴 PDF / BibTeX，并在矩阵的「核查」列标 `UNVERIFIED`。

**硬规则：禁用 Sci-Hub。** 不调用 `download_scihub`，不给任何工具传 `use_scihub=true`；`hooks/block_scihub.py` 会在 PreToolUse 拦下并报错。拿不到全文就标注「仅摘要可得」，让用户自行获取。

## Workflow
`M = ${CLAUDE_SKILL_DIR}/scripts/matrix_version.py`，在项目根执行。

1. 建骨架：`python3 $M <paper-id> init`（v1 空矩阵 + nearest-diff / thesis 模板；已存在不覆盖）。
2. 收集候选文献（用户给的 PDF / BibTeX / URL + 检索），逐篇填矩阵一行，`核查` 先写 `UNVERIFIED`。
3. **引用回查（无脚本）**——每篇先用 `mcp__paper-search__search_papers` 按题目检索，命中后用 arxiv MCP 读章节核对出处；MCP 不可达时退回 WebFetch 查两个公开 API（题目做 URL 编码）：
   - Semantic Scholar：`https://api.semanticscholar.org/graph/v1/paper/search?query=<title>&fields=title,authors,year,externalIds`
   - Crossref：`https://api.crossref.org/works?query.bibliographic=<title>&rows=1`
   - 规则：返回结果里**标题一致（忽略大小写 / 标点）且第一作者姓氏一致** → `verified`，并把 DOI / arXiv id（`externalIds`）写进 `来源`；两处都确认不了 → 保持 `UNVERIFIED`。
   - **UNVERIFIED 的文献不得进入 nearest-diff.md。** 接口不可达 → 告诉用户、请其手工给 DOI / 链接，仍标 UNVERIFIED，绝不凭记忆补作者 / 年份。
   - 查完跑 `python3 $M <paper-id> show` 看条目数与 UNVERIFIED 数。
4. 从 verified 条目中选最近邻三篇，按模板写 nearest-diff.md；每条差异附原文出处（页 / 节 / 表号）。
5. 起草一句话论点句写入 thesis.md（不得用「和」拼两个贡献）。
6. 调 `research-advisor` 预审：三篇是否真最近、差异是否只是换数据集 / 换模型级、句子是否一句话。
7. 走审批闸门（下节）。导师「确认」并给出姓名后：
   - `python3 $M <paper-id> show` 取 thesis.md 哈希，在 thesis.md 签字记录表追加一行；
   - `python3 ${CLAUDE_SKILL_DIR}/../../scripts/paper_ws.py <paper-id> thesis approved --by <导师> --hash <哈希>`
   - `python3 $M <paper-id> bump --note "D2 签字定版"` 存档本版矩阵。
8. **rescan 模式**（`--rescan`；check-submission / respond-reviews 前强制）：跳过 4–7。
   - 先 `python3 $M <paper-id> bump --note "rescan 前存档"`；
   - 重扫近邻（含近 6 个月 arXiv），新条目按第 3 步回查后加入矩阵；
   - `python3 $M <paper-id> diff` → 打印新增 / 移除 / 核查变化，写 `positioning/rescan-{date}.md`；**exit 1 = 有新增条目仍是 UNVERIFIED**，回查完再跑。
   - 把 rescan 文件交导师判断论点句是否需改；需改 → 回到正常模式第 4 步并重走 D2。

## Approval Gate

本技能含一个导师签字闸门（D2 论点句）。产出草稿后、写入最终产物前：

1. 用一页摘要呈现草稿，并附 `research-advisor` 的预审意见（放行 / 不放行 + 理由）。
2. 请导师明确选择：

> **审批检查点：论点句放行**
>
> {产物摘要 + 预审意见}
>
> 请选择：
> - ✅ **确认** — 签字放行，写入产物并记录到 status.json（approved_at / approved_by / hash）
> - ✏️ **修改** — 需要调整（说明哪里改），改后重新呈现
> - ❌ **拒绝** — 重做（说明原因），草稿作废

3. 签字是人的动作，插件不得代签；`status.json` 的 `gates.thesis` 未到 `approved` 时，下游技能拒绝以它为前置。
## Out of Scope
- 不写 RQ（propose-experiments）；不下载全文到仓库；不做引用格式化（写作阶段）。

## Example

```
$ python3 $M demo init
✓ 新建 …/positioning/literature-matrix.md（v1，空表）
✓ 新建 …/positioning/nearest-diff.md（模板）   ✓ 新建 …/positioning/thesis.md（模板）
# 回查：Voyager 标题 + 第一作者 Wang 在 Semantic Scholar 命中 → verified；
#                "Fake Ontology Paper" 两处都查不到 → UNVERIFIED，不进差异表
$ python3 $M demo show
文献矩阵 v1：2 条，UNVERIFIED 1 条；存档 无
$ python3 $M demo bump --note "rescan 前存档"      # → history/matrix-v1.md，当前 v2
$ python3 $M demo diff
新增 2：reflexion23[verified]、newarx26[UNVERIFIED]
核查状态变化 1：ontoclean UNVERIFIED→verified
→ …/positioning/rescan-2026-10-23.md
❌ 1 条新增为 UNVERIFIED —— 先回查（Semantic Scholar / Crossref）或手工补来源   (exit 1)
```

