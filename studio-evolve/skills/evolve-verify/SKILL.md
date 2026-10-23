---
name: evolve-verify
description: Adjudicate one ontology edit proposal in a sandbox under the dual gate — copy the compiled plugin, apply the proposal, run source cases (inner gate: strictly better on three growth axes within a cost band) and a freshly resampled real-only held-out set (outer gate: no regression on any axis), then ACCEPT / REVISE / REJECT and append the readings to the growth ledger. Nothing touches the live ontology. Use after evolve-attribute, once per proposal.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
user-invocable: true
---

# Evolve Verify

一份提案是假设，不是改进。本 skill 把它放进沙箱，用两道关卡裁决。判定式、留出集构造、修订与丢弃的规则全在 `${CLAUDE_SKILL_DIR}/../../references/gate-rules.md`，三维读数的算法在 `growth-metrics.md`。本 skill 严格照规范执行，不自行放宽。

> 本版没有 Python 脚本。读数由本 skill 按规范手工计算，报告「执行方式」一栏标 `MANUAL`，并在 growth-ledger 里同样标注。脚本落地后改为 `AUTO`，两种方式的读数不混比。

## Pre-check

1. 读 `studio/changes/$ARGUMENTS` 指定的提案文件（`{ontology_id} {n}`），要求 `attempt ≤ max_attempts`。
2. 确认 clife-onto-engine 可用、`{target_dir}` 可 import。不可用 → 只能做静态校验，报告 `NO_ENGINE`，不出裁决。
3. 确认 CQ 能被引擎批量执行：`cq/golden.yaml` 与 `.studio-evolve/{id}/cases/**/*.yaml` 需要一条 YAML → `ActionCQ / QueryCQ` 的加载路径（compile-rules 写明目前要人转写）。没有 → 报告 `CQ_RUNNER_MISSING`，这是整条管线的第一块砖，先停。
4. 读 `.studio-evolve/{id}/config.yaml`（`beta / window_size / stratify / backbones / repeat_runs`）。
5. 读 `growth-ledger.md` 取上一轮基线读数。

## Workflow

### 0. 沙箱
复制 `{target_dir}/` 与 `ontology-map.md` 到 `.studio-evolve/{id}/sandbox/{n}/`；在副本 IR 上应用提案的每个 ω；对副本目录以 modify 模式调 `ontology-compile`；跑 `ontology-validate` 的 A（结构）、B（治理）两段，任一不过 → 直接 `REVISE`，原因写「结构 / 治理不过」，不进关卡。

### 1. 内关卡
- `Dᵢ` = 提案 `source_cases`（含 real 与 sim）。
- 在 `Dᵢ` 上分别跑 `hᵢ`（生产插件）与 `h'`（沙箱），每条案例连同 `variants` 都跑，重复 `repeat_runs` 次。
- 算 `ΔT`（逐层）、`ΔS_consist`、`ΔW`、`ΔC_tok`；判 `growOK ∧ costOK ∧ strict`。

### 2. 外关卡
- **重抽留出集**：候选池 = `cases/real/` 减去 `Dᵢ`；按 `stratify` 分层抽样，每层 ≥ 1，总量 = `window_size`；写 `cases/holdout/{round}.yaml`（只记 id）。候选池薄 → `HOLDOUT_THIN`，按实际层数执行，不用 sim 补。
- 在 `B` 上跑 `hᵢ` 与 `h'`，算 `ΔT / ΔS（完整 min）/ ΔW / ΔC`；判 `growOK ∧ costOK`。
- `backbones` 多于一个 → 每个骨干分别算，全部通过才过；单骨干标 `SINGLE_BACKBONE`。

### 3. 裁决
- 两关都过 → `ACCEPT`：提案文件 Verdict 段回填，状态「待人确认合入」。**不合入**，合入由 `cycle` 命令在人确认后对生产 IR 重放。
- 任一关不过 → `REVISE`：回填失败读数与失败案例清单，提示 `/studio-evolve:attribute` 以这些案例为新负例修订（`attempt + 1`）。
- `attempt = max_attempts` 仍不过 → `REJECT`：提案移到 `proposals/rejected/`，失败签名（缺陷类型、元素集合、回退维度、回退 family）写进 growth-ledger 拒绝表。

### 4. 记账
向 `growth-ledger.md` 追加一行（格式见 `growth-metrics.md` 末尾），含 `MANUAL/AUTO` 与 `SINGLE_BACKBONE/PANEL` 标记。

### 5. 清理
删除 `sandbox/{n}/`。

### 6. 报告
按 `gate-rules.md` §5 的裁决报告格式输出。

## Expected Inputs

一份提案 + 生产插件 + 案例库 + config + growth-ledger + 可用的引擎与 CQ 运行器。

## Expected Outputs

提案文件 Verdict 段回填；`cases/holdout/{round}.yaml`；growth-ledger 新增一行；裁决报告。

## Out of Scope

- 不改生产 IR 与生产插件（合入在 `cycle` 命令里、人确认后）。
- 不调阈值。
- 不在留出集上做任何「再试一次」：留出集每轮只跑一次，跑完即封存。
