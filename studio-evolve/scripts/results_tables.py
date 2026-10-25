#!/usr/bin/env python3
"""results_tables.py —— 把 arm_compare / chain_drive 的产物直接排成论文 §5 的 T1–T5（markdown），formal run 冻结后数字不手抄。

  results_tables.py --arms-json .studio-evolve/x/arms/r1-6/arms.json --proposals .studio-evolve/x/proposals.yaml \
      [--chains-dir .studio-evolve/x/chains] [--holdouts sets/holdout-2.yaml,...] [--window-sets sets/real-W44.yaml,...] \
      [--cases-dir .studio-evolve/x/cases] --out-dir draft/tables [--formal]

输入
  arms.json      arm_compare.py 的输出：arms / diffs（带 ci95）/ divergent / reject_share_3d / proposals（逐条：各臂接受、regress、regress_any、regress_basis）
  proposals.yaml {proposals: {num: {id, kind: root|naive|impl, round, window, merged, pair?, rule?}}}；kind 决定 T5 的入口行与 T2 的「其中朴素修复」
  chains/*/chain.json  chain_drive.py 的链产物（T3：末轮读数、APPLY_FAIL = n_conflict）；缺省跳过 T3
  --holdouts / --window-sets  留出集文件与各窗口 real 集（ids 列表）：T5 的窗口来源分布；不在任一窗口集里的 real 案例记「W43（推断）」

判定列：按 criteria v3 的阈值算（H-1a：diff ≥ 0.10 且 CI 下界 > 0；H-1b：分歧集回退 ≥ 0.60 / < 0.20；H-3a：消融差 ≥ 0.05 且 CI 下界 > 0；
  H-2a/b：CI 下界 > −0.10），但**没有 --formal 时一律写「探索性，不判定」并在表头标注**——数字可以先看，判定要等 results gate。
不算的量（A_holdout 两列、regress 的 B_{i+1} 口径是否满足）原样留「—」并在脚注说明。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_yaml  # noqa: E402

ARMS = ["3d", "1d", "1d-strict", "random", "none", "3d-noT", "3d-noS", "3d-noW"]
F = lambda x, d=3: "—" if x is None else f"{x:.{d}f}"
CI = lambda c: "—" if not c else f"[{c[0]:+.3f}, {c[1]:+.3f}]"


def judge(ok: bool | None, text: str, formal: bool) -> str:
    if not formal:
        return f"探索性，不判定（{text}）"
    return ("✓ " if ok else "✗ ") + text if ok is not None else f"无法判定（{text}）"


def t1(arms: dict, formal: bool) -> str:
    A, D = arms["arms"], arms["diffs"]
    L = ["**T1 各臂接受率与回退率。**", "", "| 臂 | accept_rate | n_accept / n_judged | regress_rate（净）| regress_any_rate（单条）| n_any_known | $A_{holdout}$ 全集 | $A_{holdout}$ 本体口径 |", "|---|---|---|---|---|---|---|---|"]
    for m in ARMS:
        if m in A:
            a = A[m]
            L.append(f"| {m} | {F(a['accept_rate'])} | {a['n_accept']} / {a.get('n_judged', '—')} | {F(a['regress_rate'])} | {F(a['regress_any_rate'])} | {a['n_any_known']} | — | — |")
    L += ["", "| 差值 | 点估计 | 95% CI | 判定 |", "|---|---|---|---|"]
    d = D.get("regress_rate_diff", {})
    ok = d.get("value") is not None and d["value"] >= 0.10 and d.get("ci95") and d["ci95"][0] > 0
    L.append(f"| regress_rate_diff（1d − 3d） | {F(d.get('value'))} | {CI(d.get('ci95'))} | {judge(ok, 'H-1a：≥ 0.10 且 CI 下界 > 0', formal)} |")
    for k, lab in (("random_minus_3d", "random − 3d"), ("1d-strict_minus_3d", "1d-strict − 3d")):
        d = D.get(k, {})
        L.append(f"| {lab} | {F(d.get('value'))} | {CI(d.get('ci95'))} | 排除「只因为拒得多」 |")
    d = D.get("regress_any_diff", {})
    L.append(f"| regress_any_diff（1d − 3d，并排） | {F(d.get('value'))} | {CI(d.get('ci95'))} | 不判定 |")
    basis = Counter(("B_next" if str(p.get("regress_basis", "")).startswith("B_next") else p.get("regress_basis")) for p in arms.get("proposals", []))
    x3 = any(str(p.get("regress_basis", "")) == "B_next_x3" for p in arms.get("proposals", []))
    L += ["", f"回退口径：B_next {basis.get('B_next', 0)} 条{'（每轮三次重抽，按（提案, 重抽）对合并）' if x3 else ''} / B_same {basis.get('B_same', 0)} 条（协议要求 B_{{i+1}}；B_same 的行记 REGRESS_ON_SAME_B）。flags：{', '.join(arms.get('flags', [])) or '无'}。$A_{{holdout}}$ 两列由 formal run 的留出集读数填，本脚本不算。"]
    return "\n".join(L)


def t2(arms: dict, props: dict, formal: bool) -> str:
    dv = arms["divergent"]
    L = ["**T2 分歧集。**", "", "| 分歧集 | 条数 | 回退比例（净）| 回退比例（单条）| 其中朴素修复 | 提案 | 判定 |", "|---|---|---|---|---|---|---|"]
    for k, lab, rule, test in (("1d_accept_3d_reject", "1d 接受 ∧ 3d 拒绝", "H-1b：≥ 0.60", lambda r: r is not None and r >= 0.60),
                               ("3d_accept_1d_reject", "3d 接受 ∧ 1d 拒绝", "H-1b：< 0.20", lambda r: r is not None and r < 0.20)):
        s = dv[k]; ids = s.get("proposals", [])
        uniq = sorted(set(int(n) for n in ids), key=lambda n: ids.index(n) if n in ids else ids.index(str(n)))
        naive = sum(1 for n in uniq if props.get(n, {}).get("kind") == "naive")
        names = ", ".join(props.get(n, {}).get("id", f"P{n}") + (f"×{ids.count(n) or ids.count(str(n))}" if (ids.count(n) or ids.count(str(n))) > 1 else "") for n in uniq) or "∅"
        L.append(f"| {lab} | {s['n']}（{len(uniq)} 条提案） | {F(s.get('regress_rate'))} | {F(s.get('regress_any_rate'))} | {naive} | {names} | {judge(test(s.get('regress_rate')) if s['n'] else None, rule, formal)} |")
    return "\n".join(L)


def t3(chains_dir: Path | None, formal: bool) -> str:
    if not chains_dir or not chains_dir.exists():
        return "**T3 末轮能力。** 未提供 chains 目录，跳过。"
    rows = []
    for cj in sorted(chains_dir.glob("*/chain.json")):
        c = json.loads(cj.read_text(encoding="utf-8"))
        fin = c.get("final") or {}
        for set_name, rd in (fin.items() if isinstance(fin, dict) else []):
            if not isinstance(rd, dict):
                continue
            T = rd.get("T") or {}
            rows.append((c.get("gate_mode", cj.parent.name), set_name, T, rd.get("A"), c.get("n_conflict"), c.get("n_judged"), len(c.get("accepted") or [])))
    L = ["**T3 末轮能力（P-B 链）。** " + ("" if formal else "重放流近似：提案全部来自 3d 链的归因，各臂没有自己的归因；只给 F1 试运行曲线。"), "",
         "| 臂 | 评测集 | $T_1$ | $T_2$ | $T_3$ | $T_4$ | $A$ | 判 / 接受 / APPLY_FAIL |", "|---|---|---|---|---|---|---|---|"]
    for m, s, T, A, nc, nj, na in sorted(rows, key=lambda r: (ARMS.index(r[0]) if r[0] in ARMS else 99, r[1])):
        L.append(f"| {m} | {Path(s).name} | {F(T.get('T_1'), 2)} | {F(T.get('T_2'), 2)} | {F(T.get('T_3'), 2)} | {F(T.get('T_4'), 2)} | {F(A)} | {nj} / {na} / {nc} |")
    L += ["", "T_tier3_gain 与差值的 CI 需三次留出集重抽（协议），本脚本不算；H-2a/H-2b " + ("按 CI 下界 > −0.10 判。" if formal else "不判定。")]
    return "\n".join(L)


def t4(arms: dict, formal: bool) -> str:
    A, D = arms["arms"], arms["diffs"]
    L = ["**T4 消融与拒绝归属。**", "", "| 消融臂 | regress_rate | accept_rate | ablation_diff（消融 − 完整）| 95% CI | 判定 |", "|---|---|---|---|---|---|"]
    for m in ("3d-noT", "3d-noS", "3d-noW"):
        d = D.get(f"{m}_minus_3d", {}); a = A.get(m, {})
        ok = d.get("value") is not None and d["value"] >= 0.05 and d.get("ci95") and d["ci95"][0] > 0
        L.append(f"| {m} | {F(a.get('regress_rate'))} | {F(a.get('accept_rate'))} | {F(d.get('value'))} | {CI(d.get('ci95'))} | {judge(ok, '≥ 0.05 且 CI 下界 > 0', formal)} |")
    sh = arms.get("reject_share_3d", {})
    L += ["", f"**完整臂拒绝的触发维度份额**（首个不满足的判定式归属，共拒 {sh.get('n_rejected', 0)} 条）。", "", "| 维度 | 首个归属 |", "|---|---|"]
    for d, lab in (("T", "T 回退"), ("S", "S 回退"), ("W", "W 回退"), ("C", "C 超带宽"), ("strict", "无严格改善（零增长）"), ("outer", "外关卡不过"), ("unscored", "Dᵢ 不可评分"), ("unknown", "unknown")):
        L.append(f"| {lab} | {sh.get(d, 0)} |")
    L += ["", "「无严格改善」是 v3 strict 项（∃ΔT_k>0 ∨ ΔS>0 ∨ ΔW>0）不成立，不是某一维回退——朴素修多数死在这里。"]
    return "\n".join(L)


def t5(arms: dict, props: dict, holdouts: list[Path], window_sets: list[Path], cases_dir: Path | None) -> str:
    P = arms.get("proposals", [])
    kinds = {"root": "(i) 反馈归因 · 根因修复", "naive": "(i) 反馈归因 · 朴素修复", "impl": "(ii) 路由 error 行（构造性，不计分歧集）"}
    L = ["**T5 提案入口、朴素臂与窗口来源。**", "", "| 入口 / 类型 | 提案数 | 3d 接受 | 1d 接受 | 回退（净 / 单条；三次重抽时为三份均值之和）| 已合入 |", "|---|---|---|---|---|---|"]
    # 同一提案多次 attempt 只取最后一次
    last = {}
    for p in P:
        last[p["proposal"]] = p
    for k, lab in kinds.items():
        rows = [p for n, p in last.items() if props.get(int(n), {}).get("kind") == k]
        n3 = sum(1 for p in rows if p["accepted"].get("3d")); n1 = sum(1 for p in rows if p["accepted"].get("1d"))
        reg = sum(p["regress"] or 0 for p in rows); rga = sum(p["regress_any"] or 0 for p in rows if p["regress_any"] is not None)
        merged = sum(1 for n in last if props.get(int(n), {}).get("kind") == k and props[int(n)].get("merged"))
        fm = lambda v: f"{v:.2f}" if isinstance(v, float) and not float(v).is_integer() else str(int(v)) if isinstance(v, float) else str(v)
        L.append(f"| {lab} | {len(rows)} | {n3} | {n1} | {fm(reg)} / {fm(rga)} | {merged} |")
    unreg = [n for n in last if int(n) not in props]
    if unreg:
        L.append(f"| 未登记（proposals.yaml 缺） | {len(unreg)} | — | — | — | — |")
    if holdouts:
        wmap = {}
        for ws in window_sets:
            y = load_yaml(ws); ids = y.get("ids", y) if isinstance(y, dict) else y
            for i in ids:
                wmap[str(i)] = ws.stem.replace("real-", "")
        wins = sorted({w for w in wmap.values()})
        L += ["", "窗口来源分布（替代 time_half 分层）：", "", "| 留出集 | n | " + " | ".join(["W43（推断）"] + wins) + " |", "|---|---|" + "---|" * (len(wins) + 1)]
        for h in holdouts:
            y = load_yaml(h); ids = [str(i) for i in (y.get("ids", y) if isinstance(y, dict) else y)]
            c = Counter(wmap.get(i, "W43（推断）") for i in ids)
            L.append(f"| {h.name} | {len(ids)} | " + " | ".join(str(c.get(w, 0)) for w in ["W43（推断）"] + wins) + " |")
        L.append("「W43（推断）」= real 案例不在任一窗口集里（W43 没有单独的集文件）。")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms-json", required=True, type=Path)
    ap.add_argument("--proposals", required=True, type=Path)
    ap.add_argument("--chains-dir", type=Path)
    ap.add_argument("--holdouts", help="逗号分隔的留出集文件")
    ap.add_argument("--window-sets", help="逗号分隔的各窗口 real 集（real-W44.yaml …）")
    ap.add_argument("--cases-dir", type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--formal", action="store_true", help="results gate 通过后才加：判定列按阈值真判")
    a = ap.parse_args()
    arms = json.loads(a.arms_json.read_text(encoding="utf-8"))
    props = {int(k): v for k, v in (load_yaml(a.proposals).get("proposals") or {}).items()}
    holdouts = [Path(x) for x in a.holdouts.split(",")] if a.holdouts else []
    wsets = [Path(x) for x in a.window_sets.split(",")] if a.window_sets else []
    head = "" if a.formal else "> **探索性（results gate 未通过）**：数字来自试运行台账，判定列不判。formal 冻结后以 `--formal` 重生成。\n\n"
    head += f"来源：`{a.arms_json}`（{arms.get('n_proposals')} 条提案，轮次 {arms.get('rounds')}，n = {arms.get('n')}，boot {arms.get('boot')} / seed {arms.get('seed')}）。\n\n"
    tables = {"T1": t1(arms, a.formal), "T2": t2(arms, props, a.formal), "T3": t3(a.chains_dir, a.formal), "T4": t4(arms, a.formal),
              "T5": t5(arms, props, holdouts, wsets, a.cases_dir)}
    a.out_dir.mkdir(parents=True, exist_ok=True)
    for k, v in tables.items():
        (a.out_dir / f"{k}.md").write_text(head + v + "\n", encoding="utf-8")
    (a.out_dir / "ALL.md").write_text(head + "\n\n".join(tables.values()) + "\n", encoding="utf-8")
    print(f"✓ T1–T5 → {a.out_dir}（{'formal' if a.formal else '探索性'}）")


if __name__ == "__main__":
    main()
