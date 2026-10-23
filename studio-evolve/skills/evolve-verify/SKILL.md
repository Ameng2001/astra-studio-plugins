---
name: evolve-verify
description: Adjudicate one ontology edit proposal in a sandbox under the dual gate — copy the compiled plugin, apply the proposal, run source cases (inner gate: strictly better on three growth axes within a cost band) and a freshly resampled real-only held-out set (outer gate: no regression on any axis), then ACCEPT / REVISE / REJECT and append the readings to the growth ledger. Nothing touches the live ontology. Use after evolve-attribute, once per proposal.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
user-invocable: true
---

# Evolve Verify

一份提案是假设，不是改进。本 skill 把它放进沙箱，用两道关卡裁决。判定式、留出集构造、修订与丢弃的规则全在 `${CLAUDE_SKILL_DIR}/../../references/gate-rules.md`，三维读数的算法在 `growth-metrics.md`。本 skill 严格照规范执行，不自行放宽。

> 读数与裁决由 `${CLAUDE_SKILL_DIR}/../../scripts/` 的三个脚本计算，报告「执行方式」标 `AUTO`，`script_hash` 进 growth-ledger 与 `verdict.json`。只有引擎不可用、脚本跑不起来时才允许按规范手算并标 `MANUAL`；MANUAL 与 AUTO 的读数不混比。

## 脚本（`S = ${CLAUDE_SKILL_DIR}/../../scripts`）

| 脚本 | 做什么 | 对应规范 |
|---|---|---|
| `holdout_sample.py` | 每轮从 `cases/real/` 减 Dᵢ 的池里按 `family × tier × time_half` 分层重抽留出集，`--holdout-size` 与 window_size 解耦，池薄报 `HOLDOUT_THIN` 不用 sim 补，同一轮拒绝二次抽样 | gate-rules.md §2 |
| `growth_metrics.py compute` | 从案例执行结果（`results.jsonl`，字段见 `runner-spec`）算 T_1..4 / A / S_consist / S_stable / W_family / C，`--ir` 给 ontology-map.md 时算 W_evid（§8 表 verified 比例） | growth-metrics.md |
| `gate_decide.py` | 拿 hᵢ 与 h' 在 Dᵢ、B 上的两对读数，算增量、判 g_in / g_out，输出 §5 报告、`verdict.json`、台账行；`--gate-mode` 八种（见下） | gate-rules.md §1–§3、§5 |

`gate_decide.py --gate-mode`：`3d`（正式，完整三维 + 成本）· `3d-noT` / `3d-noS` / `3d-noW`（消融）· `1d`（OpsHarness 式准确率 + 成本，arXiv 2608.25661 §V-C Eqs.9–11）· `1d-strict --delta δ`（一维加硬改善余量）· `none`（全接受）· `random --accept-rate p --seed s`（接受率匹配对照，可复现）。生产进化只用 `3d`；其余模式是论文（studio-paper）对照实验用，裁决为 ACCEPT 也不得合入生产 IR。

案例执行结果由引擎侧运行器产出：`growth_metrics.py` 的 `RESULTS_RUNNERS` 目前只有 `jsonl`（读预先录好的行）。clife-onto-engine 的批量执行接入时在这里加 runner，把每条案例（含 variants、每次 run）的 pass / tokens / steps 转成同样的行。

## Pre-check

1. 读 `studio/changes/$ARGUMENTS` 指定的提案文件（`{ontology_id} {n}`），要求 `attempt ≤ max_attempts`。
2. 确认 clife-onto-engine 可用、`{target_dir}` 可 import。不可用 → 只能做静态校验，报告 `NO_ENGINE`，不出裁决。
3. 确认 CQ 能被引擎批量执行并产出 `results.jsonl`（字段约定 `python3 $S/growth_metrics.py runner-spec`）：`cq/golden.yaml` 与 `.studio-evolve/{id}/cases/**/*.yaml` 需要一条 YAML → `ActionCQ / QueryCQ` 的加载路径（compile-rules 写明目前要人转写）。没有 → 报告 `CQ_RUNNER_MISSING`，这是整条管线的第一块砖，先停。
4. 读 `.studio-evolve/{id}/config.yaml`（`beta / window_size / stratify / backbones / repeat_runs`）。
5. 读 `growth-ledger.md` 取上一轮基线读数。

## Workflow

### 0. 沙箱
复制 `{target_dir}/` 与 `ontology-map.md` 到 `.studio-evolve/{id}/sandbox/{n}/`；在副本 IR 上应用提案的每个 ω；对副本目录以 modify 模式调 `ontology-compile`；跑 `ontology-validate` 的 A（结构）、B（治理）两段，任一不过 → 直接 `REVISE`，原因写「结构 / 治理不过」，不进关卡。

### 1. 留出集
```bash
python3 $S/holdout_sample.py --cases-dir .studio-evolve/{id}/cases --round {r} --holdout-size {config.holdout_size 或 window_size} \
    --exclude-file studio/changes/{id}/proposals/{n}.md 对应的 Dᵢ 清单 --ledger .studio-evolve/{id}/growth-ledger.md
```
每轮只抽一次，跑完即封存（文件已存在脚本拒绝覆盖）。

### 2. 跑案例、算读数
- `Dᵢ` = 提案 `source_cases`（含 real 与 sim）；`B` = 本轮留出集。
- 在 `Dᵢ ∪ B` 上分别跑 `hᵢ`（生产插件）与 `h'`（沙箱），每条案例连同 `variants` 都跑，重复 `repeat_runs` 次，产出 `results.jsonl`（`state` 分别为 `h_i` / `h_prime`）。
- 四份读数：
```bash
python3 $S/growth_metrics.py compute --results results.jsonl --state h_i     --cases-file Di.yaml --ir ontology-map.md --out readings/hi-Di.json
python3 $S/growth_metrics.py compute --results results.jsonl --state h_prime --cases-file Di.yaml --ir sandbox/{n}/ontology-map.md --out readings/hp-Di.json
python3 $S/growth_metrics.py compute --results results.jsonl --state h_i     --cases-file cases/holdout/{r}.yaml --ir ontology-map.md --out readings/hi-B.json
python3 $S/growth_metrics.py compute --results results.jsonl --state h_prime --cases-file cases/holdout/{r}.yaml --ir sandbox/{n}/ontology-map.md --out readings/hp-B.json
```
- 多骨干：每个骨干各一组 `results.jsonl` 与读数，`gate_decide.py` 用多个 `--outer-pair`。

### 3. 裁决（内关卡 ∧ 外关卡）
```bash
python3 $S/gate_decide.py --config .studio-evolve/{id}/config.yaml --proposal {n} --attempt {k} --round {r} --window {w} \
    --inner-base readings/hi-Di.json --inner-cand readings/hp-Di.json \
    --outer-pair default readings/hi-B.json readings/hp-B.json \
    --gate-mode 3d --holdout-file cases/holdout/{r}.yaml --sandbox sandbox/{n} \
    --ledger .studio-evolve/{id}/growth-ledger.md --verdict-out .studio-evolve/{id}/verdicts/{r}-{n}-{k}.json
```
脚本按 §1 判 `growOK ∧ costOK ∧ strict`、按 §2 判 `growOK ∧ costOK`（S 用完整 min），`backbones` 多于一个时每个骨干都要过（`PANEL`），单骨干标 `SINGLE_BACKBONE`；某一维两边都缺读数时该维不参与判定并打 flag，不会因为算不出而通过。

### 4. 处置
- 两关都过 → `ACCEPT`：提案文件 Verdict 段回填，状态「待人确认合入」。**不合入**，合入由 `cycle` 命令在人确认后对生产 IR 重放。
- 任一关不过 → `REVISE`：回填失败读数与失败案例清单，提示 `/studio-evolve:attribute` 以这些案例为新负例修订（`attempt + 1`）。
- `attempt = max_attempts` 仍不过 → `REJECT`：提案移到 `proposals/rejected/`，失败签名（缺陷类型、元素集合、回退维度、回退 family）写进 growth-ledger 拒绝表。

### 5. 记账
`gate_decide.py --ledger` 已追加读数行（含 `exec=AUTO/{SINGLE_BACKBONE|PANEL}`、`gate_mode`、`script_hash`）；`holdout_sample.py --ledger` 已追加留出集记录。REJECT 时把失败签名手工填进「拒绝签名表」。

### 6. 清理
删除 `sandbox/{n}/`。

### 7. 报告
转述 `gate_decide.py` 打印的 §5 格式报告（也存于 `verdict.json` 的 `report`）。

## Expected Inputs

一份提案 + 生产插件 + 案例库 + config + growth-ledger + 可用的引擎与 CQ 运行器。

## Expected Outputs

提案文件 Verdict 段回填；`cases/holdout/{round}.yaml`；`verdicts/{r}-{n}-{k}.json`；growth-ledger 新增读数行与留出集记录；裁决报告。

## Out of Scope

- 不改生产 IR 与生产插件（合入在 `cycle` 命令里、人确认后）。
- 不调阈值。
- 不在留出集上做任何「再试一次」：留出集每轮只跑一次，跑完即封存。
