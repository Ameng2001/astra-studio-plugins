#!/usr/bin/env python3
"""plot_figures.py <paper-id> [--style science,ieee,no-latex] [--format pdf,png] [--dpi 300]

从 results/figures/{RQ-n}.csv（build_tables.py 产出的长表）为 figure-plan 里每个「图」出可投稿的图。
原则：
  · 只画 csv 里的行（全是 formal run），不筛行、不平滑、不外推；
  · 样式优先 SciencePlots（`pip install scienceplots`），缺则回退到内置的干净样式，并在报告里标明；
  · 每张图写 caption 行到 results/figures/captions.md（含 n、seeds、区间口径、来源 run 数）；
  · 本脚本与样式参数的哈希进 results/figures/plot-manifest.json，coverage_check 把它纳入 figures_hash → 图表同源、同冻结。
图型按数据形状自动选：
  · 同一 metric 多个 run（有 round/seq 或按 ts 排序）→ 折线（轮次 / 顺序 vs value），有 ci 画阴影；
  · 多个 metric 各只一行 → 分组柱状（value ± ci）；
  · 其它 → 散点。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import workspace, now_iso, sha256_file  # noqa: E402

FALLBACK_RC = {
    "figure.figsize": (3.5, 2.6), "figure.dpi": 150, "font.size": 8, "axes.labelsize": 8,
    "axes.titlesize": 8, "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "axes.spines.top": False, "axes.spines.right": False, "lines.linewidth": 1.2,
    "lines.markersize": 3, "legend.frameon": False, "savefig.bbox": "tight", "font.family": "serif",
}


def load_rows(p: Path) -> list[dict]:
    with p.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fnum(x):
    try:
        return float(x) if x not in (None, "") else None
    except ValueError:
        return None


def round_of(row: dict) -> float | None:
    """x 轴：优先 csv 里的 round / seq / x 列；否则 None（按 ts 顺序编号）。"""
    for k in ("round", "seq", "x", "window"):
        v = fnum(row.get(k))
        if v is not None:
            return v
    m = re.search(r"round[=_ ](\d+)", row.get("note", "") or "")
    return float(m.group(1)) if m else None


def choose_style(requested: str):
    import matplotlib.pyplot as plt
    styles = [s.strip() for s in requested.split(",") if s.strip()]
    try:
        import scienceplots  # noqa: F401
        plt.style.use(styles)
        if "no-latex" in styles or not shutil.which("latex"):
            plt.rcParams["text.usetex"] = False  # 没有 latex 可执行文件时绝不调用 TeX
        return styles, "scienceplots"
    except Exception:
        plt.rcParams.update(FALLBACK_RC)
        return ["builtin-fallback"], "fallback"


def plot_group(fig_id: str, rows: list[dict], out_stem: Path, formats: list[str], dpi: int) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_metric: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_metric[r["metric"]].append(r)
    multi_run = any(len(v) > 1 for v in by_metric.values())
    fig, ax = plt.subplots()
    kind = ""
    if multi_run:
        kind = "line"
        for metric, rs in sorted(by_metric.items()):
            rs = sorted(rs, key=lambda r: (round_of(r) if round_of(r) is not None else float("inf"), r.get("ts", ""), r["run_id"]))
            xs = [round_of(r) if round_of(r) is not None else i + 1 for i, r in enumerate(rs)]
            ys = [fnum(r["value"]) for r in rs]
            ax.plot(xs, ys, marker="o", label=metric)
            lo = [fnum(r.get("ci_low")) for r in rs]; hi = [fnum(r.get("ci_high")) for r in rs]
            if all(v is not None for v in lo + hi):
                ax.fill_between(xs, lo, hi, alpha=0.2)
        ax.set_xlabel("round" if any(round_of(r) is not None for r in rows) else "run order")
        ax.set_ylabel("value")
        if len(by_metric) > 1:
            ax.legend()
    else:
        kind = "bar"
        metrics = sorted(by_metric)
        ys = [fnum(by_metric[m][0]["value"]) for m in metrics]
        lo = [fnum(by_metric[m][0].get("ci_low")) for m in metrics]
        hi = [fnum(by_metric[m][0].get("ci_high")) for m in metrics]
        err = None
        if all(v is not None for v in lo + hi):
            err = [[y - l for y, l in zip(ys, lo)], [h - y for y, h in zip(ys, hi)]]
        ax.bar(metrics, ys, yerr=err, capsize=2)
        ax.set_ylabel("value")
    ax.set_title(fig_id)
    written = []
    for fmt in formats:
        p = out_stem.with_suffix(f".{fmt}")
        fig.savefig(p, dpi=dpi, metadata={"Creator": "studio-paper plot_figures.py"} if fmt == "pdf" else None)
        written.append(p.name)
    plt.close(fig)
    ns = sorted({r.get("n") for r in rows if r.get("n")}); seeds = sorted({r.get("seeds") for r in rows if r.get("seeds")})
    has_ci = any(fnum(r.get("ci_low")) is not None for r in rows)
    return {"fig_id": fig_id, "kind": kind, "files": written, "runs": len(rows), "metrics": sorted(by_metric),
            "n": "/".join(ns) or "—", "seeds": "/".join(seeds) or "—", "ci": "95% CI" if has_ci else "无区间（单值）"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paper_id")
    ap.add_argument("--style", default="science,ieee,no-latex", help="SciencePlots 样式串，默认 science,ieee,no-latex（不依赖本机 LaTeX；装了 TeX 想要 LaTeX 字体就去掉 no-latex）")
    ap.add_argument("--format", default="pdf,png")
    ap.add_argument("--dpi", type=int, default=300)
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    fdir = ws / "results" / "figures"
    csvs = sorted(fdir.glob("RQ-*.csv"))
    if not csvs:
        print("没有作图数据 —— 先跑 build_tables.py，且 figure-plan 里要有「图」行"); return 1
    lock = ws / "results" / "freeze.lock"
    if lock.exists():
        print(f"⚠ 已有 {lock.name}：冻结后出图会改变 figures_hash，需 iteration + 1 后重冻结")
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        print("缺 matplotlib：pip install matplotlib scienceplots"); return 2
    styles, style_src = choose_style(a.style)
    formats = [f.strip() for f in a.format.split(",") if f.strip()]
    figs, caps = [], ["# 图 caption（plot_figures.py 生成；写作时原样引用，不得改数字）", ""]
    for p in csvs:
        rows = load_rows(p)
        by_fig: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by_fig[r["fig_id"]].append(r)
        for fig_id, frs in sorted(by_fig.items()):
            info = plot_group(fig_id, frs, fdir / f"{p.stem}-{fig_id}", formats, a.dpi)
            info.update({"rq": p.stem, "content": frs[0].get("fig_content", ""), "expected": frs[0].get("expected_shape", "")})
            figs.append(info)
            caps.append(f"**图 {fig_id}（{p.stem}）：{info['content']}。** 预期：{info['expected']}；n={info['n']}，seeds={info['seeds']}，"
                        f"区间={info['ci']}；仅含 formal 行（{info['runs']} 条，指标 {', '.join(info['metrics'])}），来源 {p.name}。")
            caps.append("")
    (fdir / "captions.md").write_text("\n".join(caps), encoding="utf-8")
    manifest = {"ts": now_iso(), "script_hash": sha256_file(Path(__file__)), "style": styles, "style_source": style_src,
                "formats": formats, "dpi": a.dpi, "figures": figs,
                "outputs": {f: sha256_file(fdir / f) for info in figs for f in info["files"]}}
    (fdir / "plot-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ 出图 {len(figs)} 张（{', '.join(formats)}）· 样式 {styles}（{style_src}）→ {fdir}")
    if style_src == "fallback":
        print("  ⚠ 未安装 scienceplots，用了内置回退样式：pip install scienceplots 后重跑可得期刊样式")
    print(f"  caption → {fdir / 'captions.md'}；manifest → {fdir / 'plot-manifest.json'}（进 figures_hash）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
