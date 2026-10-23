---
description: Run one self-evolution round on an ontology — attribute trajectories into proposals, let the user review, verify each proposal under the dual gate, and replay accepted ones onto the live IR after confirmation.
---

# /studio-evolve:cycle {ontology_id}

对一个本体跑**一轮**自生长：归因 → 人看提案 → 双重闸门 → 人确认 → 合入。每轮之间用户确认，生产 IR 只在最后一步、人点头之后才动。

```
Step 1: evolve-attribute   窗口轨迹 + 反馈 → studio/changes/{id}/proposals/{n}.md（若干）
        ↓ 用户看提案（分歧点、缺陷类型、编辑行、证据）—— 可删、可改、可合并
Step 2: evolve-verify      每份提案进沙箱 → 内关卡 ∧ 外关卡 → ACCEPT / REVISE / REJECT
        ↓ REVISE 的回到 Step 1 修订（attempt+1，最多 3 次）
Step 3: 合入               ACCEPT 的提案：展示三维增量 → 用户确认 → 对生产 IR 重放编辑
                           → /studio-ontology:compile（modify）→ /studio-ontology:validate A/B 全绿
        ↓
growth-ledger 记一行；窗口号 +1
```

## 执行

1. 读 `.studio-evolve/$ARGUMENTS/config.yaml`，确定本轮窗口 `{window}`（上一轮 +1）与轨迹目录。轨迹不足 `window_size` 条 → 告知差多少，问用户是否按现有数量跑（报告标 `SHORT_WINDOW`）。
2. 调用 skill **evolve-attribute**，参数 `{ontology_id}`。
3. **停下**，逐份展示提案的 `hypothesis / deficit_type / Divergence / Edits / 证据状态`。用户可以直接改提案文件。被删的提案记 `WITHDRAWN`，不进闸门。
4. 对每份保留的提案调用 skill **evolve-verify**，参数 `{ontology_id} {n}`。按 `references/gate-rules.md` 出裁决。
5. `REVISE` 的提案：带着失败案例回 Step 2 修订一次（调 evolve-attribute 的修订模式），再 verify；最多 3 次。
6. `ACCEPT` 的提案：汇总展示 `ΔT / ΔS / ΔW / ΔC` 与留出集信息，**问用户是否合入**。确认后：
   - 对 `studio/changes/{id}/ontology-map.md` 重放提案的每个 ω（add 追加行、update 替换行、delete 删行；EVIDENCE 进 §8）。
   - 调 `/studio-ontology:compile {ontology_id}`（modify 模式，只重生成变更要素）。
   - 调 `/studio-ontology:validate {ontology_id}`，A（结构）B（治理）必须全绿；不绿则回滚本次 IR 改动并把提案改为 `REVISE`。
   - 提案文件移到 `proposals/merged/`，IR 的 `iteration` +1。
7. 结束报告：本轮提案数、ACCEPT / REVISE / REJECT / WITHDRAWN 各几、合入几、三维读数与上一轮对比、下一窗口提示。

> 两条不变量：**仿真案例不进外关卡**；**没有人确认，生产 IR 不动**。
