#!/usr/bin/env python3
"""Helpers shared by build_tables.py and coverage_check.py (stdlib only)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import read_yaml_lite  # noqa: E402

RQ_RE = re.compile(r"RQ-\d+")


def load_rqs(ws: Path) -> list[dict]:
    """proposal/rq.yaml → [{id, question, hypotheses:[...]}].

    Canonical shape (propose-experiments writes this):
        rqs:
          RQ-1:
            question: ...
            hypotheses:
              H-1a: {statement: ..., metric: ...}
    Tolerated list shape:
        rqs:
          - id: RQ-1
            question: ...
            hypotheses:
              - id: H-1a
                statement: ...
    Falls back to a regex scan of `id: RQ-n` lines when PyYAML is absent
    (read_yaml_lite does not parse block lists of maps).
    """
    p = ws / "proposal" / "rq.yaml"
    if not p.exists():
        raise SystemExit(f"缺 {p} —— 先跑 /studio-paper:propose-experiments")
    data = read_yaml_lite(p)
    rqs = data.get("rqs") if isinstance(data, dict) else None
    if isinstance(rqs, dict) and rqs:  # canonical shape: rqs: {RQ-1: {question, hypotheses: {H-1a: {...}}}}
        out = []
        for rid, body in rqs.items():
            body = body if isinstance(body, dict) else {"question": str(body)}
            hyps = body.get("hypotheses") or {}
            if isinstance(hyps, dict):
                hyps = [{"id": h, **(v if isinstance(v, dict) else {"statement": str(v)})} for h, v in hyps.items()]
            out.append({"id": str(rid), "question": body.get("question", ""), "hypotheses": hyps})
        return out
    if isinstance(rqs, list) and rqs and all(isinstance(x, dict) for x in rqs):  # tolerated list shape
        return [x for x in rqs if x.get("id")]
    text = p.read_text(encoding="utf-8")
    out, seen = [], set()
    for line in text.splitlines():
        m = re.match(r"^\s*-?\s*id:\s*[\"']?(RQ-\d+)", line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append({"id": m.group(1)})
            continue
        q = re.match(r"^\s*question:\s*(.+?)\s*$", line)
        if q and out and "question" not in out[-1]:
            out[-1]["question"] = q.group(1).strip("\"'")
    if not out:
        raise SystemExit(f"{p} 里找不到任何 RQ（期望 rqs: [{{id: RQ-1, ...}}]）")
    return out


def load_figure_plan(ws: Path) -> list[dict]:
    """proposal/figure-plan.md markdown table:
    | RQ | id | 类型 | 内容 | 预期形状 |
    | RQ-1 | T1 | 表 | 各层通过率随轮次 | 预期上升 |
    """
    p = ws / "proposal" / "figure-plan.md"
    if not p.exists():
        raise SystemExit(f"缺 {p} —— 图表规划在 propose-experiments 阶段写")
    items = []
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 5 or not RQ_RE.fullmatch(cells[0]):
            continue  # header / separator / malformed
        kind = cells[2]
        if kind not in ("表", "图"):
            raise SystemExit(f"figure-plan 类型只能是 表/图: {line}")
        items.append({"rq": cells[0], "id": cells[1], "kind": kind, "content": cells[3], "shape": cells[4]})
    return items


def run_rqs(run: dict) -> list[str]:
    rq = run.get("rq") or []
    return [rq] if isinstance(rq, str) else list(rq)


def seeds_of(run: dict):
    f = run.get("fairness") or {}
    return f.get("seeds") if isinstance(f, dict) else None


def fmt_ci(ci) -> str:
    if isinstance(ci, (list, tuple)) and len(ci) == 2:
        return f"[{ci[0]}, {ci[1]}]"
    return "—"


def n_range(runs: list[dict]) -> str:
    ns = sorted({int(r.get("n") or 0) for r in runs})
    if not ns:
        return "—"
    return str(ns[0]) if len(ns) == 1 else f"{ns[0]}–{ns[-1]}"


def seeds_range(runs: list[dict]) -> str:
    ss = sorted({s for s in (seeds_of(r) for r in runs) if isinstance(s, int)})
    if not ss:
        return "—"
    return str(ss[0]) if len(ss) == 1 else f"{ss[0]}–{ss[-1]}"
