## ADDED Requirements

### Requirement: IR 以声明式约束 DSL 表达跨对象规则

IR `§4 Rules` 的 `check` 列 SHALL 支持第三种形式——**约束 DSL**，由五个原语组成：`in_set`（集合/名录包含）、`between`（查表阈值/区间）、`link_exists`（关系存在性/多跳可达）、`agg`（聚合等式/不等式）、`exists`（对象存在 + 字段谓词）。

该形式与既有两种形式（declarative 布尔表达式、`TODO(FDE)` 自然语言）**并存**；既有 IR MUST 保持编译结果不变。

#### Scenario: DSL 表达的规则被确定性直译
- **WHEN** 某条 `backing: function` 的规则，其 `check` 命中 DSL 原语
- **THEN** 编译器 SHALL 直译成读 `ctx` 的 Python 函数体，**不 raise NotImplementedError**
- **AND** 生成过程 MUST NOT 有 LLM 参与——同一份 IR 编译两次，产物逐字节相同

#### Scenario: 表达不了的仍留给人
- **WHEN** 规则逻辑超出五个原语的表达力
- **THEN** `check` 写 `TODO(FDE): …`，编译器 SHALL 按现状产出骨架 + `raise NotImplementedError`
- **AND** IR 该行 SHALL 显式标注**为什么表达不了**

#### Scenario: 既有 IR 零变化
- **WHEN** 一份不含任何 DSL 原语的既有 IR 被重新编译
- **THEN** 产物 SHALL 与本变更之前逐字节相同

### Requirement: 三个边界语义必须显式声明，编译器不得猜

DSL 的每个原语 SHALL 强制作者声明三项边界行为，**无默认值**：`on_missing`（对象查不到时 fail 还是 pass）、`on_empty`（被检集合为空时）、`on_absent`（可选字段缺失时）。

缺任一项时，编译 MUST 失败并指出缺哪一项——**因为这是业务判断，给默认值等于让编译器替业务方拍板**。

#### Scenario: 缺边界声明即编译失败
- **WHEN** 某条 DSL 规则未写 `on_missing`
- **THEN** 编译 SHALL 失败，报告指明该规则名与缺失项，**不得**取任何默认值继续

#### Scenario: 边界语义逐条可不同
- **WHEN** 同一插件内，规则 A 声明 `on_absent: skip`（向后兼容），规则 B 声明 `on_absent: fail`
- **THEN** 两者 SHALL 各自按声明生成，互不影响

### Requirement: 编译产物与手写实现行为等价

对验收集内的每条规则，DSL 编译产物 SHALL 与现有手写实现**行为等价**：同一批输入（含对象缺失、集合为空、字段缺失三类边界）产生**相同裁决**，且失败消息语义一致。

#### Scenario: 行为漂移即回退
- **WHEN** 某条规则的编译产物与手写版裁决不一致
- **THEN** 该原语 SHALL 不进入本变更范围，`check` 退回 `TODO(FDE)`——**宁可覆盖率低，不可裁决变样**

#### Scenario: CQ 套件保持绿
- **WHEN** 验收集全部改用 DSL 编译后
- **THEN** `smoke_cq.py` SHALL 全绿，与改动前一致

### Requirement: 不引入引擎改动

五个原语 SHALL 全部可用现有 `Capability` API（`ctx.get` / `ctx.find` / `ctx.search_around`）表达；编译产物 SHALL 是普通 `@spi.rule` 函数，`backing` 仍为 `Backing.FUNCTION`。

本变更 MUST NOT 要求 `clife-onto-engine` 做任何改动。

#### Scenario: 产物可被人接管
- **WHEN** 生成的规则体不满足需要，FDE 决定手工改
- **THEN** 他 SHALL 能直接编辑那段 Python——**不需要先读懂任何解释器**
