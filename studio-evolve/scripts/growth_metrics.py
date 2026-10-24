#!/usr/bin/env python3
"""growth_metrics.py —— 三维读数（长高 T · 长壮 S · 长胖 W）与成本 C 的确定性计算。

算法原文：references/growth-metrics.md。本脚本只实现「可从案例执行结果与本体文件上算出」的量，不跑引擎。

用法
  growth_metrics.py compute --results results.jsonl --state h_prime [--cases-file ids.yaml | --case-ids a,b]
                            [--ir ontology-map.md] [--elem-counts counts.json] [--theta-tier 0.8] --out readings.json
  growth_metrics.py runner-spec        # 打印结果行（results.jsonl）的字段约定

结果行约定（由案例运行器产出，一行 = 一次执行）：
  {"state": "h_i"|"h_prime"|<任意本体状态名>, "case": "<case_id>", "variant": null|"<variant kind-n>",
   "run": 0, "pass": 0|1, "tokens": 1234, "steps": 5, "wall_s": 2.1,
   "tier": 1-4, "family": "<场景类别>", "origin": "real"|"sim", "observed_at": "YYYY-MM-DD"}
  pass 的判定由引擎对照 case.expect 得出（growth-metrics.md 开头的 pass(h, d)），脚本不复判。

运行器接口：RESULTS_RUNNERS 里注册 name -> callable(path) -> rows。jsonl = 读预先录好的结果；
onto-engine = 读 runner_onto_engine.py 产出的文件（案例 → clife-onto-engine 路由 + 预演执行）。pass=null 的行（路由不可用 / 引擎报错）不进通过率，计入 n_unscored 与 flags。

读数定义（对应 growth-metrics.md 的节）
  T_k      = Σ pass / 条数，按 tier 分层，只算原题（variant 为空）                          「长高」
  T        = max{k : T_k ≥ θ_tier}（只用于报告）
  S_consist= 平均_{d}[ 原题与全部变体的 pass 一致 ]（有变体的案例才算）                      「长壮」分量 1
  S_stable = 1 − 跨 run 的总通过率标准差（run 数 ≥ 2 才算，否则 1.0 并标 SINGLE_RUN）        「长壮」分量 3
  S_holdout 需要上一状态在同一留出集上的 A，由 gate_decide.py 在拿到两份读数后算            「长壮」分量 2
  A        = 原题总通过率（S_holdout 与一维闸门用）
  W_evid   = IR §8 Evidence 里 verified 的条目 / 应有证据条目（§1 Object + §6 Mapping + §4 function-backed Rule）「长胖」比率 1
             --ir 给了才算；无 §8 → None + NO_EVIDENCE_TABLE（分母仍报在 W_evid_n）；不给 --ir → W_EVID_NOT_COMPUTED
  W_family = 至少有一条 tier≥2 原题通过的 family 数 / 结果里出现的 family 数                「长胖」比率 2
  W_elem   = --elem-counts 给的分项计数（只报告不进闸门）
  C        = 平均每案例的 (steps, tokens, wall_s)；闸门用 tokens，没有 tokens 时用 steps
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_yaml, read_jsonl, scripts_hash, write_json  # noqa: E402

# ---------------------------------------------------------------- runners
def runner_jsonl(path: Path) -> list[dict]:
    return read_jsonl(path)


def runner_onto_engine(path: Path) -> list[dict]:
    """results.jsonl 由 runner_onto_engine.py run 产出（同一行格式，多了 q / expect / actual / kind / flags）。"""
    return read_jsonl(path)


RESULTS_RUNNERS = {"jsonl": runner_jsonl, "onto-engine": runner_onto_engine}


# ---------------------------------------------------------------- metrics
def select_rows(rows: list[dict], state: str, case_ids: set[str] | None) -> list[dict]:
    out = [r for r in rows if r.get("state") == state]
    if case_ids is not None:
        out = [r for r in out if r.get("case") in case_ids]
    return out


def _md_tables(seg: str) -> list[tuple[list[str], list[list[str]]]]:
    """段落里的全部 markdown 表：[(header_cells, [row_cells, …]), …]。"""
    tables, header, rows = [], None, []
    for line in seg.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if header is not None:
                tables.append((header, rows)); header, rows = None, []
            continue
        if set(s) <= set("|-: "):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
        else:
            rows.append(cells)
    if header is not None:
        tables.append((header, rows))
    return tables


def _ir_section(text: str, num: int, title_re: str = "") -> str | None:
    m = re.search(rf"^##\s*{num}\.?\s*{title_re}.*?$", text, re.M)
    if not m:
        return None
    seg = text[m.end():]
    nxt = re.search(r"^##\s", seg, re.M)
    return seg[: nxt.start()] if nxt else seg


def evidence_expected(text: str) -> dict[str, set[str]]:
    """应有证据的条目（growth-metrics.md W_evid 分母）：§1 Object、§6 Mapping（按 object 名）、§4 backing=function 的 Rule。
    返回 {"OBJECT": {...}, "MAPPING": {...}, "RULE": {...}}；某段找不到对应表 → 该 kind 为空集。"""
    out: dict[str, set[str]] = {"OBJECT": set(), "MAPPING": set(), "RULE": set()}
    for num, kind, head0, pick in ((1, "OBJECT", "name", None), (6, "MAPPING", "object", None), (4, "RULE", "name", "function")):
        seg = _ir_section(text, num)
        if seg is None:
            continue
        for header, rows in _md_tables(seg):
            if not header or header[0] != head0:
                continue
            if kind == "RULE":
                if "backing" not in header:
                    continue
                bi = header.index("backing")
                out[kind] |= {r[0] for r in rows if len(r) > bi and r[bi].lower().startswith(pick) and not r[0].startswith("{{")}
            else:
                out[kind] |= {r[0] for r in rows if r and r[0] and not r[0].startswith("{{")}
    return out


def evidence_rate(ir_path: Path) -> dict:
    """W_evid = §8 Evidence 里 status=verified 且属于应有证据集合的条目数 / 应有证据条目数（Object + Mapping + function-backed Rule）。

    返回 {"W_evid", "verified", "expected", "table_rows", "by_kind", "denominator", "flags"}：
      - 没有 §8 表 → W_evid None，flags 含 NO_EVIDENCE_TABLE（分母照常报告，知道「应有多少」）；
      - 有 §8 但 §1/§4/§6 都解析不到（如只写了 §8 的测试 IR）→ 退回「verified 行 / 表行数」，flags 含 W_EVID_TABLE_DENOM；
      - §8 里 kind 不在 OBJECT / MAPPING / RULE 的行（如 LINK）只计 table_rows，不进分子分母。
    同一 (kind, element) 多行只算一次，任一行 verified 即 verified。"""
    text = ir_path.read_text(encoding="utf-8")
    expected = evidence_expected(text)
    n_expected = sum(len(v) for v in expected.values())
    res = {"W_evid": None, "verified": 0, "expected": n_expected, "table_rows": 0,
           "by_kind": {k: {"expected": len(v), "verified": 0} for k, v in expected.items()},
           "denominator": "structural", "flags": []}
    seg = _ir_section(text, 8, "Evidence")
    if seg is None:
        res["flags"].append("NO_EVIDENCE_TABLE")
        return res
    status: dict[tuple[str, str], bool] = {}
    for header, rows in _md_tables(seg):
        if not header or header[0] not in ("element", "{{element}}"):
            continue
        ki = header.index("kind") if "kind" in header else 1
        si = header.index("status") if "status" in header else 4
        for r in rows:
            if len(r) <= max(ki, si) or r[0].startswith("{{"):
                continue
            res["table_rows"] += 1
            key = (r[ki].upper(), r[0])
            status[key] = status.get(key, False) or r[si].lower().startswith("verified")
    if n_expected == 0:
        res["denominator"] = "table_rows"; res["flags"].append("W_EVID_TABLE_DENOM")
        res["verified"] = sum(1 for v in status.values() if v)
        res["expected"] = len(status)
        res["W_evid"] = (res["verified"] / len(status)) if status else None
        return res
    for (kind, elem), ok in status.items():
        if ok and kind in expected and elem in expected[kind]:
            res["verified"] += 1; res["by_kind"][kind]["verified"] += 1
    res["W_evid"] = res["verified"] / n_expected
    return res


def compute(rows: list[dict], theta_tier: float = 0.8) -> dict:
    flags = []
    unscored = [r for r in rows if r.get("pass") is None]
    if unscored:
        kinds = sorted({f.split(":")[0] for r in unscored for f in (r.get("flags") or ["UNSCORED"])})
        flags.append(f"UNSCORED:{len(unscored)}:{'/'.join(kinds)}")
        rows = [r for r in rows if r.get("pass") is not None]   # 跑不起来的行不进任何通过率
    orig = [r for r in rows if not r.get("variant")]
    # 长高 T_k
    by_tier: dict[int, list[int]] = defaultdict(list)
    for r in orig:
        by_tier[int(r.get("tier", 0))].append(int(r.get("pass", 0)))
    T = {}
    n_tier = {}
    for k in (1, 2, 3, 4):
        v = by_tier.get(k, [])
        n_tier[k] = len(v)
        T[k] = (sum(v) / len(v)) if v else None
    T_max = max([k for k in (1, 2, 3, 4) if T[k] is not None and T[k] >= theta_tier], default=0)
    # A
    A = (sum(int(r.get("pass", 0)) for r in orig) / len(orig)) if orig else None
    # S_consist：按 (case, run) 聚合原题与变体
    per_case: dict[tuple, dict] = defaultdict(lambda: {"orig": None, "vars": []})
    for r in rows:
        key = (r.get("case"), r.get("run", 0))
        if r.get("variant"):
            per_case[key]["vars"].append(int(r.get("pass", 0)))
        else:
            per_case[key]["orig"] = int(r.get("pass", 0))
    consist = [1 if all(v == d["orig"] for v in d["vars"]) else 0
               for d in per_case.values() if d["orig"] is not None and d["vars"]]
    S_consist = (sum(consist) / len(consist)) if consist else None
    if S_consist is None:
        flags.append("NO_VARIANTS")
    # S_stable：跨 run 的 A 标准差
    by_run: dict[int, list[int]] = defaultdict(list)
    for r in orig:
        by_run[int(r.get("run", 0))].append(int(r.get("pass", 0)))
    run_rates = [sum(v) / len(v) for v in by_run.values() if v]
    if len(run_rates) >= 2:
        S_stable = 1 - statistics.pstdev(run_rates)
    else:
        S_stable = 1.0
        flags.append("SINGLE_RUN")
    # W_family
    fams = {r.get("family") for r in orig if r.get("family")}
    fam_ok = {r.get("family") for r in orig if int(r.get("tier", 0)) >= 2 and int(r.get("pass", 0)) == 1}
    W_family = (len(fam_ok & fams) / len(fams)) if fams else None
    # C
    def mean(key):
        v = [float(r[key]) for r in orig if r.get(key) is not None]
        return (sum(v) / len(v)) if v else None
    C = {"steps": mean("steps"), "tokens": mean("tokens"), "wall_s": mean("wall_s")}
    C_gate = C["tokens"] if C["tokens"] is not None else C["steps"]
    if C["tokens"] is None:
        flags.append("COST_BY_STEPS")
    origins = defaultdict(int)
    for cid, og in {r.get("case"): r.get("origin", "?") for r in orig}.items():
        origins[og] += 1
    case_pass: dict[str, list[int]] = defaultdict(list)   # 逐案例原题通过率（arm_compare 的 regress_any 用：h_i 全过、h' 有一次不过即单条回退）
    for r in orig:
        case_pass[str(r.get("case"))].append(int(r.get("pass") or 0))
    return {
        "n_cases": len({r.get("case") for r in orig}), "n_rows": len(rows), "n_tier": n_tier, "origins": dict(origins),
        "case_pass": {k: round(sum(v) / len(v), 4) for k, v in sorted(case_pass.items())},
        "T": {f"T_{k}": T[k] for k in (1, 2, 3, 4)}, "T_max": T_max, "A": A,
        "S_consist": S_consist, "S_stable": S_stable, "W_family": W_family, "C": C, "C_gate": C_gate,
        "runs": len(by_run), "n_unscored": len(unscored), "flags": flags,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="三维读数计算（growth-metrics.md）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compute", help="从结果行算一份读数")
    c.add_argument("--results", required=True, type=Path, help="结果文件（runner 产出）")
    c.add_argument("--runner", default="jsonl", choices=sorted(RESULTS_RUNNERS))
    c.add_argument("--state", required=True, help="取哪个本体状态的行（如 h_i / h_prime）")
    c.add_argument("--cases-file", type=Path, help="限定案例集：yaml，含 ids: [...]（holdout_sample.py 的输出）或顶层列表")
    c.add_argument("--case-ids", help="限定案例集：逗号分隔 id")
    c.add_argument("--ir", type=Path, help="ontology-map.md，算 W_evid（§8 Evidence 表）")
    c.add_argument("--elem-counts", type=Path, help="W_elem 分项计数 json（只报告）")
    c.add_argument("--theta-tier", type=float, default=0.8)
    c.add_argument("--out", required=True, type=Path)
    sub.add_parser("runner-spec", help="打印结果行字段约定")
    a = ap.parse_args()

    if a.cmd == "runner-spec":
        print(__doc__.split("结果行约定", 1)[1].split("读数定义")[0])
        return

    rows = RESULTS_RUNNERS[a.runner](a.results)
    ids = None
    if a.cases_file:
        y = load_yaml(a.cases_file)
        ids = set(map(str, y.get("ids", y) if isinstance(y, dict) else y))
    if a.case_ids:
        ids = (ids or set()) | set(x.strip() for x in a.case_ids.split(",") if x.strip())
    sel = select_rows(rows, a.state, ids)
    if not sel:
        sys.exit(f"结果里没有 state={a.state!r} 的行" + (f"（限定 {len(ids)} 个案例）" if ids else ""))
    missing = (ids or set()) - {r.get("case") for r in sel}
    reading = compute(sel, a.theta_tier)
    reading["state"] = a.state
    reading["missing_cases"] = sorted(missing)
    if missing:
        reading["flags"].append(f"MISSING_RESULTS:{len(missing)}")
    if a.ir:
        ev = evidence_rate(a.ir)
        reading["W_evid"] = ev["W_evid"]
        reading["W_evid_n"] = {k: ev[k] for k in ("verified", "expected", "table_rows", "by_kind", "denominator")}
        reading["evidence_rows"] = {"verified": ev["verified"], "total": ev["table_rows"]}   # 旧字段，向后兼容
        reading["flags"].extend(ev["flags"])
    else:
        reading["W_evid"] = None; reading["flags"].append("W_EVID_NOT_COMPUTED")
    if a.elem_counts:
        import json
        reading["W_elem"] = json.loads(a.elem_counts.read_text())
    reading["script_hash"] = scripts_hash()
    reading["exec"] = "AUTO"
    write_json(a.out, reading)
    T = reading["T"]
    _f = lambda x: "—" if x is None else f"{x:.3f}"
    print(f"✓ 读数 {a.state}：{reading['n_cases']} 案例 / {reading['n_rows']} 行 · "
          f"T=({','.join('—' if T[k] is None else f'{T[k]:.2f}' for k in T)}) A={_f(reading['A'])} "
          f"S_consist={'—' if reading['S_consist'] is None else f'{reading['S_consist']:.3f}'} "
          f"S_stable={_f(reading['S_stable'])} W_evid={'—' if reading['W_evid'] is None else f'{reading['W_evid']:.3f}'} "
          f"W_family={'—' if reading['W_family'] is None else f'{reading['W_family']:.3f}'} C={reading['C_gate']}"
          + (f"  flags={reading['flags']}" if reading["flags"] else ""))
    print(f"  → {a.out}")


if __name__ == "__main__":
    main()
