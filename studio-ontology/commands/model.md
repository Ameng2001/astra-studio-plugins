---
description: FDE pipeline — compile business analysis into a runnable clife-onto-engine ontology plugin (ontology-map → ontology-compile).
---

# /studio-ontology:model {ontology_id}

把业务分析编译成一个可运行的数智本体插件。两步流水线，每步之间用户确认：

```
Step 1: ontology-map      业务分析（behavior-matrix / domain-canvas / process-flow / event-storm）
  → 五要素 IR              → studio/changes/{ontology_id}/ontology-map.md
        ↓ 用户确认 IR（尤其规则分类与出处）
Step 2: ontology-compile   IR → clife-onto-engine 插件
  → 可运行本体骨架          → {target_dir}/（__init__.py + mappings + cq + plugin.yaml）
        ↓
FDE 收尾：在 clife-onto-engine 仓库内填 function-backed 规则体 / Action 回写 / 参考数据
        ↓
Step 3: ontology-validate  校验结构/治理/剩余TODO/行为CQ → 闭环
```

## 执行

1. 调用 skill **ontology-map**，参数 `{ontology_id}`：
   - 读上游分析产物（缺则提示先跑 `/studio-planner:plan`，或退化为现场建模）。
   - 调 `ontology-architect`（FDE）+ `rule-engineer` + 领域专家，逐段抽五要素。
   - 写出 `studio/changes/{ontology_id}/ontology-map.md`，报告五要素计数 + TODO。
2. **停下，让用户审 IR**——重点确认：对象边界、edge_semantics、规则的 severity/backing/出处、Action 的 writes/HIL。用户可直接改 ontology-map.md。
3. 用户确认后，调用 skill **ontology-compile**，参数 `{ontology_id}`：
   - 按 `references/compile-rules.md` 生成插件到 `{target_dir}/`。
   - 报告：五要素计数 + TODO 清单（待填 function 规则 / Action handler / 无依据规则 / seed）。
4. 提示下一步：到 clife-onto-engine 仓库填 TODO、跑 CQ 验证（`/studio-ontology:validate` 或引擎仓库内 pytest）。

> 定位：本命令是 **FDE 工作台**——astra-studio 建模、编译成 clife-onto-engine 的 OAG 运行时插件。
> 它产出**结构正确、五要素齐、可 import** 的骨架；function-backed 规则体与 Action 回写是 FDE 的手艺活，不假装全自动。
