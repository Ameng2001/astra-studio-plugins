---
description: Validate a compiled clife-onto-engine ontology plugin — structure, governance audit, remaining FDE TODOs, and behavioral CQ.
---

# /studio-ontology:validate {ontology_id}

调用 **ontology-validate** skill：校验 `{target_dir}/` 的本体插件。

- **结构**：五要素引用无悬空（Action 的 guards/post_rules/writes、Link 的 from/to、Function 的 reads 都已声明）。
- **治理审计**：每条规则有 source/citations（对齐 OKF 来源可查），缺者列清单。
- **剩余 TODO**：仍 `NotImplementedError` 的 function-backed 规则 / Action handler / 空 seed。
- **行为 CQ**：FDE 填完骨架后，在 clife-onto-engine 上跑 `cq/golden.yaml`（query/action/rejected 期望）。

报告分四段，不改插件，只诊断 + 给修复指引。A(结构)、B(治理) 必须全过才可交付；D(CQ) 在 FDE 收尾后全绿即闭环。
