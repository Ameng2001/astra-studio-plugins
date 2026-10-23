# Domain Map: 学术论文生产（academic-paper）

> Date: 2026-10-23
> Iteration: 1
> Lead perspective: architect（studio-planner 内置）

## Analysis Mode

⚡ 快速模式 —— 跳过领域画布、行为矩阵、机会评估。需要时可单独运行 `/studio-insight:domain-canvas` / `/studio-insight:behavior-matrix` / `/studio-insight:opportunity-brief academic-paper`。

## Artifacts

- 本文件（事件聚类 + 插件提案 + 集合结构决定）
- 上游：`event-storm.md`（26 事件 / 7 热点 / 4 项待裁定，已按建议裁定）

## Step 1 · 事件聚类

按数据亲和（共享 RQ / H 编号与台账）、角色归属、术语一致性分簇：

| 子域 | 事件 | 共享数据 | 主要角色 | 分类 |
|---|---|---|---|---|
| **D1 定位与立项** positioning + proposal | E1–E8 | 文献矩阵（有版本）· 论点句 · RQ / H 编号 · 冻结判据（哈希）· 图表规划 · 基线偏差表 | 学生起草，导师三处签字（D1 / D2 / D3 + D4） | **核心** |
| **D2 实验台账** ledger | E9–E13 | 台账（只增不删）· 假设状态 · 覆盖矩阵 · 冻结结果表 | 学生 / 运行入口 / 系统聚合，导师 D7 签字 | **核心**（唯一有状态增长、需要脚本的子域） |
| **D3 内审与回应** review + rebuttal | E15–E22, E24–E26 | claim 登记表 · 模拟评审 · 回应表 · 投稿版本快照 | 运行时审稿人角色 ×3，导师 D8 / D9 / D11 签字 | **核心**（第一篇论文必需；读 D1 + D2，不自带数据） |
| **D4 写作衔接** handoff | E14 | 证据包（论点 / RQ / 冻结图表 / 差异表 / claim 登记表） | 系统 | **支撑** → 交 studio-docs |
| **D5 文献检索与引用校验** search | E2 / E3 / E18 的外部查询 | Semantic Scholar / arXiv / Crossref 结果 | 系统 | **通用** → WebFetch 即可；MCP 可选 |
| **D6 录用决定** | E23 | — | 外部 AC / PC | 域外事件，只作状态输入 |

**语言测试**：D1–D3 的从业者用同一套词（RQ、判据、台账、claim、fatal / fixable），且全部挂在 RQ / H 编号这一个外键上 —— 它们是一个领域，不是三个。

## Step 4 · 插件提案

### 集合结构决定

架构师在事件风暴中建议 core + add-on（`paper-core` / `paper-review` / `paper-search`）。按 `plugin-architecture-guide.md` 的判定表复核：

| 信号 | 本域情况 | 指向 |
|---|---|---|
| 所有技能共享数据 / 上下文 | 是：全部以 RQ / H 编号 + 台账为主键；review 没有台账就空转 | 1 个插件 |
| 有些技能可选或领域特定 | 内审 / rebuttal 对「第一篇论文」不可选；检索是通用能力 | 不构成拆分理由 |
| 技能服务不同画像 | 三个画像在同一篇论文上协作，不是不同用户群 | 1 个插件 |
| 用户会独立装卸部分 | 单一课题组，无此需求 | 1 个插件 |
| 6+ 技能 | 风险：按子域粗分约 6–7 个 | skill-design 必须控制在 ≤ 6，用管线命令而非技能数承载阶段 |

**结论：单插件 `studio-paper`**，不建集合（本仓库的 marketplace 已是集合）。架构师的拆分建议记为**后续分裂点**：当出现第二个课题组只要台账不要评审、或检索 MCP 的不稳定开始拖累安装时，再把 D3 / D5 拆成 add-on —— 届时 RQ / H 编号与台账 schema 必须已冻结为对外接口。

### 候选表

| Plugin | Domain | Role | Action | Description | Dependencies | Priority |
|---|---|---|---|---|---|---|
| `studio-paper` | academic-paper（D1 + D2 + D3 + D4） | core | create | 论文生产管线：定位 → 开题（判据冻结）→ 实验台账（含负结果、假设聚合、结果冻结）→ 内审（三审稿人模拟评审、claim↔证据表）→ 回应表 → 投稿 / camera-ready 检查；产出证据包交 studio-docs 写作 | studio-core（init / status）；消费 studio-docs（写作、导出）；首个台账样例来自 studio-evolve | 1 |

**预期技能（粗列，skill-design 精化并控制 ≤ 6）**：
`position`（E1–E4：矩阵 / 近邻差异 / 论点句，含重扫）· `propose`（E5–E8：RQ / 判据冻结 / 图表规划 / 基线偏差表）· `ledger`（E9–E13：入账 / 聚合 / 偏离标记 / 结果冻结 / 证据包）· `review`（E15–E18：claim 回指 / 模拟评审 / fatal 处置 / 投稿前检查）· `rebuttal`（E20–E22, E24–E26：意见拆解 / 回应表 / 收窄分支 / camera-ready 一致性）· 可能的 `init-workspace`（stateful 特性自动生成）。

**MCP needs**：无必需项。文献检索 / 引用回查以 WebFetch（Semantic Scholar / Crossref 公开 API）实现，接口不可达时降级为用户贴 BibTeX / PDF；`.mcp.json` 留空。

**Pipelines**：一条主管线（选题 → 发表，以 RQ / H 编号串联），分阶段入口（`/studio-paper:position` … `/studio-paper:rebuttal`）+ 一个总编排 `/studio-paper:pipeline`。拒稿改投、rebuttal 补实验都是主管线上的回流边，不是独立管线 → **不是 multi-pipeline**。

**已裁定项（来自 event-storm 待裁定 1–4）**：
1. 导师闸门五处：E4 论点句、E6 判据冻结、E13 结果冻结、E17 fatal 处置、E19 投稿 → `hil-gated`。
2. 正式 run 必须在判据冻结之后；探索性 run 随时可入账，且不能事后升级为正式。
3. 台账第一个导入源：`.studio-evolve/{id}/ledger/growth-ledger.md`（三轴指标行 → 台账 run 行）；wandb / tensorboard 导入列为后续。
4. 形态：单插件（本节）。

**预期特性（供 skill-design 复核）**：`stateful`（`.studio-paper/{paper-id}/`：positioning / proposal / ledger / review）· `hil-gated` · `expert-scoped`（研究导师预审 + 三个审稿人角色随插件分发）· `kb-dependent`（评审表 / CFP 年度文档 + 有效性检查清单，放 references）· 非 `multi-pipeline`。

## Generic Capabilities (no custom plugin needed)

- 正文写作、章节并行生成、组装、DOCX / PDF 导出 → `studio-docs`（需新增 `academic-paper` blueprint，由 studio-paper 的证据包喂入；单向依赖，studio-docs 不感知 studio-paper）
- 文献检索 / 引用真实性回查 → WebFetch 调 Semantic Scholar / Crossref 公开 API
- 复现性元数据（commit / dirty / 配置哈希）→ git + 小脚本
- 工作区初始化 / 状态看板 → `studio-core`

## Collection Structure

- **Pattern**: single（挂在本仓库 marketplace 下，与 studio-evolve 平级）
- **Rationale**: 所有子域共享 RQ / H 外键与台账；单一课题组；检索是通用能力。分裂点与条件见上。
- **对其他插件的影响**：`studio-docs` 需加一份 blueprint（modify，另开变更工作区 `studio/changes/studio-docs/`，待 studio-paper 证据包格式定稿后再做）；`studio-evolve` 不改。

## 下一步

`/studio-planner:skill-design studio-paper`
