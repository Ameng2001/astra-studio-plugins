# studio-paper

计算机学科课题组的论文生产管线：定位 → 开题（判据冻结）→ 实验台账 → 结果冻结 → 内审 → 投稿检查 → 回应表 → camera-ready。正文写作与导出交给 `studio-docs`（`academic-paper` blueprint，输入是本插件的证据包）。

设计原则只有三条，都来自事件风暴里导师和审稿人的话：

1. **台账由运行入口写、只增不删、负结果照记**；结果表只能从台账生成。
2. **判据先于正式实验**：`criteria.lock` 不在或哈希不符，run 只能入探索性；探索性不能事后升级。
3. **六处导师签字**（论点句 / 判据冻结 / 结果冻结 / fatal 处置 / 投稿放行 / 回应表提交）：插件只预审，签字是人；提交动作由人在会议系统完成。

## 一篇论文怎么走

```
/studio-paper:init-workspace evolving-foundation --venue se:ICSE:2027 --import-evolve <ontology_id>
/studio-paper:position-paper evolving-foundation            # 🚪 D2 论点句
/studio-paper:propose-experiments evolving-foundation       # 🚪 D3 判据冻结
/studio-paper:record-run evolving-foundation --import studio-evolve   # 或每次 run 后入账
/studio-paper:freeze-results evolving-foundation            # 🚪 D7 → results/evidence-pack/
/studio-docs:generate            # academic-paper blueprint 写初稿
/studio-paper:review-internally evolving-foundation draft.md --pass 1   # 🚪 D8
/studio-paper:review-internally evolving-foundation draft.md --pass 2
/studio-paper:check-submission evolving-foundation          # 🚪 D9 → 人去会议系统提交
/studio-paper:respond-reviews evolving-foundation reviews.md            # 🚪 D11
/studio-paper:check-submission evolving-foundation --camera-ready
```

`/studio-paper:pipeline <paper-id>` 按四段串起来，每段暂停；`/studio-paper:status` 给导师看每篇卡在哪个闸门。

## 运行时工作区

`.studio-paper/{paper-id}/`（git 跟踪）：`config.yaml`（会议、阈值、台账来源、签字人）· `status.json`（阶段、六个闸门、计数、决定）· `positioning/ proposal/ ledger/ results/ review/ submission/ rebuttal/`。台账 schema 见 `references/ledger-schema.md`。

## 目录

- `skills/` 八个技能（六个带审批闸门）· `commands/` 十条命令 · `agents/` 四个运行时角色（导师预审 + 三个模拟审稿人）
- `scripts/` 共享模块：`paper_ws.py`（工作区 / 闸门记录）、`ledger_io.py`（台账只追加 I/O）
- `references/` 评审表结构（SE / ML）、有效性检查清单、声称词表、台账 schema
- `templates/` 运行时 config / status 模板

## 依赖

`studio-core`。写作需要 `studio-docs`；第一篇论文的台账来自 `studio-evolve`。文献检索走随插件配置的两个 MCP（`.mcp.json`：arxiv-mcp-server、paper-search-mcp，都用 `uvx` 启动，需要 uv）；Sci-Hub 被 `hooks/block_scihub.py` 硬拦。出图需要 `pip install matplotlib scienceplots`（无 LaTeX 也能跑）。

## 状态

v0.1.0 骨架 + 初稿实现（build-skills 产出）。设计文档在 `studio/changes/studio-paper/`，领域分析在 `studio/changes/academic-paper/`。
