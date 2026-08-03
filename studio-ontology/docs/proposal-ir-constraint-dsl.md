# 提案 · IR 约束 DSL：让 function-backed 规则也能被确定性编译

> **状态：提案，未实现。** 第一项任务就是「先审后做」——
> **IR 是 `studio-ontology` 与 `clife-onto-engine` 之间的契约，一改，所有已编译插件重新生成的结果都会变。**
>
> 提出日期：2026-08-04　·　影响范围：`studio-ontology`（引擎零改动）

---

## Why

`studio-ontology` 编译 IR 时，**只有 `backing: declarative` 的规则能被全自动写完函数体**（`compile-rules.md` §4a，从 `check` 列直译）。`backing: function` 的规则只出骨架 + `raise NotImplementedError`（§4b），因为 IR 的 `check` 列对它们只承载一句自然语言 `TODO(FDE): 查<什么>`——**没有任何可确定性翻译的结构**。

这一格的代价是可量化的（口径取自 `clife-onto-engine`）：

| 来源 | declarative | function | 全自动占比 |
|---|---|---|---|
| 康养项目（7 域全量交付，47 条规则） | 18 | 29 | **38%** |
| `plugins/grass`（17 条 `@spi.rule`） | 7 | 10 | **41%** |

**即约六成规则的函数体要 FDE 手写。** 两个后果：

1. **交付周期压在 FDE 身上**——而规则恰恰是 `§4 Rules ★FDE 核心` 那一格，量最大。
2. ⚠️ **跨行业结构会漂**——内核靠 SPI 契约吃饭，手写十家就有十种写法；`README` 的「换行业零改内核」靠的是插件结构一致。

**但"让 LLM 生成 function 函数体"是错的解法**，两条理由：

- **它把不确定性放回执行层**，直接违反引擎题眼「**确定性边界置于模型之外**」。规则是要拦住动作的东西，用概率产物去拦，等于没拦。
- **生成的 Python 谁审？** 领域专家看不懂 `@spi.rule` 装饰器——**这正是 IR 存在的全部理由**。用 LLM 直出 Python，等于把签字权从领域专家手里又拿回给工程师。

**真正的缺口不是"生成能力"，是"IR 缺一种表达跨对象约束的语法"。**

**证据**：把 `plugins/grass` 全部 **10 条 function-backed 规则**逐条读完，**10 条全部落在五种模式内，没有一条需要图灵完备逻辑**：

| 模式 | 实例 |
|---|---|
| 集合 / 名录包含 | 乡土合规 · 设备支持作业 · 目标性状可预测 |
| 查表阈值 / 区间 | 方法学年限合规 · 霉变拦截 · 参数在能力域内 · 播量区间 |
| 关系存在性 / 多跳可达 | 立地适配（走 `adapts_to` 真边）· 权属清晰 |
| 聚合等式 / 不等式 | 混播配比（Σratio == 100%） |
| 对象存在 + 字段谓词 | 亲本合规 |

## What Changes

- **IR `§4 Rules` 的 `check` 列增加一种结构化形式**（约束 DSL），与现有两种形式并存：
  - 现有 ① declarative 布尔表达式（`budget >= 0`）—— **不变**
  - 现有 ② 自然语言 `TODO(FDE): …` —— **保留为兜底**
  - **新增 ③ 约束 DSL**：五个原语 `in_set` / `between` / `link_exists` / `agg` / `exists`
- **`compile-rules.md` §4b 增加 4b-1 分支**：`check` 命中 DSL 时，**确定性直译**成读 `ctx` 的 Python；命中不了才走现有的骨架 + `TODO(FDE)`。
- **`ontology-map` 技能**：抽规则时优先尝试用 DSL 表达；表达不了才写 `TODO(FDE)`——**并在 IR 里显式标注"为什么表达不了"**。
- **`ontology-validate`**：报告新增一行「本插件 function 规则中，DSL 覆盖 N / M」。
- **非破坏**：三种 `check` 形式并存；既有 IR 一字不改仍按原样编译；`RuleDef` 的 `backing` 语义不变（DSL 生成的仍是 `Backing.FUNCTION`，因为它确实要查图谱）。

## Capabilities

### New Capabilities
- `ontology-ir-constraints`：IR 用**声明式约束 DSL** 表达跨对象规则，编译器**确定性直译**成 function-backed 规则体——**无 LLM 参与生成可执行逻辑**。

### Modified Capabilities
<!-- 无：本变更只加一种 check 形式与一条编译分支，不改既有能力语义。 -->

## Impact

- **改动文件（全部在 `studio-ontology/`）**：
  - `references/ontology-map-schema.md` §4（`check` 列的三种形式 + DSL 语法表）
  - `references/compile-rules.md` §4（新增 4b-1 DSL 直译分支，含五个原语的编译目标）
  - `skills/ontology-map/`（抽规则时优先 DSL）
  - `skills/ontology-validate/`（覆盖率报告）
- ⭐ **`clife-onto-engine` 无需改动**：五个原语全部可用现有 `Capability` API 表达（`ctx.get` / `ctx.find` / `ctx.search_around`），编译产物仍是普通 `@spi.rule` 函数。
  > 引擎侧可选的便利层（约束求值器）**不在本变更范围**——先证明纯编译方案可行。
- **验收集**：`clife-onto-engine` 的 `plugins/grass` 那 **10 条 function-backed 规则**——逐条用 DSL 重写 IR，编译产物与现有手写版**行为等价**（同输入同裁决）。
- **预期收益**：全自动占比 **38–41% → 目标 ≥ 80%**（`grass` 的 10 条按模式分析可 100% 覆盖，留余量）。
- ⚠️ **诚实边界不消失，只是位置后移**：表达不了的仍留 `TODO(FDE)`。**不得对外宣传为"全自动"。**
- ⚠️ **IR 是契约**：DSL 一旦落地，**所有已编译插件重新生成的结果都会变**。因此本变更**必须先审 spec 再动手**，且首版**只加不改**（既有 IR 行为零变化）。

## Non-Goals

- **不**让 LLM 生成 Python 函数体（见 Why）。
- **不**追求图灵完备——**DSL 覆盖不到的，就该留给人**。
- **不**在本变更里动引擎（约束求值器另议）。
- **不**改 `backing` 的取值域（DSL 产出的仍是 `function`）。

---

## 设计

### 1. 五个原语

**设计约束**：每个原语都必须能**确定性**编译成现有 `Capability` API 的调用，**不引入引擎改动**。

#### 1.1 `in_set` —— 集合 / 名录包含

```
in_set(<被检字段>, from=<Object>[<过滤>].<取值字段>)
```

**IR 例**（乡土合规）：

```
in_set(SeedPack[sp_{site_id}].species, from=NativeListing[region == Site[site_id].region].species)
```

**编译目标**：

```python
site = ctx.get("Site", ctx.params["site_id"])
if site is None:
    return RuleResult.fail("Site 不存在")
allowed = {r["species"] for r in ctx.find("NativeListing", lambda r: r["region"] == site["region"])}
sp = ctx.get("SeedPack", f"sp_{ctx.params['site_id']}") or {}
illegal = [s for s in (sp.get("species") or []) if s not in allowed]
if illegal:
    return RuleResult.fail(<message_template>, suggestion=...)
return RuleResult.ok()
```

> 与现有手写版 `native_species_compliance` **逐行同构**。

#### 1.2 `between` —— 查表阈值 / 区间

```
between(<被检字段>, lookup=<Object>[<键>].<下界字段>..<上界字段>)
between(<被检字段>, max=<Object>[<键>].<字段>)        # 单边
```

**IR 例**（参数在能力域内 / 播量区间 / 霉变拦截 / 方法学年限）：

```
between(Operation[op_id].speed, lookup=Equipment[equipment_id].speed_min..speed_max)
between(ForageSample[batch_id].霉菌毒素, max=0.05)     # 常量上界，出处写 GB 13078
```

**编译目标**：查出上下界 → 比较 → 越界 fail。**界缺失时的行为在 DSL 里显式声明**（见 §2）。

#### 1.3 `link_exists` —— 关系存在性 / 多跳可达

```
link_exists(<起点>, <link>, <终点>)
```

**IR 例**（立地适配）：

```
link_exists(GrassSpecies[each of SeedPack[sp_{site_id}].composition.species],
            adapts_to,
            SiteType[Site[site_id].site_type])
```

**编译目标**：`ctx.search_around(...)` 遍历，**每一项都必须命中**，否则 fail 并列出未命中项。

#### 1.4 `agg` —— 聚合等式 / 不等式

```
agg(<sum|count|avg|min|max>, over=<集合表达式>, <op> <值>)
```

**IR 例**（混播配比）：

```
agg(sum, over=SeedPack[sp_{site_id}].composition.ratio, == 100)
```

**编译目标**：Python 端聚合（**不下推**，因为要看 overlay 里本次暂存的写入）。浮点比较统一 `round(x, 3)`。

#### 1.5 `exists` —— 对象存在 + 字段谓词

```
exists(<Object>[<键>])
exists(<Object>[<键>], where=<布尔表达式>)
```

**IR 例**（亲本合规 / 权属清晰）：

```
exists(Germplasm[base_id]) and exists(Germplasm[candidate_id])
exists(CarbonParcel[cp_id], where=tenure != '' and tenure != '未知')
```

### 2. ⚠️ 三个必须在 DSL 里显式声明的语义（否则会静默出错）

这三条是**手写规则里最容易分歧、也最容易在生成时被猜错**的地方。**DSL 必须逼作者写出来，不给默认值猜。**

| # | 问题 | DSL 写法 |
|---|---|---|
| **①** | **对象查不到时**算通过还是拦截？ | `on_missing: fail \| pass`（**无默认值，必填**） |
| **②** | **被检集合为空时**算通过还是拦截？ | `on_empty: pass \| fail`（**必填**） |
| **③** | **可选字段缺失时**跳过校验还是拦截？ | `on_absent: skip \| fail`（**必填**） |

> **为什么必填**：`grass` 现有手写规则里，这三种情况的处理**逐条不一样**——
> 「混播配比」无 `composition` 时 `return ok()`（向后兼容，`on_absent: skip`）；
> 「乡土合规」`site is None` 时 `fail("地块不存在")`（`on_missing: fail`）。
> **这是业务判断，不是默认值。给默认值就等于让编译器替业务方拍板。**

### 3. 编译分支

```
check 列
 ├─ 是布尔表达式（无 DSL 原语）           → 4a 现状：declarative 直译
 ├─ 命中 DSL 原语                        → ⭐ 4b-1 新增：确定性直译成 function 体
 └─ 是 TODO(FDE): …                      → 4b 现状：骨架 + raise NotImplementedError
```

**4b-1 的产物仍是** `backing=Backing.FUNCTION`——**因为它确实要查图谱**。`backing` 的语义（轻/重、要不要读世界）没变，变的只是"函数体谁写"。

### 4. 为什么不做成引擎侧的约束求值器

**做得到，但不该在第一版做。**

| | 纯编译（本方案） | 引擎侧求值器 |
|---|---|---|
| 产物 | 普通 `@spi.rule` Python，**可读、可断点、可手工接管** | 一段 DSL + 一个解释器 |
| 引擎改动 | **零** | 新增求值器 + 其自身的测试与安全边界 |
| 失败时 | FDE 直接改那段 Python | 得先读懂解释器 |
| 风险 | 低 | **DSL 成了第二个执行层**——又一个要审的东西 |

> ⭐ **纯编译方案的关键好处：DSL 表达不了的那一刻，FDE 可以直接接管生成出来的 Python。**
> 求值器方案里，一旦超出 DSL 表达力就得整条推倒重写。
> **先证明纯编译够用；不够再议求值器。**

### 5. 验收方式

**验收集 = `clife-onto-engine` 的 `plugins/grass` 那 10 条 function-backed 规则。**

对每一条：

1. 用 DSL 重写它在 IR 里的 `check` 列
2. 编译
3. **与现有手写实现做行为等价比对**：同一批输入（含边界：对象缺失 / 集合为空 / 字段缺失），**裁决结果与 `RuleResult.fail` 的消息语义一致**
4. 跑 `smoke_cq.py`，CQ 套件保持绿

**不达标即回退**——本变更宁可覆盖率低，不可行为漂移。

---

## 验收标准

（以下为待落地的能力要求，格式沿用 `clife-onto-engine` 的 openspec spec 写法，便于将来搬进 openspec）

#### Requirement: IR 以声明式约束 DSL 表达跨对象规则

IR `§4 Rules` 的 `check` 列 SHALL 支持第三种形式——**约束 DSL**，由五个原语组成：`in_set`（集合/名录包含）、`between`（查表阈值/区间）、`link_exists`（关系存在性/多跳可达）、`agg`（聚合等式/不等式）、`exists`（对象存在 + 字段谓词）。

该形式与既有两种形式（declarative 布尔表达式、`TODO(FDE)` 自然语言）**并存**；既有 IR MUST 保持编译结果不变。

##### Scenario: DSL 表达的规则被确定性直译
- **WHEN** 某条 `backing: function` 的规则，其 `check` 命中 DSL 原语
- **THEN** 编译器 SHALL 直译成读 `ctx` 的 Python 函数体，**不 raise NotImplementedError**
- **AND** 生成过程 MUST NOT 有 LLM 参与——同一份 IR 编译两次，产物逐字节相同

##### Scenario: 表达不了的仍留给人
- **WHEN** 规则逻辑超出五个原语的表达力
- **THEN** `check` 写 `TODO(FDE): …`，编译器 SHALL 按现状产出骨架 + `raise NotImplementedError`
- **AND** IR 该行 SHALL 显式标注**为什么表达不了**

##### Scenario: 既有 IR 零变化
- **WHEN** 一份不含任何 DSL 原语的既有 IR 被重新编译
- **THEN** 产物 SHALL 与本变更之前逐字节相同

#### Requirement: 三个边界语义必须显式声明，编译器不得猜

DSL 的每个原语 SHALL 强制作者声明三项边界行为，**无默认值**：`on_missing`（对象查不到时 fail 还是 pass）、`on_empty`（被检集合为空时）、`on_absent`（可选字段缺失时）。

缺任一项时，编译 MUST 失败并指出缺哪一项——**因为这是业务判断，给默认值等于让编译器替业务方拍板**。

##### Scenario: 缺边界声明即编译失败
- **WHEN** 某条 DSL 规则未写 `on_missing`
- **THEN** 编译 SHALL 失败，报告指明该规则名与缺失项，**不得**取任何默认值继续

##### Scenario: 边界语义逐条可不同
- **WHEN** 同一插件内，规则 A 声明 `on_absent: skip`（向后兼容），规则 B 声明 `on_absent: fail`
- **THEN** 两者 SHALL 各自按声明生成，互不影响

#### Requirement: 编译产物与手写实现行为等价

对验收集内的每条规则，DSL 编译产物 SHALL 与现有手写实现**行为等价**：同一批输入（含对象缺失、集合为空、字段缺失三类边界）产生**相同裁决**，且失败消息语义一致。

##### Scenario: 行为漂移即回退
- **WHEN** 某条规则的编译产物与手写版裁决不一致
- **THEN** 该原语 SHALL 不进入本变更范围，`check` 退回 `TODO(FDE)`——**宁可覆盖率低，不可裁决变样**

##### Scenario: CQ 套件保持绿
- **WHEN** 验收集全部改用 DSL 编译后
- **THEN** `smoke_cq.py` SHALL 全绿，与改动前一致

#### Requirement: 不引入引擎改动

五个原语 SHALL 全部可用现有 `Capability` API（`ctx.get` / `ctx.find` / `ctx.search_around`）表达；编译产物 SHALL 是普通 `@spi.rule` 函数，`backing` 仍为 `Backing.FUNCTION`。

本变更 MUST NOT 要求 `clife-onto-engine` 做任何改动。

##### Scenario: 产物可被人接管
- **WHEN** 生成的规则体不满足需要，FDE 决定手工改
- **THEN** 他 SHALL 能直接编辑那段 Python——**不需要先读懂任何解释器**

---

## 任务清单

### 0. 先审后做（本变更改的是 IR 契约）

- [ ] 0.1 ⚠️ **proposal + design 经审阅通过**再动手——IR 一改，所有已编译插件重新生成的结果都会变
- [ ] 0.2 确认首版**只加不改**：既有 IR（布尔表达式 / `TODO(FDE)`）行为零变化

### 1. IR schema：`check` 列的第三种形式

- [ ] 1.1 `references/ontology-map-schema.md` §4：`check` 列改述为**三种形式**（布尔表达式 / **约束 DSL** / `TODO(FDE)` 兜底）
- [ ] 1.2 加 DSL 语法表：`in_set` / `between` / `link_exists` / `agg` / `exists`
- [ ] 1.3 ⚠️ 加**三个必填语义**：`on_missing` / `on_empty` / `on_absent`——**无默认值，编译器不许替业务方拍板**
- [ ] 1.4 表格式示例：把「乡土合规」那行从 `TODO(FDE)` 换成 DSL 写法

### 2. 编译规则：4b-1 直译分支

- [ ] 2.1 `references/compile-rules.md` §4：加 4b-1，写清分支判定顺序（布尔 → DSL → TODO 兜底）
- [ ] 2.2 五个原语各给一段**编译目标代码**（照 design.md §1，与手写版逐行同构）
- [ ] 2.3 明确产物仍是 `backing=Backing.FUNCTION`；`source` / `citations` 透传不变
- [ ] 2.4 三个必填语义各自的代码生成形态

### 3. 技能改动

- [ ] 3.1 `skills/ontology-map`：抽规则时**优先尝试 DSL**；表达不了才写 `TODO(FDE)`，并在该行显式标注**为什么表达不了**
- [ ] 3.2 `skills/ontology-compile`：实现 4b-1 分支
- [ ] 3.3 `skills/ontology-validate`：报告新增一行「function 规则 DSL 覆盖 N / M」

### 4. 验收（验收集在 clife-onto-engine）

- [ ] 4.1 把 `plugins/grass` 的 **10 条 function-backed 规则**逐条用 DSL 重写 IR
- [ ] 4.2 编译，与现有手写实现做**行为等价比对**：同输入同裁决，含三类边界（对象缺失 / 集合为空 / 字段缺失）
- [ ] 4.3 `smoke_cq.py` 保持绿
- [ ] 4.4 记录实际覆盖率（目标 ≥ 80%，`grass` 预期可 100%）
- [ ] 4.5 ⚠️ **行为漂移即回退**——宁可覆盖率低，不可裁决变样

### 5. 口径与教材回改

- [ ] 5.1 若落地，回改 `clife-onto-engine` 教材里的 declarative 占比口径（S3 §七 · L5 §七 · BRIEF 第二节）
- [ ] 5.2 ⚠️ **不得对外宣传"全自动"**——诚实边界不消失，只是位置后移
