#!/usr/bin/env python3
"""build_tables.py <paper-id> — 只从台账 formal 行生成每个 RQ 的结果表与作图数据。

产出（全部可重复生成，不手填数字）：
  results/tables/{RQ-n}.md     每个 RQ 一张表 + 脱离正文可读的 caption
  results/figures/{RQ-n}.csv   figure-plan 里有「图」的 RQ：长表格式作图输入
  results/figures/README.md    作图由 plot_figures.py 完成（SciencePlots），这里给长表数据
  results/exploratory.md       探索性结果单列，永不进主表
  results/build.json           生成时的 ledger_hash（coverage_check 用来判表是否过期）
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import load_config, load_status, now_iso, workspace  # noqa: E402
from ledger_io import effective_runs, formal_blockers, ledger_hash  # noqa: E402
from _freeze_common import (expected_and_verdict, fmt_ci, load_figure_plan, load_verdicts, load_rqs, n_range,  # noqa: E402
                            run_rqs, seeds_of, seeds_range)

COLS = ["run_id", "kind", "metric", "value", "ci", "n", "seeds", "commit", "owner"]


def row_cells(r: dict) -> list[str]:
    m = r.get("metric") or {}
    g = r.get("git") or {}
    commit = (g.get("commit") or "—") + ("+dirty" if g.get("dirty") else "")
    return [r["run_id"], r.get("kind", ""), str(m.get("name", "—")), str(m.get("value", "—")),
            fmt_ci(m.get("ci")), str(r.get("n", "—")), str(seeds_of(r) if seeds_of(r) is not None else "—"),
            commit, r.get("owner") or "—"]


def md_table(rows: list[list[str]], cols: list[str]) -> str:
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in rows]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="从台账 formal 行生成结果表与作图数据")
    ap.add_argument("paper_id")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    lock = ws / "results" / "freeze.lock"
    if lock.exists():
        it = load_status(ws).get("iteration", 1)
        if json.loads(lock.read_text(encoding="utf-8")).get("iteration") == it:
            raise SystemExit(f"第 {it} 轮结果已冻结（{lock}），不再重建表。冻结后改实验 = 新一轮："
                             "status.json iteration + 1 后重跑（旧 freeze.lock 与证据包保留在 git 历史）。")
    cfg = load_config(ws)
    rqs = load_rqs(ws)
    plan = load_figure_plan(ws)
    runs = sorted(effective_runs(ws), key=lambda r: (r.get("ts", ""), r["run_id"]))
    formal = [r for r in runs if r.get("status") == "formal"]
    explo = [r for r in runs if r.get("status") == "exploratory"]

    res = ws / "results"
    tdir, fdir = res / "tables", res / "figures"
    tdir.mkdir(parents=True, exist_ok=True)
    fdir.mkdir(parents=True, exist_ok=True)
    for old in list(tdir.glob("RQ-*.md")) + list(fdir.glob("RQ-*.csv")):
        old.unlink()  # 生成物：每次全量重建，避免残留旧表

    verdicts = load_verdicts(ws)
    if not verdicts:
        print("⚠ ledger/hypotheses.md 缺失或旧于台账：caption 的「台账判定」会写「未生成」—— 先跑 rollup_hypotheses.py")
    written_tables, written_figs = [], []
    for rq in rqs:
        rid = rq["id"]
        rr = [r for r in formal if rid in run_rqs(r)]
        tabs = [p for p in plan if p["rq"] == rid and p["kind"] == "表"]
        figs = [p for p in plan if p["rq"] == rid and p["kind"] == "图"]
        if not tabs and not figs:
            print(f"⚠ {rid}：figure-plan 没有该 RQ 的表/图 —— coverage_check 会阻塞")
        tab_ids = "/".join(p["id"] for p in tabs) or "（figure-plan 未规划）"
        content = "；".join(p["content"] for p in tabs) or (rq.get("question") or "")
        shape = "；".join(p["shape"] for p in tabs)
        caption = (f"表 {tab_ids}：{rid} {content}（{expected_and_verdict(shape, verdicts.get(rid))}）"
                   + f"；n={n_range(rr)}，seeds={seeds_range(rr)}，区间=95% CI；"
                   f"仅含 formal 行（{len(rr)} 条），来源 ledger/runs.jsonl。")
        body = ["<!-- 由 build_tables.py 从 ledger/runs.jsonl 生成，勿手改；改数据 = 改台账后重跑（生成时间见 results/build.json） -->",
                f"# {rid}", ""]
        if rq.get("question"):
            body += [f"> {rq['question']}", ""]
        body += [f"**{caption}**", ""]
        body += [md_table([row_cells(r) for r in rr], COLS) if rr else "_（无 formal 行 —— 该 RQ 尚无可写结果）_", ""]
        (tdir / f"{rid}.md").write_text("\n".join(body), encoding="utf-8")
        written_tables.append(f"{rid}.md")

        if figs:
            with (fdir / f"{rid}.csv").open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["rq", "fig_id", "fig_content", "expected_shape", "run_id", "kind", "metric",
                            "value", "ci_low", "ci_high", "n", "seed", "seeds", "ts"])
                for p in figs:
                    for r in rr:
                        m = r.get("metric") or {}
                        ci = m.get("ci") if isinstance(m.get("ci"), list) and len(m.get("ci")) == 2 else [None, None]
                        w.writerow([rid, p["id"], p["content"], p["shape"], r["run_id"], r.get("kind"),
                                    m.get("name"), m.get("value"), ci[0], ci[1], r.get("n"),
                                    r.get("seed"), seeds_of(r), r.get("ts")])
            written_figs.append(f"{rid}.csv")

    (fdir / "README.md").write_text(
        "# 作图数据\n\n`{RQ-n}.csv` 是长表格式作图输入（每行 = 一个 formal run × 一个规划图）；用同目录 `plot_figures.py` 出图，"
        "由 build_tables.py 从台账生成。\n\n**作图是写作者的事**：用任何工具读 CSV 出图，"
        "但不得在作图时过滤、挑选或改写行；需要不同的子集 = 改 figure-plan（propose-experiments --amend）。\n",
        encoding="utf-8")

    # 探索性结果单列
    lines = ["<!-- 由 build_tables.py 从 ledger/runs.jsonl 生成，勿手改 -->", "# 探索性结果（不进主表）", "",
             "以下 run 未达 formal 判定，只能在正文以「探索性」措辞出现，不得支撑主 claim。", ""]
    if explo:
        rows = []
        for r in explo:
            why = [b for b in formal_blockers(r, cfg, True) if not b.startswith("criteria.lock")]
            rows.append(row_cells(r) + [", ".join(run_rqs(r)) or "—", "；".join(why) or "登记为 exploratory"])
        lines.append(md_table(rows, COLS + ["rq", "为何不是 formal"]))
    else:
        lines.append("_（无）_")
    (res / "exploratory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (res / "build.json").write_text(json.dumps({
        "built_at": now_iso(), "ledger_hash": ledger_hash(ws),
        "tables": written_tables, "figures": written_figs,
        "formal": len(formal), "exploratory": len(explo)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"✓ 表 {len(written_tables)} 张 → {tdir}")
    print(f"✓ 作图数据 {len(written_figs)} 份 → {fdir}（作图由写作者完成）")
    print(f"✓ 探索性 {len(explo)} 条 → {res / 'exploratory.md'}")
    print(f"下一步：python3 {Path(__file__).with_name('coverage_check.py')} {a.paper_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
