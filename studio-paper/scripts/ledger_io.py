#!/usr/bin/env python3
"""Ledger I/O for studio-paper — runs.jsonl is append-only.

Schema: references/ledger-schema.md. Shared by record-run, freeze-results,
review-internally, check-submission and respond-reviews.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import now_iso, sha256_file  # noqa: E402

STUDENT_FIELDS = ["rq", "metric", "n", "contamination", "fairness"]
CONTAMINATION = {"excluded", "known_overlap", "unknown"}
KINDS = {"main", "baseline", "ablation", "pilot", "rebuttal"}
STATUSES = {"formal", "exploratory", "failed", "void"}


def ledger_path(ws: Path) -> Path:
    return ws / "ledger" / "runs.jsonl"


def read_runs(ws: Path, include_void: bool = False) -> list[dict]:
    p = ledger_path(ws)
    if not p.exists():
        return []
    runs = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            raise SystemExit(f"runs.jsonl 第 {i} 行不是合法 JSON: {e}")
        if r.get("status") == "void" and not include_void:
            continue
        runs.append(r)
    return runs


def next_run_id(ws: Path) -> str:
    today = now_iso()[:10]
    existing = {r["run_id"] for r in read_runs(ws, include_void=True) if r.get("run_id", "").startswith(f"r-{today}-")}
    return f"r-{today}-{len(existing) + 1:04d}"


def append_run(ws: Path, run: dict) -> dict:
    """Append one run. Never rewrites existing lines."""
    missing = [k for k in ("run_id", "kind", "status", "ts") if k not in run]
    if missing:
        raise SystemExit(f"run 缺必需字段 {missing}")
    if run["kind"] not in KINDS or run["status"] not in STATUSES:
        raise SystemExit(f"kind/status 非法: {run['kind']}/{run['status']}")
    p = ledger_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")
    return run


def void_run(ws: Path, run_id: str, reason: str) -> dict:
    """Voiding = appending a tombstone row; the original line stays."""
    if not reason.strip():
        raise SystemExit("作废必须写理由")
    orig = [r for r in read_runs(ws, include_void=True) if r.get("run_id") == run_id]
    if not orig:
        raise SystemExit(f"找不到 run {run_id}")
    tomb = {**orig[-1], "status": "void", "void_reason": reason, "ts": now_iso(), "supersedes": orig[-1].get("ts")}
    return append_run(ws, tomb)


def formal_blockers(run: dict, cfg: dict, lock_ok: bool, criteria: dict | None = None) -> list[str]:
    """Why a run cannot be formal. Empty list == formal.

    n_min 取 config.thresholds.n_min 与该 run 所属各 RQ 在 criteria.yaml 里的 n_min 的最大者（RQ 级判据更严时以 RQ 为准）。
    config.thresholds.formal_preconditions（列表，每项 {id, text, met}）里任一 met 不为 true 的前置条件也阻塞——
    这是协议级的「正式 run 前置」（如 real 池规模、读数由脚本计算），样本量再大也不能绕过；满足后把 met 改成 true。
    """
    b = []
    if not lock_ok:
        b.append("criteria.lock 缺失或与 criteria.yaml 哈希不一致")
    for k in STUDENT_FIELDS:
        v = run.get(k)
        if v in (None, "", [], {}):
            b.append(f"缺学生字段 {k}")
    th = cfg.get("thresholds") or {}
    n_min = int(th.get("n_min", 30))
    crit = (criteria or {}).get("criteria") or criteria or {}
    for rq in run.get("rq") or []:
        try:
            rq_min = int((crit.get(rq) or {}).get("n_min") or 0)
        except (TypeError, ValueError):
            rq_min = 0
        if rq_min > n_min:
            n_min = rq_min
    try:
        if int(run.get("n") or 0) < n_min:
            b.append(f"n={run.get('n')} < n_min={n_min}")
    except (TypeError, ValueError):
        b.append("n 不是整数")
    for pre in th.get("formal_preconditions") or []:
        if not isinstance(pre, dict):
            continue
        if pre.get("met") is not True:
            b.append(f"正式 run 前置未满足：{pre.get('id') or '?'}（{pre.get('text') or ''}）")
    if run.get("contamination") not in CONTAMINATION:
        b.append(f"contamination 须为 {sorted(CONTAMINATION)}")
    if run.get("confounded"):
        b.append("confounded=true（一次对比变了多个因素）")
    if run.get("status") == "failed":
        b.append("status=failed")
    return b


def ledger_hash(ws: Path) -> str | None:
    p = ledger_path(ws)
    return sha256_file(p) if p.exists() else None


def effective_runs(ws: Path) -> list[dict]:
    """Latest row per run_id, voids removed."""
    latest: dict[str, dict] = {}
    for r in read_runs(ws, include_void=True):
        latest[r["run_id"]] = r
    return [r for r in latest.values() if r.get("status") != "void"]


if __name__ == "__main__":
    import argparse
    from paper_ws import workspace
    ap = argparse.ArgumentParser(description="ledger_io: list / void runs")
    ap.add_argument("paper_id")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list"); ls.add_argument("--status", default=None)
    vd = sub.add_parser("void"); vd.add_argument("run_id"); vd.add_argument("--reason", required=True)
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    if a.cmd == "list":
        for r in effective_runs(ws):
            if a.status and r["status"] != a.status:
                continue
            m = r.get("metric") or {}
            print(f"{r['run_id']}  {r['status']:<11} {r['kind']:<8} rq={','.join(r.get('rq') or [])}  {m.get('name','')}={m.get('value','')}  n={r.get('n')}")
    else:
        print(json.dumps(void_run(ws, a.run_id, a.reason), ensure_ascii=False))
