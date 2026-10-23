---
name: init-workspace
description: Initialize the studio-paper runtime workspace for one paper — .studio-paper/{paper-id}/ with venue, thresholds, ledger sources, gate status and seven stage directories. Use when starting a new paper, when someone says "set up" or "新建论文工作区", or when a studio-paper skill reports the workspace is missing.
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# Initialize Workspace

为一篇论文在当前项目建 `.studio-paper/{paper-id}/`：目标会议与评审表家族、阈值（n_min / seeds_min / 基线预算比）、台账导入源、六个闸门的状态、七个阶段目录。所有后续技能都以这个目录为家；导师用 `/studio-paper:status` 看每篇卡在哪个闸门。

## Intent
- 让一篇论文从第一天起就有一个带闸门状态的工作区，而不是散落的笔记。
- 触发：用户说「新建论文」「init」「set up」，或其他 studio-paper 技能发现工作区缺失。

## Expected Inputs
- `paper-id`（kebab-case，目录名，2–64 字符）
- `--venue se|ml[:<名称>[:<年份>]]`：家族必填（决定评审表与 rebuttal 规则）；名称缺省 `TBD`，年份缺省 `null`
- 可选：`--title`（缺省用 paper-id）、`--cfp-url`、`--rebuttal-limit`（缺省按家族写 `TBD（按当年 CFP…）`）
- 可选：`--import-evolve <ontology_id>` 登记 `.studio-evolve/{id}/ledger/growth-ledger.md` 为台账导入源；文件不存在时拒绝，除非加 `--allow-missing-source`

## Expected Outputs
```
.studio-paper/{paper-id}/
├── config.yaml          # 由 templates/runtime-config.yaml.tmpl 填充（ledger.sources 无导入源时为 []）
├── status.json          # 由 templates/runtime-status.json.tmpl 填充（stage=positioning，六闸门 not_started）
├── README.md            # 一页：阶段目录、六个闸门、下一步命令
└── positioning/ proposal/ ledger/ results/ review/ submission/ rebuttal/   # 各含 .gitkeep
```

## Preconditions
- 项目根（cwd 或 `$STUDIO_PAPER_ROOT`）可写。
- `.studio-paper/{paper-id}/` 已存在 → 脚本打印当前阶段与六闸门状态后退出 0，不覆盖任何文件。

## Workflow
1. 校验参数。用户没说会议家族时问一次：「投 SE 会议（ICSE/FSE/ASE）还是 ML 会议（NeurIPS/ICLR/ICML）？」。
2. 若用户提到 studio-evolve 的本体，确认 `ontology_id`，并检查 `.studio-evolve/{id}/ledger/growth-ledger.md` 是否存在。
3. 运行：
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/init_workspace.py <paper-id> --venue <family>:<name>:<year> \
       [--title "..."] [--cfp-url URL] [--import-evolve <ontology_id>] [--rebuttal-limit "..."]
   ```
   退出码：0 已创建或已存在；2 参数非法 / 导入源缺失 / 目录存在但不是工作区。
4. 把脚本输出的摘要转述给用户（会议、导入源、目录），并给出下一步：`/studio-paper:position-paper {paper-id}`。
5. 提醒：`.studio-paper/` 应纳入 git（判据冻结的 commit 追溯依赖它）。

## Example
```bash
# 新建，SE 会议，挂 studio-evolve 的成长台账
python3 ${CLAUDE_SKILL_DIR}/scripts/init_workspace.py evolving-foundation \
    --venue se:ICSE:2027 --title "Self-Evolving Ontologies" --import-evolve clife-health
# ✓ 已创建 .studio-paper/evolving-foundation/ … 下一步: /studio-paper:position-paper evolving-foundation

# 还没定会议，只知道是 ML 方向
python3 ${CLAUDE_SKILL_DIR}/scripts/init_workspace.py tier-curriculum --venue ml

# 再跑一次 → 不覆盖，打印「阶段: positioning」与六闸门状态，exit 0
```

## Out of Scope
- 不写任何研究内容；不初始化 `studio/`（那是 studio-core）；不装 studio-docs 的 blueprint。
- 不改阈值：阈值在 `config.yaml` 里由人改，改阈值等于开新一轮（iteration）。
