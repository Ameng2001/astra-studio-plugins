"""bom_rationale — 用规则引擎的独立重判为功能点补 rationale。

**只在规则引擎独立重判后与现有类型一致、且无竞争规则时才生成。**
这是两条独立路径的相互印证，不是给既有结论编理由 —— 后者等于把门禁变成摆设。

生成的 rationale 自带出身声明，且 `counted_by` 记 `rule-corroborated@<版本>`，
与人工撰写的可区分。G-02c（双人复核）不受影响，仍全量阻断 released：
印证满足「有没有理由」，人工复核回答「理由对不对」，两件事。

不一致、无法判定、有竞争规则的一律**不生成**，导出待人工清单。

用法：
    python3 bom_rationale.py --bom <dir> --version <new> [--out <csv>] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Any

import nesma_classify as nc
from bom_schema import FP_COUNTED_CLASSES, Bom

PROVENANCE = ("※ 本理由为**规则引擎独立重判后与原判定一致**所得的相互印证，"
              "非人工逐条撰写 —— 仍需双人复核（G-02c）方可进入 released。")


def corroborate(bom: Bom, version: str) -> tuple[int, list[dict[str, Any]], Counter]:
    generated = 0
    pending: list[dict[str, Any]] = []
    stats: Counter = Counter()

    for i in bom.active():
        if i.cls not in FP_COUNTED_CLASSES or not i.nesma:
            continue
        if (i.nesma.rationale or "").strip():
            stats["已有"] += 1
            continue

        v = nc.classify(f"{i.name} {i.description}")
        if v.type == i.nesma.type and not v.competing:
            i.nesma.rationale = (
                f"判为 {v.type}：{v.rule.summary}（{v.rule.citation}）；"
                f"命中原文「{'、'.join(v.hits[:4])}」。{PROVENANCE}")
            i.nesma.counted_by = (f"{i.nesma.counted_by or ''}; "
                                  f"rule-corroborated@{version}")
            generated += 1
            stats["一致且无竞争"] += 1
            continue

        if v.type is None:
            why, key = "规则引擎无法从描述判定类型 —— 描述信息不足，须先补描述", "无法判定"
        elif v.type != i.nesma.type:
            why = f"规则引擎判为 {v.type}，与现类型 {i.nesma.type} 分歧，须人工裁定"
            key = "分歧"
        else:
            others = [t for t, _ in v.competing]
            why = f"一致但同时命中 {others}，该行可能含多个基本处理，须先裁定是否拆分"
            key = "一致但有竞争"
        stats[key] += 1
        pending.append({"id": i.id, "system": i.path.system, "name": i.name,
                        "current_type": i.nesma.type, "rule_type": v.type or "",
                        "reason": why, "description": (i.description or "")[:120]})
    return generated, pending, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="规则印证补 rationale")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--version", required=True, help="生成后写入的 BOM 版本号")
    ap.add_argument("--out", type=Path, help="待人工清单 CSV 路径")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    n, pending, stats = corroborate(bom, args.version)
    total = sum(v for k, v in stats.items() if k != "已有")

    print(f"功能点条目 {stats['已有'] + total}，已有 rationale {stats['已有']}，"
          f"本次处理 {total}")
    for k in ("一致且无竞争", "无法判定", "分歧", "一致但有竞争"):
        if stats[k]:
            print(f"   {k:<12} {stats[k]:5d}  {stats[k] / total:5.1%}")
    print(f"生成 {n} 条；仍待人工 {len(pending)} 条")

    if args.dry_run:
        print("  [dry-run] 未写入")
        return

    bom.version = args.version
    bom.validate()
    bom.save(args.bom)
    out = args.out or (args.bom / "rationale-pending.csv")
    if pending:
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(pending[0].keys()))
            w.writeheader()
            w.writerows(pending)
        print(f"  待人工清单 → {out}")


if __name__ == "__main__":
    main()
