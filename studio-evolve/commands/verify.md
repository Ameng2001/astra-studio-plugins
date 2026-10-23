---
description: Adjudicate one proposal in a sandbox under the dual gate (inner: strictly better on source cases; outer: no regression on a resampled real-only held-out set) and record the three growth readings.
---

# /studio-evolve:verify {ontology_id} {proposal_n}

调用 **evolve-verify** skill：把提案 `{proposal_n}` 放进沙箱副本，应用、重编译、结构与治理校验，再过两道关卡。

- 内关卡：来源案例上三维（长高逐层 / 长壮 / 长胖两比率）都不降、成本增幅 ≤ β、至少一项严格改善。
- 外关卡：每轮重抽的、只含真实案例的留出集上三维不回退；多骨干时每个骨干都要过。
- 裁决 `ACCEPT / REVISE / REJECT`，读数追加到 `growth-ledger.md`；`ACCEPT` 也不合入，合入在 `/studio-evolve:cycle` 里人确认后进行。

前置：clife-onto-engine 可用，且 YAML 案例能被引擎批量执行（缺则报 `CQ_RUNNER_MISSING`，先补这块）。本版无脚本，读数按规范手算，标 `MANUAL`。
