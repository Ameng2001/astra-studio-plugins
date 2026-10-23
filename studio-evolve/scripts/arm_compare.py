#!/usr/bin/env python3
"""arm_compare.py —— 把 gate_decide.py 的 verdict.json 汇成论文 P-A 配对协议的臂间指标（criteria.yaml RQ-1 / RQ-3 的口径）。

  arm_compare.py --verdicts-dir .studio-evolve/{id}/verdicts [--readings-dir .studio-evolve/{id}/readings] \
      --out-dir paper/arms [--rounds 1,2] [--boot 1000] [--seed 0] [--tier-min 8] [--tier-drop 0.02]

每个 verdict 文件名 {round}-{proposal}-{attempt}-{gate_mode}.json；同一 (round, proposal, attempt) 在各 gate_mode 下的裁决就是
「同一条提案流、各臂独立裁决」。本脚本只读 verdict（与可选的读数文件），不重算闸门。

口径（criteria.yaml 头注 [P-A]）：
  回退 regress(P)：被某臂接受的提案 P，在留出集上 A_holdout 下降 > 0，或任一样本数 ≥ tier-min 的层 T_k 下降 > tier-drop → 1。
    协议要求用下一轮留出集 B_{i+1}；verdict 里只有裁决当轮的 B_i，本脚本按 B_i 算并打 REGRESS_ON_SAME_B 旗——这样算出的
    结果只能入探索性（提案在 B_i 上「不回退」本身就是 3d / 1d 外关卡的准入条件，用它判回退对接受臂有利）。
    层样本数从 --readings-dir 的 P{p}-hi-B.json（growth_metrics 的 n_tier / runs）取；没有读数文件则不过滤层样本并打 TIER_N_UNKNOWN。
    按协议算（B_{i+1}）：--readings-dir 下有 P{p}-hi-B{r+1}.json 与 P{p}-hp-B{r+1}.json（round r 的提案在 round r+1 留出集上的读数，
    hᵢ 与 Apply(hᵢ,P) 各一份）时，该提案的回退按这对读数算（dA = A(h') − A(hᵢ)，dT_k 同理），不打 REGRESS_ON_SAME_B；
    只有部分提案有下一轮读数时打 REGRESS_MIXED_B。
  单条回退 regress_any(P)（2026-10-23 加，与净口径并排、不进 criteria 判定）：留出集里任一案例在 hᵢ 上原题全过（case_pass = 1）而在
    Apply(hᵢ,P) 上有一次不过（case_pass < 1）→ 1。用的是同一对读数里的 case_pass（growth_metrics 逐案例原题通过率）；读数没有
    case_pass 时该提案 regress_any = null 并打 REGRESS_ANY_UNAVAILABLE。净口径会让同层的修对盖住单条改错（round 5 P9c：B_5 上
    r08 由对变错，被 r73 / r74 修对抵消，净 A +0.025，regress = 0），单条口径看得见。
  regress_rate(臂) = 臂接受集中 regress=1 的比例；regress_any_rate(臂) 同理按 regress_any；accept_rate(臂) = 接受数 / 提案数。
  regress_rate_diff = rate(1d) − rate(3d)；regress_any_diff 同理；regress_rate_ablation_diff = rate(3d-noX) − rate(3d)；
  matched_diff = rate(random) − rate(3d)、rate(1d-strict) − rate(3d)（排除「只因为拒得多」）。
  分歧集：1d 接受 ∧ 3d 拒绝 → divergent_regress_rate；3d 接受 ∧ 1d 拒绝 → reverse_divergent_regress_rate。
  reject_share_{T,S,W,C}：3d 臂拒绝的提案按「首个不满足的判定式」归属（顺序 T → S → W → C，偏向 T；另报多维重叠数）。
  区间：按提案联合重采样 --boot 次（两臂接受集不同，不能只在交集配对），每次分别重算两臂比例再做差；百分位 95% CI。

输出 --out-dir/：arms.json（全部数字 + flags）· T1.md（各臂 accept_rate / regress_rate / regress_any_rate 带 CI）· T2.md（分歧集，净口径与单条口径并排）·
  T4.md（3d 拒绝的触发维度份额）· metrics.json（record_run.py 可直接读：metrics{…}、n、flags）。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
from pathlib import Path

FNAME = re.compile(r"^(\d+)-(\d+)-(\d+)-([A-Za-z0-9\-]+)\.json$")
FULL, ONE_D = "3d", "1d"
ARM_ORDER = ["3d", "1d", "random", "1d-strict", "none", "3d-noT", "3d-noS", "3d-noW"]


def load_verdicts(vdir: Path, rounds: set[int] | None):
    out: dict[tuple, dict] = {}  # (round, proposal, attempt) -> {mode: verdict}
    for f in sorted(vdir.glob("*.json")):
        m = FNAME.match(f.name)
        if not m:
            continue
        r, p, a, mode = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
        if rounds and r not in rounds:
            continue
        out.setdefault((r, p, a), {})[mode] = json.loads(f.read_text(encoding="utf-8"))
    return out


def tier_cases(readings_dir: Path | None, proposal: str) -> dict[str, int] | None:
    """留出集上每层案例数（n_tier 是行数，除以 runs）。"""
    if not readings_dir:
        return None
    f = readings_dir / f"P{proposal}-hi-B.json"
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    runs = max(1, int(d.get("runs") or 1))
    return {k: int(v) // runs for k, v in (d.get("n_tier") or {}).items()}


def next_round_deltas(readings_dir: Path | None, proposal: str, rnd: int):
    """round r 的提案在 B_{r+1} 上的 (deltas, tiers_n)；没有读数对则 None。"""
    if not readings_dir:
        return None
    hi, hp = readings_dir / f"P{proposal}-hi-B{rnd + 1}.json", readings_dir / f"P{proposal}-hp-B{rnd + 1}.json"
    if not (hi.exists() and hp.exists()):
        return None
    a, b = (json.loads(f.read_text(encoding="utf-8")) for f in (hi, hp))
    dT = {k: (b["T"][k] - a["T"][k]) for k in (a.get("T") or {}) if a["T"].get(k) is not None and (b.get("T") or {}).get(k) is not None}
    runs = max(1, int(a.get("runs") or 1))
    return {"backbone": "next", "deltas": {"dA": (b.get("A") or 0) - (a.get("A") or 0), "dT": dT}}, {k: int(v) // runs for k, v in (a.get("n_tier") or {}).items()}


def regress_of(verdict: dict, tiers_n: dict[str, int] | None, tier_min: int, tier_drop: float, outer=None) -> tuple[int, list[str]]:
    """按 criteria 头注判回退：任一骨干上 A 下降 > 0，或样本够的层 T_k 下降 > tier_drop。outer 给定时用它代替 verdict 的外关卡读数。"""
    why = []
    for o in (outer if outer is not None else (verdict.get("outer") or [])):
        d = o.get("deltas") or {}
        if (d.get("dA") or 0) < 0:
            why.append(f"{o.get('backbone')}:dA={d['dA']:+.3f}")
        for k, v in (d.get("dT") or {}).items():
            tk = k.split("_")[-1]
            if tiers_n is not None and tiers_n.get(tk, 0) < tier_min:
                continue
            if v is not None and v < -tier_drop:
                why.append(f"{o.get('backbone')}:d{k}={v:+.3f}")
    return (1 if why else 0), why


def _reading(readings_dir: Path | None, name: str) -> dict | None:
    f = (readings_dir / name) if readings_dir else None
    return json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else None


def regress_any_of(hi: dict | None, hp: dict | None) -> tuple[int | None, list[str]]:
    """单条回退：hᵢ 全过（case_pass==1）而 h' 有一次不过的案例列表；任一读数缺 case_pass → (None, [])。"""
    a, b = (hi or {}).get("case_pass"), (hp or {}).get("case_pass")
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None, []
    broken = sorted(c for c, v in a.items() if float(v) >= 1.0 and c in b and float(b[c]) < 1.0)
    return (1 if broken else 0), broken


def reject_dim(verdict: dict) -> str:
    """3d 拒绝归属：首个不满足的判定式（T → S → W → C）；判不出来记 unknown。"""
    reasons = " ".join(str(x) for j in (verdict.get("inner"), *(verdict.get("outer") or []))
                       if isinstance(j, dict) for x in ((j.get("judge") or {}).get("reasons") or []))
    for dim, pat in (("T", r"ΔT|T_\d|tier"), ("S", r"ΔS|S_"), ("W", r"ΔW|W_"), ("C", r"ΔC|cost|C_")):
        if re.search(pat, reasons):
            return dim
    return "unknown"


def rate(xs: list[int]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--verdicts-dir", required=True, type=Path)
    ap.add_argument("--readings-dir", type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--rounds", help="逗号分隔；缺省全部")
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--tier-min", type=int, default=8)
    ap.add_argument("--tier-drop", type=float, default=0.02)
    a = ap.parse_args()

    rounds = {int(x) for x in a.rounds.split(",")} if a.rounds else None
    streams = load_verdicts(a.verdicts_dir, rounds)
    if not streams:
        sys.exit("没有 verdict 文件")
    flags: set[str] = set()
    same_b = next_b = 0
    modes = sorted({m for v in streams.values() for m in v}, key=lambda m: (ARM_ORDER.index(m) if m in ARM_ORDER else 99, m))
    for need in (FULL, ONE_D):
        if need not in modes:
            sys.exit(f"缺 {need} 臂的 verdict，算不了 regress_rate_diff")

    # 每条提案：各臂是否接受、回退与否（回退只与提案有关，不随臂变）
    props = []
    for key in sorted(streams):
        vs = streams[key]
        ref = vs.get(FULL) or next(iter(vs.values()))
        nxt = next_round_deltas(a.readings_dir, key[1], key[0])
        if nxt is not None:  # 协议口径：在下一轮留出集上判回退
            next_b += 1
            reg, why = regress_of(ref, nxt[1], a.tier_min, a.tier_drop, outer=[nxt[0]])
            pair = (f"P{key[1]}-hi-B{key[0] + 1}.json", f"P{key[1]}-hp-B{key[0] + 1}.json")
        else:
            same_b += 1
            tiers_n = tier_cases(a.readings_dir, key[1])
            if tiers_n is None:
                flags.add("TIER_N_UNKNOWN")
            reg, why = regress_of(ref, tiers_n, a.tier_min, a.tier_drop)
            pair = (f"P{key[1]}-hi-B.json", f"P{key[1]}-hp-B.json")
        reg_any, broken = regress_any_of(_reading(a.readings_dir, pair[0]), _reading(a.readings_dir, pair[1]))
        if reg_any is None:
            flags.add(f"REGRESS_ANY_UNAVAILABLE:{key[1]}")
        accepted = {m: (vs[m].get("decision") == "ACCEPT") for m in modes if m in vs}
        missing = [m for m in modes if m not in vs]
        if missing:
            flags.add("ARM_MISSING:" + ",".join(missing))
        props.append({"round": key[0], "proposal": key[1], "attempt": key[2], "accepted": accepted, "regress": reg, "regress_why": why,
                      "regress_any": reg_any, "regress_any_cases": broken,
                      "regress_basis": "B_next" if nxt is not None else "B_same",
                      "reject_dim_3d": (None if accepted.get(FULL) else reject_dim(vs[FULL])) if FULL in vs else None,
                      "window": ref.get("window"), "origins": (ref.get("inner") or {}).get("origins")})

    def arm_stats(sample: list[dict]) -> dict:
        st = {}
        for m in modes:
            acc = [p for p in sample if p["accepted"].get(m)]
            st[m] = {"accept_rate": len(acc) / len(sample) if sample else None, "n_accept": len(acc),
                     "regress_rate": rate([p["regress"] for p in acc]),
                     "regress_any_rate": rate([p["regress_any"] for p in acc if p["regress_any"] is not None]),
                     "n_any_known": sum(1 for p in acc if p["regress_any"] is not None)}
        return st

    def diffs(st: dict) -> dict:
        r3, r1 = st[FULL]["regress_rate"], st[ONE_D]["regress_rate"]
        d = {"regress_rate_diff": (r1 - r3) if (r1 is not None and r3 is not None) else None}
        a3, a1 = st[FULL]["regress_any_rate"], st[ONE_D]["regress_any_rate"]
        d["regress_any_diff"] = (a1 - a3) if (a1 is not None and a3 is not None) else None
        for m in ("random", "1d-strict", "3d-noT", "3d-noS", "3d-noW"):
            rm = st.get(m, {}).get("regress_rate")
            d[f"{m}_minus_3d"] = (rm - r3) if (rm is not None and r3 is not None) else None
        return d

    if same_b and next_b:
        flags.add("REGRESS_MIXED_B")
    elif same_b:
        flags.add("REGRESS_ON_SAME_B")
    point = arm_stats(props)
    pdiff = diffs(point)
    rng = random.Random(a.seed)
    boots = {k: [] for k in pdiff}
    for _ in range(a.boot):
        sample = [props[rng.randrange(len(props))] for _ in props]
        for k, v in diffs(arm_stats(sample)).items():
            if v is not None:
                boots[k].append(v)

    def ci(xs: list[float]):
        if len(xs) < 20:
            return None
        xs = sorted(xs)
        return [xs[int(0.025 * (len(xs) - 1))], xs[int(0.975 * (len(xs) - 1))]]

    div_1d_not_3d = [p for p in props if p["accepted"].get(ONE_D) and not p["accepted"].get(FULL)]
    div_3d_not_1d = [p for p in props if p["accepted"].get(FULL) and not p["accepted"].get(ONE_D)]
    rejected_3d = [p for p in props if FULL in p["accepted"] and not p["accepted"][FULL]]
    share = {d: sum(1 for p in rejected_3d if p["reject_dim_3d"] == d) for d in ("T", "S", "W", "C", "unknown")}
    n_min_arm = min(point[m]["n_accept"] for m in (FULL, ONE_D))
    res = {
        "n_proposals": len(props), "rounds": sorted({p["round"] for p in props}), "modes": modes,
        "arms": point,
        "diffs": {k: {"value": v, "ci95": ci(boots[k]), "boot": len(boots[k])} for k, v in pdiff.items()},
        "divergent": {"1d_accept_3d_reject": {"n": len(div_1d_not_3d), "regress_rate": rate([p["regress"] for p in div_1d_not_3d]),
                                              "regress_any_rate": rate([p["regress_any"] for p in div_1d_not_3d if p["regress_any"] is not None]),
                                              "proposals": [p["proposal"] for p in div_1d_not_3d]},
                      "3d_accept_1d_reject": {"n": len(div_3d_not_1d), "regress_rate": rate([p["regress"] for p in div_3d_not_1d]),
                                              "regress_any_rate": rate([p["regress_any"] for p in div_3d_not_1d if p["regress_any"] is not None]),
                                              "proposals": [p["proposal"] for p in div_3d_not_1d]}},
        "reject_share_3d": {"n_rejected": len(rejected_3d), **share},
        "n": n_min_arm, "flags": sorted(flags), "seed": a.seed, "boot": a.boot, "tier_min": a.tier_min, "tier_drop": a.tier_drop,
        "proposals": props,
    }
    a.out_dir.mkdir(parents=True, exist_ok=True)
    (a.out_dir / "arms.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    f = lambda x: "—" if x is None else f"{x:.3f}"
    fci = lambda c: "—" if not c else f"[{c[0]:+.3f}, {c[1]:+.3f}]"
    t1 = ["| 臂 | accept_rate | n_accept | regress_rate（净） | regress_any_rate（单条） | n_any_known |", "|---|---|---|---|---|---|"]
    t1 += [f"| {m} | {f(point[m]['accept_rate'])} | {point[m]['n_accept']} | {f(point[m]['regress_rate'])} | {f(point[m]['regress_any_rate'])} | {point[m]['n_any_known']} |" for m in modes]
    t1 += ["", "| 差值 | 点估计 | 95% CI（提案联合重采样） |", "|---|---|---|"]
    t1 += [f"| {k} | {f(v['value'])} | {fci(v['ci95'])} |" for k, v in res["diffs"].items()]
    t1.append(f"\n表 T1：{len(props)} 条提案（round {','.join(map(str, res['rounds']))}），n = min 两臂接受数 = {n_min_arm}；flags = {', '.join(res['flags'])}。"
              f"regress_rate 是 criteria 的净口径（A_holdout / 层 T_k 下降），regress_any_rate 是并排报告的单条口径（任一 hᵢ 全过案例在 h' 失败），不进判定。")
    (a.out_dir / "T1.md").write_text("\n".join(t1) + "\n", encoding="utf-8")
    dv = res["divergent"]
    anyc = lambda ps: "; ".join(f"{p['proposal']}:{','.join(p['regress_any_cases'])}" for p in ps if p["regress_any_cases"]) or "—"
    t2 = ["| 分歧集 | 条数 | 回退比例（净） | 回退比例（单条） | 提案 | 单条回退案例 |", "|---|---|---|---|---|---|",
          f"| 1d 接受 ∧ 3d 拒绝 | {dv['1d_accept_3d_reject']['n']} | {f(dv['1d_accept_3d_reject']['regress_rate'])} | {f(dv['1d_accept_3d_reject']['regress_any_rate'])} | {', '.join(dv['1d_accept_3d_reject']['proposals']) or '—'} | {anyc(div_1d_not_3d)} |",
          f"| 3d 接受 ∧ 1d 拒绝 | {dv['3d_accept_1d_reject']['n']} | {f(dv['3d_accept_1d_reject']['regress_rate'])} | {f(dv['3d_accept_1d_reject']['regress_any_rate'])} | {', '.join(dv['3d_accept_1d_reject']['proposals']) or '—'} | {anyc(div_3d_not_1d)} |",
          f"\n表 T2：分歧集；H-1b 要前者 ≥ 0.60、后者 < 0.20（按净口径判；单条口径并排报告）。"]
    (a.out_dir / "T2.md").write_text("\n".join(t2) + "\n", encoding="utf-8")
    rs = res["reject_share_3d"]
    t4 = ["| 维度 | 3d 拒绝归属条数 |", "|---|---|"] + [f"| {d} | {rs[d]} |" for d in ("T", "S", "W", "C", "unknown")]
    t4.append(f"\n表 T4：3d 臂共拒绝 {rs['n_rejected']} 条，按首个不满足的判定式归属（T → S → W → C）。")
    (a.out_dir / "T4.md").write_text("\n".join(t4) + "\n", encoding="utf-8")
    metrics = {"metrics": {k: v["value"] for k, v in res["diffs"].items() if v["value"] is not None}, "n": n_min_arm, "flags": res["flags"],
               "ci": {k: v["ci95"] for k, v in res["diffs"].items()}}
    metrics["metrics"].update({f"regress_rate_{m}": point[m]["regress_rate"] for m in modes if point[m]["regress_rate"] is not None})
    metrics["metrics"].update({f"regress_any_rate_{m}": point[m]["regress_any_rate"] for m in modes if point[m]["regress_any_rate"] is not None})
    metrics["metrics"]["divergent_regress_any_rate"] = dv["1d_accept_3d_reject"]["regress_any_rate"]
    metrics["metrics"].update({f"accept_rate_{m}": point[m]["accept_rate"] for m in modes})
    metrics["metrics"]["divergent_regress_rate"] = dv["1d_accept_3d_reject"]["regress_rate"]
    (a.out_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✓ 臂间对比：{len(props)} 条提案 × {len(modes)} 臂 → {a.out_dir}（T1 / T2 / T4 / arms.json / metrics.json）")
    print(f"  regress_rate 3d={f(point[FULL]['regress_rate'])} 1d={f(point[ONE_D]['regress_rate'])} diff={f(pdiff['regress_rate_diff'])} CI {fci(res['diffs']['regress_rate_diff']['ci95'])} · n={n_min_arm} · flags {res['flags']}")
    print(f"  regress_any_rate 3d={f(point[FULL]['regress_any_rate'])} 1d={f(point[ONE_D]['regress_any_rate'])} diff={f(pdiff['regress_any_diff'])} CI {fci(res['diffs']['regress_any_diff']['ci95'])}（单条口径，不进判定）")


if __name__ == "__main__":
    main()
