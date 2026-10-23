# Changelog: academic-paper

## 2026-10-23

**Summary**: 首次事件风暴（快速模式）。四角色（PM · 架构师 · research-advisor · peer-reviewer）各自列事件，合并去重为 26 条、七簇；三画像、三旅程、三流程；七热点，前三为高严重度。

### Added
- event-storm.md：26 事件 / 12 决策点 / 7 热点 / KB 依赖 / 专家范围 / 4 项待裁定
- personas：lin-phd-author · wang-pi · chen-new-master
- journeys：lin-topic-to-submission · wang-gate-review-week · chen-takeover-rebuttal
- processes：proposal-and-criteria-freeze · experiment-ledger-and-hypothesis-rollup · review-submit-rebuttal
- studio/agents：research-advisor · peer-reviewer（本项目专家，规划 + 运行两用）

### Revised
- （首轮无）

### Impact on Plugins
- `studio-paper`（待建）：new plugin needed —— 本域的核心产物；形态（单插件 vs core + add-on）在 domain-model 裁定
- `studio-docs`：needs modification —— 需新增 `academic-paper` blueprint（章节结构 / 引用规则），输入为证据包；单向依赖
- `studio-evolve`：no change —— 作为第一篇论文的实验来源，其 growth-ledger 是台账导入的首个样例

### Domain model（同日，快速模式）
- 单插件 `studio-paper`（core, create）；架构师的 core + add-on 方案记为后续分裂点
- 四项待裁定已按建议定案（闸门五处 / 探索性 run 不升级 / 台账首导入 studio-evolve / 单插件）
- 创建 `studio/changes/studio-paper/status.json` 与 `studio-paper/{skills,commands}/` 空骨架

### build-skills（同日）
- 八个技能全部 built：18 个 Python 脚本（stdlib）+ 2 个共享模块 `scripts/paper_ws.py` / `ledger_io.py`，4 个运行时 agent，5 份 references，7 个模板
- 构建中统一的三处接口：rq.yaml / criteria.yaml 按 id 嵌套的 map（`failure` 文字 + `pass`/`fail` 机器条件 + `metrics` 允许指标）；figure-plan.md 五列表；studio-evolve 导入的 seeds 缺省取其 config 的 repeat_runs
- 端到端冒烟：init → 论点句签字 → 判据冻结 → 导入成长台账 12 行 → 假设判定「支持」→ 结果表 → 冻结 → 证据包，全程无阻塞

### studio-docs：academic-paper blueprint（同日）
- 新增 `studio-docs/templates/blueprints/academic-paper.yaml`：10 章（Abstract…Conclusion + Appendix），`artifact_sources` 指向证据包文件，9 条 compliance（量化 claim 必带表号 / run_id；声称词必带比较对象与区间；证据包外的数字一律不得出现；探索性只进附录…）
- 新字段 `artifact_root`：plan-document 对声明了它的 blueprint 跳过 studio 映射、按目录解析；缺文件一律记 requires-input
- define-blueprint 内置表加一行；用 paper-e2e 的证据包核对：10 章全部映射到现有文件；validate_plugin studio-docs 无红项

### studio-paper：外部集成第一批（2026-10-23）
- `.mcp.json` 接 arxiv-mcp-server（按章节读 LaTeX、出 BibTeX）与 paper-search-mcp（多源检索去重、OA 下载），都走 `uvx`
- Sci-Hub 硬禁：`hooks/block_scihub.py` 在 PreToolUse 拦 `download_scihub` 与任何 `use_scihub=true`（exit 2 回报模型）；position-paper SKILL 写明硬规则，WebFetch 退为 MCP 不可达时的回退
- freeze-results 新增 `plot_figures.py`：读 `results/figures/RQ-*.csv` 出 pdf+png，SciencePlots 默认 `science,ieee,no-latex`（无 TeX 也能跑）；写 captions.md 与 plot-manifest.json，manifest 进 `figures_hash`，所以冻结后出图必须 iteration+1 重冻结
- plugin.json.draft 加 `mcpServers` / `hooks`；paper-e2e 冒烟：出图 1 张 → 重冻结 iteration 3 → 证据包含 figures
