#!/usr/bin/env python3
"""coverage_check.py <paper-id> [--freeze --by <签字人>]

检查（任一不过 = 阻塞，exit 2）：
  · rq.yaml 每个 RQ ≥ 1 条 formal 行，且 figure-plan 为它规划了表/图
  · formal 行 n ≥ thresholds.n_min；main/ablation 的 fairness.seeds ≥ thresholds.seeds_min
  · formal 行未标 confounded
  · 同一 RQ 的 formal 行共用同一个 metric 名（否则疑似挑窗/挑指标）
  · 每条 void 行都有 void_reason
  · ledger/drift.md 写明「无偏离」
  · results/tables 由当前台账生成（build.json 的 ledger_hash 与现台账一致）
产出 results/coverage.md。

--freeze --by X（仅在无阻塞时）：要求 gates.criteria 已签字 → 写 results/freeze.lock →
gates.results=approved（签字人 X，hash=tables_hash）→ 打包 results/evidence-pack/。
--by 必须是导师本人在审批检查点给出的名字，插件不得代签。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import (gate_require, gate_set, load_config, load_status, now_iso,  # noqa: E402
                      save_status, sha256_text, workspace, STAGES)
from ledger_io import effective_runs, formal_blockers, ledger_hash, read_runs  # noqa: E402
from _freeze_common import load_figure_plan, load_rqs, n_range, run_rqs, seeds_of  # noqa: E402

SEED_KINDS = {"main", "ablation"}


def tables_hash(ws: Path) -> str:
    files = sorted((ws / "results" / "tables").glob("*.md"))
    return sha256_text("".join(f"## {p.name}\n" + p.read_text(encoding="utf-8") for p in files))


def figures_hash(ws: Path) -> str:
    """作图数据 csv + 出图产物（pdf/png，经 plot-manifest.json 的输出哈希）+ 绘图脚本与样式 → 图表同源、同冻结。"""
    fdir = ws / "results" / "figures"
    files = sorted(fdir.glob("*.csv"))
    text = "".join(f"## {p.name}\n" + p.read_text(encoding="utf-8") for p in files)
    man = fdir / "plot-manifest.json"
    if man.exists():
        m = json.loads(man.read_text(encoding="utf-8"))
        text += "## plot\n" + json.dumps({"script_hash": m.get("script_hash"), "style": m.get("style"), "dpi": m.get("dpi"),
                                           "outputs": m.get("outputs")}, sort_keys=True)
    return sha256_text(text)


def check(ws: Path):
    cfg = load_config(ws)
    th = cfg.get("thresholds") or {}
    n_min, seeds_min = int(th.get("n_min", 30)), int(th.get("seeds_min", 3))
    rqs = load_rqs(ws)
    plan = load_figure_plan(ws)
    runs = effective_runs(ws)
    blockers: list[str] = []
    for rel in ("positioning/thesis.md", "positioning/nearest-diff.md"):
        if not (ws / rel).exists():
            blockers.append(f"缺 {rel} —— 论点句 / 近邻差异表是证据包的必需件（先跑 /studio-paper:position-paper）")
    warnings: list[str] = []
    per_rq = []

    for rq in rqs:
        rid = rq["id"]
        mine = [r for r in runs if rid in run_rqs(r)]
        formal = [r for r in mine if r.get("status") == "formal"]
        items = [p for p in plan if p["rq"] == rid]
        if not formal:
            blockers.append(f"{rid}：没有 formal 行（有 RQ 无表）")
        if not items:
            blockers.append(f"{rid}：figure-plan 未规划表/图")
        metrics = sorted({str((r.get("metric") or {}).get("name")) for r in formal})
        allowed = declared_metrics(ws, rid, [h.get("id") for h in (rq.get("hypotheses") or []) if isinstance(h, dict)])
        if allowed:
            stray = [m for m in metrics if m not in allowed]
            if stray:
                blockers.append(f"{rid}：formal 行用了预登记之外的指标 {stray}（criteria.yaml 声明的是 {sorted(allowed)}）"
                                "—— 评测协议不一致，疑似挑窗/挑指标；作废或改判据开新一轮")
        elif len(metrics) > 1:
            warnings.append(f"{rid}：criteria.yaml 未声明指标，formal 行却有多个 {metrics} —— 请确认不是挑指标")
        per_rq.append({"rq": rid, "items": items, "formal": formal,
                       "exploratory": [r for r in mine if r.get("status") == "exploratory"],
                       "failed": [r for r in mine if r.get("status") == "failed"], "metrics": metrics})

    known = {rq["id"] for rq in rqs}
    for r in runs:
        orphan = [x for x in run_rqs(r) if x not in known]
        if orphan and r.get("status") == "formal":
            warnings.append(f"{r['run_id']} 指向 rq.yaml 中不存在的 {orphan}")
        if r.get("status") != "formal":
            continue
        if int(r.get("n") or 0) < n_min:
            blockers.append(f"{r['run_id']}：formal 但 n={r.get('n')} < n_min={n_min}（小样本混入主表）")
        if r.get("kind") in SEED_KINDS:
            s = seeds_of(r)
            if not isinstance(s, int) or s < seeds_min:
                blockers.append(f"{r['run_id']}：{r.get('kind')} 的 fairness.seeds={s!r} < seeds_min={seeds_min}")
        if r.get("confounded"):
            blockers.append(f"{r['run_id']}：formal 但 confounded=true（一次对比变了多个因素）")
        if (r.get("git") or {}).get("dirty"):
            warnings.append(f"{r['run_id']}：git dirty —— 结果对应的代码不可完全复现")

    # 只看每个 run_id 的最新一行：台账只追加，补一条带理由的 void 行即可覆盖早先缺理由的那条
    latest: dict[str, dict] = {}
    for r in read_runs(ws, include_void=True):
        latest[r["run_id"]] = r
    voids = [r for r in latest.values() if r.get("status") == "void"]
    for r in voids:
        if not str(r.get("void_reason") or "").strip():
            blockers.append(f"{r['run_id']}：void 行缺 void_reason")

    drift = ws / "ledger" / "drift.md"
    if not drift.exists():
        blockers.append("ledger/drift.md 不存在 —— 需 record-run 写出并确认「无偏离」")
    elif "无偏离" not in drift.read_text(encoding="utf-8"):
        blockers.append("ledger/drift.md 有未处置偏离（未写明「无偏离」）—— 先回流 position-paper / propose-experiments --amend")

    build = ws / "results" / "build.json"
    if not build.exists():
        blockers.append("results/tables 尚未生成 —— 先跑 build_tables.py")
    else:
        b = json.loads(build.read_text(encoding="utf-8"))
        if b.get("ledger_hash") != ledger_hash(ws):
            blockers.append("结果表与当前台账不一致（台账在 build 之后变过）—— 重跑 build_tables.py")

    excluded = []
    for r in runs:
        if r.get("status") == "exploratory":
            why = [x for x in formal_blockers(r, cfg, True) if not x.startswith("criteria.lock")]
            excluded.append((r, "exploratory", "；".join(why) or "登记为 exploratory"))
        elif r.get("status") == "failed":
            excluded.append((r, "failed", r.get("note") or "运行失败"))
    for r in voids:
        excluded.append((r, "void", r.get("void_reason") or "（缺理由）"))
    return per_rq, excluded, blockers, warnings, {"n_min": n_min, "seeds_min": seeds_min}


def write_coverage(ws: Path, per_rq, excluded, blockers, warnings, th) -> Path:
    L = [f"<!-- 由 coverage_check.py 生成于 {now_iso()} -->", "# 结果覆盖检查", "",
         f"阈值：n_min={th['n_min']}，seeds_min={th['seeds_min']}；台账 {ledger_hash(ws)}", "",
         "## RQ → 表号", "", "| RQ | 表/图 | 指标 | formal | exploratory | failed | n |", "|---|---|---|---|---|---|---|"]
    for x in per_rq:
        ids = ", ".join(f"{p['kind']} {p['id']}" for p in x["items"]) or "—"
        L.append(f"| {x['rq']} | {ids} | {', '.join(x['metrics']) or '—'} | {len(x['formal'])} | "
                 f"{len(x['exploratory'])} | {len(x['failed'])} | {n_range(x['formal'])} |")
    L += ["", "## 被排除的 run（披露）", ""]
    if excluded:
        L += ["| run_id | 状态 | rq | 理由 |", "|---|---|---|---|"]
        L += [f"| {r['run_id']} | {st} | {', '.join(run_rqs(r)) or '—'} | {why} |" for r, st, why in excluded]
        L += ["", "探索性结果明细见 `results/exploratory.md`。"]
    else:
        L.append("_（无）_")
    L += ["", "## 阻塞项", ""] + ([f"- ❌ {b}" for b in blockers] or ["- 无"])
    L += ["", "## 提示", ""] + ([f"- ⚠ {w}" for w in warnings] or ["- 无"])
    p = ws / "results" / "coverage.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p


def build_evidence_pack(ws: Path, per_rq) -> Path:
    pack = ws / "results" / "evidence-pack"
    if pack.exists():
        shutil.rmtree(pack)
    pack.mkdir(parents=True)
    for rel in ["positioning/thesis.md", "positioning/nearest-diff.md", "proposal/rq.yaml", "results/coverage.md",
                "results/exploratory.md", "results/freeze.lock"]:
        src = ws / rel
        if src.exists():
            shutil.copy2(src, pack / src.name)
        else:
            print(f"⚠ 证据包缺 {rel}")
    for d in ["tables", "figures"]:
        shutil.copytree(ws / "results" / d, pack / d)
    L = ["# claim 登记表骨架", "", "每个 RQ 一行；claim 由写作者填，强度不得超过表中证据（n / 区间 / 判据）。"
         "review-internally 会把每条量化 claim 回指这里的表号与 run_id。", "",
         "| RQ | claim（待填） | 表号 | run_ids | n | 判定 |", "|---|---|---|---|---|---|"]
    for x in per_rq:
        tabs = "/".join(p["id"] for p in x["items"]) or "—"
        rids = ", ".join(r["run_id"] for r in x["formal"]) or "—"
        L.append(f"| {x['rq']} | 〈待填〉 | {tabs} | {rids} | {n_range(x['formal'])} | 待填 |")
    (pack / "claims-skeleton.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return pack


def declared_metrics(ws, rid: str, h_ids) -> set:
    """Metric names criteria.yaml pre-registers for this RQ (RQ-level pass/fail + its H-level ones)."""
    p = ws / "proposal" / "criteria.yaml"
    if not p.exists():
        return set()
    from paper_ws import read_yaml_lite
    crit = (read_yaml_lite(p) or {}).get("criteria") or {}
    names = set()
    num = rid.split("-", 1)[1] if "-" in rid else rid
    h_keys = [k for k in crit if str(k).startswith(f"H-{num}") and str(k)[len(f"H-{num}"):].isalpha()]  # H-1a / H-1b 归 RQ-1
    for key in dict.fromkeys([rid, *(h_ids or []), *h_keys]):
        c = crit.get(key) or {}
        ftm = c.get("fail_to_measure_when") if isinstance(c.get("fail_to_measure_when"), dict) else {}
        for cond in ftm.values():   # 机读的功效不足条件引用的量也是预登记的（criteria v5 机读化补注）
            if isinstance(cond, dict):
                for k in ("metric", "missing"):
                    if cond.get(k):
                        names.add(str(cond[k]))
        for part in ("pass", "fail"):
            m = (c.get(part) or {}).get("metric") if isinstance(c.get(part), dict) else None
            if m:
                names.add(str(m))
        if c.get("metric"):
            names.add(str(c["metric"]))
        for m in (c.get("metrics") or []) if isinstance(c.get("metrics"), list) else []:
            names.add(str(m))
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description="结果覆盖检查；--freeze 在导师签字后冻结并打包证据包")
    ap.add_argument("paper_id")
    ap.add_argument("--freeze", action="store_true", help="无阻塞时写 freeze.lock 并打包 evidence-pack")
    ap.add_argument("--by", default="", help="签字导师（--freeze 必填，须是导师本人给出的名字）")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    per_rq, excluded, blockers, warnings, th = check(ws)
    cov = write_coverage(ws, per_rq, excluded, blockers, warnings, th)

    for x in per_rq:
        print(f"{x['rq']}: formal={len(x['formal'])} exploratory={len(x['exploratory'])} failed={len(x['failed'])} "
              f"表={'/'.join(p['id'] for p in x['items']) or '—'}")
    for w in warnings:
        print(f"⚠ {w}")
    print(f"→ {cov}")
    if blockers:
        print(f"\n❌ {len(blockers)} 个阻塞项：")
        for b in blockers:
            print(f"  - {b}")
        if a.freeze:
            print("未冻结：先清掉阻塞项。")
        return 2
    print("✓ 无阻塞项")
    if not a.freeze:
        print("下一步：research-advisor 预审 → 导师审批检查点 → 签字后 --freeze --by <导师>")
        return 0

    if not a.by.strip():
        raise SystemExit("--freeze 必须带 --by <导师>：签字是人的动作，插件不得代签")
    gate_require(ws, "criteria")
    st = load_status(ws)
    it = st.get("iteration", 1)
    lock = ws / "results" / "freeze.lock"
    if lock.exists():
        old = json.loads(lock.read_text(encoding="utf-8"))
        if old.get("iteration") == it:
            raise SystemExit(f"第 {it} 轮已冻结（{old.get('ts')} by {old.get('by')}）。冻结后改实验 = 新一轮："
                             "status.json iteration + 1 后重跑。")
    th_hash = tables_hash(ws)
    rec = {"tables_hash": th_hash, "figures_hash": figures_hash(ws), "ledger_hash": ledger_hash(ws),
           "ts": now_iso(), "by": a.by, "iteration": it}
    lock.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate_set(ws, "results", "approved", a.by, th_hash, notes=f"freeze iteration {it}")
    st = load_status(ws)
    if STAGES.index(st.get("stage", "positioning")) < STAGES.index("writing"):
        st["stage"] = "writing"
        save_status(ws, st)
    pack = build_evidence_pack(ws, per_rq)
    print(f"🔒 已冻结 → {lock}\n   tables_hash={th_hash}\n   gates.results=approved by {a.by}")
    print(f"📦 证据包 → {pack}")
    print("下一步：/studio-docs:generate（academic-paper blueprint，输入 = evidence-pack）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
