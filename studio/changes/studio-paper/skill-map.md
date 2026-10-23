# Skill Map: studio-paper

> Date: 2026-10-23
> Action: create
> Domain: academic-paper（`studio/changes/academic-paper/domain-map.md`）
> Lead perspective: architect；闸门与硬规则来自 research-advisor / peer-reviewer

## Step 1 · 能力清单（用户故事）

| # | 作为 | 我想 | 以便 | 事件 |
|---|---|---|---|---|
| C1 | 林同学 | 为一篇论文建一个工作区，记录目标会议、阈值、台账来源 | 后面所有产物有一个家，导师能看到每篇的状态 | — |
| C2 | 林同学 | 把读过的论文登进矩阵，并在任何时候重扫近邻 | 新颖性不是在内审才暴露 | E2 / E3 |
| C3 | 王老师 | 看最近邻三篇差异表后签字放行一句话论点句 | 后面的 RQ 都挂在这句话下 | E3 / E4 🚪 |
| C4 | 林同学 | 给 RQ / 假设编号，写下每条的成功 / 失败判据、n、数据与预训练语料关系、基线清单、每个 RQ 对应哪张图 | 判据先于实验 | E5 / E7 |
| C5 | 王老师 | 签字冻结判据（时间戳 + 哈希 + commit） | 事后改判据有痕 | E6 🚪 |
| C6 | 林同学 | 一次 run 结束后一条命令入账，自动带 commit / 配置哈希，系统判它是正式还是探索性 | 负结果不丢，登记负担低 | E9 |
| C7 | 林同学 | 把 studio-evolve 的 growth-ledger 批量导入台账 | 第一篇论文不用手填 | E9 |
| C8 | 系统 | 每次入账后重算假设状态与 RQ 覆盖矩阵，负结果与论点冲突时标记偏离 | 导师看到的是全貌 | E10 / E11 |
| C9 | 林同学 | 改 RQ / 判据时留痕（谁、何时、为何），旧版本保留 | pilot 后可改，但不是事后编造 | E12 |
| C10 | 王老师 | 从台账生成结果表与图后签字冻结，交付证据包给 studio-docs | 写作只从冻结结果出发 | E13 / E14 🚪 |
| C11 | 林同学 | 在初稿上把每条量化 claim 回指台账，扫声称词 | 措辞不强于证据 | E15 |
| C12 | 王老师 | 让三个审稿人角色按目标会议评审表做模拟评审，得到 claim↔证据表和 Top-3 拒稿理由，签字处置 fatal 项 | 内审前移，改结构代价低 | E16 / E17 🚪 |
| C13 | 王老师 | 投稿前过一遍检查（近邻重扫、匿名、artifact 干净环境、哈希快照）后放行 | 不靠记忆的清单 | E18 / E19 🚪 |
| C14 | 小陈 | 把外审意见逐条拆解分类、按评审人可移动性排序、分诊哪些补实验 | 三天内不乱 | E20 |
| C15 | 小陈 | 产出逐条回应表（每行带证据指针和 run_id），新数据削弱 claim 时走收窄分支，导师签字后才提交 | 无表不提交 | E21 / E22 🚪 |
| C16 | 林同学 | camera-ready 前核对论文数字与 artifact 一致、修订清单逐条兑现 | 不被撤稿 | E24 / E25 |
| C17 | 王老师 | rebuttal 后记录分数移动与哪类回应有效 | 组内经验沉淀 | E26 |

## Step 1.5 · 插件特征

| 特征 | 检测 | 依据 | 下游影响 |
|---|---|---|---|
| stateful | ✅ | 台账跨会话增长；一篇论文从选题到发表数月；多篇并行 | `.studio-paper/{paper-id}/` 运行时工作区 + `init-workspace` 技能 + config / status 模板 |
| hil-gated | ✅ | 六处导师签字：论点句 D2、判据冻结 D3、结果冻结 D7、fatal 处置 D8、投稿放行 D9、回应表提交 D11 | 每个闸门独占一个技能，SKILL.md 带 `## Approval Gate`；决定记入 status.json |
| kb-dependent | ✅ | 目标会议评审表与 CFP（年度变）、有效性检查清单、声称词表、台账 schema | 插件级 `references/`（四份文档，跨技能共享）；不建伴生 KB 插件 |
| multi-pipeline | — | 一条主管线（选题 → 发表），拒稿改投与补实验是回流边 | 一个总编排命令 `pipeline` + 每技能一个命令 |
| expert-scoped | ✅ | 规划用 PM / 架构师；运行时需要研究导师预审 + 三个审稿人角色（近邻作者 / 方法统计 / 相邻领域读者） | `studio-paper/agents/` 随插件分发四个 agent；`studio/agents/` 的两个是规划版本 |

## Step 2 · 技能划分

硬约束：**一个技能至多一个闸门**。六个闸门 → 六个带闸门技能；加 stateful 标准技能 `init-workspace` 和无闸门但脚本最重的 `record-run`，共 **8 个**。domain-map 曾希望 ≤ 6，闸门规则优先；用户面向的阶段仍是四段（定位 / 开题 / 实验 / 评审），由 `pipeline` 命令编排。

合并裁定：
- E8 基线复现并入 `record-run`（`kind: baseline` 的 run，附偏差表；D4 是判定不是签字）。
- E7 图表规划并入 `propose-experiments`（与判据同一次冻结）。
- E23 录用决定是外部输入，作为 `check-submission` / `respond-reviews` 的状态参数。
- E26 经验回写并入 `respond-reviews` 的收尾。

## Skills

### init-workspace
- **Description**: 为一篇论文创建 `.studio-paper/{paper-id}/` 运行时工作区：目标会议与评审表、阈值（n_min / seeds_min）、台账来源、目录骨架、status.json。
- **Inputs**: paper-id、目标会议（se / ml 家族 + 年份）、可选台账导入源（如 `.studio-evolve/{id}`）
- **Outputs**: `.studio-paper/{paper-id}/{config.yaml,status.json,positioning/,proposal/,ledger/,results/,review/,rebuttal/,submission/}`
- **Out of scope**: 不写任何研究内容；不初始化 `studio/`（那是 studio-core）
- **Complexity**: Moderate
- **allowed-tools**: Read, Write, Bash, Glob
- **Preconditions**: 项目根可写；paper-id 未被占用
- **HIL checkpoint**: no

### position-paper
- **Description**: 建立 / 更新带版本的文献矩阵，对最近邻三篇做差异对照，起草一句话论点句，交导师签字；`--rescan` 模式只做近邻重扫并与上一版矩阵比对（投稿前、rebuttal 前强制）。
- **Inputs**: 选题描述；用户给的 PDF / BibTeX / URL；可选 WebFetch 查 Semantic Scholar / arXiv
- **Outputs**: `positioning/literature-matrix.md`（版本号递增）、`positioning/nearest-diff.md`（每条差异附原文出处）、`positioning/thesis.md`（含签字记录）
- **Out of scope**: 不写 RQ；不下载论文全文到仓库；不做引用格式化（写作阶段）
- **Complexity**: Moderate（矩阵版本号与 diff 比对用小脚本；检索接口不可达时降级为手工输入）
- **allowed-tools**: Read, Write, Edit, Bash, Glob, Grep, WebFetch, Agent
- **Preconditions**: 工作区存在
- **HIL checkpoint**: yes — D2 论点句放行（research-advisor 预审：一句话、无「和」字拼两个贡献、三篇差异不是换数据集级）
- **KB needs**: 无（检索为实时数据，走 WebFetch）

### propose-experiments
- **Description**: 给 RQ / 假设编号立项，写每条的成功 / 失败判据、样本量与区间要求、数据与预训练语料关系、基线清单与公平性预算、每个 RQ 对应的图表规划；导师签字后冻结（时间戳 + 内容哈希 + git commit 写入 lock 文件）。改判据走 `--amend`，旧版本保留并留痕。
- **Inputs**: `positioning/thesis.md`；学生草稿
- **Outputs**: `proposal/rq.yaml`（RQ-n / H-n 编号，全链路外键）、`proposal/criteria.yaml` + `proposal/criteria.lock`、`proposal/figure-plan.md`、`proposal/baselines.md`、`proposal/changes.md`（变更留痕）
- **Out of scope**: 不跑实验；不复现基线（record-run）；不生成图
- **Complexity**: Moderate（`freeze_criteria.py`：哈希 / 时间戳 / commit / lock 校验）
- **allowed-tools**: Read, Write, Edit, Bash, Glob, Grep, Agent
- **Preconditions**: thesis.md 已签字；未签字 → 拒绝立项
- **HIL checkpoint**: yes — D3 判据冻结（research-advisor 预审：每个 RQ 有「什么算失败」、n 算得出区间、污染关系已说明、基线预算并排）
- **KB needs**: `references/validity-checklist.md`

### record-run
- **Description**: 一次实验结束后把它入账：自动采集 commit / dirty / 配置哈希 / seed / 耗时，学生补填 rq / metric / n / 污染关系 / 公平性字段；系统按判据是否冻结、字段是否齐、n 是否达阈值、是否混杂判 formal / exploratory（探索性不可事后升级）；入账后重算假设状态与 RQ 覆盖矩阵，负结果与论点冲突时标记偏离；`kind: baseline` 的 run 附原论文偏差表；`--import studio-evolve` 批量导入 growth-ledger。
- **Inputs**: run 的输出目录或指标 JSON；`proposal/rq.yaml` + `criteria.lock`；可选 growth-ledger.md
- **Outputs**: `ledger/runs.jsonl`（只追加；作废行带 `void_reason`）、`ledger/hypotheses.md`（支持 / 证伪 / 待定 + 覆盖矩阵）、`ledger/drift.md`（偏离标记，回流提示）、`ledger/baseline-deviation.md`
- **Out of scope**: 不跑实验；不改 RQ（propose-experiments --amend）；不生成结果表（freeze-results）
- **Complexity**: Script-heavy（`record_run.py` 采集 + schema 校验 + 追加；`rollup_hypotheses.py` 确定性聚合；`import_evolve_ledger.py`）
- **allowed-tools**: Read, Write, Edit, Bash, Glob, Grep
- **Preconditions**: 工作区存在；formal 需 criteria.lock 存在且与 criteria.yaml 哈希一致
- **HIL checkpoint**: no（D5 / D6 由系统判定 + 提示导师，不阻塞入账）
- **KB needs**: `references/ledger-schema.md`、`references/validity-checklist.md`

### freeze-results
- **Description**: 只从台账的 formal 行生成每个 RQ 的结果表与图（按 figure-plan），检查每个 RQ 有表、无挑窗挑种子、被排除 run 已披露理由、小样本未混入主表；导师签字冻结后打包证据包（论点 / RQ / 冻结图表 / 差异表 / claim 登记表骨架）交 studio-docs。
- **Inputs**: `ledger/runs.jsonl`、`proposal/figure-plan.md`、`criteria.lock`
- **Outputs**: `results/tables/*.md`、`results/figures/*`、`results/coverage.md`、`results/freeze.lock`、`results/evidence-pack/`（studio-docs `academic-paper` blueprint 的输入）
- **Out of scope**: 不写正文；不手填任何数字；冻结后改实验 = iteration + 1
- **Complexity**: Moderate（`build_tables.py`、`coverage_check.py`）
- **allowed-tools**: Read, Write, Bash, Glob, Grep, Agent
- **Preconditions**: 每个 RQ 至少一条 formal 行；无未处置的 drift
- **HIL checkpoint**: yes — D7 结果冻结（research-advisor 预审）
- **KB needs**: `references/validity-checklist.md`

### review-internally
- **Description**: 对初稿做模拟评审：先扫声称词（first / novel / significantly / SOTA）并把每条量化 claim 回指台账 run_id 与表号，再派三个审稿人角色按目标会议当年评审表各写一份评审（分数 + 置信度），合成 claim↔证据对照表与 Top-3 拒稿理由（fatal / fixable）；导师签字处置 fatal（收窄 claim / 回到设计）；fixable 入待补队列。写作未完时跑一次，投稿冻结前再跑一次。
- **Inputs**: 初稿 markdown（studio-docs 产出）；`results/evidence-pack/`；`ledger/runs.jsonl`；`config.yaml` 的目标会议
- **Outputs**: `review/claims.md`（claim 登记表：原句 / 强度词 / 表号 / run_id / n / 判定）、`review/internal-{n}/{nearest-author,methodology,adjacent}.md`、`review/internal-{n}/verdict.md`（Top-3 + 处置签字）、`ledger/todo-runs.md`（fixable 指派）
- **Out of scope**: 不改稿（回 studio-docs）；不补实验（record-run）
- **Complexity**: Moderate（`scan_claims.py` 声称词与回指检查；评审本身是 Agent）
- **allowed-tools**: Read, Write, Bash, Glob, Grep, Agent
- **Preconditions**: freeze.lock 存在；初稿路径可读
- **HIL checkpoint**: yes — D8 fatal 处置
- **KB needs**: `references/review-forms/`（SE / ML 评审表 + 前十分钟清单）、`references/claim-words.md`

### check-submission
- **Description**: 投稿前 / camera-ready 前的放行检查：近邻重扫（调 position-paper --rescan）、声称词再扫、双盲清单（作者名 / 仓库链接 / 致谢 / 自引措辞）、artifact 在干净环境跑通、修订清单逐条勾销（major revision / shepherd）、论文数字与 artifact 一致；全部通过后生成版本快照（代码 / 数据 / 台账哈希），导师放行；提交动作由人在会议系统完成。
- **Inputs**: 稿件、artifact 目录、`review/internal-{n}/verdict.md`（fatal 须已处置）、可选修订清单
- **Outputs**: `submission/checklist-{submit|camera-ready}.md`、`submission/snapshot.json`、`submission/anonymity-report.md`
- **Out of scope**: 不替人点提交；不做会议模板 / LaTeX（studio-docs）
- **Complexity**: Moderate（`anonymity_scan.py`、`snapshot.py`；artifact 干净环境跑通用 Bash）
- **allowed-tools**: Read, Bash, Glob, Grep, Write
- **Preconditions**: 两次 internal review 已完成且无未处置 fatal（camera-ready 模式：修订清单存在）
- **HIL checkpoint**: yes — D9 投稿放行
- **KB needs**: `references/review-forms/`（当年 CFP 的匿名与 artifact 要求）

### respond-reviews
- **Description**: 收到外审后逐条拆解（不合并不遗漏），每条分类 fatal / fixable / 误解 / 澄清，按评审人可移动性排序，分诊哪些值得补实验并给算力 / 时间预算；补实验入账后生成逐条回应表（ID / 原话 / 分类 / 回应 / 证据指针 / run_id / 修改承诺 / 是否新 claim），新数据削弱原 claim 时强制走收窄分支并同步 camera-ready 清单；字数预算裁剪；导师签字后由人提交；决定下来后记录分数移动与有效回应类型。
- **Inputs**: 外审原文；`ledger/runs.jsonl`；`review/claims.md`；会议的 rebuttal 规则（字数 / 页数 / 轮次）
- **Outputs**: `rebuttal/reviews.md`（拆解分类 + 排序）、`rebuttal/todo-runs.md`、`rebuttal/responses.md`（回应表）、`rebuttal/outcome.md`（E26）
- **Out of scope**: 不跑补实验（record-run）；不改 RQ；不引入无数据的新 claim
- **Complexity**: Moderate（`budget_check.py` 字数预算；表生成与分类是 Agent）
- **allowed-tools**: Read, Write, Edit, Bash, Glob, Grep, Agent
- **Preconditions**: snapshot.json 存在（投稿版本）；回应表引用的 run_id 都在台账里
- **HIL checkpoint**: yes — D11 新数据削弱 claim 的收窄 + 提交前签字
- **KB needs**: `references/review-forms/`（rebuttal 规则）、`references/claim-words.md`

## Data Flow

```
[init-workspace]
    │ .studio-paper/{paper-id}/
    ▼
[position-paper] ──🚪D2── thesis.md ───────────────────────────────┐
    │ (required)                                                   │ --rescan（check-submission / respond-reviews 前强制）
    ▼                                                              │
[propose-experiments] ──🚪D3── rq.yaml + criteria.lock + figure-plan│
    │ (required)                                                   │
    ▼                                                              │
[record-run] ×N ──── runs.jsonl → hypotheses.md → drift.md ────────┤  drift → 回流 position-paper / propose-experiments --amend
    │ (required: 每个 RQ ≥1 formal)                                │
    ▼                                                              │
[freeze-results] ──🚪D7── tables/ figures/ evidence-pack/ ─────────┤
    │                                                              │
    ▼ (studio-docs generate → 初稿)                                │
[review-internally] ×2 ──🚪D8── claims.md verdict.md ──┐           │
    │                         fixable → todo-runs → [record-run]   │
    ▼                                                  │           │
[check-submission] ──🚪D9── snapshot.json  ◄───────────┘           │
    │ (人提交)                                                     │
    ▼ 外审                                                         │
[respond-reviews] ──🚪D11── responses.md ── 补实验 → [record-run]   │
    │ (人提交)                                                     │
    ▼ 决定：accept → [check-submission --camera-ready]；reject → [position-paper --rescan] ──┘
```

必需边：init → position → propose → record → freeze → review → check → respond。
可选边：record-run 可在 propose 之前以 exploratory 入账；review-internally 可在无初稿时只跑 claims 扫描（对 evidence-pack）。
无环：回流边都通过人决定 + iteration 递增，不是自动循环。

## Complexity Summary

| Skill | Tier | Scripts needed | MCP needed | HIL |
|---|---|---|---|---|
| init-workspace | Moderate | `init_workspace.py` | — | no |
| position-paper | Moderate | `matrix_version.py`（版本 + 重扫 diff） | —（WebFetch 可选） | D2 |
| propose-experiments | Moderate | `freeze_criteria.py` | — | D3 |
| record-run | Script-heavy | `record_run.py` · `rollup_hypotheses.py` · `import_evolve_ledger.py` | — | no |
| freeze-results | Moderate | `build_tables.py` · `coverage_check.py` | — | D7 |
| review-internally | Moderate | `scan_claims.py` | — | D8 |
| check-submission | Moderate | `anonymity_scan.py` · `snapshot.py` | — | D9 |
| respond-reviews | Moderate | `budget_check.py` | — | D11 |

共享脚本库：`scripts/ledger_io.py`（runs.jsonl 读写与 schema）、`scripts/gate.py`（签字记录写 status.json）。

## Plugin Traits

| Trait | Detected | Evidence |
|---|---|---|
| stateful | ✅ | `.studio-paper/{paper-id}/` 跨会话；台账只增 |
| hil-gated | ✅ | D2 / D3 / D7 / D8 / D9 / D11 六处签字，各占一技能 |
| kb-dependent | ✅ | `references/review-forms/`（年度）、`validity-checklist.md`、`claim-words.md`、`ledger-schema.md` |
| multi-pipeline | — | 单主管线 + 回流边 |
| expert-scoped | ✅ | 运行时 agents：research-advisor（预审）、reviewer-nearest-author、reviewer-methodology、reviewer-adjacent |

## Runtime Workspace（stateful）

```
.studio-paper/{paper-id}/
├── config.yaml          # venue{family,name,year} · thresholds{n_min,seeds_min} · ledger_sources · rebuttal{limit}
├── status.json          # stage · iteration · gates{thesis,criteria,results,review,submission,rebuttal}{approved_at,by,hash}
├── positioning/         # literature-matrix.md(v) · nearest-diff.md · thesis.md
├── proposal/            # rq.yaml · criteria.yaml · criteria.lock · figure-plan.md · baselines.md · changes.md
├── ledger/              # runs.jsonl · hypotheses.md · drift.md · baseline-deviation.md · todo-runs.md
├── results/             # tables/ · figures/ · coverage.md · freeze.lock · evidence-pack/
├── review/              # claims.md · internal-{n}/
├── submission/          # checklist-*.md · snapshot.json · anonymity-report.md
└── rebuttal/            # reviews.md · todo-runs.md · responses.md · outcome.md
```

## Commands

每技能一条同名命令，加 `pipeline`（四段编排：定位 → 开题 → 实验 → 评审，每段结束暂停）和 `status`（每篇在哪个闸门，供王老师）。

## Implementation Order

1. `init-workspace` — 无依赖；定下 config / status 模板与目录
2. `record-run` — 台账 schema 是整个插件的主键；先用 studio-evolve 的 growth-ledger 导入跑通（第一篇论文立即可用）
3. `propose-experiments` — criteria.lock 的格式被 record-run 的 formal 判定消费
4. `freeze-results` — 依赖 runs.jsonl + figure-plan
5. `position-paper` — 相对独立；`--rescan` 被后两者调用
6. `review-internally` — 依赖 evidence-pack 与 references/review-forms
7. `check-submission` — 依赖 verdict + rescan
8. `respond-reviews` — 依赖 snapshot + claims + 台账

> 顺序 2 在 3 之前是刻意的：先让台账能装数据，再加「正式 / 探索性」的锁；对第一篇论文而言，今天就能把 studio-evolve 的成长台账导进来。
