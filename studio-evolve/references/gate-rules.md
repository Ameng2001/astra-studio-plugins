# Gate Rules — 双重闸门（内关卡 ∧ 外关卡）

> 一份归因出来的提案只是假设，不是改进。它可能过拟合来源案例，也可能这边有益、那边有害。
> 提案只有在沙箱里通过**两道关卡**才合入；任一道失败就退回修订，最多三轮，仍不过则丢弃并记档。
> 口径沿用 OpsHarness 的双重关卡（源案例严格更优 + 留出集不回退 + 成本容忍带），把单一准确率换成 `growth-metrics.md` 的三维读数。

符号：当前本体 `hᵢ`，应用提案 `P` 后的候选 `h' = Apply(hᵢ, P)`；来源案例集 `Dᵢ`（本轮归因用到的案例，含 `real` 与 `sim`）；留出集 `B`（只含 `real`，每轮重抽）。

---

## 0. 沙箱

1. 把 `{target_dir}/` 整目录复制到 `.studio-evolve/{id}/sandbox/{proposal}/`，连同 `ontology-map.md` 的副本。
2. 在副本上应用提案（改 IR 副本 → 以 modify 模式调 `ontology-compile` 到副本目录）。
3. 生产插件与生产 IR 在裁决前**不被触碰**。
4. 裁决后无论通过与否，沙箱目录清理；通过的提案由 `cycle` 命令在人确认后对生产 IR 重放。

---

## 1. 内关卡 g_in — 在来源案例上严格更优

在 `Dᵢ` 上分别测 `hᵢ` 与 `h'`，取三维增量与成本增量：

```
ΔT = T_k(h', Dᵢ) − T_k(hᵢ, Dᵢ)          逐层向量，k = 1..4
ΔS = S_consist(h', Dᵢ) − S_consist(hᵢ, Dᵢ)
ΔW = ( W_evid(h') − W_evid(hᵢ),  W_family(h', Dᵢ) − W_family(hᵢ, Dᵢ) )
ΔC = C_tok(h', Dᵢ) − C_tok(hᵢ, Dᵢ)
```

判定：

```
growOK  ≡ 每一层 ΔT_k ≥ 0  ∧  ΔS ≥ 0  ∧  ΔW 两分量 ≥ 0            三维都不降
costOK  ≡ ΔC ≤ max( β · C_tok(hᵢ, Dᵢ), 1 )                          成本增幅在容忍带内，默认 β = 0.05
strict  ≡ 存在某层 ΔT_k > 0  ∨  ΔS > 0  ∨  ΔW 某分量 > 0              成长轴至少一项严格改善（v2：ΔC 不计）
g_in    ≡ growOK ∧ costOK ∧ strict
```

`strict` 把「无实际作用的变更」挡在外面；`costOK` 的下限 1 是为了旧成本接近零时不把约束卡死。

> **v2（2026-10-23，criteria amend）**：strict 原含 `ΔC < 0`。round 5 的朴素修法 P9b 在 Dᵢ 上零增长（ΔT=ΔS=ΔW=0），却因 ΔC −0.8 过了内关卡——runner 的 steps 成本把违规规则数计入，「拒绝改放行」永远更便宜。自 v2 起 ΔC 只进 `costOK`（约束），不构成严格改善；一维基线 `1d` / `1d-strict` 的判定式沿用 OpsHarness（ΔA>0 ∨ ΔC<0），不随本条改，差异只来自判定式本身。v1 下所有 3d 族 ACCEPT（P2–P8）在 v2 下仍成立（内关卡都有 ΔT_k>0），不重判。

---

## 2. 外关卡 g_out — 在留出集上不回退

### 留出集 B 的构造（每轮重抽，不固定）

1. 候选池 = `cases/real/` 中**不在 `Dᵢ` 里**的案例。
2. 按 `family × tier` 分层，再按案例发生时间分前后两半，分层抽样；每层至少 1 条，总量默认等于本轮进化窗口的大小（`config.yaml` 的 `window_size`）。
3. 写 `cases/holdout/{round}.yaml`，只记案例 id；标签私有，不进任何提示词。
4. 候选池不足以每层出 1 条时，报告 `HOLDOUT_THIN` 并按实际可抽的层数执行，不用 `sim` 补。

每轮重抽的目的：提案没法跨轮次拟合一个静态留出集。

### 判定

```
g_out ≡ growOK(h', hᵢ; B) ∧ costOK(h', hᵢ; B)
```

只要求不回退，不要求严格改善：留出集上的提升是额外收获，不是准入条件。
`S` 在这里用完整的 `min(S_consist, S_holdout, S_stable)`。

### 跨骨干面板（可选）

`config.yaml` 的 `backbones` 列出多于一个骨干时，`g_out` 在每个骨干上分别算，**全部**通过才算通过。这是「行业本体不随骨干分叉」的闸门化表达；只配一个骨干时退化为单骨干评估，报告标 `SINGLE_BACKBONE`。

---

## 3. 采纳、修订、丢弃

```
accept ≡ g_in(h', hᵢ; Dᵢ) ∧ g_out(h', hᵢ; B)
```

- **accept**：候选进入「待合入」状态，`cycle` 命令展示三维增量给用户，人确认后原子地 `hᵢ₊₁ ← h'`（对生产 IR 重放提案、modify 模式重编译、`ontology-validate` 结构与治理两段必须全绿）。
- **某道关卡失败**：提案退回 `evolve-attribute`，把失败的案例作为新的负例，修订后重新进沙箱。同一提案最多 **3 次**尝试。
- **三次仍不过**：丢弃，提案文件移到 `proposals/rejected/`，连同失败签名（哪些案例、哪一维回退）记进 `growth-ledger.md`。后续归因看到同一失败签名时不得重复提议。

---

## 4. 配置项（`.studio-evolve/{id}/config.yaml`）

| 键 | 默认 | 说明 |
|---|---|---|
| `window_size` | 20 | 一个进化窗口包含的轨迹数，也是留出集默认大小 |
| `beta` | 0.05 | 成本容忍带 |
| `theta_tier` | 0.8 | 某层算「稳定通过」的通过率阈值（只用于报告 `T`） |
| `repeat_runs` | 3 | 算 `S_stable` 的重复次数 |
| `max_attempts` | 3 | 单提案最多尝试次数 |
| `stratify` | `[family, tier, time_half]` | 留出集分层维度 |
| `backbones` | `[default]` | 评估面板；多于一个时启用跨骨干闸门 |

阈值在一次进化过程中固定。改阈值要开新的 growth-ledger，旧读数不可与新阈值混比。

---

## 5. 裁决报告格式（`evolve-verify` 必须输出）

```
提案 {n}（第 {attempt}/{max_attempts} 次）· 本体 {ontology_id} · 沙箱 {path}
来源案例 Dᵢ：{m} 条（real {a} / sim {b}）    留出集 B：{k} 条（real，{层数} 层，round {r}）
内关卡 g_in：
  ΔT = ({ΔT_1}, {ΔT_2}, {ΔT_3}, {ΔT_4})   ΔS_consist = {..}   ΔW = ({..}, {..})   ΔC_tok = {..}（带宽 {..}）
  growOK {✓/✗}  costOK {✓/✗}  strict {✓/✗}  → {PASS/FAIL}
外关卡 g_out（骨干 {name}）：
  ΔT = (...)   ΔS = {..}（min 分量 = {S_consist/S_holdout/S_stable}）   ΔW = (...)   ΔC_tok = {..}
  growOK {✓/✗}  costOK {✓/✗}  → {PASS/FAIL}
裁决：ACCEPT（待人确认合入）/ REVISE（退回归因，失败案例：…）/ REJECT（三次未过，签名已记档）
执行方式：AUTO（脚本）/ MANUAL（本版无脚本，按规范手算，读数可能有人工误差）
```
