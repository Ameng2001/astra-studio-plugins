# Ontology Map IR — 五段式中间表示规范

> `ontology-map.md` 是 studio-ontology 的核心中间表示（IR），夹在「业务分析」与「本体编译」之间。
> 它把 DDD/事件风暴的分析产物，归一成 clife-onto-engine 的**五要素**（Object / Link / Function / Rule / Action），
> 结构化到可被 `ontology-compile` 机械编译成可运行本体插件的程度。
>
> 写到：`studio/changes/{ontology_id}/ontology-map.md`
> 三件事每段都要交代：**字段定义**（IR 怎么写）· **上游来源**（从哪个分析产物的哪列抽）· **编译去向**（compiles to clife-onto-engine 的什么）。

---

## 设计原则

1. **五要素对齐元模型**：每段直接对应 clife-onto-engine 的 `ObjectType / LinkType / FunctionDef / RuleDef / ActionDef`，字段名尽量同名，编译即"填表"。
2. **限界上下文 = namespace**：一个 ontology_id = 一个 bounded context（domain-model 的一个插件候选）。多上下文 = 多 ontology-map。
3. **规则是 FDE 的核心产出**：规则在本段做**抽取 + 分类 + 挂出处**；function-backed 规则只声明意图与读取面，函数体留给编译期生成骨架 + 人填。
4. **无法确定就标 `TODO(FDE)`**：IR 允许留洞，但必须显式标注，供 ontology-validate 审计与 FDE 收尾。

---

## 0. Header — Namespace & Source

```
# Ontology Map: {ontology_id}

- ontology_id: grass
- bounded_context: 生态修复            # 来自 domain-model 的插件候选
- target_dir: plugins/grass            # 编译产物落点（clife-onto-engine 仓库内）
- source_artifacts:                    # 本次编译依据
    - studio/changes/{domain}/behavior-matrix.md
    - studio/changes/{domain}/domain-canvas.md
    - studio/changes/{domain}/processes/*.md
    - studio/changes/{domain}/event-storm.md
- iteration: 1
```

---

## 1. Objects（对象）— compiles to `ObjectType`

| 字段 | 含义 | 上游来源 |
|---|---|---|
| `name` | 对象类型名（PascalCase，英文标识） | behavior-matrix 的 **Data Entity Map** 的 entity；domain-canvas 的 **Owns** |
| `primary_key` | 业务主键属性名 | 实体的天然主键；无则 FDE 命名 `{obj}_id` |
| `properties` | `名:type[:unit][:required][:enum=v1\|v2][:密级]` 列表 | behavior-matrix 的 Data In/Out 字段；process-flow 状态字段 |
| `lifecycle` | 状态机 `states` + `initial`（可选） | process-flow 的状态流转 / event-storm 的过去式事件序列 |
| `source` | 数据来源标注（建表/来源系统） | behavior-matrix Data Entity Map 的 written/read by |

`type` 取值：`string \| number \| enum \| geometry \| list \| object \| ref(<Object>)`。

**表格式：**
```
| name | primary_key | properties | lifecycle | source |
|------|-------------|-----------|-----------|--------|
| Site | parcel_id | area_mu:number:亩:required, region:string:required, site_type:enum=沙地\|盐碱\|矿山\|草原, tenure:string:密级=confidential | draft,surveyed,treating,accepted (initial=draft) | 政务确权+遥感 |
| Degradation | deg_id | level:string, type:string | — | 退化分级标准 |
```

> **规则**：聚合根、有独立生命周期/属性集/被引用的 → Object；纯计算的派生量**不要**当 Object（去第 3 段 Function）。

---

## 2. Links（关系）— compiles to `LinkType`

| 字段 | 含义 | 上游来源 |
|---|---|---|
| `name` | 关系名（snake_case 动词，如 `treated_by`） | domain-canvas 的 **Relationship Map**；behavior-matrix 中 entity 间读写关系 |
| `from` / `to` | 源/目标 Object 类型 | 同上 |
| `cardinality` | `1:1 \| 1:N \| N:N` | FDE 判定 |
| `edge_semantics` | `root_cause \| hypothesis \| derivation` —— 内核多跳推理的终止判定依据 | process-flow 因果链：根因边=root_cause、假设/待查边=hypothesis、派生边=derivation |
| `properties` | 边属性（可选） | 关系上的限定（适应度、年份、比例等） |

**表格式：**
```
| name | from | to | cardinality | edge_semantics | properties |
|------|------|-----|-------------|----------------|-----------|
| suffers | Site | Degradation | N:N | hypothesis | level, area_pct |
| treated_by | Degradation | RestorationMethod | N:N | derivation | applicability |
| uses | RestorationMethod | SeedPack | 1:N | derivation | — |
```

> **edge_semantics 的判法**（关键，决定多跳推理停在哪）：这条边指向的是"根因/可终止结论" → `root_cause`；指向"需继续追溯的中间假设" → `hypothesis`；纯结构派生 → `derivation`。

---

## 3. Functions（函数 / 派生量）— compiles to `FunctionDef`

| 字段 | 含义 | 上游来源 |
|---|---|---|
| `name` | 派生量名 | behavior-matrix 的 **Data Out（计算得到的）**；opportunity-brief 的 KPI；process-flow 中的"算出来的量" |
| `reads` | 读取的 Object 类型列表 | 该计算汇聚了哪些实体 |
| `returns` | `type[:unit]` | — |
| `intent` | 一句话：算什么、怎么算（公式/口径） | 专家口径 / 标准公式 |

**约束**：Function 是**只读派生量**，无副作用。是"读模型/投影"，不是"动作"。

**表格式：**
```
| name | reads | returns | intent |
|------|-------|---------|--------|
| 载畜量 | MonitorObs, Site | number:羊单位 | 可食产草量×利用率 /(单位畜日采食量×放牧天数) |
| RFV分级 | ForageSample | string | 按 RFV 阈值映射到 特级/一级/二级/等外 |
```

---

## 4. Rules（规则 / 不变式）— compiles to `RuleDef`  ★FDE 核心

| 字段 | 含义 | 上游来源 |
|---|---|---|
| `name` | 规则名 | process-flow 的 **Decision Point 条件**；event-storm 的 **Hotspot**；SOP/标准条款 |
| `severity` | `hard`（违反即回滚） \| `soft`（advisory 告警） | 是否硬约束（"必须/不得"=hard） |
| `backing` | `declarative`（只看参数/上下文，轻） \| `function`（要查图谱/跨对象，重） | 校验只看入参→declarative；要查名录/年限/库存→function |
| `evaluation` | `pre`（guard，写前） \| `post_write`（不变式，写后看新状态） | 参数/权限类→pre；全局不变式→post_write |
| `on_actions` | 触发本规则的 Action 名 | behavior-matrix Action × 该规则的关联 |
| `intent` | 一句话：拦什么 | Decision Point 的 true/false 语义 |
| `source` | 依据（标准/方法学/专家口径/名录） | event-storm/process-flow 标注 + 专家访谈 |
| `citations` | 引用（标准号/方法学号），列表 | 同上；编译进 OKF `# Citations` |
| `check` | declarative：可直接写的判定式（如 `budget >= 0`）；function：留 `TODO(FDE): 查<什么>` | FDE |

**表格式：**
```
| name | severity | backing | evaluation | on_actions | intent | source | citations | check |
|------|----------|---------|------------|-----------|--------|--------|-----------|-------|
| 预算非负 | hard | declarative | pre | 出一地一方 | 预算不能为负 | 通用 | — | budget is None or budget >= 0 |
| 乡土合规 | hard | function | post_write | 出一地一方 | 种子包草种须在本地乡土名录内 | 乡土草种名录 | GB/T 37067, DB15/T 草原修复规程 | TODO(FDE): 查地块 region 的乡土名录，校验 species ⊆ 名录 |
```

> **规则三问**（FDE 抽规则时逐条问）：① 违反要不要拦回滚（hard/soft）？② 校验只看入参还是要查图谱（declarative/function）？③ 依据是哪条标准/口径（source/citations）—— 没有依据的规则要标 `TODO(FDE): 补依据`，ontology-validate 会审计出来。

---

## 5. Actions（动作）— compiles to `ActionDef`

| 字段 | 含义 | 上游来源 |
|---|---|---|
| `name` | 动作名（业务上有名字、需审计） | behavior-matrix 的 **Action**；event-storm 的 **Command/Event**；process-flow 泳道里的"做" |
| `description` | 一句话 | — |
| `params` | `名:type:required` 列表 | 该动作的入参（behavior-matrix Data In） |
| `guards` | 前置 declarative 规则名（第 4 段里 evaluation=pre 的） | — |
| `post_rules` | 写后规则名（第 4 段里 evaluation=post_write 的） | — |
| `writes` | 写入的 Object 类型列表 | behavior-matrix 的 Data Out（落库的实体） |
| `hil` | `reviewer_role / when`（如 `乡土草种合规官 / confidence<0.75`） | event-storm/skill-design 的 **hil-gated**；合规/对外凭证类一律设 HIL |
| `validate_supported` | 是否支持无副作用预演 | 造价/配比类→true |

**表格式：**
```
| name | params | guards | post_rules | writes | hil | validate |
|------|--------|--------|-----------|--------|-----|----------|
| 出一地一方 | site_id:ref(Site):required, species:list:required, budget:number | 预算非负,角色权限 | 乡土合规 | Project,SeedPack | 乡土草种合规官 / confidence<0.75 | true |
```

> **判 Action vs Function**：改变世界状态、需留痕 → Action；只读算个数 → Function。一切"对外凭证/派工单/评级定价/审批"都是 Action 且多半带 HIL。

---

## 6. Mapping Hints（映射注册表线索）— compiles to `mappings/objects.yaml`

| 字段 | 含义 | 上游来源 |
|---|---|---|
| `object` | 对象类型 | 第 1 段 |
| `store` / `table` / `key` | 物理落点（postgis/clickhouse/...、表、主键列） | behavior-matrix Data Entity Map 的来源系统 |
| `columns` | 落原生列的字段（ASCII 字段可索引/谓词下推） | 第 1 段 properties 里的可索引字段 |
| `materialization` | `virtual \| materialized \| hybrid` | 主数据/推理主干→materialized；明细/时序→virtual；默认 hybrid |

```
| object | store | table | key | columns | materialization |
|--------|-------|-------|-----|---------|-----------------|
| Site | postgis | geo_parcel | parcel_id | area_mu, region, site_type | hybrid |
| ForageSample | clickhouse | forage_qc | batch_id | CP, NDF, ADF, RFV | hybrid |
```

---

## 7. CQ — Competency Questions（能力验证问题）— compiles to `cq/golden.yaml`

每条 CQ 标注期望的处理方式（query/action），作为 ontology-validate 的验收用例 + 黄金问题集。

```
- q: 这块盐碱地属于什么退化等级？应该用哪套种子包配方？
  expect: query           # 应路由到 OQL 查询
- q: 能否对 parcel_001 自动生成修复方案工单并派发？
  expect: action(出一地一方)
- q: 给这块地用紫花苜蓿（非乡土）出方案
  expect: rejected(乡土合规)   # 验证本体兜底
```

来源：event-storm 的 Decision Points + 七类用户的高频提问（如果有 persona/journey 产物）。

---

## 完整性自检（写完 ontology-map 后逐项核对）

- [ ] 每个 Object 有 primary_key；被 Action 写入的 Object 都在第 1 段声明
- [ ] 每条 Link 的 from/to 都是已声明 Object；edge_semantics 已判定
- [ ] 每条 Rule 标了 severity + backing + evaluation；function-backed 的 check 标了 `TODO(FDE): 查…`
- [ ] 每条 Rule 有 source（无则 `TODO(FDE): 补依据`）
- [ ] 每个 Action 的 guards/post_rules 都在第 4 段存在；writes 都在第 1 段存在
- [ ] 合规/对外凭证类 Action 都设了 hil
- [ ] CQ 至少覆盖：一个 query、一个 commit action、一个 rejected（验证规则兜底）

> 这份 IR 一旦写全，`ontology-compile` 即可机械地编译成 clife-onto-engine 插件（见 `references/compile-rules.md`）。
