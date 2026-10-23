#!/usr/bin/env python3
"""smoke_gate_modes.py —— 合成案例集上的端到端冒烟：holdout_sample → growth_metrics → gate_decide × 8 种 gate_mode。

  smoke_gate_modes.py [--workdir DIR] [--keep]

合成数据：2 family × 4 tier × {real, sim} × 5 条 = 80 条案例，每条 2 个变体，3 次 run。
两份提案：
  P1「只长不稳」：h' 把 tier-3/4 的通过率抬高（A ↑），成本在带内，但变体一致率掉（S ↓）
      → 预期 3d / 3d-noT / 3d-noW 不过（S 触发），3d-noS 过，1d 过，1d-strict(δ=0.15) 不过（ΔA≈+0.13），none 过。
  P2「全面改善」：T ↑（ΔA≈+0.20）、S 不降、成本略降 → 预期七种判定模式都过。
random 模式：对 200 个提案号以 accept_rate=0.6 裁决，经验接受率须在 0.6 ± 0.08 内，且同 seed 可复现。
退出码 0 = 全部断言通过。
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable


def sh(*args, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run([PY, *map(str, args)], capture_output=True, text=True)
    if check and r.returncode != 0:
        print(r.stdout); print(r.stderr); raise SystemExit(f"命令失败: {' '.join(map(str, args))}")
    return r


def build_bank(root: Path, rng: random.Random) -> dict[str, dict]:
    cases = {}
    fams = ["enrollment", "review-plan"]
    for fam in fams:
        for tier in (1, 2, 3, 4):
            for origin in ("real", "sim"):
                for n in range(5):
                    cid = f"{fam}-t{tier}-{origin}-{n}"
                    month = 3 + (n * 2 + tier) % 8
                    c = {"id": cid, "q": f"{fam} tier{tier} case {n}", "expect": "query" if tier < 3 else "action(X)",
                         "tier": tier, "family": fam, "origin": origin, "observed_at": f"2026-{month:02d}-{10 + n:02d}",
                         "elements": ["Patient"], "label": {"answer": "private", "source": "feedback" if origin == "real" else "simulate"},
                         "variants": [{"q": "para", "kind": "paraphrase"}, {"q": "swap", "kind": "entity_swap"}]}
                    d = root / "cases" / origin
                    d.mkdir(parents=True, exist_ok=True)
                    (d / f"{cid}.yaml").write_text(
                        "\n".join([f"id: {cid}", f'q: "{c["q"]}"', f"expect: {c['expect']}", f"tier: {tier}", f"family: {fam}",
                                   f"origin: {origin}", f"observed_at: {c['observed_at']}", "elements: [Patient]",
                                   "label:", '  answer: "private"', f"  source: {c['label']['source']}", "variants:",
                                   '  - q: "para"', "    kind: paraphrase", '  - q: "swap"', "    kind: entity_swap"]) + "\n")
                    cases[cid] = c
    return cases


def gen_results(cases: dict, ids: list[str], rng: random.Random, state: str, pass_p: dict, consist: float, tok: float) -> list[dict]:
    rows = []
    for cid in ids:
        c = cases[cid]
        for run in range(3):
            p = int(rng.random() < pass_p[c["tier"]])
            base = {"state": state, "case": cid, "run": run, "tier": c["tier"], "family": c["family"], "origin": c["origin"],
                    "observed_at": c["observed_at"], "tokens": round(rng.gauss(tok, tok * 0.05), 1), "steps": 4, "wall_s": 1.0}
            rows.append({**base, "variant": None, "pass": p})
            for i, v in enumerate(c["variants"]):
                vp = p if rng.random() < consist else 1 - p
                rows.append({**base, "variant": f"{v['kind']}-{i}", "pass": vp})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path)
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    work = a.workdir or Path(tempfile.mkdtemp(prefix="evolve-smoke-"))
    work.mkdir(parents=True, exist_ok=True)
    rng = random.Random(7)
    cases = build_bank(work, rng)
    (work / "config.yaml").write_text("ontology_id: smoke\ntarget_dir: plugins/smoke\nwindow_size: 20\nbeta: 0.05\ntheta_tier: 0.8\n"
                                      "repeat_runs: 3\nmax_attempts: 3\nstratify: [family, tier, time_half]\nbackbones: [default]\n")
    (work / "ontology-map.md").write_text("# Ontology Map: smoke\n\n## 8. Evidence（证据）\n\n| element | kind | probe | sample | status | verified_at |\n|---|---|---|---|---|---|\n"
                                          "| Patient | OBJECT | SELECT 1 | 20 行 | verified | 2026-10-01 |\n| Plan | OBJECT | SELECT 1 | 0 行 | missing | 2026-10-01 |\n"
                                          "| Patient→Plan | MAPPING | SELECT 1 | 20 行 | verified | 2026-10-01 |\n")
    tmpl = (HERE.parent / "templates" / "growth-ledger.md.tmpl").read_text()
    ledger = work / "growth-ledger.md"
    ledger.write_text(tmpl.replace("{{ontology_id}}", "smoke").replace("{{beta}}", "0.05").replace("{{window_size}}", "20").replace("{{backbones}}", "[default]"))

    # Dᵢ：10 real + 10 sim
    real_ids = sorted(k for k, v in cases.items() if v["origin"] == "real")
    sim_ids = sorted(k for k, v in cases.items() if v["origin"] == "sim")
    Di = rng.sample(real_ids, 10) + rng.sample(sim_ids, 10)
    (work / "Di.yaml").write_text("source_cases:\n" + "".join(f"  - {x}\n" for x in Di))

    # 留出集
    r = sh(HERE / "holdout_sample.py", "--cases-dir", work / "cases", "--round", 1, "--holdout-size", 24,
           "--exclude-file", work / "Di.yaml", "--ledger", ledger)
    print(r.stdout.strip())
    hold = work / "cases" / "holdout" / "1.yaml"
    B = [l.strip()[2:] for l in hold.read_text().splitlines() if l.startswith("  - ")]
    assert len(B) == 24 and not (set(B) & set(Di)) and all(cases[x]["origin"] == "real" for x in B), "留出集构造违规"
    # 再抽一次应被拒（每轮只抽一次）
    r2 = sh(HERE / "holdout_sample.py", "--cases-dir", work / "cases", "--round", 1, "--holdout-size", 24, check=False)
    assert r2.returncode != 0, "同一轮重复抽样应被拒绝"

    base_p = {1: 0.95, 2: 0.8, 3: 0.5, 4: 0.3}
    p1_p = {1: 0.95, 2: 0.8, 3: 0.85, 4: 0.6}      # 只长不稳
    p2_p = {1: 1.0, 2: 0.95, 3: 0.8, 4: 0.6}       # 全面改善（与 h_i 同一随机序列 → 通过集是 h_i 的超集）
    rows = []
    rows += gen_results(cases, Di + B, random.Random(1), "h_i", base_p, consist=0.95, tok=1000)
    rows += gen_results(cases, Di + B, random.Random(1), "p1", p1_p, consist=0.55, tok=1030)
    rows += gen_results(cases, Di + B, random.Random(1), "p2", p2_p, consist=1.0, tok=960)
    res = work / "results.jsonl"
    res.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows) + "\n")

    def reading(state, case_set, name):
        out = work / "readings" / f"{name}.json"
        ids = ",".join(case_set)
        sh(HERE / "growth_metrics.py", "compute", "--results", res, "--state", state, "--case-ids", ids, "--ir", work / "ontology-map.md", "--out", out)
        return out
    rd = {}
    for st in ("h_i", "p1", "p2"):
        rd[(st, "Di")] = reading(st, Di, f"{st}-Di")
        rd[(st, "B")] = reading(st, B, f"{st}-B")
    hi_B = json.loads(rd[("h_i", "B")].read_text())
    assert hi_B["n_cases"] == 24 and hi_B["W_evid"] == 2 / 3 and hi_B["runs"] == 3, hi_B

    expect = {"P1": {"3d": "REVISE", "3d-noT": "REVISE", "3d-noS": "ACCEPT", "3d-noW": "REVISE", "1d": "ACCEPT", "1d-strict": "REVISE", "none": "ACCEPT"},
              "P2": {"3d": "ACCEPT", "3d-noT": "ACCEPT", "3d-noS": "ACCEPT", "3d-noW": "ACCEPT", "1d": "ACCEPT", "1d-strict": "ACCEPT", "none": "ACCEPT"}}
    got = {"P1": {}, "P2": {}}
    for pn, st in (("P1", "p1"), ("P2", "p2")):
        for mode in ("3d", "3d-noT", "3d-noS", "3d-noW", "1d", "1d-strict", "none"):
            vout = work / "verdicts" / f"{pn}-{mode}.json"
            r = sh(HERE / "gate_decide.py", "--config", work / "config.yaml", "--proposal", pn, "--attempt", 1, "--round", 1,
                   "--inner-base", rd[("h_i", "Di")], "--inner-cand", rd[(st, "Di")],
                   "--outer-pair", "default", rd[("h_i", "B")], rd[(st, "B")],
                   "--gate-mode", mode, "--delta", 0.15, "--ledger", ledger, "--verdict-out", vout, "--holdout-file", hold)
            v = json.loads(vout.read_text())
            got[pn][mode] = v["decision"]
            if mode == "3d":
                print(v["report"])
    bad = [(pn, m, got[pn][m], expect[pn][m]) for pn in expect for m in expect[pn] if got[pn][m] != expect[pn][m]]
    print("\n裁决矩阵：")
    for pn in got:
        print(f"  {pn}: " + "  ".join(f"{m}={d}" for m, d in got[pn].items()))
    assert not bad, f"裁决与预期不符: {bad}"
    v3d = json.loads((work / "verdicts" / "P1-3d.json").read_text())
    assert v3d["triggered"] == ["S"], v3d["triggered"]
    assert v3d["exec"] == "AUTO" and v3d["script_hash"].startswith("sha256:")

    # random：接受率与复现
    acc = []
    for n in range(200):
        vout = work / "verdicts" / f"R{n}.json"
        sh(HERE / "gate_decide.py", "--config", work / "config.yaml", "--proposal", f"R{n}", "--round", 1, "--gate-mode", "random",
           "--accept-rate", 0.6, "--seed", 42, "--verdict-out", vout)
        acc.append(json.loads(vout.read_text())["decision"] == "ACCEPT")
    rate = sum(acc) / len(acc)
    vout2 = work / "verdicts" / "R0-again.json"
    sh(HERE / "gate_decide.py", "--config", work / "config.yaml", "--proposal", "R0", "--round", 1, "--gate-mode", "random", "--accept-rate", 0.6, "--seed", 42, "--verdict-out", vout2)
    assert json.loads(vout2.read_text())["decision"] == json.loads((work / "verdicts" / "R0.json").read_text())["decision"], "random 不可复现"
    print(f"\nrandom 模式：200 个提案经验接受率 {rate:.3f}（目标 0.6 ± 0.08）")
    assert abs(rate - 0.6) <= 0.08, rate

    # 台账
    led = ledger.read_text()
    n_rows = sum(1 for l in led.splitlines() if l.startswith("| 1 |"))
    assert n_rows >= 14, n_rows
    assert "| 1 | 1.yaml | 24 |" in led
    print(f"台账：读数行 {n_rows} 条，留出集记录 1 条 → {ledger}")
    print(f"\n✓ 冒烟全部通过（workdir {work}）")
    if not a.keep and not a.workdir:
        shutil.rmtree(work)


if __name__ == "__main__":
    main()
