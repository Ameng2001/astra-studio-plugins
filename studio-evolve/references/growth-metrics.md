# Growth Metrics — 三维测评的可计算定义（长高 · 长壮 · 长胖）

> 本体的「成长」不是一个分数，是三个正交的轴。每一轴都必须能从 CQ 案例库和本体文件上**确定性地算出来**，否则不能当闸门用。
> 符号：本体状态 `h`（IR + 编译产物），案例集 `D`，案例 `d = (q, expect, label, tier, family, origin)`。
> 单条案例的通过判定 `pass(h, d) ∈ {0, 1}`，由 clife-onto-engine 执行 `d.q` 后对照 `d.expect`（`query` / `action(X)` / `rejected(R)`）得出。

---

## 案例的扩展字段

`cq/golden.yaml` 原有 `q` / `expect` 两个字段。studio-evolve 的案例库在此之上加五个字段（见 `templates/case.yaml.tmpl`）：

| 字段 | 取值 | 用途 |
|---|---|---|
| `tier` | 1–4 | 难度层级，算长高 |
| `family` | 场景类别（自由文本，建议取自 event-storm 的 Hotspot 或 process-flow 的决策点名） | 留出集分层抽样、算长胖的场景覆盖 |
| `origin` | `real` \| `sim` | 真实轨迹派生 / 仿真派生。**只有 `real` 可进外关卡** |
| `label` | 私有的真实答案或真实根因（不进提示词） | 判 pass；由反馈钩子或仿真生成器写入 |
| `variants` | 扰动变体列表（改写问法 / 换实体 / 注入无关字段） | 算长壮 |

难度层级的判法（按案例需要的本体能力，不按问法长短）：

| tier | 需要什么 | 例子 |
|---|---|---|
| 1 | 单对象查询 | 这块地的退化等级是什么 |
| 2 | 跨关系多跳 | 这块地适用哪套种子包（Site → Degradation → RestorationMethod → SeedPack） |
| 3 | 带规则拦截的动作 | 用非乡土草种出方案，应被「乡土合规」回滚 |
| 4 | 多动作编排或带 HIL 的动作链 | 出方案 → 派工 → 验收，中途触发复核 |

---

## 长高 T(h, D) — 能力深度

```
T_k(h, D) = Σ_{d∈D, d.tier=k} pass(h,d) / |{d∈D : d.tier=k}|        每层通过率，k = 1..4
T(h, D)   = max { k : T_k(h, D) ≥ θ_tier }                            能稳定通过的最高层，默认 θ_tier = 0.8
```

报告两样：四层通过率向量 `(T_1, T_2, T_3, T_4)` 和最高稳定层 `T`。
闸门比较用向量的**逐层**增量（任一层下降即视为长高回退），不用单一的 `T`，因为 `T` 太粗、一个案例就能撬动。

---

## S(h, D) — 长壮 · 稳健性

三个分量，取最小值作为长壮读数（短板决定）：

```
S_consist(h, D) = 平均_{d∈D} [ pass(h,d) == pass(h, v) 对 d.variants 中所有 v 都成立 ]   扰动一致率
S_holdout(h, B) = 1 − max(0, A(h_prev, B) − A(h, B))                                       留出集不回退（A = 总通过率）
S_stable(h, D)  = 1 − 重复运行 r 次的通过率标准差                                           运行稳定性，默认 r = 3
S(h, D)         = min( S_consist, S_holdout, S_stable )
```

`S_holdout` 只在外关卡算（需要留出集 `B`）；内关卡报 `S_consist` 与 `S_stable`。
跨骨干面板开启时（`config.yaml` 的 `backbones` 多于一个），`S_consist` 在每个骨干上分别算后取最小，这就是「本体不随骨干分叉」的度量。

---

## W(h, D) — 长胖 · 覆盖广度

```
W_elem(h)    = 五要素 + 映射 + CQ 的条目总数（分项报告：Object / Link / Function / Rule / Action / Mapping / CQ）
W_evid(h)    = 有证据（IR §8 挂了探测查询与采样结果）的条目数 / 应有证据的条目数        证据率，应有证据 = Object + Mapping + function-backed Rule
W_family(h, D) = 至少有一条 tier≥2 案例通过的 family 数 / D 中出现的 family 数            场景覆盖率
W(h, D)      = (W_evid, W_family)                                                           两个比率，不合成
```

`W_elem` 只报告、不进闸门：条目数增长本身不是成长。进闸门的是两个比率。
长胖的回退判定：`W_evid` 或 `W_family` 任一下降。这样「堆了一批没证据的条目」会被判为长胖回退而不是长胖。

`W_evid` 的可计算口径（`growth_metrics.py compute --ir ontology-map.md`）：
- 分母是**结构计数**，从 IR 正文数出来：§1 Objects 表的行数 + §6 Mapping 表的行数（按 object 名）+ §4 Rules 表里 `backing=function` 的行数；不是 §8 表的行数——否则「只探测一条、一条 verified」会得到 1.0。
- 分子是 §8 Evidence 里 `status=verified` 且 (kind, element) 落在分母集合里的条目数；同一条目多行只算一次；`kind=LINK` 等不在分母的行只计 `table_rows`。
- 读数里 `W_evid_n = {verified, expected, table_rows, by_kind, denominator}` 给出分子分母与分 kind 明细。
- IR 没有 §8（`evolve-ground` 没跑过或数据源不可达）→ `W_evid = null` + `NO_EVIDENCE_TABLE`，分母照常报告；没给 `--ir` → `W_EVID_NOT_COMPUTED`。`gate_decide.py` 遇到任一侧 `W_evid` 为 null 时 ΔW 只看 `W_family`，verdict 记 `W_EVID_UNAVAILABLE`，不因算不出而通过或失败。
- 提案补丁只改编译产物（插件代码）、不改 IR 时，hᵢ 与 h' 的 `W_evid` 相同、ΔW_evid = 0；要让证据率进闸门，提案必须连 IR §8 一起改（新增条目带探测行）。

---

## 成本 C(h, D)

```
C(h, D) = 平均每案例的（引擎执行步数, 推理 token 数, 墙钟秒）
```

三个分量都报告；闸门用 token 数（没有 token 计量时用步数）。

---

## 读数的呈现（growth-ledger.md 每轮一行）

```
| round | proposal | T_1..T_4 | T | S_consist | S_holdout | W_evid | W_family | C_tok | inner | outer | verdict | attempts |
```

曲线图两张，每个进化窗口各一个点：三维读数随窗口的变化；`W_elem` 与总通过率叠画（证明长胖在收敛不在膨胀）。

---

## 不要做的事

- 不把三维合成一个总分。合成分会让「长胖掩盖长壮回退」。
- 不用单一 `T`（最高稳定层）做闸门比较，用逐层向量。
- 不把 `sim` 案例算进 `S_holdout`。
- 不在留出集上调阈值 `θ_tier`、`β`：阈值写在 `config.yaml`，整个进化过程固定。
