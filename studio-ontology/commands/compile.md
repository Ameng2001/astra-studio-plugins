---
description: Compile the ontology IR (ontology-map.md) into a runnable clife-onto-engine plugin.
---

# /studio-ontology:compile {ontology_id}

调用 **ontology-compile** skill：读 `studio/changes/{ontology_id}/ontology-map.md`，按 `references/compile-rules.md` 编译成 clife-onto-engine 插件（`__init__.py` 五要素注册 + 规则/动作 handler 骨架 + `mappings/objects.yaml` + `cq/golden.yaml` + `plugin.yaml`）到 `{target_dir}/`。

declarative 规则全自动；function-backed 规则体、Action 回写、参考数据生成骨架 + `TODO(FDE)`，由 FDE 在引擎仓库内手填。
