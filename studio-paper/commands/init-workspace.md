---
description: Initialize the studio-paper runtime workspace for one paper
argument-hint: [paper-id] [--venue se|ml:<name>:<year>] [--import-evolve <ontology_id>]
---

# /studio-paper:init-workspace {paper-id}

为一篇论文建 `.studio-paper/{paper-id}/`：config.yaml（目标会议、阈值、台账来源）、status.json（六个闸门的状态）、七个阶段目录。`--import-evolve` 登记 studio-evolve 成长台账为导入源。

Use skill: "init-workspace"
