#!/usr/bin/env python3
"""把一次实验 run 入账（ledger/runs.jsonl，只追加），然后重算假设状态。

用法:
  record_run.py <paper-id> <run-dir|metrics.json> --rq RQ-1[,RQ-2] --metric name=value[,ci_lo,ci_hi]
      --n N --contamination excluded|known_overlap|unknown
      --fairness "budget=..;search_space=..;seeds=..;hardware=.."
      [--kind main|baseline|ablation|pilot|rebuttal] [--h H-1a] [--varied a,b] [--seed S]
      [--status failed] [--note ..] [--config path] [--baseline NAME]
  record_run.py <paper-id> --void <run_id> --reason "..."      # 追加作废行并重算

自动采集：run_id ts git owner config_hash seed duration_s source。学生五项缺了照样入账，判 exploratory。

metrics.json（或 run-dir/metrics.json）可选字段：
  {"metric": {"name": "T_tier3", "value": 0.71, "ci": [0.64, 0.78]},  # 或 "metrics": {"T_tier3": 0.71, ...}
   "n": 60, "seed": 3, "duration_s": 1840}
--metric 只写名字（不带 =value）时，从 metrics.json 的 metrics[name] / 顶层 name 取值。

--kind baseline 读 proposal/baselines.md 中的表格行（列顺序固定，表头可任意）：
  | 方法 | 指标 | 原论文数值 | 出处 | ...
  | EvoOntology | T_tier3 | 0.62 | Li et al. 2025, Tab.3 |
按「指标」列等于 metric 名匹配（多行匹配时用 --baseline 指定方法名），偏差追加到 ledger/baseline-deviation.md。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import workspace, load_config, git_info, sha256_file, now_iso  # noqa: E402
from ledger_io import append_run, next_run_id, formal_blockers, void_run, KINDS, CONTAMINATION  # noqa: E402
from record_common import lock_status, rel  # noqa: E402
from rollup_hypotheses import rollup  # noqa: E402

DEV_TOL = 0.05  # 相对偏差超过 5% 标「需解释」；config.thresholds.baseline_deviation_tol 可覆盖


def csv(s: str | None) -> list[str]:
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def num(x):
    try:
        f = float(x)
        return int(f) if f.is_integer() and "." not in str(x) else f
    except (TypeError, ValueError):
        return x


def parse_fairness(s: str | None) -> dict:
    out = {}
    for part in (s or "").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = num(v.strip()) if k.strip() == "seeds" else v.strip()
    return out


def load_metrics(src: Path) -> tuple[dict, Path | None]:
    mj = src if src.is_file() else src / "metrics.json"
    if mj.is_file() and mj.suffix == ".json":
        try:
            return json.loads(mj.read_text(encoding="utf-8")), mj
        except json.JSONDecodeError as e:
            print(f"⚠️  {mj} 不是合法 JSON，忽略：{e}")
    return {}, None


def parse_metric(arg: str | None, mj: dict) -> dict | None:
    if not arg:
        m = mj.get("metric")
        if isinstance(m, dict) and "name" in m:
            return {"name": m["name"], "value": m.get("value"), "ci": m.get("ci")}
        return None
    name, _, rest = arg.partition("=")
    name = name.strip()
    if not rest:
        pool = mj.get("metrics") if isinstance(mj.get("metrics"), dict) else {}
        m = mj.get("metric") if isinstance(mj.get("metric"), dict) else {}
        if m.get("name") == name:
            return {"name": name, "value": m.get("value"), "ci": m.get("ci")}
        v = pool.get(name, mj.get(name))
        return {"name": name, "value": v, "ci": None} if v is not None else {"name": name, "value": None, "ci": None}
    parts = [p.strip() for p in rest.split(",")]
    val = num(parts[0])
    ci = [num(parts[1]), num(parts[2])] if len(parts) >= 3 else None
    return {"name": name, "value": val, "ci": ci}


def find_config(src: Path, explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit(f"--config 文件不存在: {p}")
        return p
    d = src if src.is_dir() else src.parent
    cands = sorted(x for x in d.glob("config.*") if x.is_file())
    return cands[0] if cands else None


def baseline_deviation(ws: Path, run: dict, cfg: dict, method: str | None) -> str:
    m = run.get("metric") or {}
    bl = ws / "proposal" / "baselines.md"
    if not bl.exists():
        return "⚠️  proposal/baselines.md 不存在，偏差表未写"
    hits = []
    for line in bl.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.strip().startswith("|") and len(cells) >= 3 and cells[1] == m.get("name"):
            if method and cells[0] != method:
                continue
            try:
                hits.append((cells[0], float(cells[2]), cells[3] if len(cells) > 3 else ""))
            except ValueError:
                continue
    if not hits:
        return f"⚠️  baselines.md 无指标 {m.get('name')}{'、方法 ' + method if method else ''} 的数值行，偏差表未写"
    note = ""
    if len(hits) > 1:
        note = f"（{len(hits)} 行匹配，取第一行 {hits[0][0]}；用 --baseline 指定）"
    name, orig, cite = hits[0]
    ours = m.get("value")
    if not isinstance(ours, (int, float)):
        return "⚠️  本 run 无数值 metric，偏差表未写"
    tol = float((cfg.get("thresholds") or {}).get("baseline_deviation_tol", DEV_TOL))
    dev = ours - orig
    rel = dev / orig if orig else float("inf")
    flag = "可接受" if abs(rel) <= tol else "需解释"
    out = ws / "ledger" / "baseline-deviation.md"
    if not out.exists():
        out.write_text(
            "# 基线复现偏差表\n\n> record_run.py --kind baseline 追加。|相对偏差| > 容差即「需解释」；解释写在 note 列，"
            "写不出可解释原因前不进入主实验（validity-checklist #5 / 基线复现闸门）。\n\n"
            "| run_id | 方法 | 指标 | 原论文 | 复现 | 偏差 | 相对偏差 | 容差 | 判定 | 出处 | n | seeds | note |\n"
            "|--------|------|------|--------|------|------|----------|------|------|------|---|-------|------|\n",
            encoding="utf-8")
    seeds = (run.get("fairness") or {}).get("seeds", "")
    with out.open("a", encoding="utf-8") as f:
        f.write(f"| {run['run_id']} | {name} | {m['name']} | {orig:g} | {ours:g} | {dev:+.4g} | {rel:+.1%} | ±{tol:.0%} | "
                f"{flag} | {cite} | {run.get('n')} | {seeds} | {run.get('note') or ''} |\n")
    return f"基线偏差：{name} {m['name']} 原 {orig:g} → 复现 {ours:g}（{rel:+.1%}，{flag}）{note}"


def main() -> None:
    ap = argparse.ArgumentParser(description="入账一次实验 run（只追加），并重算假设状态")
    ap.add_argument("paper_id")
    ap.add_argument("source", nargs="?", help="run 输出目录或 metrics.json")
    ap.add_argument("--rq", help="RQ-1[,RQ-2]")
    ap.add_argument("--h", dest="hypothesis", help="H-1a[,H-1b]（可选）")
    ap.add_argument("--metric", help="name=value[,ci_lo,ci_hi]，或只写 name 从 metrics.json 取值")
    ap.add_argument("--n", type=int)
    ap.add_argument("--contamination", choices=sorted(CONTAMINATION))
    ap.add_argument("--fairness", help='"budget=..;search_space=..;seeds=..;hardware=.."')
    ap.add_argument("--kind", default="main", choices=sorted(KINDS))
    ap.add_argument("--varied", help="本次相对对照变了哪些因素，逗号分隔；多于一个即 confounded")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--status", choices=["failed"], help="崩溃 / 未产出结果的 run 照样入账")
    ap.add_argument("--note", default="")
    ap.add_argument("--config", help="实验配置文件（算 config_hash）；缺省找 run-dir 下的 config.*")
    ap.add_argument("--baseline", help="--kind baseline 时在 baselines.md 中匹配的方法名")
    ap.add_argument("--void", metavar="RUN_ID", help="作废一个 run（追加 tombstone 行）")
    ap.add_argument("--reason", default="", help="--void 的理由（必填）")
    a = ap.parse_args()
    ws = workspace(a.paper_id)

    if a.void:
        t = void_run(ws, a.void, a.reason)
        res = rollup(a.paper_id)
        print(f"已作废 {t['run_id']}：{t['void_reason']}（原行保留）。RQ 覆盖 {len(res['covered'])}/{len(res['rqs'])}；论点偏离 {res['drift']} 条")
        return
    if not a.source:
        ap.error("需要 <run-dir|metrics.json>（或用 --void）")
    src = Path(a.source)
    if not src.exists():
        raise SystemExit(f"run 输出不存在: {src}")

    cfg = load_config(ws) if (ws / "config.yaml").exists() else {}
    mj, mj_path = load_metrics(src)
    g = git_info()
    cfg_file = find_config(src, a.config)
    varied = csv(a.varied)
    n = a.n if a.n is not None else (mj.get("n") if isinstance(mj.get("n"), int) else None)
    seed = a.seed if a.seed is not None else mj.get("seed")
    fairness = parse_fairness(a.fairness) or (mj.get("fairness") if isinstance(mj.get("fairness"), dict) else {})

    run = {
        "run_id": next_run_id(ws),
        "paper": a.paper_id,
        "kind": a.kind,
        "rq": csv(a.rq),
        "hypothesis": csv(a.hypothesis),
        "status": "failed" if a.status == "failed" else "exploratory",
        "void_reason": None,
        "owner": g["user"],
        "ts": now_iso(),
        "git": {"commit": g["commit"], "dirty": g["dirty"]},
        "config_hash": sha256_file(cfg_file) if cfg_file else None,
        "config_path": rel(cfg_file) if cfg_file else None,
        "seed": seed,
        "duration_s": mj.get("duration_s"),
        "metric": parse_metric(a.metric, mj),
        "n": n,
        "contamination": a.contamination or mj.get("contamination"),
        "varied": varied,
        "confounded": len(varied) > 1,
        "fairness": fairness,
        "source": "manual",
        "note": a.note,
    }
    m = run["metric"] or {}
    if m and m.get("value") is None and run["status"] != "failed":
        print(f"⚠️  metric {m.get('name')} 没有数值（--metric name=value 或 metrics.json 提供）")
    lock_ok, lock_msg = lock_status(ws)
    blockers = formal_blockers(run, cfg, lock_ok)
    if not lock_ok:
        blockers[0] = f"判据未冻结：{lock_msg}"
    if run["status"] != "failed":
        run["status"] = "formal" if not blockers else "exploratory"
    append_run(ws, run)

    extra = ""
    if a.kind == "baseline":
        extra = baseline_deviation(ws, run, cfg, a.baseline)
    res = rollup(a.paper_id)

    head = f"已入账 {run['run_id']}（{run['kind']}，{m.get('name', '无指标')}={m.get('value', '—')}，n={run['n']}）→ **{run['status']}**"
    if run["status"] == "formal":
        why = "。五项齐、判据已冻结、n 达标、未混杂。"
    else:
        shown = [b for b in blockers if b != "status=failed"]
        why = "。不能算 formal 的原因：" + "；".join(shown) + "。exploratory 不能事后升级，要正式就补齐后重跑。" if shown else "。"
    cov = f" 当前 formal 覆盖的 RQ：{', '.join(res['covered']) or '无'}（{len(res['covered'])}/{len(res['rqs'])}）；论点偏离 {res['drift']} 条" + ("，见 ledger/drift.md，请提示导师。" if res["drift"] else "。")
    print(head + why + cov)
    if run["confounded"]:
        print(f"⚠️  confounded：一次对比变了 {varied}，写作阶段不得丢掉该标记。")
    if mj_path:
        print(f"（读取了 {mj_path}）")
    if extra:
        print(extra)


if __name__ == "__main__":
    main()
