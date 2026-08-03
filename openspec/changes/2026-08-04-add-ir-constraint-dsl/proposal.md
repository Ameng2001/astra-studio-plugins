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
