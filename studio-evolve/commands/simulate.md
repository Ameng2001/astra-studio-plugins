---
description: Derive labelled, tiered competency cases from a grounded ontology (rules → rejected, actions → committed, links → multi-hop, functions → derived values) into the sim case bank. Sim cases feed the inner gate only.
---

# /studio-evolve:simulate {ontology_id}

调用 **evolve-simulate** skill：从本体结构派生带标签的 CQ 案例，写到 `.studio-evolve/{ontology_id}/cases/sim/`。

- 每条案例带 `tier`（1–4）、`family`、`origin: sim`、私有 `label`、2–3 个扰动 `variants`。
- 实参取自 IR §8 的采样值，不用编的；标签算不出的（function-backed 未填）跳过，不凑数。
- 领域专家抽检 10%，不符现实口径的删。

硬规则：`sim` 案例**只进内关卡**，不进留出集。前置：建议先跑 `/studio-evolve:ground`。
