---
description: Ground every Object / Mapping / function-backed Rule of an ontology IR in real data via probe queries — appends IR §8 Evidence.
---

# /studio-evolve:ground {ontology_id}

调用 **evolve-ground** skill：给 `studio/changes/{ontology_id}/ontology-map.md` 的对象、映射、function-backed 规则逐条发只读探测查询，把采样结果写成 IR §8 Evidence。

- 判定每条：`verified / mismatch / missing / unreachable`。
- `mismatch` 与 `missing` 不改 IR 正文，列清单交用户裁定（是数据缺还是建模错）。
- 报告证据率 `W_evid`（长胖的分量之一）。

前置：IR 已有 §6 Mapping Hints，数据源可达。一次性跑；数据源或 schema 变了再跑。
