# 领域 Intake（填好样例）· 辣椒 chili

> 这是 [`templates/domain-intake.md.tmpl`](../templates/domain-intake.md.tmpl) 的一份**填好样例**（第二个领域，非草业），
> 对照 clife-onto-engine 里手写的 `plugins/chili`，展示"业务方填表 → ontology-map → 编译成插件"的对应。
> 给 FDE 做参照：一份填到这个程度的 intake，喂给 `ontology-map` 即可编出与 `plugins/chili` 五要素相近的插件。

---

## 0. 元信息（FDE 填）

| 字段 | 内容 |
|---|---|
| ontology_id | chili |
| 限界上下文 | 辣椒种植 + 产品分级 |
| 业务方/受访专家 | 海南辣椒合作社农技员、收购商 |
| FDE | {你} |
| 日期 | 2026-06-30 |

---

## 1. 业务概述（业务方 / PM 填）

- **这是什么业务**：给辣椒种植地块制定种植方案（选品种、定密度、下种子订单），给采收辣椒做分级与溯源。
- **核心用户与诉求**：种植户要选对适配本地的品种、定合理密度；收购商要客观分级、不收残次超标的货。
- **最想让系统"会做"的 3 件事**：① 制定种植方案 ② 辣椒分级出溯源码 ③ 查某地块适配哪些品种。

## 2. 角色 Actors（业务方 / 领域专家 填 → 角色对象 / Action 的 actor）

| 角色 | 能做什么 | 备注 |
|---|---|---|
| 种植户 | 制定种植方案、分级 | |
| 合作社 | 制定方案、分级 | |
| 农技员 | 制定方案、分级、审核 | |
| 收购商 | 分级 | |

## 3. 业务对象 Entities（领域专家 / FDE 填 → **Object**）

| 对象 | 主键 | 关键属性（名:类型[:单位][:必填]） | 状态流转（如有） | 数据在哪 |
|---|---|---|---|---|
| 地块 Field | field_id | area_mu:数:亩:必填, region:文:必填, soil_type:枚举 | 待规划→已规划→种植中→已采收 | 政务/合作社台账 |
| 种植方案 PlantingPlan | plan_id | variety:文, density:数:株每亩 | — | 农技系统 |
| 种子订单 SeedOrder | order_id | variety:文, qty_per_mu:数 | — | 采购系统 |
| 适配名录 AdaptListing | variety | region:文 | — | 品种区划库 |
| 分级样品 GradeSample | batch_id | length:数:cm, SHU:数, defect_rate:数 | — | 收购/检测点 |

## 4. 关系 Relationships（领域专家 / FDE 填 → **Link**）

> 辣椒这个上下文关系较少（不像草业的修复因果链）。核心是"品种—地块适配"，多以对象引用表达。可选地建为图关系：

| 关系 | 从 | 到 | 因果性质 |
|---|---|---|---|
| 地块 产出 分级样品 | Field | GradeSample | 派生 |
| 方案 下单 种子订单 | PlantingPlan | SeedOrder | 派生 |

## 5. 业务动作 Operations（业务方 + FDE 填 → **Action**）

| 动作 | 谁触发 | 输入 | 产出/写入 | 需人工审批？(谁) |
|---|---|---|---|---|
| 制定种植方案 | 种植户 | 地块、品种、密度、预算 | 种植方案、种子订单 | 是·农技审核员 |
| 辣椒分级 | 收购商 | 批次、检测值 | 分级样品(等级+溯源码) | 是·分级定价员 |

## 6. 派生指标 Metrics / KPIs（领域专家 填 → **Function**）

| 指标 | 读取哪些对象 | 返回 | 口径/公式 |
|---|---|---|---|
| 等级计算 | GradeSample | 等级 | 残次率≤2%且长度≥12cm→特级；≤5%→一级；≤10%→二级；否则等外 |

## 7. 业务规则 / 约束 Rules（领域专家 + FDE/规则工程师 填 → **Rule**）★最关键

| 规则 | 拦截还是告警 | 只看入参/要查数据 | 依据（标准号/方法学/专家） | 拦什么 |
|---|---|---|---|---|
| 品种适配 | 拦截+回滚 | 要查（地块 region 的适配名录） | 区域辣椒品种适应性区划；NY/T 辣椒生产技术规程 | 选了不适配本地的品种 |
| 残次拦截 | 拦截+回滚 | 要查（样品残次率） | GB/T 辣椒分级标准 | 残次率>15% |
| 预算非负 | 拦截 | 只看入参 | 通用常识 | 预算<0 |
| 密度合规 | 拦截 | 只看入参 | 辣椒高产栽培技术规程 | 密度不在 1000~4000 株/亩 |
| 检测项完整 | 拦截 | 只看入参 | 通用 | 缺 length/SHU/defect_rate |
| 种植角色权限 | 拦截 | 只看入参 | 通用 | 非种植户/合作社/农技员制定方案 |
| 分级角色权限 | 拦截 | 只看入参 | 通用 | 非种植户/合作社/农技员/收购商分级 |

## 8. 决策点 Decision Points（领域专家 填 → 也归 **Rule**）

- 如果 所选品种 ∉ 本地适配名录 → 拦截，提示改用适配品种（→ 品种适配规则）
- 如果 残次率 > 15% → 判不合格、禁止分级定价（→ 残次拦截规则）
- 如果 残次率≤2% 且 长度≥12cm → 特级（→ 等级计算 Function）

## 9. 数据来源 Data Sources（数据/IT 填 → 映射注册表）

| 对象 | 存哪（库/表） | 主键列 | 关键可查字段 |
|---|---|---|---|
| Field | postgis / chili_field | field_id | region, soil_type, area_mu |
| GradeSample | clickhouse / chili_grade | batch_id | length, SHU, defect_rate |

## 10. 能力问题 CQ（业务方 + FDE 填 → 验收用例）

- [查] 海南有哪些地块？
- [查] field_001 适配哪些辣椒品种？
- [做] 给 field_001 制定种植方案，种朝天椒，密度2000，预算500。
- [该被拦] 给 field_001 用紫色甜椒（不适配）制定方案 → 应被品种适配拦回。
- [该被拦] 残次率0.30 的批次分级 → 应被残次拦截拦回。

## 11. 治理要求 Governance（合规/法务 + FDE 填）

- 必须**人工复核**（HIL）：制定种植方案（农技审核员）、辣椒分级（分级定价员）。
- 必须**留审计**：方案制定、分级定价、溯源码签发。
- 数据密级：暂无特殊密级字段。

---

## 编译后对应（参照 plugins/chili）

填到上面这个程度，`ontology-map` → `ontology-compile` 会产出（与手写 `plugins/chili` 相近）：

| 要素 | 本样例产出 |
|---|---|
| Objects | Field, PlantingPlan, SeedOrder, AdaptListing, GradeSample |
| Functions | 等级计算（reads GradeSample，有口径→全自动） |
| Rules | 预算非负/密度合规/检测项完整/角色权限×2（declarative 全自动）+ 品种适配/残次拦截（function-backed 骨架+TODO+出处） |
| Actions | 制定种植方案（writes PlantingPlan,SeedOrder；hil 农技审核员）、辣椒分级（writes GradeSample；hil 分级定价员） |
| Mapping | Field→postgis、GradeSample→clickhouse |
| CQ | 含 1 查 + 1 做 + 2 该被拦 |

> 与草业内嵌示例对比：**同一份 intake 模板、不同领域，填出来的结构一致**——这正是"换行业零改模板/内核"在建模端的体现。
