#!/usr/bin/env python3
"""回应表引用核对 —— respond-reviews 前置与第 4 步。

    check_refs.py <paper-id> rebuttal/responses.md

  - 文中每个 run_id（r-YYYY-MM-DD-NNNN）必须在台账 effective_runs 中，且 status == formal 或 kind == rebuttal
    （rebuttal 补跑的 run 允许 exploratory，但会告警；failed / void 一律不可引用）
  - 每个新表编号 T-R<n> 必须在 rebuttal/tables/ 下有对应文件（T-R<n>.md / .csv / .tex …）
  - 回应表「是否新 claim」列为 是 / yes 的行必须带 run_id（新 claim 必须有数据）
退出码：0 全部可核对 · 2 有缺失引用 · 1 文件错误。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import workspace  # noqa: E402
from ledger_io import effective_runs, read_runs  # noqa: E402
from rebuttal_table import col, read_table, resolve  # noqa: E402

RUN_RE = re.compile(r"r-\d{4}-\d{2}-\d{2}-\d{4}")
TR_RE = re.compile(r"\bT-R(\d+)\b")


def main() -> int:
    ap = argparse.ArgumentParser(description="回应表 run_id / 新表引用核对")
    ap.add_argument("paper_id")
    ap.add_argument("responses")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    path = resolve(ws, a.responses)
    if not path.is_file():
        print(f"回应表不存在: {path}", file=sys.stderr)
        return 1
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    eff = {r["run_id"]: r for r in effective_runs(ws)}
    voided = {r["run_id"] for r in read_runs(ws, include_void=True) if r.get("status") == "void"}
    errors, warns = [], []

    cited = list(dict.fromkeys(RUN_RE.findall(text)))
    for rid in cited:
        r = eff.get(rid)
        if r is None:
            errors.append(f"{rid}：{'已作废（void）' if rid in voided else '不在台账'} —— 先经 record-run 入账")
        elif r.get("status") == "failed":
            errors.append(f"{rid}：status=failed，不能作为证据引用")
        elif r.get("status") != "formal" and r.get("kind") != "rebuttal":
            errors.append(f"{rid}：{r.get('kind')}/{r.get('status')}，非 formal 且非 rebuttal 补跑")
        elif r.get("status") != "formal":
            warns.append(f"{rid}：rebuttal 补跑但是 {r.get('status')}（n 或判据不足），回应里须写明局限")

    tdir = ws / "rebuttal" / "tables"
    have = [p.name for p in tdir.iterdir()] if tdir.is_dir() else []
    tables = list(dict.fromkeys(TR_RE.findall(text)))
    for n in tables:
        if not any(re.match(rf"^T-R{n}(?!\d)", f) for f in have):
            errors.append(f"T-R{n}：rebuttal/tables/ 下没有对应文件")

    header, rows = read_table(text)
    nc = col(header, "是否新 claim", "新 claim", "new claim") if header else None
    rc = col(header, "run_id") if header else None
    idc = (col(header, "ID") or (header[0] if header else None))
    if nc:
        for r in rows:
            if r.get(nc, "").strip().lower() in ("是", "yes", "y", "true"):
                if not RUN_RE.search(r.get(rc, "") if rc else "") and not RUN_RE.search(" ".join(r.values())):
                    errors.append(f"{r.get(idc, '?')}：标为新 claim 但没有 run_id —— 新 claim 必须有数据")

    print(f"run_id 引用 {len(cited)} 个 · 新表 T-R {len(tables)} 个 · 回应表 {len(rows)} 行")
    for w in warns:
        print(f"告警：{w}")
    for e in errors:
        print(f"缺失：{e}")
    if errors:
        print(f"阻塞：{len(errors)} 处引用不可核对。")
        return 2
    print("全部引用可核对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
