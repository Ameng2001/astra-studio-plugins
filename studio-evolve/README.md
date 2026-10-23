# studio-evolve

> **自生长管线** —— 让 `studio-ontology` 编译出的本体插件，从自己的运行轨迹里长出新本体。
> studio-ontology 负责「分析 → 本体 v0」，本插件负责「本体 vₙ → 本体 vₙ₊₁」，clife-onto-engine 负责运行并留下轨迹。

设计来源是两篇自进化外挂层的论文：OpsHarness（arXiv 2608.25661，自进化 RCA harness，双重关卡验证）和 EvoOntology（arXiv 2609.15779，数据智能体的自进化本体层，证据接地 + 归因补丁 + 配对评估）。本插件把两者的循环搬到五要素本体上，并把验证闸门改成三维测评：**长高 · 长壮 · 长胖**。

## 在管线里的位置

```
studio-planner ──▶ studio-ontology ──▶ clife-onto-engine ──▶ 运行轨迹（审计日志 / 会话）
                    map → compile → validate         │
                          ▲                          ▼
                          │                  studio-evolve
                          │        ground → simulate → [attribute → verify]ⁿ
                          └──── 通过闸门的提案以 modify 模式回到 compile ◀──┘
```

一次性或结构变化时跑：`ground`（证据接地）、`simulate`（仿真案例）。
每个进化窗口跑一轮：`attribute`（归因出提案）→ `verify`（双重闸门裁决）。

## 四个 skill

| skill | 环节 | 输入 | 输出 |
|---|---|---|---|
| `evolve-ground` | 动态本体构建 | `ontology-map.md` + 数据源 | IR 新增 §8 证据段：每条对象 / 映射 / 规则挂探测查询与采样结果 |
| `evolve-simulate` | 仿真加强 | 本体 + 证据 | `.studio-evolve/{id}/cases/sim/*.yaml`：从规则 / 动作 / 关系派生、带标签与难度层级的 CQ 案例 |
| `evolve-attribute` | 自生长迭代 · 归因 | 运行轨迹 + 反馈标签 | `studio/changes/{id}/proposals/{n}.md`：分歧点 → 五要素缺陷类型 → 原子 IR 编辑提案 |
| `evolve-verify` | 三维双重闸门 | 一份提案 | 沙箱裁决：内关卡（来源案例严格更优）∧ 外关卡（重抽留出集不回退），三维增量写入 growth-ledger；读数与判定由 `scripts/` 三个脚本算（AUTO） |

## 脚本（`scripts/`）

| 脚本 | 用途 |
|---|---|
| `runner_onto_engine.py` | 案例 → clife-onto-engine（IntentCompiler 路由 + validate / OQL 预演执行）→ `results.jsonl`；`--router manual|live`；`golden-to-cases` 转黄金集。跑不起来的案例记 `pass=null` + 原因 flag，不进通过率 |
| `holdout_sample.py` | 每轮重抽 real-only 分层留出集（`--holdout-size` 与 window_size 解耦；池薄报 `HOLDOUT_THIN`；同轮拒绝重抽） |
| `growth_metrics.py` | 从案例执行结果 `results.jsonl` 算三维读数 T / S / W 与成本 C；`runner-spec` 打印结果行字段约定；`--ir` 算 W_evid |
| `gate_decide.py` | 双重闸门裁决 + §5 报告 + `verdict.json` + 台账行；`--gate-mode` 八种：`3d`（生产）、`3d-noT/noS/noW`、`1d`、`1d-strict`、`none`、`random`（后七种只给论文对照实验） |
| `smoke_runner.py` | runner 冒烟：引擎自带 grass 插件 + 引擎录制的真 Qwen 路由，9 条案例 6 通过 3 真阴性（没有引擎时 SKIP） |
| `smoke_gate_modes.py` | 合成案例集上的端到端冒烟（80 条案例 × 2 变体 × 3 run，两份提案 × 7 种判定模式 + random 接受率校验） |

```bash
python3 studio-evolve/scripts/smoke_gate_modes.py        # 闸门链全绿
python3 studio-evolve/scripts/smoke_runner.py            # 引擎链全绿（需 ../onto-fundary-plugins）
```

引擎侧现状（2026-10-23）：路由与执行都接通了；真正的缺口在**插件实现**——glycowise 编译产物的 handler 与 function-backed 规则多为 TODO(FDE)（28 条黄金问题里 27 条不可判），以及每个案例的路由录制（`--router live` 可现录，需 LLM 端点）。

两份规范是脊梁：

| 文件 | 内容 |
|---|---|
| [`references/growth-metrics.md`](references/growth-metrics.md) | 三维指标的可计算定义：长高 = 难度阶梯，长壮 = 扰动一致与不回退，长胖 = 覆盖与证据率 |
| [`references/gate-rules.md`](references/gate-rules.md) | 双重闸门：内关卡 / 外关卡的判定式、留出集重抽、成本容忍带、修订与丢弃 |
| [`references/proposal-schema.md`](references/proposal-schema.md) | 原子提案格式：三种动作 × 五要素 + 映射 + CQ，缺陷类型分类，分歧点记录 |

## 用法

```bash
/studio-evolve:ground {ontology_id}      # 一次性：给 IR 挂证据
/studio-evolve:simulate {ontology_id}    # 一次性 / 结构变化后：生成仿真案例库
/studio-evolve:cycle {ontology_id}       # 每窗口一轮：attribute → 用户看提案 → verify → 通过的回 compile
# 或分步：
/studio-evolve:attribute {ontology_id}
/studio-evolve:verify {ontology_id} {proposal_n}
```

## 运行时工作区（stateful）

```
.studio-evolve/{ontology_id}/
├── config.yaml            # 窗口大小、闸门阈值 β、留出集分层维度、骨干面板
├── cases/
│   ├── real/              # 来自真实轨迹的案例（带私有标签）—— 外关卡唯一来源
│   ├── sim/               # 仿真派生案例 —— 只进内关卡
│   └── holdout/{round}.yaml   # 每轮重抽的留出集（案例 id 清单）
├── trajectories/{window}/ # 切块后的诊断轨迹（含反馈标签）
├── sandbox/{proposal}/    # 沙箱副本，裁决后清理
└── growth-ledger.md       # 每轮的三维读数、接受 / 拒绝、提案尝试次数
```

## 硬规则

1. **仿真案例不进外关卡。** 外关卡的留出集只从 `cases/real/` 重抽，否则是在验证仿真器而不是本体。
2. **闸门裁决是确定性的。** skill 负责读轨迹、写提案；接受与否由 `scripts/` 下的脚本按 `gate-rules.md` 算（脚本在后续版本落地，本版先定规范）。
3. **提案不直接改 IR。** 先落 `studio/changes/{id}/proposals/`，两道闸门都过、人看过，才合入 `ontology-map.md` 并以 modify 模式重编译。
4. **证据先于条目。** 没有探测查询支撑的新增对象 / 映射 / 规则，提案里必须标 `TODO(FDE)`，闸门按「证据率」扣长胖分。

## 诚实边界

- 闸门要在沙箱里跑 CQ，前提是 clife-onto-engine 可用，且 `cq/golden.yaml` 能被引擎批量执行。当前 compile-rules 写明 YAML 需人转写成 `cq.py`，这是本插件第一块要补的砖（见 `evolve-verify` 的 Pre-check）。
- 归因依赖引擎审计日志记录每一步动作、规则触发与回滚。若只记最终结果，`evolve-attribute` 退化为从会话轨迹提签名，精度下降，报告会明示。
- 闸门的数值计算、留出集重抽、裁决、记账与案例执行（runner）已脚本化（`scripts/`，标 `AUTO`）。仍缺：沙箱复制 / 重编译的脚本化（目前仍由 skill 按 §0 手工执行）；以及目标插件自身的实现完整度（handler 为 TODO 的本体跑不出可判读数）。

## 依赖

- 上游：`studio-core`（工作区）、`studio-ontology`（IR、编译、validate）。
- 运行时：clife-onto-engine（沙箱与轨迹来源）。
