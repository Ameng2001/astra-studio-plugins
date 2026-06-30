---
description: Build the five-element ontology IR (ontology-map.md) from business analysis artifacts.
---

# /studio-ontology:map {ontology_id}

调用 **ontology-map** skill：读上游分析产物（behavior-matrix / domain-canvas / process-flow / event-storm），调 `ontology-architect`(FDE) + `rule-engineer` + 领域专家，抽成五要素 IR，写到 `studio/changes/{ontology_id}/ontology-map.md`。

字段与上游映射规则见 `references/ontology-map-schema.md`。完成后用 `/studio-ontology:compile {ontology_id}` 编译。
