# 台账 schema（ledger/runs.jsonl，一行一个 run，只追加）

```json
{
  "run_id": "r-2026-10-23-0007",
  "paper": "evolving-foundation",
  "kind": "main | baseline | ablation | pilot | rebuttal",
  "rq": ["RQ-2"], "hypothesis": ["H-2a"],
  "status": "formal | exploratory | failed | void",
  "void_reason": null,
  "owner": "lin",
  "ts": "2026-10-23T10:12:00+08:00",
  "git": {"commit": "5605dda", "dirty": false},
  "config_hash": "sha256:…", "config_path": "configs/evo_w20.yaml",
  "seed": 3, "duration_s": 1840,
  "metric": {"name": "T_tier3", "value": 0.71, "ci": [0.64, 0.78]},
  "n": 60,
  "contamination": "excluded | known_overlap | unknown",
  "varied": ["ontology_version"],
  "confounded": false,
  "fairness": {"budget": "20 trials", "search_space": "lr,beta", "seeds": 3, "hardware": "M3 Max"},
  "source": "manual | studio-evolve:growth-ledger",
  "source_ref": {"file": "…/growth-ledger.md", "round": 3},
  "criteria_hash": "sha256:…",
  "blockers": [],
  "note": ""
}
```

字段说明补充：`source_ref` 只在导入行出现，用于重导入时跳过已有行；`criteria_hash` 记该 run 被判定时的 criteria.lock 哈希（formal 行与它判定所依据的判据版本绑定）；`blockers` 记它为何没入 formal（formal 行为空列表）。

## 自动采集（record_run.py）
`run_id · ts · git · config_hash · seed · duration_s · owner · source`

## 学生补填（≤ 5 项）
`rq · metric · n · contamination · fairness`（缺项不拒入账，判 exploratory 并提示）

## formal 判定
`criteria.lock` 哈希一致 ∧ 五项齐 ∧ `n ≥ n_min` ∧ `confounded == false` ∧ `status != failed`。exploratory 不能事后升级；要正式就重跑。

## studio-evolve 导入映射（import_evolve_ledger.py）
growth-ledger 每轮一行 → `kind: main`，`metric` 按三轴拆成三行（`T_tier{k}` / `S` / `W_evid`）+ 一行成本 `C_tok`；`rq` 由用户在 config 中映射一次（如 T → RQ-1，S → RQ-2，W → RQ-3）；`n` = 该轮窗口大小；`contamination` 由用户声明一次（留出集与仿真集分离 → `excluded`）。
