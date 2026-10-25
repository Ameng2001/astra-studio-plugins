#!/usr/bin/env python3
"""确定性重算假设状态、RQ 覆盖矩阵与论点偏离。

用法: rollup_hypotheses.py <paper-id>

读: ledger/runs.jsonl（effective_runs + 作废行）、proposal/rq.yaml、proposal/criteria.yaml、criteria.lock
写: ledger/hypotheses.md、ledger/drift.md；若 status.json 存在则更新 counts。
永远 exit 0；打印偏离条数。rq.yaml / criteria.yaml 的形状见 record_common.py 文件头。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import workspace, load_config, load_status, save_status, now_iso  # noqa: E402
from ledger_io import effective_runs, read_runs, formal_blockers  # noqa: E402
from record_common import lock_status, load_rqs, load_criteria, evaluate, fmt_cond, fail_to_measure, load_dispositions  # noqa: E402

DRIFT_OPTIONS = (
    "- [ ] **改 claim**：收窄论点句，使其与该证伪结果相容（回 position-paper，需导师重签 thesis）\n"
    "- [ ] **换实验**：判据本身合理但实验设计有缺陷 → propose-experiments --amend 后开新一轮（旧结果保留）\n"
    "- [ ] **弃 RQ**：该 RQ 不再支撑论点 → propose-experiments --amend 删除，并在 changes.md 留痕\n"
)


def _runs_for(runs: list[dict], rq: str, h: str | None = None) -> list[dict]:
    out = []
    for r in runs:
        if rq not in (r.get("rq") or []):
            continue
        hs = r.get("hypothesis") or []
        if h and hs and h not in hs:
            continue
        out.append(r)
    return out


def rollup(paper_id: str) -> dict:
    ws = workspace(paper_id)
    cfg = load_config(ws) if (ws / "config.yaml").exists() else {}
    lock_ok, lock_msg = lock_status(ws)
    rqs = load_rqs(ws)
    crits = load_criteria(ws)
    runs = sorted(effective_runs(ws), key=lambda r: r["run_id"])
    formal = [r for r in runs if r["status"] == "formal"]

    # 作废：每个 run_id 的最后一行是 void
    latest: dict[str, dict] = {}
    for r in read_runs(ws, include_void=True):
        latest[r["run_id"]] = r
    voided = sorted((r for r in latest.values() if r.get("status") == "void"), key=lambda r: r["run_id"])

    rq_ids = list(rqs) or sorted({q for r in runs for q in (r.get("rq") or [])})
    disp = load_dispositions(ws)
    # 第一遍：阈值判定（fail_to_measure_when 的 requires 依赖这些 RQ 级判定）
    base = {rq: (evaluate(crits[rq], _runs_for(formal, rq)) if crits.get(rq) else
                 {"verdict": "待定", "why": "criteria.yaml 无此 RQ 判据", "used": [], "value": None}) for rq in rq_ids}
    verdicts = {rq: ev["verdict"] for rq, ev in base.items()}

    def ftm(ev, res):
        hits, used = res
        if not hits:
            return ev
        return {**ev, "verdict": "无法判定", "why": "fail_to_measure：" + "；".join(hits) + f"（阈值比对：{ev['why']}）",
                "used": sorted(set(ev["used"]) | set(used))}

    def cat(a, b):
        return a[0] + b[0], a[1] + b[1]

    results, drifts = [], []
    for rq in rq_ids:
        c = crits.get(rq) or {}
        rq_hits = fail_to_measure(c, _runs_for(formal, rq), verdicts)
        ev = ftm(base[rq], rq_hits)
        results.append((rq, None, c, ev))
        if ev["verdict"] == "证伪":
            drifts.append((rq, None, c, ev))
        for h in (rqs.get(rq) or {}).get("hypotheses", {}):
            hc = crits.get(h)
            if hc:
                hev = evaluate(hc, _runs_for(formal, rq, h))
                hev = ftm(hev, cat(rq_hits, fail_to_measure(hc, _runs_for(formal, rq, h), verdicts)))
            else:
                hev = {**ev, "why": f"继承 {rq}：{ev['why']}"}
            results.append((rq, h, hc or c, hev))
            if hc and hev["verdict"] == "证伪":
                drifts.append((rq, h, hc, hev))
    open_drifts = [d for d in drifts if (f"{d[0]}/{d[1]}" if d[1] else d[0]) not in disp]

    # 覆盖矩阵
    known = set(rq_ids)
    rows = {rq: {"formal": 0, "exploratory": 0, "failed": 0} for rq in rq_ids}
    for r in runs:
        targets = [q for q in (r.get("rq") or []) if q in known] or (
            ["（未登记 RQ）"] if r.get("rq") else ["（无 RQ）"])
        for q in targets:
            rows.setdefault(q, {"formal": 0, "exploratory": 0, "failed": 0})
            rows[q][r["status"]] = rows[q].get(r["status"], 0) + 1

    L = [f"# 假设状态 — {paper_id}", "",
         f"> rollup_hypotheses.py 自动生成，勿手改。生成于 {now_iso()}；有效 run {len(runs)} 条（formal {len(formal)}），作废 {len(voided)} 条。",
         f"> 判据冻结：{'✅ ' if lock_ok else '⚠️ '}{lock_msg}。只有 formal 行（kind ∈ main/ablation/rebuttal）参与判定。", "",
         "## RQ / 假设判定", "",
         "| RQ | H | 判定 | pass | fail | 依据 | formal runs |",
         "|----|---|------|------|------|------|-------------|"]
    for rq, h, c, ev in results:
        L.append(f"| {rq} | {h or '—'} | **{ev['verdict']}** | {fmt_cond(c.get('pass'))} | {fmt_cond(c.get('fail'))} | {ev['why']} | {', '.join(ev['used']) or '—'} |")
    L += ["", "## 覆盖矩阵", "", "| RQ | formal | exploratory | failed | 已覆盖 |", "|----|--------|-------------|--------|--------|"]
    for q, cnt in rows.items():
        L.append(f"| {q} | {cnt['formal']} | {cnt['exploratory']} | {cnt['failed']} | {'✅' if cnt['formal'] else '❌'} |")
    L += ["", "## 被排除的 run（不进判定，须在论文中披露）", "", "### 作废（void）", ""]
    L += [f"- `{r['run_id']}` {r.get('kind')} {(r.get('metric') or {}).get('name', '')} — 理由：{r.get('void_reason')}" for r in voided] or ["- 无"]
    L += ["", "### 探索性（exploratory）", ""]
    expl = [r for r in runs if r["status"] == "exploratory"]
    for r in expl:
        why = [b for b in formal_blockers(r, cfg, True) if b != "status=failed"] or ["入账时 criteria.lock 缺失或与 criteria.yaml 不一致"]
        m = r.get("metric") or {}
        L.append(f"- `{r['run_id']}` {r.get('kind')} rq={','.join(r.get('rq') or []) or '—'} {m.get('name', '')}={m.get('value', '')} — {'；'.join(why)}")
    if not expl:
        L.append("- 无")
    L += ["", "### 失败（failed）", ""]
    L += [f"- `{r['run_id']}` {r.get('kind')} rq={','.join(r.get('rq') or []) or '—'} — {r.get('note') or '（无备注）'}"
          for r in runs if r["status"] == "failed"] or ["- 无"]
    (ws / "ledger").mkdir(exist_ok=True)
    (ws / "ledger" / "hypotheses.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    D = [f"# 论点偏离 — {paper_id}", "", f"> rollup_hypotheses.py 自动生成于 {now_iso()}。formal 证据落入 fail 判据即记偏离；不阻塞入账，但 freeze-results 要求偏离已处置。"
         "处置记录在 `ledger/dispositions.yaml`；fail_to_measure_when 命中的项记「无法判定」，不进本文件。", ""]
    if not open_drifts:
        D.append("无偏离（未处置 0 条）" + (f"；已处置 {len(drifts)} 条见下" if drifts else ""))
    thesis = ws / "positioning" / "thesis.md"
    for rq, h, c, ev in open_drifts:
        q = (rqs.get(rq) or {}).get("question", "")
        label = f"{rq}/{h}" if h else rq
        D += [f"## 论点偏离：{label}", "",
              f"- RQ：{q or '（rq.yaml 无描述）'}" + (f"\n- 假设：{rqs[rq]['hypotheses'].get(h, '')}" if h else ""),
              f"- 判据：fail = {fmt_cond(c.get('fail'))}；{ev['why']}",
              f"- 证据 run：{', '.join(ev['used'])}",
              f"- 对照论点句：`{thesis.relative_to(ws)}`" + ("" if thesis.exists() else "（尚不存在）"),
              "", "处置（导师选一项并签字记录）：", "", DRIFT_OPTIONS]
    done = [d for d in drifts if d not in open_drifts]
    if done:
        D += ["", "## 已处置", ""]
        for rq, h, c, ev in done:
            label = f"{rq}/{h}" if h else rq
            x = disp[label]
            D += [f"- **{label}**：{x.get('choice', '—')}（{x.get('by', '—')}，{x.get('date', '—')}，依据 `{x.get('doc', '—')}`）；证据 run {', '.join(ev['used'])}"
                  + (f"；落实：{x['followup']}" if x.get("followup") else "")]
    stale = [k for k in disp if k not in {(f"{d[0]}/{d[1]}" if d[1] else d[0]) for d in drifts}]
    if stale:
        D += ["", "## 处置记录里已不再是偏离的项", ""] + [f"- **{k}**：{disp[k].get('choice', '—')}（依据 `{disp[k].get('doc', '—')}`）——当前判定已不构成偏离" for k in stale]
    (ws / "ledger" / "drift.md").write_text("\n".join(D) + "\n", encoding="utf-8")

    if (ws / "status.json").exists():
        st = load_status(ws)
        st.setdefault("counts", {}).update({
            "runs_formal": len(formal),
            "runs_exploratory": sum(1 for r in runs if r["status"] == "exploratory"),
            "rqs": len(rq_ids),
            "rqs_covered": sum(1 for q in rq_ids if rows[q]["formal"]),
        })
        save_status(ws, st)
    covered = [q for q in rq_ids if rows[q]["formal"]]
    return {"drift": len(open_drifts), "drift_disposed": len(drifts) - len(open_drifts), "covered": covered, "rqs": rq_ids, "results": results}


def main() -> None:
    ap = argparse.ArgumentParser(description="重算 ledger/hypotheses.md 与 ledger/drift.md")
    ap.add_argument("paper_id")
    a = ap.parse_args()
    try:
        res = rollup(a.paper_id)
    except SystemExit as e:  # 永远 exit 0：rollup 失败不影响已入账的行
        print(f"rollup 未完成：{e}")
        return
    print(f"rollup 完成：RQ 覆盖 {len(res['covered'])}/{len(res['rqs'])}；论点偏离 {res['drift']} 条未处置、{res['drift_disposed']} 条已处置"
          + ("（见 ledger/drift.md，请提示导师）" if res["drift"] else ""))


if __name__ == "__main__":
    main()
