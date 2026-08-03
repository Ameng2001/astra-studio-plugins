# Compile Rules — ontology-map → clife-onto-engine 插件

> `ontology-compile` 读 `ontology-map.md`（IR），按本规则机械编译成一个 **clife-onto-engine 插件**
> （Python 注册 + 规则/动作 handler 骨架 + 映射 YAML + CQ）。
>
> 编译目标：`{target_dir}/`（如 `plugins/grass/`，在 clife-onto-engine 仓库内）
> 输出与 IR 的对应：IR 七段 → 下列文件。declarative 规则可全自动；function-backed 规则与 Action 副作用生成**骨架 + `TODO(FDE)`**。

## ⚠️ 编译产物对 SPI 七槽位的覆盖度

**编译产物不填满七槽位。** 逐格如下（口径以本文件下面各节的实际生成规则为准）：

| # | 槽位 | 覆盖 | 说明 |
|---|---|---|---|
| **①** | Schema 包 | ✅ **全自动** | `__init__.py` 的 Object / Link 注册 ← IR §1 §2 |
| **②** | 映射注册表 | ✅ **全自动** | `mappings/objects.yaml` ← IR §6 |
| **③** | Function / Rule 实现 | ⚠️ **部分** | **declarative 规则全自动写完函数体**（§4a）；**function-backed 规则与 Function 派生量只出骨架 + `TODO(FDE)`**（§4b、§3） |
| **④** | Action handlers | ⚠️ **只出骨架** | 装饰器 + `stage_write` 注释 + `set_confidence` + `raise NotImplementedError`（§5） |
| **⑤** | 记忆词典 + 提示词骨架 | ❌ **不生成** | 输出结构里没有 `memory/`；生成的 `plugin.yaml` 的 `provides` 里也没有这一项 |
| **⑥** | Agent 角色定义 | ❌ **不生成** | 同上，没有 `agents/`；HIL 关口只体现在 Action 的 `hil` 参数里，**没有独立的角色清单** |
| **⑦** | CQ 验收集 | ⚠️ **产物形态不同** | 编译出的是 **`cq/golden.yaml`**（YAML）；而引擎的 `run_cq_suite` 消费的是 **Python 的 `ActionCQ` / `QueryCQ` 对象**（见 `plugins/grass/cq.py` 的 `CQ_SUITE`）。**YAML 需要人转写成 `cq.py`** |

### 为什么 ⑤⑥ 不生成——是结构性的，不是待补功能

**IR 里压根没有对应的段。** IR 是七段（§0 Header / §1 Objects / §2 Links / §3 Functions / §4 Rules / §5 Actions / §6 Mapping Hints / §7 CQ），
**没有"记忆词典"段，也没有"Agent 角色"段**——编译器无中生有不出来。

> 要补齐，得先给 IR 加段，那是 **IR 契约变更**，不是编译器改一改的事。

### 这一节的用法

- **对 FDE**：这张表就是**收尾清单的上界**——⑤⑥ 从一开始就得排进人工工时，别等编译完才发现。
- **对客户**：准确说法是「**建模端把结构立起来，⑤⑥ 与 ③④ 的函数体要人补**」，
  ⛔ **不要说"编译产物正好填满七槽位"**——那是本文件曾经的写法，与实际生成规则不符，已更正。

---

## 输出结构

```
{target_dir}/
├── __init__.py            # ONTOLOGY 常量 + Object/Link 注册 + guard/规则/Function/Action handler + seed 桩
├── mappings/objects.yaml  # ← IR 第 6 段
├── cq/golden.yaml         # ← IR 第 7 段
└── plugin.yaml            # 插件清单（ontology_id / version / provides 槽位）
```

**幂等**：目标文件已存在则**不覆盖**，只告警 + 输出 diff 建议（沿用 spec-generate 约定）。`modify` 模式只重生成本轮 IR 变更涉及的要素。

**只 import sdk**（红线）：生成的 `__init__.py` 仅 `from clife_onto_engine.sdk import ...`，不碰内核内部。

---

## __init__.py 头部（固定）

```python
"""{ontology_id} 本体插件 —— 由 studio-ontology 从 ontology-map 编译生成。
bounded_context: {bounded_context}。仅 import clife_onto_engine.sdk。"""
from __future__ import annotations
from pathlib import Path as _Path
from clife_onto_engine.sdk import (
    Backing, EdgeSemantics, HilPolicy, LinkType, ObjectType, ParamSpec,
    PropertySpec, RuleResult, Severity, spi,
)
from clife_onto_engine.query import InMemoryStore

ONTOLOGY = "{ontology_id}"
```

---

## 1. Objects → `spi.registry.add_object(ObjectType(...))`

IR 行 → 一条注册。`properties` 串解析为 `PropertySpec`；`lifecycle` 解析为 `states`/`initial_state`。

**IR**：`| Site | parcel_id | area_mu:number:亩:required, region:string:required, site_type:enum=沙地\|盐碱 | draft,surveyed,treating,accepted (initial=draft) | 政务确权+遥感 |`

**→ 生成**：
```python
spi.registry.add_object(ObjectType(
    name="Site", namespace=ONTOLOGY, primary_key="parcel_id",
    properties=(
        PropertySpec("area_mu", "number", unit="亩", required=True),
        PropertySpec("region", "string", required=True),
        PropertySpec("site_type", "enum"),
    ),
    states=("draft", "surveyed", "treating", "accepted"), initial_state="draft",
))
```
- 无 properties 的对象（如纯参考数据/中间对象）：`ObjectType(name=..., namespace=ONTOLOGY, primary_key=...)`。
- `密级=confidential` → `PropertySpec(..., classification="confidential")`。

## 2. Links → `spi.registry.add_link(LinkType(...))`

**IR**：`| treated_by | Degradation | RestorationMethod | N:N | derivation | applicability |`
**→**：
```python
spi.registry.add_link(LinkType("treated_by", ONTOLOGY, "Degradation", "RestorationMethod",
                               cardinality="N:N", edge_semantics=EdgeSemantics.DERIVATION))
```
`edge_semantics`：`root_cause→EdgeSemantics.ROOT_CAUSE`、`hypothesis→HYPOTHESIS`、`derivation→DERIVATION`。

## 3. Functions → `@spi.function`（骨架 + TODO）

**IR**：`| RFV分级 | ForageSample | string | 按 RFV 阈值映射到 特级/一级/二级/等外 |`
**→**：
```python
@spi.function(ONTOLOGY, "RFV分级", reads=("ForageSample",))
def rfv_grade(ctx) -> str:
    """{intent}。读取：ForageSample。"""
    # TODO(FDE): 实现派生量计算（只读，无副作用）
    sample = ctx.get("ForageSample", ctx.params["batch_id"]) or {}
    raise NotImplementedError("rfv_grade 待实现")
```
> 简单阈值/公式类，若 IR `intent` 给了明确口径，可直接生成实现；否则留 TODO。

## 4. Rules → `@spi.rule`（declarative 全自动 / function 骨架）

公共：`severity`→`Severity.HARD|SOFT`，`backing`→`Backing.DECLARATIVE|FUNCTION`，`evaluation`→`Evaluation.PRE|POST_WRITE`，`source`/`citations` 原样带上（供 OKF 导出）。

**4a. declarative（可全自动，从 `check`）**
**IR**：`| 预算非负 | hard | declarative | pre | … | 通用 | — | budget is None or budget >= 0 → 合法 |`
**→**：
```python
@spi.rule(ONTOLOGY, "预算非负", backing=Backing.DECLARATIVE, severity=Severity.HARD,
          source="通用")                              # ← source 透传（含 "通用"），治理审计据此放行
def 预算非负(ctx) -> RuleResult:
    budget = ctx.params.get("budget")
    if not (budget is None or budget >= 0):          # ← 取自 check（取反为违反条件）
        return RuleResult.fail("预算不能为负", suggestion="提供 budget >= 0")
    return RuleResult.ok()
```
> **source 必须透传**，declarative 规则也不例外：IR 给了 `source=通用`（自明/常识，如 budget>=0、角色集合）就编成 `source="通用"`。
> 这样 ontology-validate 的治理审计把"通用"视为合规、不算缺依据——**只有 source 为空或 `TODO(FDE): 补依据` 的才是治理缺口**。
> 角色权限类 guard 同理：从 `check`（如 `role in {施工方,牧民,监管}`）直接生成，并带 `source="通用"`。

**4b. function-backed（骨架 + TODO，挂出处）**
**IR**：`| 乡土合规 | hard | function | post_write | 出一地一方 | … | 乡土草种名录 | GB/T 37067, DB15/T | TODO(FDE): 查地块 region 的乡土名录，校验 species⊆名录 |`
**→**：
```python
@spi.rule(ONTOLOGY, "乡土合规", backing=Backing.FUNCTION, severity=Severity.HARD,
          message_template="种子包含非乡土草种，已拦截",
          source="乡土草种名录",
          citations=("GB/T 37067 退化草地修复技术规范", "DB15/T 草原生态修复技术规程"))
def 乡土合规(ctx) -> RuleResult:
    """意图：种子包草种须在本地乡土名录内。"""
    # TODO(FDE): 查地块 region 的乡土名录，校验 species ⊆ 名录
    # 可用：ctx.get / ctx.find / ctx.search_around（写后可见本次暂存写入）
    raise NotImplementedError("乡土合规 待实现")
```
- 无 `source` 的规则：生成时在 docstring 标 `# TODO(FDE): 补依据`，并由 ontology-validate 报告。

## 5. Actions → `@spi.action`（骨架）

`params`→`ParamSpec` 元组；`guards`/`post_rules`/`writes` 原样；`hil` → `HilPolicy`（`when` 编译成 predicate）；`validate`→`validate_supported`。handler 生成 stage_write 骨架。

**IR**：`| 出一地一方 | site_id:ref(Site):required, species:list:required, budget:number | 预算非负,角色权限 | 乡土合规 | Project,SeedPack | 乡土草种合规官 / confidence<0.75 | true |`
**→**：
```python
@spi.action(
    ONTOLOGY, "出一地一方",
    description="{description}",
    params=(
        ParamSpec("site_id", "ref(Site)", required=True),
        ParamSpec("species", "list", required=True),
        ParamSpec("budget", "number", required=False),
    ),
    guards=("预算非负", "角色权限"),
    post_rules=("乡土合规",),
    writes=("Project", "SeedPack"),
    validate_supported=True,
    hil=HilPolicy(reviewer_role="乡土草种合规官",
                  predicate=lambda confidence, touched_hard: confidence < 0.75),
)
def 出一地一方(ctx) -> None:
    # TODO(FDE): 业务回写。为每个 writes 对象生成 stage_write 骨架：
    # ctx.stage_write("Project", f"proj_{ctx.params['site_id']}", {...})
    # ctx.stage_write("SeedPack", f"sp_{ctx.params['site_id']}", {...})
    # ctx.emit_effect("workitem", on="accepted", template="...")   # 如有派工单
    ctx.set_confidence(0.8)
    # ctx.add_evidence(source="...")                                # 来自规则 source
    raise NotImplementedError("出一地一方 handler 待实现")
```
- `hil.when` 编译：`confidence<0.75` → `predicate=lambda confidence, touched_hard: confidence < 0.75`；`severity_touched==hard` → `predicate=lambda confidence, touched_hard: touched_hard`。
- 写入对象的 key 约定：`{action前缀}_{某主键}`，骨架里给注释让 FDE 定。

## 6. Mapping Hints → `mappings/objects.yaml`

IR 第 6 段表 → YAML（结构见 clife-onto-engine 的 `MappingRegistry`）：
```yaml
objects:
  - object: Site
    materialization: hybrid
    physical: { store: postgis, table: geo_parcel, key: parcel_id, columns: [area_mu, region, site_type] }
```
并在 `__init__.py` 末尾追加：`spi.load_mappings(ONTOLOGY, _Path(__file__).parent / "mappings" / "objects.yaml")`。

## 7. CQ → `cq/golden.yaml`

IR 第 7 段原样落 YAML（供 ontology-validate 跑）：
```yaml
- q: 这块盐碱地属于什么退化等级？应该用哪套种子包配方？
  expect: query
- q: 给这块地用紫花苜蓿出方案
  expect: rejected(乡土合规)
```

## seed 桩（__init__.py 末尾）

```python
def seed_reference_data(store: InMemoryStore) -> None:
    """TODO(FDE): 写入参考数据（乡土名录等），供 function-backed 规则查。"""
    pass
```

## plugin.yaml（清单）

```yaml
plugin: {ontology_id}
ontology_id: {ontology_id}
version: 0.1.0
engine_compat: ">=0.1.0"
bounded_context: {bounded_context}
provides:
  schema: __init__.py
  mappings: ./mappings/objects.yaml
  cq: ./cq/golden.yaml
generated_by: studio-ontology
```

---

## 编译后报告（ontology-compile 必须输出）

```
本体插件已生成：{target_dir}/
  对象 N · 关系 M · 函数 F · 规则 R（declarative 全自动 X / function 骨架 Y）· 动作 A
待 FDE 收尾（TODO 清单）：
  - function-backed 规则 Y 条需填函数体：乡土合规, …
  - Action handler A 个需填回写逻辑：出一地一方, …
  - 无依据规则 Z 条需补 source/citations：…
  - seed_reference_data 需填参考数据
下一步：在 clife-onto-engine 仓库 `python -m plugins.{ontology_id}...` 验证；或 /studio-ontology:validate
```

> 编译只保证**结构对、可 import、五要素齐**；function-backed 规则体、Action 回写、参考数据是 FDE 的手艺活（见 `ontology-map-schema.md` 的"诚实边界"）——这正是 FDE 不可替代之处。
