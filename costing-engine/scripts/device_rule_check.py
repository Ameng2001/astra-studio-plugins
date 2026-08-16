"""device_rule_check —— 设备 BOM 第一层的「规则 vs 现状」偏差表。

这张表是共创的**输入**，不是结论。它回答的是：

  · 哪些配置口径还没有（163/199 个位点没规则）
  · 已有的口径和各园实际数量对不对得上
  · 从口径反推出来的园所规模参数（班级数/教室数/幼儿数）自不自洽
  · 哪些行看起来是重复计列

## 为什么反推规模参数有意义

`per_class qty=1` 的设备，某园配了 18 台，就意味着该园 18 个班。
同一个园里多台 per_class 设备反推出来的班级数**应该一致** —— 不一致就说明
要么数量填错了，要么口径不是 per_class。这个检查不需要任何外部数据，
纯靠内部一致性，所以现在就能做，不必等业务侧给园所规模。

## 这张表不做判决

偏差 ≠ 错。宝骏城园的网络音箱配 3 个而规则说每园 1 个，可能是真有 3 个门。
所以这里只列「对不上」，不改数、不猜原因 —— 判决是业务侧的事，
而判决完要留痕（跟逻辑文件的单一维护者裁决同一个形状）。

用法：
    python3 device_rule_check.py --root <项目根> [--out <md路径>]
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


def cell(v: Any, n: int = 44) -> str:
    """表格单元：换行会把 markdown 表格从中间劈开，管道符会多切一列。"""
    t = " ".join(str(v or "").split()).replace("|", "／")
    return (t[:n] + "…") if len(t) > n else (t or "—")


#: 200 所批次不是一个园，是批量 —— 规则按「每园」算的话要乘 200，
#: 而实际数量并非严格 200 倍（AC管理器各园 1、批次 201）。单列，不混算。
BATCH = "06_200所基础园配置"

#: 反推规模参数：口径 → 该口径反推的是什么量纲
SCALE_OF = {"per_class": "班级数", "per_room": "教室数", "per_child": "在园幼儿数"}


def load(root: Path) -> dict[str, Any]:
    d = root / "bom" / "devices"
    return {n: yaml.safe_load((d / f"{n}.yaml").read_text(encoding="utf-8"))
            for n in ("taxonomy", "materials", "catalog")}


def _rule_parts(rule: dict) -> list[dict]:
    if rule["kind"] == "composite":
        return rule["parts"]
    return [rule] if rule["kind"] in SCALE_OF or rule["kind"] in (
        "per_garden", "per_facility") else []


def check(root: Path) -> dict[str, Any]:
    D = load(root)
    cat, mats = D["catalog"]["entries"], D["materials"]["materials"]
    tax = D["taxonomy"]["scenes"]
    gardens = sorted({g for e in cat for g in e["gardens"]})
    single = [g for g in gardens if g != BATCH]

    # ---- A 场景层 ----
    used = {e["scene"] for e in cat}
    unreg = [s["scene"] for s in tax if s["origin"].startswith("**未登记**")]
    unused = [s["scene"] for s in tax if s["scene"] not in used
              and not s["origin"].startswith("**未登记**")]
    unscened = [e for e in cat if e["scene"].startswith("未归类")]

    # ---- B 物料层 ----
    m_by = {m["code"]: m for m in mats}
    pending = [m for m in mats if m["price_status"].startswith("**")]
    conflict = [m for m in mats if "conflict" in m]
    short_ref = [m for m in mats
                 if len(m["ref_vendors"]) + (1 if m["model"] and not
                                             m["model"].startswith("**") else 0) < 3]

    # ---- C 口径层 ----
    plc = [(e, p) for e in cat for p in e["placements"]]
    kinds = Counter(p["rule"]["kind"] for _, p in plc)
    reasons = Counter(p["rule"].get("reason", "") for _, p in plc
                      if p["rule"]["kind"] == "unparsed")
    partial = [(e, p) for e, p in plc
               if p["rule"]["kind"] not in ("unparsed",) and not p["rule"].get("covered")]

    # ---- D per_garden 自洽性 ----
    pg = []
    for e, p in plc:
        parts = [x for x in _rule_parts(p["rule"]) if x["kind"] == "per_garden"]
        if not parts:
            continue
        n = parts[0]["qty"]
        bad = {g: q for g, q in p["qty_by_garden"].items()
               if g != BATCH and q != n}
        # 批次量从**条目**层取：200 所那张表的备注常与单园不同，
        # 于是落在另一个位点里，按位点取会永远是空。
        batch = e["gardens"].get(BATCH)
        pg.append({"e": e, "p": p, "n": n, "bad": bad, "batch": batch})

    # ---- E 反推园所规模参数 ----
    implied: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for e, p in plc:
        for part in _rule_parts(p["rule"]):
            dim = SCALE_OF.get(part["kind"])
            if not dim or not part.get("qty"):
                continue
            for g, q in p["qty_by_garden"].items():
                if g == BATCH or not q:
                    continue
                if q % part["qty"] == 0:
                    implied[g][dim].append((q // part["qty"], e["name"], p["note"]))

    # ---- F 疑似重复计列 ----
    dup = []
    for e in cat:
        notes = [p["note"] for p in e["placements"]]
        if len(notes) > 1 and len(set(n for n in notes if n)) < len([n for n in notes if n]):
            dup.append(e)
        blank = [p for p in e["placements"] if not p["note"]]
        if len(e["placements"]) > 1 and len(blank) >= 1 and len(set(notes)) < len(notes):
            pass
    # 同一条目里出现两个位点、备注完全相同（含都为空）→ 疑似同一配置写了两行
    dup = [e for e in cat
           if len(e["placements"]) > 1
           and len({p["note"] for p in e["placements"]}) < len(e["placements"])]
    # 备注都为空且多位点的，在 catalog 里已被合并成一条，故另查 gardens 维度
    return {"gardens": gardens, "single": single, "cat": cat, "mats": mats,
            "tax": tax, "unreg": unreg, "unused": unused, "unscened": unscened,
            "pending": pending, "conflict": conflict, "short_ref": short_ref,
            "kinds": kinds, "reasons": reasons, "partial": partial,
            "plc": plc, "pg": pg, "implied": implied, "dup": dup}


def render(r: dict[str, Any]) -> str:
    L: list[str] = []
    w = L.append
    n_plc = len(r["plc"])
    n_rule = n_plc - r["kinds"]["unparsed"]
    n_cov = sum(1 for _, p in r["plc"]
                if p["rule"]["kind"] != "unparsed" and p["rule"].get("covered"))
    w("# 设备 BOM 第一层 · 规则与现状偏差表")
    w("")
    w("由 `bom_build_devices.py` + `device_rule_check.py` 生成。"
      "**这是共创的输入，不是结论** —— 偏差 ≠ 错，判决是业务侧的事，判完要留痕。")
    w("")
    w("## 0. 规模一览")
    w("")
    w("| 项 | 数 |")
    w("|---|---:|")
    w(f"| 规范子场景（99 表声明） | {len(r['tax']) - len(r['unreg'])} |")
    w(f"| 物料 | {len(r['mats'])} |")
    w(f"| 目录条目（子场景 × 物料） | {len(r['cat'])} |")
    w(f"| 配置位点 | {n_plc} |")
    w(f"| ├ 已解析出配置口径 | {n_rule}（完整覆盖 {n_cov} / 部分覆盖 {n_rule - n_cov}） |")
    w(f"| └ **待补口径** | {r['kinds']['unparsed']} |")
    w("")

    w("## 1. 配置口径的缺口（最大的一块）")
    w("")
    w(f"{n_plc} 个位点里 **{r['kinds']['unparsed']} 个没有配置口径**，"
      f"占 {r['kinds']['unparsed'] / n_plc:.0%}。按缺的原因分：")
    w("")
    w("| 原因 | 位点数 | 说明 |")
    w("|---|---:|---|")
    EXP = {"备注为空": "源表该行备注就是空的，从来没写过口径",
           "过程痕迹/状态标记，非配置口径": "备注写的是「全量增补」「待补单价」这类，是过程痕迹不是口径",
           "文本未命中任何已登记口径": "写了话但不是已登记的句式，需人工判读后补进词表或改写"}
    for k, v in r["reasons"].most_common():
        w(f"| {k} | {v} | {EXP.get(k, '')} |")
    w("")
    w("已解析的口径分布：" + "、".join(
        f"`{k}` {v}" for k, v in r["kinds"].most_common() if k != "unparsed"))
    w("")

    if r["partial"]:
        w("### 1.1 部分覆盖：规则只解析出了一半")
        w("")
        w("这些位点的备注含多段口径，只解析出其中一段。**按它推数量会系统性偏少**，"
          "所以标出来而不是当成已解析。")
        w("")
        w("| 场景 | 设备 | 已解析 | 残余未解析 | 原文 |")
        w("|---|---|---|---|---|")
        for e, p in r["partial"]:
            rule = p["rule"]
            got = (rule["kind"] if rule["kind"] != "composite"
                   else "+".join(x["kind"] for x in rule["parts"]))
            w(f"| {cell(e['scene'],14)} | {cell(e['name'],20)} | `{got}` | "
              f"{cell(rule.get('unmatched'),30)} | {cell(p['note'],40)} |")
        w("")

    w("## 2. `per_garden`（每园固定量）规则 vs 实际数量")
    w("")
    bad = [x for x in r["pg"] if x["bad"]]
    w(f"共 {len(r['pg'])} 个位点声明了「每园 N 台」，其中 **{len(bad)} 个与实际数量对不上**。")
    w("")
    w("| 场景 | 设备 | 规则 | 对不上的园 | 200所批次 | 原文 |")
    w("|---|---|---:|---|---:|---|")
    for x in sorted(r["pg"], key=lambda y: -len(y["bad"])):
        b = "、".join(f"{g.split('_')[-1]}={q:g}" for g, q in sorted(x["bad"].items())) or "—"
        bt = f"{x['batch']:g}" if x["batch"] is not None else "—"
        flag = " ⚠" if x["bad"] else ""
        w(f"| {cell(x['e']['scene'],14)} | {cell(x['e']['name'],22)} | 每园 {x['n']}{flag} | "
              f"{cell(b,30)} | {bt} | {cell(x['p']['note'],30)} |")
    w("")
    w("> 200 所批次不是一个园，是批量。按「每园 N」推应为 200×N，"
      "但实际并非严格 200 倍（如 AC管理器各园 1、批次 201）—— "
      "**第二层要把它建模成 batch 而不是 garden**，否则规则一套上去全对不上，"
      "而那会看起来像规则错了。")
    w("")

    w("## 3. 反推的园所规模参数是否自洽")
    w("")
    w("`per_class` / `per_room` / `per_child` 的设备，用「实际数量 ÷ 口径数量」"
      "可以反推该园的班级数 / 教室数 / 幼儿数。同一个园、同一量纲、多台设备"
      "反推出来的值**应该一致**。这个检查不需要任何外部数据。")
    w("")
    w("| 园所 | 量纲 | 反推值 | 一致? | 依据设备 |")
    w("|---|---|---|---|---|")
    for g in r["single"]:
        for dim, lst in sorted(r["implied"].get(g, {}).items()):
            vals = sorted({v for v, _, _ in lst})
            ok = "✔" if len(vals) == 1 else "**✗ 不一致**"
            src = "、".join(f"{cell(nm,12)}→{v}" for v, nm, _ in lst[:4])
            w(f"| {g.split('_')[-1]} | {dim} | {'/'.join(str(v) for v in vals)} | {ok} | {src} |")
    w("")
    w("> 不一致不等于错 —— 也可能是口径判错了（比如「每班1台」其实是「每教室1台」，"
      "而一个班占两个教室）。这正是要业务侧裁决的地方。")
    w("")

    w("## 4. 疑似重复计列")
    w("")
    if r["dup"]:
        w(f"同一「场景 × 设备」下出现多个位点、而备注**完全相同**的 {len(r['dup'])} 组："
          "同一配置写了两行，还是两处不同用途？")
        w("")
        w("| 场景 | 设备 | 位点数 | 各位点数量 |")
        w("|---|---|---:|---|")
        for e in r["dup"]:
            q = "；".join("+".join(f"{g.split('_')[-1]}={v:g}"
                                   for g, v in sorted(p["qty_by_garden"].items()))
                          for p in e["placements"])
            w(f"| {cell(e['scene'],14)} | {cell(e['name'],22)} | {len(e['placements'])} | {cell(q,70)} |")
    else:
        w("未发现。")
    w("")

    w("## 5. 场景与物料的基础问题")
    w("")
    w(f"- **未归类条目**：{len(r['unscened'])} 个「场景 × 设备」的场景名是"
      f"「✓」或「基准补录」而非场景名，须补场景归属")
    if r["unused"]:
        w(f"- **声明了但没用到的场景** {len(r['unused'])} 个：{'、'.join(r['unused'])}"
          f" —— 是本次不建，还是漏配了？")
    if r["unreg"]:
        w(f"- **在用但 99 表未声明的场景** {len(r['unreg'])} 个：{'、'.join(r['unreg'])}")
    w(f"- **待核价物料** {len(r['pending'])} 个 —— 有设备有参数，缺价格依据")
    w(f"- **参考品牌型号不足 3 个** {len(r['short_ref'])}/{len(r['mats'])} 个物料"
      f"（表7 注3 要求）")
    if r["conflict"]:
        w(f"- **物料字段冲突** {len(r['conflict'])} 个：")
        for m in r["conflict"]:
            w(f"    - `{m['code']}` {m['name'][:20]}：{m['conflict']}")
    w("")
    w("---")
    w("")
    w("## 下一步")
    w("")
    w(f"1. 第 1 节的 {r['kinds']['unparsed']} 个空口径 —— "
      f"这是共创的主要工作量，按场景分给产品/方案。")
    w("2. 第 2、3 节的不一致 —— 逐条裁决：改数量、改口径，还是确认例外（例外要写理由）。")
    w("3. 第 5 节的场景归属与待核价 —— 已在送审件的复核意见里，此处只是同一批事的设备侧视角。")
    w("4. 规模参数（班级数/在园幼儿数/教室数）目前**哪儿都没有**，"
      "第 3 节是反推的。第二层建起来时要业务侧正式给一份 `garden-profile.yaml`。")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    r = check(a.root)
    out = a.out or (a.root / "docs" / "设备BOM规则偏差表.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(r), encoding="utf-8")
    n = len(r["plc"])
    print(f"偏差表 → {out}")
    print(f"  位点 {n}，待补口径 {r['kinds']['unparsed']}（{r['kinds']['unparsed']/n:.0%}）")
    print(f"  per_garden 规则 {len(r['pg'])} 条，对不上 {sum(1 for x in r['pg'] if x['bad'])} 条")
    inc = sum(1 for g, d in r["implied"].items() for dim, l in d.items()
              if len({v for v, _, _ in l}) > 1)
    print(f"  反推规模参数不一致 {inc} 处")
    print(f"  疑似重复计列 {len(r['dup'])} 组")


if __name__ == "__main__":
    main()
