# studio-paper 外部集成评估

> Date: 2026-10-23 · 来源：web 调研（星数 / 许可证 / 最近提交经 GitHub API 核对于当日）
> 结论先行：官方 anthropics/skills 与 claude-plugins-official 里没有学术论文类插件；可用的都是社区 skill / MCP。集成原则：**只接「证据包之外的能力」（检索、出图、引用核验、模板），不接会替代台账与闸门的整套方案**。

## 推荐接入（按价值 / 成本）

| # | 外部件 | 形态 · 许可 · 活跃度 | 接到哪 | 做法 | 成本 |
|---|---|---|---|---|---|
| 1 | [arxiv-mcp-server](https://github.com/blazickjp/arxiv-mcp-server) | MCP（`uvx arxiv-mcp-server`）· Apache-2.0 · 3.2k⭐ · 10-08 | `position-paper` | `.mcp.json` 加一条；差异表「原文出处」直接引用它按章节读到的 LaTeX | 低 |
| 2 | [paper-search-mcp](https://github.com/openags/paper-search-mcp) | MCP · MIT · 2.9k⭐ · 10-02 | `position-paper` 初筛 | `.mcp.json` 加一条；**禁用 Sci-Hub 源**（合规） | 低 |
| 3 | [K-Dense `scientific-visualization`](https://github.com/K-Dense-AI/scientific-agent-skills) + [SciencePlots](https://github.com/garrettj403/SciencePlots) | skill 目录 · MIT · 54k⭐ · 10-19 | `freeze-results` | 从 `results/figures/{RQ}.csv` 出图；绘图脚本与样式一起进 freeze.lock，图表同源 | 中（vendor skill 或声明依赖） |
| 4 | [K-Dense `citation-management`](https://github.com/K-Dense-AI/scientific-agent-skills) | skill · MIT | `check-submission` | DOI → BibTeX、引用真实性回查，替代我们 SKILL.md 里手写的 Semantic Scholar / Crossref 查询说明 | 中 |
| 5 | [zotero-mcp](https://github.com/54yyyu/zotero-mcp) | MCP（PyPI `zotero-mcp-server`）· MIT · 5.5k⭐ · 10-16 | `position-paper` 数据源；写作阶段导 `.bib` 给 studio-docs | 课题组已用 Zotero 时价值最高；否则跳过 | 低 |
| 6 | [K-Dense `venue-templates`](https://github.com/K-Dense-AI/scientific-agent-skills) | skill · MIT | `check-submission` | 页数 / 匿名规则 / PDF 检查，补我们的双盲扫描 | 低 |

## 只借鉴设计、不 vendor

| 外部件 | 为什么不直接用 | 吸收什么 |
|---|---|---|
| [Imbad0202/academic-research-skills (ARS)](https://github.com/Imbad0202/academic-research-skills) 53k⭐ | 许可证 CC BY-NC 4.0（GitHub 标 NOASSERTION），不能商用、不能 vendor；整套自包含，与 studio-docs 分工冲突；与我们的台账 / 评审团 / repro_lock 重叠最大 | 作为 review-internally 与 record-run 的**对照基准**：7 人评审团含 Devil's Advocate、R&R 追踪矩阵、experiment_provenance 字段 |
| [TobiasLee/Rebuttal-Skill](https://github.com/TobiasLee/Rebuttal-Skill) 481⭐ | 无 license | `respond-reviews` 加 Stage 0「值不值得 rebuttal / 还是重投」判断；补实验 P0–P3 排序；ACL 的 AI 使用政策提醒 |
| [Orchestra `ara-rigor-reviewer`](https://github.com/Orchestra-Research/AI-Research-SKILLs) 14k⭐ · MIT | 可 vendor，但更适合做角色 | `review-internally` 增第四位审稿人「严谨性审稿人」：可证伪性 / 范围校准 / 探索完整性六维 |
| [K-Dense `peer-review`](https://github.com/K-Dense-AI/scientific-agent-skills) | 与我们的三审稿人重叠 | 其离线 checklist 并入 `references/validity-checklist.md` |

## 不建议

- **ARS 整套替换**：许可证 + 分工冲突（见上）。
- **K-Dense `claude-scientific-writer`**（2.5k⭐）：端到端写作，与 studio-docs 重复，且必需 `PARALLEL_API_KEY`。
- **K-Dense `scientific-schematics` / Orchestra `academic-plotting` 的生图路径 / PaperBanana**：依赖 Gemini / OpenRouter 生图，结果不可复现，与「冻结证据」原则冲突；方法示意图可作可选增强，但不进证据包。
- **paper-search-mcp 的 Sci-Hub 源**：合规风险。
- **Mermaid MCP**：studio-docs 导出已渲染 Mermaid。
- **可复现性 / artifact 打包类 skill**：全是 0 星新仓。改为把 [sysartifacts](https://github.com/sysartifacts/sysartifacts.github.io) 的 AE 徽章 checklist 抄进 `check-submission/templates/checklist.md.tmpl`，自己实现。

## 落地顺序建议

1. `.mcp.json`：arxiv-mcp-server + paper-search-mcp（禁 Sci-Hub）→ 跑 `/studio-quality:wire-mcp studio-paper`。
2. `freeze-results` 加 `scripts/plot_figures.py`，依赖 matplotlib + SciencePlots，样式与脚本进 freeze.lock；SKILL.md 引用 K-Dense scientific-visualization 的多面板 / 不确定性表达规范。
3. `check-submission` 引入 citation-management 的 DOI 回查；AE checklist 进模板。
4. `respond-reviews` 加 Stage 0 与 P0–P3；`review-internally` 加严谨性审稿人角色。
5. zotero-mcp 视课题组习惯决定。

> 所有星数与许可证都是调研当日读数；接入前再核一次 README 的依赖与 key 要求。
