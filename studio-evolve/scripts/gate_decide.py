#!/usr/bin/env python3
"""gate_decide.py —— 双重闸门裁决（gate-rules.md §1–§3），八种 gate_mode，写 verdict.json 与 growth-ledger 行。

  gate_decide.py --config config.yaml --proposal 7 --attempt 1 --round 3 \
      --inner-base Di_h_i.json --inner-cand Di_h_prime.json \
      --outer-pair default B_h_i.json B_h_prime.json [--outer-pair gpt B_h_i_gpt.json B_h_prime_gpt.json] \
      --gate-mode 3d [--delta 0.03] [--accept-rate 0.6] [--seed 0] \
      [--ledger growth-ledger.md] [--verdict-out verdict.json] [--holdout-file cases/holdout/3.yaml]

读数文件由 growth_metrics.py compute 产出。候选 h' 相对基线 hᵢ 的增量：
  ΔT_k = T_k(h') − T_k(hᵢ)（只比两边都有读数的层）   ΔS = S_consist 差   ΔW = (W_evid 差, W_family 差)   ΔC = C_gate 差
  外关卡的 S 用完整 min(S_consist, S_holdout, S_stable)，其中 S_holdout(h') = 1 − max(0, A(hᵢ,B) − A(h',B))，S_holdout(hᵢ)=1。
  某一维两边都缺读数（None）→ 该维不参与判定并记 flag（不会因为算不出而「通过」或「失败」）。

gate_mode（每条引用 gate-rules.md 条款；1d 系引用 OpsHarness arXiv 2608.25661 §V-C Eqs.9–11 的判定式）
  3d       完整三维：内关卡 g_in = growOK ∧ costOK ∧ strict（§1），外关卡 g_out = growOK ∧ costOK（§2）；
           growOK = 每层 ΔT_k ≥ 0 ∧ ΔS ≥ 0 ∧ ΔW 两分量 ≥ 0；costOK = ΔC ≤ max(β·C(hᵢ), 1)；
           strict = ∃ΔT_k > 0 ∨ ΔS > 0 ∨ ΔW 某分量 > 0 ∨ ΔC < 0。多骨干时 g_out 每个骨干都过（§2「跨骨干面板」）。
  3d-noT   消融：growOK / strict 里去掉 T 项（S、W、C 保留）。
  3d-noS   消融：去掉 S 项。
  3d-noW   消融：去掉 W 项。
  1d       一维基线（OpsHarness Eqs.9–10 内关卡：ΔA ≥ 0 ∧ costOK ∧ (ΔA > 0 ∨ ΔC < 0)；Eq.11 外关卡：ΔA ≥ 0 ∧ costOK）。
  1d-strict 一维加严：内关卡 ΔA ≥ --delta（硬改善余量，δ 由 baselines.md 规则预登记：选使接受率与 3d 臂最接近的 δ），
           外关卡同 1d。
  none     无闸门：全部 ACCEPT（成长上界 / 稳定下界对照）。
  random   随机接受：以 --accept-rate 的概率接受，随机数由 (seed, proposal, attempt) 决定，可复现；
           用来匹配 3d 臂的接受率，排除「只因为拒得多」。
裁决（§3）：accept → ACCEPT（待人确认合入）；否则 attempt < max_attempts → REVISE，= max_attempts → REJECT。
脚本不合入、不碰生产 IR；执行方式恒为 AUTO，script_hash 进 verdict 与台账。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fmt, load_yaml, scripts_hash, write_json  # noqa: E402

MODES = ["3d", "3d-noT", "3d-noS", "3d-noW", "1d", "1d-strict", "none", "random"]


def load_reading(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def deltas(base: dict, cand: dict, outer: bool) -> dict:
    d: dict = {"flags": []}
    dT = {}
    for k in ("T_1", "T_2", "T_3", "T_4"):
        b, c = base["T"].get(k), cand["T"].get(k)
        if b is None or c is None:
            continue
        dT[k] = c - b
    if not dT:
        d["flags"].append("NO_T_LAYERS")
    d["dT"] = dT
    if base.get("S_consist") is None or cand.get("S_consist") is None:
        d["dS"] = None; d["flags"].append("S_CONSIST_MISSING")
    else:
        d["dS"] = cand["S_consist"] - base["S_consist"]
    if outer:
        A_b, A_c = base.get("A"), cand.get("A")
        S_hold_c = 1 - max(0.0, (A_b or 0) - (A_c or 0)) if (A_b is not None and A_c is not None) else None
        parts_c = [x for x in (cand.get("S_consist"), S_hold_c, cand.get("S_stable")) if x is not None]
        parts_b = [x for x in (base.get("S_consist"), 1.0, base.get("S_stable")) if x is not None]
        d["S_holdout_cand"] = S_hold_c
        d["S_full_base"], d["S_full_cand"] = (min(parts_b) if parts_b else None), (min(parts_c) if parts_c else None)
        if d["S_full_base"] is not None and d["S_full_cand"] is not None:
            d["dS"] = d["S_full_cand"] - d["S_full_base"]
            d["flags"] = [f for f in d["flags"] if f != "S_CONSIST_MISSING"]
        names = ["S_consist", "S_holdout", "S_stable"]
        vals = [cand.get("S_consist"), S_hold_c, cand.get("S_stable")]
        d["S_min_component"] = min((n for n, v in zip(names, vals) if v is not None), key=lambda n: vals[names.index(n)], default=None)
    dW = {}
    for k in ("W_evid", "W_family"):
        b, c = base.get(k), cand.get(k)
        if b is not None and c is not None:
            dW[k] = c - b
    if len(dW) < 2:
        d["flags"].append("W_PARTIAL" if dW else "W_MISSING")
    d["dW"] = dW
    if base.get("A") is None or cand.get("A") is None:
        d["dA"] = None; d["flags"].append("A_MISSING")
    else:
        d["dA"] = cand["A"] - base["A"]
    if base.get("C_gate") is None or cand.get("C_gate") is None:
        d["dC"] = None; d["C_base"] = None; d["flags"].append("C_MISSING")
    else:
        d["dC"] = cand["C_gate"] - base["C_gate"]; d["C_base"] = base["C_gate"]
    return d


def judge(d: dict, mode: str, beta: float, outer: bool, delta: float) -> dict:
    """返回 {growOK, costOK, strict, pass, reasons, triggered}；triggered = 导致不过的维度集合。"""
    use_T = mode in ("3d", "3d-noS", "3d-noW")
    use_S = mode in ("3d", "3d-noT", "3d-noW")
    use_W = mode in ("3d", "3d-noT", "3d-noS")
    one_d = mode in ("1d", "1d-strict")
    reasons, trig = [], set()
    # costOK（§1）：ΔC ≤ max(β·C(hᵢ), 1)
    if d["dC"] is None:
        costOK, band = True, None
    else:
        band = max(beta * d["C_base"], 1.0)
        costOK = d["dC"] <= band
        if not costOK:
            reasons.append(f"ΔC={d['dC']:.1f} > 带宽 {band:.1f}"); trig.add("C")
    if one_d:
        dA = d["dA"]
        if dA is None:
            growOK = True; reasons.append("A 缺读数，不判")
        else:
            need = delta if (mode == "1d-strict" and not outer) else 0.0
            growOK = dA >= need
            if not growOK:
                reasons.append(f"ΔA={dA:+.3f} < {need:.3f}"); trig.add("A")
        strict = True if outer else ((d["dA"] or 0) > 0 or (d["dC"] is not None and d["dC"] < 0))
        if not outer and not strict:
            reasons.append("无严格改善（ΔA ≤ 0 且 ΔC ≥ 0）")
        return {"growOK": growOK, "costOK": costOK, "strict": strict, "pass": growOK and costOK and strict,
                "reasons": reasons, "triggered": sorted(trig), "band": band}
    growOK, strict = True, False
    if use_T:
        bad = [k for k, v in d["dT"].items() if v < 0]
        if bad:
            growOK = False; reasons.append("长高回退层 " + ",".join(f"{k}({d['dT'][k]:+.2f})" for k in bad)); trig.add("T")
        if any(v > 0 for v in d["dT"].values()):
            strict = True
    if use_S and d["dS"] is not None:
        if d["dS"] < 0:
            growOK = False; reasons.append(f"长壮回退 ΔS={d['dS']:+.3f}"); trig.add("S")
        if d["dS"] > 0:
            strict = True
    if use_W:
        bad = [k for k, v in d["dW"].items() if v < 0]
        if bad:
            growOK = False; reasons.append("长胖回退 " + ",".join(f"{k}({d['dW'][k]:+.3f})" for k in bad)); trig.add("W")
        if any(v > 0 for v in d["dW"].values()):
            strict = True
    if d["dC"] is not None and d["dC"] < 0:
        strict = True
    if outer:
        strict = True  # §2：外关卡只要求不回退
    elif not strict:
        reasons.append("无严格改善（strict 不成立）")
    return {"growOK": growOK, "costOK": costOK, "strict": strict, "pass": growOK and costOK and strict,
            "reasons": reasons, "triggered": sorted(trig), "band": band}


def ledger_append(ledger: Path, row: dict) -> None:
    text = ledger.read_text(encoding="utf-8")
    lines = text.splitlines()
    hi = next((i for i, l in enumerate(lines) if l.startswith("| round") and "T_1" in l), None)
    if hi is None:
        sys.exit(f"{ledger} 里找不到读数表（表头需含 round 与 T_1）")
    header = [c.strip() for c in lines[hi].strip("|").split("|")]
    known = {k for k in row}
    extra = {k: row[k] for k in row if k not in header and k not in ("notes",)}
    notes = row.get("notes", "")
    if extra:
        notes = (notes + " " if notes else "") + " ".join(f"{k}={v}" for k, v in extra.items())
    cells = [fmt(row.get(c, "")) if c != "notes" else notes for c in header]
    cells = [c if c != "" else "" for c in cells]
    # 插在表的最后一行之后
    j = hi + 2
    while j < len(lines) and lines[j].startswith("|"):
        j += 1
    lines.insert(j, "| " + " | ".join(cells) + " |")
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="双重闸门裁决（gate-rules.md）", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("gate_mode", 1)[1])
    ap.add_argument("--config", required=True, type=Path, help=".studio-evolve/{id}/config.yaml")
    ap.add_argument("--proposal", required=True, help="提案号")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--window", default="")
    ap.add_argument("--inner-base", type=Path, help="hᵢ 在 Dᵢ 上的读数")
    ap.add_argument("--inner-cand", type=Path, help="h' 在 Dᵢ 上的读数")
    ap.add_argument("--outer-pair", nargs=3, action="append", metavar=("BACKBONE", "BASE", "CAND"), default=[],
                    help="留出集 B 上 hᵢ / h' 的读数，按骨干可重复")
    ap.add_argument("--gate-mode", default="3d", choices=MODES)
    ap.add_argument("--delta", type=float, default=0.0, help="1d-strict 的准确率硬改善余量")
    ap.add_argument("--accept-rate", type=float, help="random 模式的接受率")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--ledger", type=Path, help="growth-ledger.md，追加读数行")
    ap.add_argument("--verdict-out", type=Path, help="verdict.json 路径")
    ap.add_argument("--holdout-file", type=Path, help="本轮留出集文件（记录 size / thin）")
    ap.add_argument("--sandbox", default="", help="沙箱路径（只写进报告）")
    a = ap.parse_args()

    cfg = load_yaml(a.config)
    beta = float(cfg.get("beta", 0.05)); max_attempts = int(cfg.get("max_attempts", 3))
    backbones = cfg.get("backbones") or ["default"]
    if isinstance(backbones, str):
        backbones = [b.strip() for b in backbones.strip("[]").split(",") if b.strip()]
    oid = cfg.get("ontology_id", "?")
    mode = a.gate_mode
    needs_readings = mode not in ("none", "random")
    if needs_readings and not (a.inner_base and a.inner_cand and a.outer_pair):
        sys.exit("gate_mode 需要 --inner-base/--inner-cand 与至少一个 --outer-pair")

    inner = outer = None
    outer_results = []
    if a.inner_base and a.inner_cand:
        ib, ic = load_reading(a.inner_base), load_reading(a.inner_cand)
        d_in = deltas(ib, ic, outer=False)
        inner = {"deltas": d_in, "judge": judge(d_in, mode, beta, False, a.delta) if needs_readings else None,
                 "n": ic.get("n_cases"), "origins": ic.get("origins")}
    for bb, bp, cp in a.outer_pair:
        ob, oc = load_reading(Path(bp)), load_reading(Path(cp))
        d_out = deltas(ob, oc, outer=True)
        outer_results.append({"backbone": bb, "deltas": d_out, "judge": judge(d_out, mode, beta, True, a.delta) if needs_readings else None,
                              "n": oc.get("n_cases")})
    panel = "PANEL" if len(backbones) > 1 else "SINGLE_BACKBONE"
    if len(backbones) > 1:
        missing = set(backbones) - {o["backbone"] for o in outer_results}
        if missing and needs_readings:
            sys.exit(f"跨骨干面板缺少骨干 {sorted(missing)} 的 --outer-pair（§2：全部通过才算通过）")

    if mode == "none":
        accept, inner_pass, outer_pass = True, True, True
    elif mode == "random":
        if a.accept_rate is None:
            sys.exit("random 模式需要 --accept-rate")
        seed_src = f"{a.seed}:{a.proposal}:{a.attempt}".encode()
        r = random.Random(int(hashlib.sha256(seed_src).hexdigest(), 16) % (2**63))
        accept = r.random() < a.accept_rate
        inner_pass = outer_pass = accept
    else:
        inner_pass = inner["judge"]["pass"]
        outer_pass = all(o["judge"]["pass"] for o in outer_results)
        accept = inner_pass and outer_pass
    decision = "ACCEPT" if accept else ("REVISE" if a.attempt < max_attempts else "REJECT")
    triggered = set(inner["judge"]["triggered"]) if (inner and inner["judge"]) else set()
    for o in outer_results:
        if o["judge"]:
            triggered |= set(o["judge"]["triggered"])
    triggered = sorted(triggered)
    sh = scripts_hash()
    holdout_meta = load_yaml(a.holdout_file) if a.holdout_file and a.holdout_file.is_file() else {}

    # ---- 报告（gate-rules.md §5）
    def dT_str(d):
        return "(" + ", ".join(f"{d['dT'].get(k, float('nan')):+.2f}" if k in d["dT"] else "—" for k in ("T_1", "T_2", "T_3", "T_4")) + ")"
    def dW_str(d):
        return "(" + ", ".join(f"{d['dW'][k]:+.3f}" if k in d["dW"] else "—" for k in ("W_evid", "W_family")) + ")"
    def yn(b):
        return "✓" if b else "✗"
    out = [f"提案 {a.proposal}（第 {a.attempt}/{max_attempts} 次）· 本体 {oid} · 沙箱 {a.sandbox or '—'} · gate_mode={mode}"]
    if inner:
        og = inner["origins"] or {}
        out.append(f"来源案例 Dᵢ：{inner['n']} 条（real {og.get('real', 0)} / sim {og.get('sim', 0)})"
                   + (f"    留出集 B：{holdout_meta.get('size', '?')} 条（real，{holdout_meta.get('strata', '?')} 层，round {a.round}"
                      + (", HOLDOUT_THIN" if str(holdout_meta.get('thin')).lower() == 'true' else "") + "）" if holdout_meta else ""))
        if needs_readings:
            d, j = inner["deltas"], inner["judge"]
            out.append("内关卡 g_in：")
            out.append(f"  ΔT = {dT_str(d)}   ΔS_consist = {fmt(d['dS'])}   ΔW = {dW_str(d)}   ΔC_tok = {fmt(d['dC'])}（带宽 {fmt(j['band'])}）"
                       + (f"   ΔA = {d['dA']:+.3f}" if d['dA'] is not None else ""))
            out.append(f"  growOK {yn(j['growOK'])}  costOK {yn(j['costOK'])}  strict {yn(j['strict'])}  → {'PASS' if j['pass'] else 'FAIL'}"
                       + (f"   原因：{'；'.join(j['reasons'])}" if j['reasons'] else "") + (f"   flags={d['flags']}" if d['flags'] else ""))
    for o in outer_results:
        if not needs_readings:
            break
        d, j = o["deltas"], o["judge"]
        out.append(f"外关卡 g_out（骨干 {o['backbone']}）：")
        out.append(f"  ΔT = {dT_str(d)}   ΔS = {fmt(d['dS'])}（min 分量 = {d.get('S_min_component') or '—'}）   ΔW = {dW_str(d)}   ΔC_tok = {fmt(d['dC'])}"
                   + (f"   ΔA = {d['dA']:+.3f}" if d['dA'] is not None else ""))
        out.append(f"  growOK {yn(j['growOK'])}  costOK {yn(j['costOK'])}  → {'PASS' if j['pass'] else 'FAIL'}"
                   + (f"   原因：{'；'.join(j['reasons'])}" if j['reasons'] else "") + (f"   flags={d['flags']}" if d['flags'] else ""))
    if mode == "random":
        out.append(f"随机接受：accept_rate={a.accept_rate}，seed={a.seed} → {'接受' if accept else '拒绝'}")
    if mode == "none":
        out.append("无闸门：全部接受")
    tail = {"ACCEPT": "ACCEPT（待人确认合入）", "REVISE": f"REVISE（退回归因，触发维度：{','.join(triggered) or '—'}）",
            "REJECT": f"REJECT（{max_attempts} 次未过，签名已记档，触发维度：{','.join(triggered) or '—'}）"}[decision]
    out.append(f"裁决：{tail}")
    out.append(f"执行方式：AUTO（{sh[:19]}…）· {panel}")
    report = "\n".join(out)
    print(report)

    # ---- verdict.json
    verdict = {"ontology_id": oid, "proposal": a.proposal, "attempt": a.attempt, "round": a.round, "gate_mode": mode,
               "delta": a.delta if mode == "1d-strict" else None, "accept_rate": a.accept_rate if mode == "random" else None,
               "seed": a.seed if mode == "random" else None, "beta": beta, "max_attempts": max_attempts, "panel": panel,
               "inner": inner, "outer": outer_results, "inner_pass": inner_pass, "outer_pass": outer_pass,
               "decision": decision, "triggered": triggered, "exec": "AUTO", "script_hash": sh, "report": report}
    if a.verdict_out:
        write_json(a.verdict_out, verdict)
        print(f"  verdict → {a.verdict_out}")
    # ---- growth-ledger 行
    if a.ledger:
        cand_out = outer_results[0]["deltas"] if outer_results else {}
        rd = load_reading(Path(a.outer_pair[0][2])) if a.outer_pair else (load_reading(a.inner_cand) if a.inner_cand else {})
        row = {"round": a.round, "window": a.window or "—", "proposal": a.proposal, "attempt": a.attempt,
               **{k: rd.get("T", {}).get(k) for k in ("T_1", "T_2", "T_3", "T_4")},
               "S_consist": rd.get("S_consist"), "S_holdout": cand_out.get("S_holdout_cand"), "S_stable": rd.get("S_stable"),
               "W_evid": rd.get("W_evid"), "W_family": rd.get("W_family"),
               "W_elem": (sum(v for v in rd["W_elem"].values() if isinstance(v, (int, float))) if isinstance(rd.get("W_elem"), dict) else "—"),
               "C_tok": rd.get("C_gate"), "inner": "PASS" if inner_pass else "FAIL", "outer": "PASS" if outer_pass else "FAIL",
               "verdict": decision, "exec": f"AUTO/{panel}", "gate_mode": mode, "script_hash": sh[:23],
               "notes": ("触发 " + ",".join(triggered)) if triggered else ""}
        ledger_append(a.ledger, row)
        print(f"  ledger ← round {a.round} proposal {a.proposal} {decision}")
    sys.exit(0)


if __name__ == "__main__":
    main()
