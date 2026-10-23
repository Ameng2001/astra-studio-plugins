#!/usr/bin/env python3
"""holdout_sample.py —— 每轮重抽只含 real 案例的留出集（gate-rules.md §2「留出集 B 的构造」）。

  holdout_sample.py --cases-dir .studio-evolve/{id}/cases --round 3 --holdout-size 100 \
      [--exclude-file Di.yaml | --exclude a,b] [--stratify family,tier,time_half] [--seed 0] \
      [--out cases/holdout/3.yaml] [--ledger growth-ledger.md]

规则（逐条对应 §2）
  1. 候选池 = cases/real 中不在 Dᵢ 的案例；origin=sim 一律不进（§2 第 1 条 + growth-metrics「不把 sim 算进 S_holdout」）。
  2. 按 --stratify 分层（默认 family × tier × time_half；time_half 按候选池 observed_at 的中位数分前后半），
     每层至少 1 条，其余名额按层大小比例分配；总量 = --holdout-size（与 window_size 解耦：论文判据用 100）。
  3. 写 cases/holdout/{round}.yaml，只记 id 与分层键；标签不进任何提示词。
  4. 候选池不够每层 1 条、或总量不足 → 报 HOLDOUT_THIN，按实际可抽执行，不用 sim 补。
  5. --merge-min N（协议 v3，2026-10-23）：分层后样本 < N 的层并入同 family 的「其余 tier」层（tier 键记为 rest），
     --stratify 此时应为 family,tier；--check-pool 只做前置复核不抽样：打印并层后每层样本数，池 ≥ 2×holdout_size 且每层 ≥ N 才 ✓（exit 0），否则 exit 3。
  每轮重抽（seed 建议 = round 或 config 指定），同一轮只抽一次：输出文件已存在则拒绝覆盖。
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_cases, load_yaml  # noqa: E402


def strat_key(case: dict, dims: list[str], median_date: str | None) -> tuple:
    key = []
    for d in dims:
        if d == "time_half":
            od = str(case.get("observed_at", ""))
            key.append("late" if (median_date and od > median_date) else "early")
        else:
            key.append(str(case.get(d, "?")))
    return tuple(key)


def allocate(strata: dict[tuple, list[str]], size: int) -> tuple[dict[tuple, int], list[str]]:
    """每层 ≥1，其余按比例（最大余数法）。返回 (每层名额, 警告)。"""
    warn = []
    layers = {k: v for k, v in strata.items() if v}
    if not layers:
        return {}, ["HOLDOUT_THIN: 候选池为空"]
    pool = sum(len(v) for v in layers.values())
    if pool < size:
        warn.append(f"HOLDOUT_THIN: 候选池 {pool} < 目标 {size}，按实际可抽")
        return {k: len(v) for k, v in layers.items()}, warn
    if len(layers) > size:
        warn.append(f"HOLDOUT_THIN: 层数 {len(layers)} > 目标 {size}，部分层抽不到")
        return {k: (1 if i < size else 0) for i, k in enumerate(sorted(layers))}, warn
    quota = {k: 1 for k in layers}
    rest = size - len(layers)
    shares = {k: rest * len(v) / pool for k, v in layers.items()}
    for k in layers:
        quota[k] += int(shares[k])
    left = size - sum(quota.values())
    for k in sorted(layers, key=lambda k: shares[k] - int(shares[k]), reverse=True)[:left]:
        quota[k] += 1
    for k, v in layers.items():
        if quota[k] > len(v):
            warn.append(f"HOLDOUT_THIN: 层 {k} 只有 {len(v)} 条，名额 {quota[k]} 截到 {len(v)}")
            quota[k] = len(v)
    return quota, warn


def main() -> None:
    ap = argparse.ArgumentParser(description="重抽 real-only 留出集（gate-rules.md §2）")
    ap.add_argument("--cases-dir", required=True, type=Path, help=".studio-evolve/{id}/cases（会读其下 real/）")
    ap.add_argument("--round", required=True, type=int)
    ap.add_argument("--holdout-size", required=True, type=int)
    ap.add_argument("--exclude-file", type=Path, help="Dᵢ：yaml，含 source_cases/ids 列表")
    ap.add_argument("--exclude", help="Dᵢ：逗号分隔 id")
    ap.add_argument("--stratify", default="family,tier,time_half")
    ap.add_argument("--seed", type=int, help="默认 = round")
    ap.add_argument("--out", type=Path, help="默认 {cases-dir}/holdout/{round}.yaml")
    ap.add_argument("--ledger", type=Path, help="growth-ledger.md：追加「留出集记录」一行")
    ap.add_argument("--force", action="store_true", help="允许覆盖已存在的留出集文件（违反「每轮只抽一次」，仅调试用）")
    ap.add_argument("--merge-min", type=int, default=0, help="协议 v3：样本 < N 的层并入同 family 的 rest 层；0 = 不并层")
    ap.add_argument("--check-pool", action="store_true", help="只复核正式 run 前置（池 ≥ 2×holdout_size 且并层后每层 ≥ merge-min），不抽样、不写文件")
    a = ap.parse_args()

    real_dir = a.cases_dir / "real"
    if not real_dir.is_dir():
        sys.exit(f"没有 {real_dir}（留出集只能来自 real 案例）")
    cases = {k: v for k, v in load_cases(real_dir).items() if v.get("origin", "real") == "real"}
    excl: set[str] = set()
    if a.exclude_file:
        y = load_yaml(a.exclude_file)
        lst = y.get("source_cases") or y.get("ids") or [] if isinstance(y, dict) else y
        excl |= set(map(str, lst))
    if a.exclude:
        excl |= {x.strip() for x in a.exclude.split(",") if x.strip()}
    pool = {k: v for k, v in cases.items() if k not in excl}
    dims = [d.strip() for d in a.stratify.split(",") if d.strip()]
    dates = sorted(str(c.get("observed_at", "")) for c in pool.values() if c.get("observed_at"))
    median = dates[len(dates) // 2] if dates else None
    strata: dict[tuple, list[str]] = defaultdict(list)
    for cid, c in sorted(pool.items()):
        strata[strat_key(c, dims, median)].append(cid)
    merged_note = []
    if a.merge_min > 0:
        if dims[:1] != ["family"]:
            sys.exit("--merge-min 要求 --stratify 以 family 开头（协议 v3：family × tier，<N 并入同 family 的 rest 层）")
        small = [k for k, v in strata.items() if len(v) < a.merge_min]
        for k in small:
            rest = (k[0],) + ("rest",) * (len(k) - 1)
            strata[rest].extend(strata.pop(k))
            merged_note.append(f"{'×'.join(k)}→{'×'.join(rest)}")
        for k in list(strata):
            strata[k].sort()
    if a.check_pool:
        need_pool = 2 * a.holdout_size
        print(f"real-pool 前置复核：池 {len(pool)}（排除 Dᵢ {len(excl)}），分层 {', '.join(dims)}，并层阈值 {a.merge_min}，层数 {len(strata)}")
        if merged_note:
            print("  并层：" + "；".join(merged_note))
        thin_layers = []
        for k in sorted(strata, key=lambda k: (k[0], str(k[1:]))):
            n = len(strata[k]); mark = "" if n >= a.merge_min else "  ← 不足"
            print(f"  {'×'.join(k):<28} {n:>4}{mark}")
            if n < a.merge_min:
                thin_layers.append(k)
        ok = len(pool) >= need_pool and not thin_layers
        print(f"{'✓' if ok else '✗'} 池 {len(pool)} {'≥' if len(pool) >= need_pool else '<'} {need_pool}；并层后不足 {a.merge_min} 的层 {len(thin_layers)} 个"
              + (f"：{'；'.join('×'.join(k) for k in thin_layers)}" if thin_layers else ""))
        sys.exit(0 if ok else 3)
    quota, warn = allocate(strata, a.holdout_size)
    rng = random.Random(a.round if a.seed is None else a.seed)
    picked: list[tuple[str, tuple]] = []
    for k in sorted(quota):
        ids = sorted(strata[k])
        rng.shuffle(ids)
        picked += [(cid, k) for cid in ids[: quota[k]]]
    picked.sort()
    out = a.out or (a.cases_dir / "holdout" / f"{a.round}.yaml")
    if out.exists() and not a.force:
        sys.exit(f"{out} 已存在：留出集每轮只抽一次，跑完即封存（调试覆盖加 --force）")
    out.parent.mkdir(parents=True, exist_ok=True)
    thin = any(w.startswith("HOLDOUT_THIN") for w in warn)
    lines = [f"# 留出集 round {a.round} —— 只记 id 与分层键；标签私有，不进任何提示词",
             f"round: {a.round}", f"seed: {a.round if a.seed is None else a.seed}", f"size: {len(picked)}",
             f"target_size: {a.holdout_size}", f"strata: {len([k for k in quota if quota[k] > 0])}",
             f"stratify: [{', '.join(dims)}]", f"merge_min: {a.merge_min}", f"median_observed_at: {median or 'null'}",
             f"excluded: {len(excl)}", f"pool: {len(pool)}", f"thin: {'true' if thin else 'false'}",
             "ids:"] + [f"  - {cid}" for cid, _ in picked] + ["strata_of:"] + \
            [f"  {cid}: [{', '.join(k)}]" for cid, k in picked]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if a.ledger and a.ledger.is_file():
        text = a.ledger.read_text(encoding="utf-8")
        row = f"| {a.round} | {out.name} | {len(picked)} | {len([k for k in quota if quota[k] > 0])} | {'HOLDOUT_THIN' if thin else 'no'} |\n"
        marker = "| round | file | size | strata | thin |"
        if marker in text:
            head, tail = text.split(marker, 1)
            sep_end = tail.find("\n", tail.find("\n") + 1) + 1  # 跳过表头下一行分隔线
            tail = tail[:sep_end] + row + tail[sep_end:]
            text = head + marker + tail
        else:
            text = text.rstrip("\n") + "\n\n## 留出集记录\n\n" + marker + "\n|-------|------|------|--------|------|\n" + row
        a.ledger.write_text(text, encoding="utf-8")
    print(f"{'⚠' if thin else '✓'} 留出集 round {a.round}：{len(picked)}/{a.holdout_size} 条，{len([k for k in quota if quota[k]>0])} 层"
          f"（池 {len(pool)}，排除 Dᵢ {len(excl)}，sim 不计）→ {out}")
    for w in warn:
        print("  " + w)


if __name__ == "__main__":
    main()
