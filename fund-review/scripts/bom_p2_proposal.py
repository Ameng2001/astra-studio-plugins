"""bom_p2_proposal — 依据标准识别规则测算 BOM 重拆方案，产出提案与裁决清单。

**只测算与提案，不改写 BOM。** 每项调整都标注：
  · 依据的标准条款
  · 置信度（高 = 标准条款可直接判定；中 = 规则推导需实体确认；低 = 需领域判断）
  · 方向（多算 → 减；漏算 → 增）

分四类调整：
  A 数据功能折叠   表6.4 / 表7.3 —— 同一逻辑文件的多种视图只计一次        方向：减
  B 数据功能完整性 表6.2 / 表7.1 —— 每个 ILF 至少 1 EI 且至少 1 EO/EQ   方向：增
  C 补齐缺失 ILF   表8.1 / 表8.3 —— 有 EI 必有其维护的 ILF              方向：增
  D 机械碎片合并   表8.6 反向    —— 同一基本处理被切成多行              方向：减
  E AI 资产重拆    表8.1「应用内唯一」—— 共享平台功能全局计一次，不逐个重复  方向：增

用法：
    python3 bom_p2_proposal.py --bom <dir> --out <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import nesma_classify as nc
from bom_schema import Bom, BomItem
from nesma_weights import ESTIMATED_WEIGHTS as W

#: 机械切分残片 —— 描述形如「支持」+短语，不足以描述一个基本处理
STUB = re.compile(r"支持?.{0,28}[。．]?$")

#: 表6.2 / 表7.1 要求的配套事务功能
ILF_COMPANIONS = [("EI", "表6.2 p.17 每个 ILF 至少包含一个外部输入"),
                  ("EQ", "表6.2 p.17 每个 ILF 至少包含一个外部输出或外部查询")]
ELF_COMPANIONS = [("EQ", "表7.1 p.18 一个 ELF 至少存在一个外部输出或一个外部查询")]

#: AI 资产每个实体独有的事务功能（共享平台功能不在此列 —— 表8.1「应用内唯一」）
AI_PER_ENTITY = {
    "保守": [("EI", "规则/阈值配置")],
    "中性": [("EI", "规则/阈值配置"), ("EQ", "历史结果查询")],
}
AI_SYSTEMS = ["智能能力中枢-行业专业模型", "智能能力中枢-康养智能体"]


def _data_entities(items: list[BomItem]) -> dict:
    """按 (system, l2, l3, type) 归并数据功能实体。"""
    g: dict[tuple, list[BomItem]] = defaultdict(list)
    for i in items:
        if i.nesma and i.nesma.type in ("ILF", "ELF"):
            g[(i.path.system, i.path.l2, i.path.l3, i.nesma.type)].append(i)
    return g


def adj_a_collapse(bom: Bom) -> dict:
    """A 数据功能折叠。"""
    ents = _data_entities(bom.active())
    rows = []
    delta = 0
    for (system, l2, l3, t), v in ents.items():
        if len(v) > 1:
            d = -(len(v) - 1) * W[t]
            delta += d
            rows.append({"system": system, "entity": f"{l2} / {l3}", "type": t,
                         "rows": len(v), "keep": 1, "delta_ufp": d,
                         "ids": [i.id for i in v]})
    return {"id": "A", "name": "数据功能折叠", "citation": "表6.4 p.17 / 表7.3 p.18",
            "rule": "同一逻辑文件的多种用户视图、访问路径、文件索引只计为一项",
            "confidence": "高", "delta_ufp": delta, "detail": rows}


def adj_b_completeness(bom: Bom) -> dict:
    """B 数据功能完整性 —— 折叠后的每个实体补齐配套事务功能。"""
    ents = _data_entities(bom.active())
    by_system_type: Counter = Counter()
    for (system, _, _, t) in ents:
        by_system_type[(system, t)] += 1

    # 该 system 已有的事务功能类型
    have: dict[str, Counter] = defaultdict(Counter)
    for i in bom.active():
        if i.nesma and i.nesma.type in ("EI", "EO", "EQ"):
            have[i.path.system][i.nesma.type] += 1

    rows = []
    delta = 0
    for (system, t), n in by_system_type.items():
        companions = ILF_COMPANIONS if t == "ILF" else ELF_COMPANIONS
        for ctype, citation in companions:
            # 该 system 完全没有该类事务功能时才补 —— 已有的视为已覆盖，不重复补
            if have[system][ctype] > 0 or (ctype == "EQ" and have[system]["EO"] > 0):
                continue
            d = n * W[ctype]
            delta += d
            rows.append({"system": system, "data_type": t, "entities": n,
                         "add_type": ctype, "add_count": n, "delta_ufp": d,
                         "citation": citation})
    return {"id": "B", "name": "数据功能完整性补齐", "citation": "表6.2 p.17 / 表7.1 p.18",
            "rule": "每个 ILF 至少含一个 EI，且至少含一个 EO 或 EQ；每个 ELF 至少含一个 EO 或 EQ",
            "confidence": "中", "delta_ufp": delta, "detail": rows}


def adj_c_missing_ilf(bom: Bom) -> dict:
    """C 补齐缺失 ILF —— 有 EI 必有其所维护的 ILF。"""
    rows = []
    delta = 0
    for system, items in bom.by_system().items():
        fp = [i for i in items if i.nesma]
        types = Counter(i.nesma.type for i in fp)
        if types.get("ILF", 0) > 0 or types.get("EI", 0) == 0:
            continue
        # 以「含 EI 的二级功能模块」近似业务实体
        ents = {(i.path.l1, i.path.l2) for i in fp if i.nesma.type == "EI"}
        d = len(ents) * W["ILF"]
        delta += d
        rows.append({"system": system, "ei_count": types["EI"], "current_ilf": 0,
                     "proposed_ilf": len(ents), "delta_ufp": d,
                     "entities": sorted(f"{a} / {b}" for a, b in ents)[:40]})
    return {"id": "C", "name": "补齐缺失 ILF", "citation": "表8.1 p.19 / 表8.3 p.19",
            "rule": "EI 的定义是对内部逻辑文件执行新增/更改/删除；每个可单独维护的 ILF "
                    "至少识别一个 EI。有 EI 而无 ILF 在逻辑上不成立",
            "confidence": "中（实体数以二级功能模块近似，需逐个确认）",
            "delta_ufp": delta, "detail": rows}


def adj_d_fragments(bom: Bom) -> dict:
    """D 机械碎片合并 —— 同一基本处理被切成多行。"""
    g: dict[tuple, list[BomItem]] = defaultdict(list)
    for i in bom.active():
        if i.nesma and i.nesma.type in ("EI", "EO", "EQ"):
            g[(i.path.system, i.path.l1, i.path.l2, i.path.l3)].append(i)
    rows = []
    delta = 0
    for k, v in g.items():
        stubs = [i for i in v if STUB.fullmatch(i.description or "")]
        if len(stubs) < 2:
            continue
        byt: dict[str, list[BomItem]] = defaultdict(list)
        for i in stubs:
            byt[i.nesma.type].append(i)
        for t, group in byt.items():
            if len(group) < 2:
                continue
            d = -(len(group) - 1) * W[t]
            delta += d
            rows.append({"system": k[0], "path": " / ".join(str(x) for x in k[1:]),
                         "type": t, "rows": len(group), "keep": 1, "delta_ufp": d,
                         "ids": [i.id for i in group],
                         "descriptions": [i.description for i in group][:6]})
    return {"id": "D", "name": "机械碎片合并", "citation": "表8.6 p.19（反向适用）",
            "rule": "输入屏幕上的不同功能才计为不同 EI。同一基本处理被机械切分成的多行"
                    "应合并为一条 —— 源表将「建设详情」按分号与短语枚举切分，产生了大量残片",
            "confidence": "中（需逐组确认是否真为同一基本处理）",
            "delta_ufp": delta, "detail": rows}


def adj_e_ai(bom: Bom, scenario: str) -> dict:
    """E AI 资产重拆 —— 每个模型/智能体补其独有的事务功能。

    共享平台功能（模型注册表、训练任务提交、编排配置、会话存储…）按表8.1
    「在待测量的应用程序中是独特的」全局计一次，**不逐个实体重复计**，
    且多数已计在「数智底座-平台能力」与「智能能力中枢-智能体平台」中。
    """
    rows = []
    delta = 0
    for system in AI_SYSTEMS:
        items = [i for i in bom.active() if i.path.system == system and i.nesma]
        if not items:
            continue
        entities = {i.path.l3 or i.path.l2 for i in items}
        for ctype, label in AI_PER_ENTITY[scenario]:
            d = len(entities) * W[ctype]
            delta += d
            rows.append({"system": system, "entities": len(entities),
                         "add_type": ctype, "label": label, "delta_ufp": d})
    return {"id": f"E-{scenario}", "name": f"AI 资产重拆（{scenario}档）",
            "citation": "表8.1 p.19 / 表6.4 p.17",
            "rule": "每个模型/智能体补其独有的事务功能；共享平台功能全局计一次不重复",
            "confidence": "低（需领域确认每个实体的独有功能边界）",
            "delta_ufp": delta, "detail": rows}


def reclassification(bom: Bom) -> tuple[list[dict], dict]:
    """规则引擎独立重判，产出一致/分歧清单。"""
    rows = []
    stats = Counter()
    for i in bom.active():
        if not i.nesma:
            continue
        v = nc.classify(f"{i.name} {i.description}")
        if v.type is None:
            stats["undecidable"] += 1
            verdict = "无法判定"
        elif v.type == i.nesma.type:
            stats["agree"] += 1
            verdict = "一致"
        else:
            stats["disagree"] += 1
            verdict = "分歧"
        if v.competing:
            stats["competing"] += 1
        rows.append({
            "id": i.id, "system": i.path.system, "name": i.name,
            "imported_type": i.nesma.type,
            "rule_type": v.type or "",
            "verdict": verdict,
            "rule_id": v.rule.id if v.rule else "",
            "citation": v.rule.citation if v.rule else "",
            "hits": "、".join(v.hits),
            "competing": "；".join(f"{t}({'、'.join(h[:3])})" for t, h in v.competing),
            "rationale": v.rationale() if verdict == "一致" and v.confident else "",
            "description": (i.description or "")[:120],
        })
    return rows, dict(stats)


def main() -> None:
    ap = argparse.ArgumentParser(description="BOM P2 重拆提案测算")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    bom = Bom.load(args.bom)
    base = sum(W[i.nesma.type] for i in bom.active() if i.nesma)

    adjustments = [adj_a_collapse(bom), adj_b_completeness(bom),
                   adj_c_missing_ilf(bom), adj_d_fragments(bom)]
    ai = {s: adj_e_ai(bom, s) for s in ("保守", "中性")}

    core = sum(a["delta_ufp"] for a in adjustments)
    result = {
        "baseline_ufp": base,
        "adjustments": adjustments,
        "ai_scenarios": ai,
        "totals": {
            "core_delta": core,
            "conservative": base + core + ai["保守"]["delta_ufp"],
            "moderate": base + core + ai["中性"]["delta_ufp"],
        },
    }
    (args.out / "proposal.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    rows, stats = reclassification(bom)
    result["reclassification_stats"] = stats
    with (args.out / "reclassification.csv").open("w", encoding="utf-8-sig", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)

    # 裁决清单：只导出需要人工决定的部分
    with (args.out / "decisions-needed.csv").open("w", encoding="utf-8-sig", newline="") as f:
        wtr = csv.DictWriter(f, fieldnames=["kind", "target", "current", "proposed",
                                            "delta_ufp", "citation", "note"])
        wtr.writeheader()
        for a in adjustments:
            for d in a["detail"]:
                wtr.writerow({
                    "kind": f"{a['id']}-{a['name']}",
                    "target": d.get("system", "") + " | " + str(
                        d.get("entity") or d.get("path") or d.get("data_type") or ""),
                    "current": d.get("rows") or d.get("current_ilf") or d.get("entities") or "",
                    "proposed": d.get("keep") or d.get("proposed_ilf") or d.get("add_count") or "",
                    "delta_ufp": d["delta_ufp"],
                    "citation": a["citation"],
                    "note": a["rule"][:60],
                })
        for r in rows:
            if r["verdict"] == "分歧":
                wtr.writerow({"kind": "F-类型分歧", "target": f"{r['system']} | {r['id']}",
                              "current": r["imported_type"], "proposed": r["rule_type"],
                              "delta_ufp": W[r["rule_type"]] - W[r["imported_type"]],
                              "citation": r["citation"], "note": r["name"][:40]})

    print(f"基线 UFP {base}")
    for a in adjustments:
        print(f"  {a['id']} {a['name']:<16} {a['delta_ufp']:+6d}  置信 {a['confidence'][:2]}")
    print(f"  小计 {core:+d}")
    print(f"  E AI 重拆 保守 {ai['保守']['delta_ufp']:+d} / 中性 {ai['中性']['delta_ufp']:+d}")
    print(f"合计：保守 {result['totals']['conservative']} / "
          f"中性 {result['totals']['moderate']}（基线 {base}）")
    print(f"重判：{stats}")


if __name__ == "__main__":
    main()
