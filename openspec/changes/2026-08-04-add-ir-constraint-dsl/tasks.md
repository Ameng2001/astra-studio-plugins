## 0. 先审后做（本变更改的是 IR 契约）

- [ ] 0.1 ⚠️ **proposal + design 经审阅通过**再动手——IR 一改，所有已编译插件重新生成的结果都会变
- [ ] 0.2 确认首版**只加不改**：既有 IR（布尔表达式 / `TODO(FDE)`）行为零变化

## 1. IR schema：`check` 列的第三种形式

- [ ] 1.1 `references/ontology-map-schema.md` §4：`check` 列改述为**三种形式**（布尔表达式 / **约束 DSL** / `TODO(FDE)` 兜底）
- [ ] 1.2 加 DSL 语法表：`in_set` / `between` / `link_exists` / `agg` / `exists`
- [ ] 1.3 ⚠️ 加**三个必填语义**：`on_missing` / `on_empty` / `on_absent`——**无默认值，编译器不许替业务方拍板**
- [ ] 1.4 表格式示例：把「乡土合规」那行从 `TODO(FDE)` 换成 DSL 写法

## 2. 编译规则：4b-1 直译分支

- [ ] 2.1 `references/compile-rules.md` §4：加 4b-1，写清分支判定顺序（布尔 → DSL → TODO 兜底）
- [ ] 2.2 五个原语各给一段**编译目标代码**（照 design.md §1，与手写版逐行同构）
- [ ] 2.3 明确产物仍是 `backing=Backing.FUNCTION`；`source` / `citations` 透传不变
- [ ] 2.4 三个必填语义各自的代码生成形态

## 3. 技能改动

- [ ] 3.1 `skills/ontology-map`：抽规则时**优先尝试 DSL**；表达不了才写 `TODO(FDE)`，并在该行显式标注**为什么表达不了**
- [ ] 3.2 `skills/ontology-compile`：实现 4b-1 分支
- [ ] 3.3 `skills/ontology-validate`：报告新增一行「function 规则 DSL 覆盖 N / M」

## 4. 验收（验收集在 clife-onto-engine）

- [ ] 4.1 把 `plugins/grass` 的 **10 条 function-backed 规则**逐条用 DSL 重写 IR
- [ ] 4.2 编译，与现有手写实现做**行为等价比对**：同输入同裁决，含三类边界（对象缺失 / 集合为空 / 字段缺失）
- [ ] 4.3 `smoke_cq.py` 保持绿
- [ ] 4.4 记录实际覆盖率（目标 ≥ 80%，`grass` 预期可 100%）
- [ ] 4.5 ⚠️ **行为漂移即回退**——宁可覆盖率低，不可裁决变样

## 5. 口径与教材回改

- [ ] 5.1 若落地，回改 `clife-onto-engine` 教材里的 declarative 占比口径（S3 §七 · L5 §七 · BRIEF 第二节）
- [ ] 5.2 ⚠️ **不得对外宣传"全自动"**——诚实边界不消失，只是位置后移
