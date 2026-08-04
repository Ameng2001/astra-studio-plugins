"""bom_apply — 把 bom_p2_proposal 的调整项落实到 BOM，产出新版本。

原则：
  · **条目永不物理删除** —— 只置 status=deprecated + deprecated_in，
    保证任何历史报价都能凭 deal.lock.json 精确重算
  · **合并不丢信息** —— 被折叠行的描述并入保留行，而非丢弃
  · **每次落实都填 rationale** —— 内容是触发的标准条款，可追溯

目前实现调整项 A（数据功能折叠）。B–E 需人工确认实体边界后再落实。

用法：
    python3 bom_apply.py --bom <dir> --adjustment A --new-version 0.10.0 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from bom_schema import Bom, BomItem
from nesma_weights import ESTIMATED_WEIGHTS as W

#: 成熟度保守序 —— 合并后取组内最不成熟者（该逻辑文件整体仍需建设）
MATURITY_ORDER = {"new": 0, "partial": 1, "existing": 2}

COLLAPSE_CITATION = "表6.4 p.17 / 表7.3 p.18"
COLLAPSE_RULE = ("已被识别出的内部/外部逻辑文件可能存在多种不同的用户视图、"
                 "访问路径、文件索引，但该逻辑文件只能计为一项")


def _clean(desc: str) -> str:
    """去掉机械切分留下的「支持」前缀与句末句号，便于拼接。"""
    d = (desc or "").strip()
    d = re.sub(r"^支持", "", d)
    return d.rstrip("。．;；").strip()


def collapse_data_functions(bom: Bom, new_version: str) -> tuple[Bom, dict[str, Any]]:
    """调整项 A：同一逻辑文件的多行折叠为一条。"""
    groups: dict[tuple, list[BomItem]] = defaultdict(list)
    for it in bom.items:
        if it.status != "deprecated" and it.nesma and it.nesma.type in ("ILF", "ELF"):
            groups[(it.path.system, it.path.l2, it.path.l3, it.nesma.type)].append(it)

    log: list[dict[str, Any]] = []
    delta = 0

    for (system, l2, l3, ftype), members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda i: i.id)
        keeper, dropped = members[0], members[1:]

        # 描述合并 —— 被折叠行写的是同一文件的字段/接入方式/脱敏规则，属补充信息
        parts, seen = [], set()
        for m in members:
            c = _clean(m.description)
            if c and c not in seen:
                seen.add(c)
                parts.append(c)
        if parts:
            keeper.description = "；".join(parts) + "。"

        # 名称用实体名 —— 折叠后该条目代表的就是这个逻辑文件
        if l3:
            keeper.name = str(l3)

        # 成熟度取组内最不成熟者
        worst = min(members, key=lambda i: MATURITY_ORDER.get(i.maturity, 0))
        if worst.maturity != keeper.maturity:
            keeper.maturity = worst.maturity
            keeper.maturity_evidence = (worst.maturity_evidence or keeper.maturity_evidence)
        if keeper.maturity_evidence:
            keeper.maturity_evidence += f"（折叠自 {len(members)} 行，取最不成熟者）"

        keeper.nesma.rationale = (
            f"判为 {ftype}：{COLLAPSE_RULE}（{COLLAPSE_CITATION}）。"
            f"本条已合并同一逻辑文件的 {len(members)} 行"
            f"（原分列其数据内容、接入方式、字段与脱敏规则），合并前 id："
            f"{', '.join(m.id for m in members)}"
        )
        keeper.nesma.counted_by = f"{keeper.nesma.counted_by or ''}; collapse:A@{new_version}"

        # 运营期证据合并
        ev = sorted({e for m in members for e in m.runtime.evidence})
        keeper.runtime.evidence = ev
        keeper.runtime.needs_inference_gpu = any(m.runtime.needs_inference_gpu for m in members)
        keeper.runtime.calls_external_llm = any(m.runtime.calls_external_llm for m in members)

        for d in dropped:
            d.status = "deprecated"
            d.deprecated_in = new_version

        d_ufp = -len(dropped) * W[ftype]
        delta += d_ufp
        log.append({
            "system": system, "entity": f"{l2} / {l3}", "type": ftype,
            "collapsed": len(members), "keeper": keeper.id,
            "deprecated": [d.id for d in dropped], "delta_ufp": d_ufp,
        })

    # 注意：不改任何条目的 since —— 它记录首次引入版本，与折叠无关
    bom.version = new_version
    return bom, {"adjustment": "A", "citation": COLLAPSE_CITATION,
                 "groups": len(log), "delta_ufp": delta, "detail": log}


ADJUSTMENTS = {"A": collapse_data_functions}


def main() -> None:
    ap = argparse.ArgumentParser(description="落实 BOM 调整项")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--adjustment", required=True, choices=sorted(ADJUSTMENTS))
    ap.add_argument("--new-version", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    before = sum(W[i.nesma.type] for i in bom.active() if i.nesma)
    before_n = len(bom.active())

    bom, report = ADJUSTMENTS[args.adjustment](bom, args.new_version)
    bom.validate()

    after = sum(W[i.nesma.type] for i in bom.active() if i.nesma)
    after_n = len(bom.active())
    report["ufp_before"], report["ufp_after"] = before, after
    report["active_before"], report["active_after"] = before_n, after_n

    print(f"调整项 {args.adjustment} — {report['groups']} 组折叠")
    print(f"  有效条目 {before_n} → {after_n}（废弃 {before_n - after_n} 条，未物理删除）")
    print(f"  UFP {before} → {after}（{report['delta_ufp']:+d}）")

    if args.dry_run:
        print("  [dry-run] 未写入")
        return

    bom.save(args.bom)
    (args.bom / f"apply-{args.adjustment}-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已写入 {args.bom}（VERSION → {args.new_version}）")


if __name__ == "__main__":
    main()
