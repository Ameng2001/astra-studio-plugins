---
description: Mine one window of runtime trajectories into atomic ontology edit proposals — divergence point → deficit type → single-hypothesis proposal. Does not adjudicate.
---

# /studio-evolve:attribute {ontology_id}

调用 **evolve-attribute** skill：读本窗口的轨迹与反馈标签，切块、分正负集，从正轨迹提共性、从负轨迹找分歧点并归到七类本体缺陷之一，写出 `studio/changes/{ontology_id}/proposals/{n}.md`。

- 顺手把每条有反馈的轨迹落成 `cases/real/` 的真实案例（外关卡的唯一来源）。
- 核对 growth-ledger 的拒绝签名，命中的提案不写（`SUPPRESSED`）。
- `ontology-evolver` 与 `rule-engineer` 复核分歧点与规则极性。

只写提案，不裁决、不改生产 IR。下一步 `/studio-evolve:verify {ontology_id} {n}`。
