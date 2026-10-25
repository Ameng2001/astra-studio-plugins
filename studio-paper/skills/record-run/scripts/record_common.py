#!/usr/bin/env python3
"""record-run 三个脚本共用的小工具：criteria.lock 校验、rq/criteria 读取、判据求值。

proposal/rq.yaml 形状（map 键控，兼容 paper_ws.read_yaml_lite）::

    rqs:
      RQ-1:
        question: "进化后本体能否通过更高层级的案例"
        hypotheses:
          H-1a: {statement: "T_tier3 通过率随窗口单调不降", direction: increase, metric: T_tier3}
          # 也接受 H-1a: "一句话"

proposal/criteria.yaml 形状（每个 RQ 一个 pass 与一个 fail；可选按 H 覆盖）::

    criteria:
      RQ-1:
        pass: {metric: T_tier3, op: ">=", value: 0.7}
        fail: {metric: T_tier3, op: "<", value: 0.5}
        failure: "文字说明什么算失败（propose-experiments 要求）"
        metrics: [T_tier1, T_tier2, T_tier3, T_tier4]   # 可选：该 RQ 允许入主表的全部指标
        agg: mean            # mean | median | min | max | last，多条 formal run 的合并方式，默认 mean（进化轮次建议 last）
      H-1a:                  # 可选：H 级判据；缺省继承所属 RQ 的判定
        pass: {metric: T_tier3, op: ">=", value: 0.75}
        fail: {metric: T_tier3, op: "<", value: 0.5}

可选 fail_to_measure_when（键控 map，任一命中 → 该 RQ / H 记「无法判定」，不记支持也不记证伪、不进 drift）::

      RQ-3:
        fail_to_measure_when:
          no_w_evid: {missing: W_evid}                 # 该 RQ 已有 formal run，但没有任何 W_evid 读数
          rq1_not_supported: {requires: RQ-1, verdict: 支持}   # 依赖的 RQ 判定不是「支持」
          underpowered: {metric: n_accept_min, op: "<", value: 30}   # 指标阈值（formal run 取 mean）
      RQ 级命中时其下全部假设一并记「无法判定」；H 级可另写自己的条件。只在该 RQ 有 formal run 时才求值。

ledger/dispositions.yaml（可选）：导师对 drift 的处置，键为 RQ 或 RQ/H::

    dispositions:
      RQ-1: {choice: 改 claim, by: 王老师, date: "2026-10-25", doc: proposal/advisor-drift-disposition-2026-10-25.md, followup: "thesis v3 待重签"}

proposal/criteria.lock：JSON，至少含 {"hash": "sha256:<criteria.yaml 文本的 sha256>"}。
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import read_yaml_lite, sha256_text, project_root  # noqa: E402

OPS = {
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}
AGGS = {"mean": statistics.fmean, "median": statistics.median, "min": min, "max": max,
        "last": lambda xs: xs[-1]}  # last = run_id 最大的那条（如进化最后一轮）
EVIDENCE_KINDS = {"main", "ablation", "rebuttal"}  # baseline / pilot 不参与判据


def rel(p: Path) -> str:
    """相对项目根的路径（台账里不写绝对路径，换机器可复核）。"""
    try:
        return str(Path(p).resolve().relative_to(project_root()))
    except ValueError:
        return str(p)


def lock_status(ws: Path) -> tuple[bool, str]:
    """(lock_ok, 说明)。lock 存在且 hash 等于当前 criteria.yaml 文本哈希才算一致。"""
    crit = ws / "proposal" / "criteria.yaml"
    lock = ws / "proposal" / "criteria.lock"
    if not crit.exists():
        return False, "proposal/criteria.yaml 不存在"
    if not lock.exists():
        return False, "proposal/criteria.lock 不存在（判据未冻结）"
    raw = lock.read_text(encoding="utf-8")
    try:
        h = (json.loads(raw) or {}).get("hash")
    except json.JSONDecodeError:
        m = re.search(r"hash\s*[:=]\s*\"?(sha256:[0-9a-f]{64})", raw)
        h = m.group(1) if m else None
    cur = sha256_text(crit.read_text(encoding="utf-8"))
    if h != cur:
        return False, f"criteria.lock 哈希 {h} ≠ 当前 criteria.yaml {cur}"
    return True, "criteria.lock 一致"


def load_rqs(ws: Path) -> dict:
    """{RQ-id: {"question": str, "hypotheses": {H-id: str}}}；文件缺失返回 {}。"""
    p = ws / "proposal" / "rq.yaml"
    if not p.exists():
        return {}
    data = read_yaml_lite(p) or {}
    rqs = data.get("rqs") or {}
    out = {}
    for rid, body in rqs.items():
        body = body if isinstance(body, dict) else {"question": str(body)}
        hyps = body.get("hypotheses") or {}
        if isinstance(hyps, list):  # 容忍 [H-1a, H-1b]
            hyps = {str(h): "" for h in hyps}
        out[str(rid)] = {"question": body.get("question", ""),
                         "hypotheses": {str(k): (v.get("statement", "") if isinstance(v, dict) else v) for k, v in hyps.items()}}
    return out


def load_criteria(ws: Path) -> dict:
    p = ws / "proposal" / "criteria.yaml"
    if not p.exists():
        return {}
    return (read_yaml_lite(p) or {}).get("criteria") or {}


def check(cond: dict | None, value: float) -> bool:
    if not isinstance(cond, dict) or "value" not in cond:
        return False
    op = str(cond.get("op", ">="))
    if op not in OPS:
        raise SystemExit(f"criteria.yaml 里的 op 非法: {op!r}；可选 {list(OPS)}")
    return OPS[op](value, float(cond["value"]))


def fmt_cond(cond: dict | None) -> str:
    if not cond:
        return "—"
    return f"{cond.get('metric')} {cond.get('op', '>=')} {cond.get('value')}"


def evaluate(crit: dict, runs: list[dict]) -> dict:
    """对一条判据（RQ 或 H 级）求值。runs 已按 rq/h 过滤、只含 formal。"""
    conds = [c for c in (crit.get("pass"), crit.get("fail")) if isinstance(c, dict)]
    metric = (conds[0].get("metric") if conds else None) or crit.get("metric")
    used = [r for r in sorted(runs, key=lambda r: r["run_id"]) if r.get("kind") in EVIDENCE_KINDS
            and (r.get("metric") or {}).get("name") == metric
            and isinstance((r.get("metric") or {}).get("value"), (int, float))]
    if not metric:
        return {"verdict": "待定", "why": "判据未写 metric", "used": [], "value": None}
    if not used:
        return {"verdict": "待定", "why": f"无 {metric} 的 formal run", "used": [], "value": None}
    agg = str(crit.get("agg", "mean"))
    v = AGGS.get(agg, statistics.fmean)([r["metric"]["value"] for r in used])
    if check(crit.get("pass"), v):
        verdict, why = "支持", f"{agg}={v:.4g} 满足 pass（{fmt_cond(crit.get('pass'))}）"
    elif check(crit.get("fail"), v):
        verdict, why = "证伪", f"{agg}={v:.4g} 落入 fail（{fmt_cond(crit.get('fail'))}）"
    else:
        verdict, why = "待定", f"{agg}={v:.4g} 介于 pass 与 fail 之间"
    return {"verdict": verdict, "why": why, "used": [r["run_id"] for r in used], "value": v}


def fail_to_measure(crit: dict, runs: list[dict], verdicts: dict) -> tuple[list[str], list[str]]:
    """criteria 的 fail_to_measure_when 命中项（文字说明, 依据 run_id）；runs 已按 rq/h 过滤、只含 formal。无 formal run 时不求值。"""
    conds = (crit or {}).get("fail_to_measure_when") or {}
    ev_runs = [r for r in runs if r.get("kind") in EVIDENCE_KINDS]
    if not isinstance(conds, dict) or not ev_runs:
        return [], []
    hits, used = [], []
    for name, c in conds.items():
        if not isinstance(c, dict):
            continue
        if "requires" in c:
            need, got = str(c.get("verdict", "支持")), verdicts.get(str(c["requires"]))
            if got is not None and got != need:
                hits.append(f"{name}：{c['requires']} 判定为「{got}」，本项须其为「{need}」")
        elif "missing" in c:
            m = str(c["missing"])
            if not any((r.get("metric") or {}).get("name") == m and isinstance((r.get("metric") or {}).get("value"), (int, float)) for r in ev_runs):
                hits.append(f"{name}：无 {m} 的 formal 读数")
        elif "metric" in c:
            rs = [r for r in ev_runs if (r.get("metric") or {}).get("name") == c["metric"]
                  and isinstance((r.get("metric") or {}).get("value"), (int, float))]
            if rs:
                v = statistics.fmean(r["metric"]["value"] for r in rs)
                if check(c, v):
                    hits.append(f"{name}：{c['metric']}={v:.4g}（{fmt_cond(c)}）")
                    used += [r["run_id"] for r in rs]
    return hits, used


def load_dispositions(ws: Path) -> dict:
    p = ws / "ledger" / "dispositions.yaml"
    if not p.exists():
        return {}
    d = (read_yaml_lite(p) or {}).get("dispositions") or {}
    return {str(k): v for k, v in d.items() if isinstance(v, dict)}
