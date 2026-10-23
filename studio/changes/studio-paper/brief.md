# Plugin Brief: studio-paper

> Created: 2026-10-23
> Domain: academic-paper（`studio/changes/academic-paper/`）

## Business Context

计算机学科课题组（PI + 博士 / 硕士）产出软件工程 / AI 系统方向的会议论文。今天文献散在各人笔记，RQ 与判据和导师口头约定，实验结果躺在日志里、失败与负结果无人登记，内审是导师投稿前三天读一遍，rebuttal 几天内赶出来。三个画像：林同学（博二主力作者，怕「漏了 X」被一句话打回）、王老师（PI，信不过事后整理的台账）、小陈（新入组硕士，接手没有台账的论文）。

第一个用户是本仓库自己：用这条管线写「自进化的行业基石大模型」这篇论文，实验来自 studio-evolve 的成长台账。

## Plugin Candidates

单插件 `studio-paper`（core, create），不拆 add-on。八个技能覆盖定位 → 开题（判据冻结）→ 实验台账（含负结果、假设聚合、结果冻结）→ 内审（三审稿人模拟评审）→ 投稿检查 → 回应表 → camera-ready。正文写作与导出交 studio-docs（需新增 `academic-paper` blueprint，输入是本插件的证据包）。详见 `skill-map.md`。

## Success Criteria

对应事件风暴的七个热点，前三个是验收线：

1. **HS-1 负结果不丢**：任何一次 run 一条命令入账；结果表只能从台账生成；台账行只增不删。验收：用 studio-evolve 的 growth-ledger 导入后，覆盖矩阵能列出每个 RQ 的全部 run（含失败）。
2. **HS-2 判据先于实验**：`criteria.lock` 不存在或哈希不符时，run 只能入探索性；探索性不能事后升级。验收：改 criteria.yaml 一个字后，新 run 被拒入正式。
3. **HS-3 近邻不漏扫**：投稿前与 rebuttal 前强制 `--rescan`，与上一版矩阵比对出新增近邻。
4. HS-4 声称词扫描命中率 100%（first / novel / significantly / SOTA / outperforms 全部进 claim 登记表）。
5. HS-5 两次内审各产出三份按当年评审表填的模拟评审 + Top-3 拒稿理由。
6. HS-6 基线 run 带偏差表与公平性字段，缺字段不能进主表。
7. HS-7 投稿前匿名扫描与版本快照自动生成。

## Notes

- 六处导师签字（论点句 / 判据冻结 / 结果冻结 / fatal 处置 / 投稿放行 / 回应表提交）是硬闸门，插件只预审，签字是人；提交动作由人在会议系统完成。
- 评审表字段每年变：运行时读当年 CFP，`references/review-forms/` 只放结构与通用清单。
- 入账负担是采用率的决定因素：自动采集的字段不让学生填；学生补填不超过五项。
- 文献检索走 WebFetch（Semantic Scholar / Crossref 公开 API），接口不可达时降级为用户贴 BibTeX / PDF；不依赖 MCP。
- 架构师的 core + add-on 拆分留作后续分裂点（条件见 domain-map.md）。
