---
name: evolve-ground
description: Ground a five-element ontology IR in real data — for every Object / Mapping / function-backed Rule, issue a probe query against the declared data source, keep the sample as an Evidence row (IR §8), and mark unverifiable entries TODO(FDE). The dynamic-ontology-construction step of the self-evolving pipeline. Use once after ontology-map, and again whenever the data source or schema changes.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent
user-invocable: true
---

# Evolve Ground

给 `ontology-map.md` 的每一条「声称存在于数据里的东西」挂一个**探测查询与采样结果**。本体里的对象、映射、function-backed 规则，没有数据证据之前只是建模者的假设；接地之后才是本体。

这是 EvoOntology 的「证据接地提交」在五要素上的翻版：只有探测结果能验证的候选才算正式条目，探测记录保留为证据。证据率是长胖的分量之一（`${CLAUDE_SKILL_DIR}/../../references/growth-metrics.md` 的 `W_evid`）。

## Pre-check

1. 读 `studio/changes/$ARGUMENTS/ontology-map.md`。缺则让用户先跑 `/studio-ontology:map`。
2. 读 IR §6 Mapping Hints，取每个对象的 `store / table / key / columns`。没有 §6 或为空 → 停下，让用户先补映射（没有物理落点就没法探测）。
3. 确认数据源可达：按 `store` 类型找连接方式（`.studio-evolve/{id}/config.yaml` 的 `sources` 段，或用户当场给）。不可达的 store 整体标 `UNREACHABLE`，不猜。
4. 读 `${CLAUDE_SKILL_DIR}/../../references/proposal-schema.md` §1 的「证据随行」约束，和 `templates/evidence-row.md.tmpl`。

## Workflow

1. **列应接地条目**：
   - IR §1 每个 Object → 一条（主键存在、属性列存在、lifecycle 的状态值在数据里出现过）。
   - IR §6 每条 Mapping → 一条（表存在、key 列唯一、columns 都在）。
   - IR §4 每条 `backing=function` 的 Rule → 一条（规则要查的参考数据存在；declarative 规则不接地）。
   - IR §2 的 Link 若 §6 给了连接路径 → 一条（两端键能对上，抽样连接非空）。
2. **逐条探测**（用 Bash 跑只读查询；每条探测只取样，默认 `LIMIT 20`，不扫全表）：
   - 存在性：表 / 列 / 键在不在。
   - 类型与取值分布：枚举型属性的实际取值集合，数值型的范围与空值率。
   - 一致性：lifecycle 声明的状态集合 ⊇ 数据里出现的状态集合；枚举声明 ⊇ 实际取值。
3. **判定每条**：`verified`（探测全过）/ `mismatch`（存在但声明与数据不一致，写明差异）/ `missing`（探测不到）/ `unreachable`（数据源不可达）。
4. **写 IR §8 Evidence**（追加段，不改 §1–§7 的内容）：

   ```
   | element | kind | probe | sample | status | verified_at |
   |---------|------|-------|--------|--------|-------------|
   | Site | OBJECT | SELECT parcel_id, area_mu, region, site_type FROM geo_parcel LIMIT 20 | 20 行；site_type ∈ {沙地,盐碱,矿山} | mismatch: 声明含 草原，数据未出现 | 2026-10-23 |
   ```

   `mismatch` 与 `missing` 的条目**不改 IR 正文**，在 `status` 里写明，交给用户裁定（可能是数据缺、也可能是建模错）。
5. **调专家复核**：`ontology-architect` 看 `mismatch` 条目该改模型还是改数据；`rule-engineer` 看 function-backed 规则的参考数据是否够用。
6. **报告**：

   ```
   证据接地：{ontology_id}
   应接地 N 条：verified V · mismatch M · missing X · unreachable U
   证据率 W_evid = V / N
   mismatch 清单（需用户裁定）：…
   missing 清单（标 TODO(FDE): 待探测）：…
   下一步：/studio-evolve:simulate {ontology_id}
   ```

## Expected Inputs

`studio/changes/{ontology_id}/ontology-map.md`（含 §6）+ 可达的数据源。

## Expected Outputs

IR 追加 §8 Evidence；`.studio-evolve/{id}/ground-report.md`。

## Out of Scope

- 不改 §1–§7 的建模内容（mismatch 由人裁定后再改）。
- 不写全表扫描或任何写操作的查询。
- 不编译（接地后若改了 IR，由用户决定是否重跑 `/studio-ontology:compile`）。
