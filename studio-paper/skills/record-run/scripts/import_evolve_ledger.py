#!/usr/bin/env python3
"""把 studio-evolve 的 growth-ledger.md 批量导入 runs.jsonl，然后重算假设状态。

用法:
  import_evolve_ledger.py <paper-id> <growth-ledger.md> --map "T=RQ-1,S=RQ-2,W=RQ-3,C=RQ-4"
      --contamination excluded [--n-from window|none] [--n N] [--include-baseline]
      [--fairness "budget=..;..."] [--dry-run]

每轮（round）拆成：T_tier1..T_tier4 各一行（轴 T）、S 一行（min(S_consist, S_holdout, S_stable)，
note 写出哪个分量最小）、W_evid 一行（轴 W）、C_tok 一行（轴 C）。空格或「—」的读数不入账。
kind=main，source="studio-evolve:growth-ledger"，source_ref={file, round}；
(source, source_ref.file, round, metric) 已存在则跳过（幂等）。--map 缺省读 config.yaml 的 ledger.evolve_map。
fairness 缺省读 config.yaml 的 ledger.evolve_fairness，再缺省 {"budget": "same harness", ...}。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import workspace, load_config, git_info, sha256_file, now_iso  # noqa: E402
from ledger_io import append_run, next_run_id, formal_blockers, read_runs, CONTAMINATION  # noqa: E402
from record_common import lock_status, rel  # noqa: E402
from rollup_hypotheses import rollup  # noqa: E402
from record_run import parse_fairness  # noqa: E402
from paper_ws import read_yaml_lite  # noqa: E402

SOURCE = "studio-evolve:growth-ledger"
DEFAULT_FAIRNESS = {"budget": "same harness", "search_space": "evolve proposals (one per round)", "seeds": 1, "hardware": "same harness"}


def parse_table(path: Path) -> list[dict]:
    """取第一张表头含 round 与 T_1 的 markdown 表（## 读数）。"""
    rows, header = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if header and rows:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            if cells and cells[0] == "round" and "T_1" in cells:
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(dict(zip(header, cells + [""] * (len(header) - len(cells)))))
    if header is None:
        raise SystemExit(f"{path} 里找不到读数表（表头需含 round 与 T_1）")
    return rows


def fnum(x: str):
    x = (x or "").strip()
    if x in ("", "—", "-", "–", "n/a"):
        return None
    try:
        return float(x)
    except ValueError:
        return None


def parse_map(s) -> dict:
    if isinstance(s, dict):
        return {k.strip(): str(v).strip() for k, v in s.items()}
    out = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="导入 studio-evolve growth-ledger 到论文台账")
    ap.add_argument("paper_id")
    ap.add_argument("ledger", help=".studio-evolve/{id}/ledger/growth-ledger.md")
    ap.add_argument("--map", help='轴 → RQ，如 "T=RQ-1,S=RQ-2,W=RQ-3,C=RQ-4"；缺省读 config ledger.evolve_map')
    ap.add_argument("--contamination", choices=sorted(CONTAMINATION))
    ap.add_argument("--n-from", default="window", choices=["window", "none"], help="n 取自哪一列")
    ap.add_argument("--n", type=int, help="所有行统一 n（覆盖 --n-from）")
    ap.add_argument("--fairness")
    ap.add_argument("--include-baseline", action="store_true", help="同时导入 round 0 冷启动基线")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    cfg = load_config(ws) if (ws / "config.yaml").exists() else {}
    lcfg = cfg.get("ledger") or {}
    src = Path(a.ledger)
    if not src.is_file():
        raise SystemExit(f"growth-ledger 不存在: {src}")

    amap = parse_map(a.map or lcfg.get("evolve_map"))
    if not amap:
        raise SystemExit("缺轴→RQ 映射：传 --map \"T=RQ-1,S=RQ-2,W=RQ-3,C=RQ-4\"，或在 config.yaml 的 ledger.evolve_map 写一次")
    if a.map and not lcfg.get("evolve_map"):
        print(f"提示：把映射记进 config.yaml 以后就不用再传 --map：\nledger:\n  evolve_map: {{{', '.join(f'{k}: {v}' for k, v in amap.items())}}}")
    contamination = a.contamination or lcfg.get("evolve_contamination")
    fairness = parse_fairness(a.fairness) or (lcfg.get("evolve_fairness") if isinstance(lcfg.get("evolve_fairness"), dict) else None) or dict(DEFAULT_FAIRNESS)
    # 每轮读数是 repeat_runs 次重复的结果（S_stable 的分母）→ seeds 缺省取 studio-evolve config 的 repeat_runs
    evo_cfg = Path(a.ledger).resolve().parent.parent / "config.yaml"
    if evo_cfg.exists() and (not a.fairness or "seeds" not in (parse_fairness(a.fairness) or {})):
        try:
            rr = int((read_yaml_lite(evo_cfg) or {}).get("repeat_runs") or 0)
            if rr > 0:
                fairness = {**fairness, "seeds": rr}
        except Exception:
            pass

    evo_cfg = src.resolve().parent.parent / "config.yaml"
    cfg_hash = sha256_file(evo_cfg) if evo_cfg.is_file() else sha256_file(src)
    cfg_path = rel(evo_cfg if evo_cfg.is_file() else src)
    ref_file = rel(src)
    seen = {(r.get("source"), (r.get("source_ref") or {}).get("file"), (r.get("source_ref") or {}).get("round"), (r.get("metric") or {}).get("name"))
            for r in read_runs(ws, include_void=True)}
    g = git_info()
    lock_ok, lock_msg = lock_status(ws)

    added, skipped, statuses = 0, 0, {}
    for row in parse_table(src):
        rnd = row.get("round", "").strip()
        if not rnd.isdigit() or (rnd == "0" and not a.include_baseline):
            continue
        rnd_i = int(rnd)
        n = a.n if a.n is not None else (int(fnum(row.get("window")) or 0) or None if a.n_from == "window" else None)
        base_note = f"growth-ledger round {rnd_i}; proposal={row.get('proposal', '')}; verdict={row.get('verdict', '')}"
        items = []  # (axis, metric_name, value, extra_note)
        for k in range(1, 5):
            items.append(("T", f"T_tier{k}", fnum(row.get(f"T_{k}")), ""))
        comps = {c: fnum(row.get(c)) for c in ("S_consist", "S_holdout", "S_stable")}
        comps = {c: v for c, v in comps.items() if v is not None}
        if comps:
            arg = min(comps, key=comps.get)
            items.append(("S", "S", comps[arg], f"S=min({', '.join(f'{c}={v:g}' for c, v in comps.items())}) → {arg}"))
        items.append(("W", "W_evid", fnum(row.get("W_evid")), ""))
        items.append(("C", "C_tok", fnum(row.get("C_tok")), ""))
        for axis, mname, val, xnote in items:
            if val is None or axis not in amap:
                continue
            if (SOURCE, ref_file, rnd_i, mname) in seen:
                skipped += 1
                continue
            run = {
                "run_id": next_run_id(ws) if not a.dry_run else f"(dry)-{rnd_i}-{mname}",
                "paper": a.paper_id, "kind": "main", "rq": [amap[axis]], "hypothesis": [],
                "status": "exploratory", "void_reason": None, "owner": g["user"], "ts": now_iso(),
                "git": {"commit": g["commit"], "dirty": g["dirty"]},
                "config_hash": cfg_hash, "config_path": cfg_path, "seed": None, "duration_s": None,
                "metric": {"name": mname, "value": val, "ci": None}, "n": n,
                "contamination": contamination, "varied": ["ontology_version"], "confounded": False,
                "fairness": fairness, "source": SOURCE,
                "source_ref": {"file": ref_file, "round": rnd_i},
                "note": "; ".join(x for x in (base_note, xnote) if x),
            }
            run["status"] = "exploratory" if formal_blockers(run, cfg, lock_ok) else "formal"
            statuses[run["status"]] = statuses.get(run["status"], 0) + 1
            if not a.dry_run:
                append_run(ws, run)
                seen.add((SOURCE, ref_file, rnd_i, mname))
            added += 1

    res = rollup(a.paper_id) if not a.dry_run else {"covered": [], "rqs": [], "drift": 0}
    print(f"{'[dry-run] 将' if a.dry_run else '已'}导入 {added} 行（{', '.join(f'{k} {v}' for k, v in statuses.items()) or '无'}），"
          f"跳过已存在 {skipped} 行。" + ("" if lock_ok else f" 注意：{lock_msg}，全部只能 exploratory。"))
    if not a.dry_run:
        print(f"RQ 覆盖 {len(res['covered'])}/{len(res['rqs'])}（{', '.join(res['covered']) or '无'}）；论点偏离 {res['drift']} 条"
              + ("，见 ledger/drift.md。" if res["drift"] else "。"))


if __name__ == "__main__":
    main()
