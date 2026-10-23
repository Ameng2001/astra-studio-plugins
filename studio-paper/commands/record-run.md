---
description: Record one experiment run into the ledger — auto-collect git/config/seed, classify formal vs exploratory, roll up hypothesis status, flag thesis drift; --import bulk-loads a studio-evolve growth ledger
argument-hint: [paper-id] <run-dir|metrics.json> [--rq RQ-n] [--kind main|baseline|ablation] [--import studio-evolve]
---

# /studio-paper:record-run {paper-id} <run>

一次 run 结束后入账：自动采集 commit / dirty / 配置哈希 / seed / 耗时，学生补填不超过五项；系统按判据是否冻结、字段是否齐、n 是否达阈值判 formal / exploratory（探索性不可事后升级）；重算假设状态与 RQ 覆盖矩阵；负结果与论点冲突时写 drift.md。无闸门。

Use skill: "record-run"
