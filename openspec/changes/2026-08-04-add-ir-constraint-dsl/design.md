# 设计：IR 约束 DSL

## 1. 五个原语

**设计约束**：每个原语都必须能**确定性**编译成现有 `Capability` API 的调用，**不引入引擎改动**。

### 1.1 `in_set` —— 集合 / 名录包含

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

### 1.2 `between` —— 查表阈值 / 区间

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

### 1.3 `link_exists` —— 关系存在性 / 多跳可达

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

### 1.4 `agg` —— 聚合等式 / 不等式

```
agg(<sum|count|avg|min|max>, over=<集合表达式>, <op> <值>)
```

**IR 例**（混播配比）：

```
agg(sum, over=SeedPack[sp_{site_id}].composition.ratio, == 100)
```

**编译目标**：Python 端聚合（**不下推**，因为要看 overlay 里本次暂存的写入）。浮点比较统一 `round(x, 3)`。

### 1.5 `exists` —— 对象存在 + 字段谓词

```
exists(<Object>[<键>])
exists(<Object>[<键>], where=<布尔表达式>)
```

**IR 例**（亲本合规 / 权属清晰）：

```
exists(Germplasm[base_id]) and exists(Germplasm[candidate_id])
exists(CarbonParcel[cp_id], where=tenure != '' and tenure != '未知')
```

## 2. ⚠️ 三个必须在 DSL 里显式声明的语义（否则会静默出错）

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

## 3. 编译分支

```
check 列
 ├─ 是布尔表达式（无 DSL 原语）           → 4a 现状：declarative 直译
 ├─ 命中 DSL 原语                        → ⭐ 4b-1 新增：确定性直译成 function 体
 └─ 是 TODO(FDE): …                      → 4b 现状：骨架 + raise NotImplementedError
```

**4b-1 的产物仍是** `backing=Backing.FUNCTION`——**因为它确实要查图谱**。`backing` 的语义（轻/重、要不要读世界）没变，变的只是"函数体谁写"。

## 4. 为什么不做成引擎侧的约束求值器

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

## 5. 验收方式

**验收集 = `clife-onto-engine` 的 `plugins/grass` 那 10 条 function-backed 规则。**

对每一条：

1. 用 DSL 重写它在 IR 里的 `check` 列
2. 编译
3. **与现有手写实现做行为等价比对**：同一批输入（含边界：对象缺失 / 集合为空 / 字段缺失），**裁决结果与 `RuleResult.fail` 的消息语义一致**
4. 跑 `smoke_cq.py`，CQ 套件保持绿

**不达标即回退**——本变更宁可覆盖率低，不可行为漂移。
