# 有效性检查清单（研究导师的闸门视角）

插件必须让这些错误**难以发生**；每条给出字段或检查落点。

| # | 错误 | 落点 | 规则 |
|---|---|---|---|
| 1 | 测试集污染 | `runs.jsonl.contamination` ∈ {known_overlap, unknown, excluded} | 未填 → 只能 exploratory；LLM 类工作另记模型版本与截止日期 |
| 2 | 混杂对比（模型 × 框架同时变） | `runs.jsonl.varied` 只列一个因素 | 多于一个 → `confounded: true`，写作阶段不得丢掉该标记 |
| 3 | 小测试集无区间 | `n`、`ci` 或多 seed 方差 | n < n_min 或无区间 → 不进主表，只进「探索性」 |
| 4 | 负结果不入账 | 运行入口自动写；行只增不删 | 作废需 `void_reason`；结果表只从台账生成 |
| 5 | 基线劣势 | `fairness`{budget, search_space, seeds, hardware} 与本方法并排 | 预算比 > `baseline_budget_ratio_max` → 告警 |
| 6 | 挑窗 / 挑种子 | 预登记的评测协议与 seed 数 | 事后改协议 = 新一轮，旧结果保留 |
| 7 | 事后改 RQ / 判据 | `criteria.lock` 哈希 + `changes.md` | 写作只引最终版；评审可见变更史 |
| 8 | claim 强于证据 | `review/claims.md` 回指 | 回指不到的 claim 不得出现在初稿 |
| 9 | 近邻漏扫 | `position-paper --rescan` | 投稿前、rebuttal 前未重扫 → 不放行 |
| 10 | 匿名与 artifact | `anonymity-report.md`、干净环境跑通 | 泄露或跑不通 → 阻塞 |

## 各阶段导师闸门（必须存在的产物）

| 阶段 | 产物 | 不放行 |
|---|---|---|
| 选题 → 定位 | 近邻三篇矩阵 | 说不出三篇；差异只是换数据集 / 换模型 |
| 定位 → 开题 | 一句话论点句 | 讲不清；用「和」连接两个贡献 |
| 开题 → 实验 | RQ + 预登记判据 + 划分方案 + 基线清单 | 没有失败判据；n 算不出区间；污染关系未说明 |
| 基线复现 → 主实验 | 偏差表 | 偏差不可解释却继续；基线劣势配置 |
| 实验 → 写作 | 冻结表图 + 完整台账 | 只有成功 run；有 RQ 无表；挑窗挑种子 |
| 写作 → 投稿 | claim 回指、威胁有效性、artifact 一键跑通 | claim 比证据强；artifact 只在作者机器能跑；匿名泄露 |
| 评审 → rebuttal | 每条意见的证据归类 | 「我们认为」回应数据质疑；补实验没入账 |
