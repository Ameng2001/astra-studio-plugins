---
name: record-run
description: Record one experiment run into the paper ledger — auto-collect git commit/dirty, config hash, seed and duration, take at most five student fields (rq, metric, n, contamination, fairness), classify the run formal or exploratory against the frozen criteria, append to runs.jsonl (append-only), roll up hypothesis status and RQ coverage, and flag thesis drift when a negative result contradicts the thesis. --kind baseline adds a deviation table against the original paper; --import studio-evolve bulk-loads a growth ledger. Use after every run, including failed and negative ones.
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
user-invocable: true
---

# Record Run

台账是整个插件的主键。它必须由运行入口写、只增不删、负结果照记；结果表只能从它生成。入账负担要低：能自动采的不让学生填，学生补填不超过五项。formal / exploratory 由系统判，探索性不能事后升级。

## Intent
- 堵住负结果丢失与选择性呈现；让导师看到的是全部 run。
- 触发：任何一次 run 结束（含崩溃）；基线复现；导入 studio-evolve 成长台账。

## Expected Inputs
- run 的输出目录或指标 JSON（`metrics.json` 可带 `metric{name,value,ci}` 或 `metrics{name: value}`、`n`、`seed`、`duration_s`）
- `proposal/rq.yaml` + `proposal/criteria.yaml` + `proposal/criteria.lock`（formal 判定与假设判定依据，形状见下）
- 学生补填：`rq`、`metric`、`n`、`contamination`（excluded | known_overlap | unknown）、`fairness`（budget / search_space / seeds / hardware）
- `--kind baseline`：`proposal/baselines.md` 表格行 `| 方法 | 指标 | 原论文数值 | 出处 |`，如 `| EvoOntology | T_tier3 | 0.62 | Li et al. 2025, Tab.3 |`
- 导入：`.studio-evolve/{id}/ledger/growth-ledger.md`（每轮一行 T_1..T_4 / S_* / W_* / C_tok）

`rq.yaml` 与 `criteria.yaml`（map 键控，`read_yaml_lite` 可读；完整说明见 `scripts/record_common.py` 文件头）：
```yaml
rqs:                                  # proposal/rq.yaml
  RQ-1:
    question: "进化能否提升高层级案例通过率"
    hypotheses: {H-1a: "T_tier3 通过率达到 0.7"}
criteria:                             # proposal/criteria.yaml
  RQ-1:
    pass: {metric: T_tier3, op: ">=", value: 0.7}
    fail: {metric: T_tier3, op: "<", value: 0.5}
    agg: last                         # mean(默认) | median | min | max | last
  H-1a: {...}                         # 可选：H 级判据，缺省继承所属 RQ
```
`criteria.lock` 是 JSON，`hash` = `sha256:` + criteria.yaml 文本的 sha256（与 `paper_ws.sha256_text` 一致）。

## Expected Outputs
- `ledger/runs.jsonl`（schema 见 `${CLAUDE_SKILL_DIR}/../../references/ledger-schema.md`；只追加；作废 = 追加带 `void_reason` 的同 run_id 行）
- `ledger/hypotheses.md`（每个 RQ / H：支持 / 证伪 / 待定 + 所用 formal run；覆盖矩阵 RQ × formal/exploratory/failed；作废、探索性、失败 run 及理由）
- `ledger/drift.md`（formal 证据落入 fail 判据的 RQ / H → 「论点偏离」+ 改 claim / 换实验 / 弃 RQ 三选一；无则写「无偏离」）
- `ledger/baseline-deviation.md`（`--kind baseline`：原论文 vs 复现、相对偏差、容差、需解释与否）
- `status.json` 的 `counts`（runs_formal / runs_exploratory / rqs / rqs_covered）

## Preconditions
- 工作区 `.studio-paper/{paper-id}/` 存在（项目根 = cwd 或 `$STUDIO_PAPER_ROOT`）。
- formal 需 criteria.lock 存在且与 criteria.yaml 当前哈希一致；否则照样入账，但只能 exploratory。

## Workflow
脚本目录 `S=${CLAUDE_SKILL_DIR}/scripts`。每个脚本入账后自动调 rollup，不需要手动再跑。

1. 一次 run 结束（含崩溃、负结果）→ 入账：
   `python3 $S/record_run.py <paper-id> <run-dir|metrics.json> --rq RQ-1 --metric T_tier3=0.71,0.64,0.78 --n 60 --contamination excluded --fairness "budget=20 trials;search_space=lr,beta;seeds=3;hardware=M3 Max" [--kind ablation] [--h H-1a] [--varied ontology_version] [--seed 3] [--config configs/x.yaml] [--note ..]`
   - 自动采集 run_id / ts / git commit+dirty / owner(git user) / config_hash（`--config`，否则 run-dir 下 `config.*`）/ seed / duration_s。
   - 学生五项缺了不拒入账：判 exploratory 并逐条打印原因；`--varied` 多于一个因素 → `confounded: true`。
   - 崩溃的 run：加 `--status failed --note "原因"`。
2. 判定（系统做，不由学生选）：lock 一致 ∧ 五项齐 ∧ n ≥ n_min ∧ 非混杂 ∧ 非 failed → formal；否则 exploratory。探索性不能事后升级，要正式就重跑。
3. 基线复现：`--kind baseline [--baseline 方法名]`，偏差追加到 `ledger/baseline-deviation.md`。|相对偏差| > 5%（`thresholds.baseline_deviation_tol` 可覆盖）标「需解释」，把解释写进 `--note`；写不出解释前不进主实验。
4. 导入 studio-evolve：
   `python3 $S/import_evolve_ledger.py <paper-id> .studio-evolve/<id>/ledger/growth-ledger.md --map "T=RQ-1,S=RQ-2,W=RQ-3,C=RQ-4" --contamination excluded [--include-baseline] [--dry-run]`
   每轮 → `T_tier1..4`、`S`（三分量取最小，note 注明哪个）、`W_evid`、`C_tok` 各一行；n = window 列；重复导入按 (source, 文件, round, metric) 跳过。映射可写进 `config.yaml` 的 `ledger.evolve_map` 免传。
5. 作废（只追加 tombstone，原行保留）：`python3 $S/record_run.py <paper-id> --void <run_id> --reason "..."`
6. 单独重算：`python3 $S/rollup_hypotheses.py <paper-id>`（确定性；总是 exit 0；打印偏离条数）。
7. 打印出「论点偏离 N 条」且 N > 0 → 把 `ledger/drift.md` 指给导师，请其三选一；不阻塞入账，但 freeze-results 要求偏离已处置。

## Example
```bash
S=${CLAUDE_SKILL_DIR}/scripts
python3 $S/record_run.py evolving-foundation runs/2026-10-23-w20 --rq RQ-1 --metric T_tier3=0.71,0.64,0.78 \
  --n 60 --contamination excluded --fairness "budget=20 trials;search_space=lr,beta;seeds=3;hardware=M3 Max" --varied ontology_version
python3 $S/record_run.py evolving-foundation runs/evo-repro/metrics.json --kind baseline --baseline EvoOntology \
  --rq RQ-1 --metric T_tier3 --n 50 --contamination excluded --fairness "budget=official;search_space=default;seeds=3;hardware=M3 Max"
python3 $S/import_evolve_ledger.py evolving-foundation .studio-evolve/onto1/ledger/growth-ledger.md \
  --map "T=RQ-1,S=RQ-2,W=RQ-3,C=RQ-4" --contamination excluded
```

## Out of Scope
- 不跑实验；不改 RQ（propose-experiments --amend）；不生成结果表（freeze-results）；无闸门。
