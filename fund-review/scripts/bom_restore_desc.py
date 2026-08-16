"""bom_restore_desc — 从源工作簿回源恢复被机械切分的描述。

这些残片（「支持待办事项。」「支持女享受服务次数。」）不是描述写得简，
而是源表把「建设详情」单元格机械切碎的产物。**原始未切分的完整文本还在源表里。**

因此正确做法是**回源恢复**，不是替产品重写需求：

  1. 按层级键定位源表的父级「建设详情」原文
  2. 把父级按编号项（1、2、3、）或分号切成语义段
  3. 用字符重合度把残片对回它所属的那一段 → 该段即恢复后的描述
  4. **对不齐任何分段的**（如从「男女人数」中间切断的「男」「女」两片）
     → 判为机械碎片，标记待合并，不硬补描述

第 4 类是关键：给一个本不该独立存在的条目补描述，等于把切分错误固化下来。

用法：
    python3 bom_restore_desc.py --bom <dir> --source <建设清单.xlsx> \
        --version <new> [--only <ids.csv>] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl

from bom_build import _key
from bom_schema import Bom

DETAIL_HEADERS = ("建设详情", "功能描述", "功能点描述")
HW_SHEET = "7.硬件设备（服务站点）"

#: BOM 的 system → 源表 sheet
SHEET_OF = {
    "数智底座-平台能力": "1.数智底座-平台能力",
    "数智底座-知识工程": "1.数智底座-知识工程",
    "数智底座-数据建设": "1.数智底座-数据建设",
    "智能能力中枢-行业专业模型": "2.智能能力中枢-行业专业模型",
    "智能能力中枢-智能体平台": "2.智能能力中枢-智能体平台",
    "智能能力中枢-康养智能体": "2.智能能力中枢-康养智能体",
    "生态运营与交易中台": "3.生态运营与交易中台",
}
#: 残片与分段的字符重合度下限 —— 低于此值视为对不齐
MATCH_FLOOR = 0.45


def build_detail_index(source: Path) -> dict[tuple, str]:
    """层级键 → 源表父级「建设详情」原文。"""
    wb = openpyxl.load_workbook(source, data_only=True)
    idx: dict[tuple, str] = {}
    for ws in wb.worksheets:
        if ws.title in ("总表", "报价参照说明", HW_SHEET):
            continue
        header = [str(c).strip() if c else ""
                  for c in next(ws.iter_rows(min_row=2, max_row=2, values_only=True))]
        try:
            dc = next(i for i, h in enumerate(header) if h in DETAIL_HEADERS)
        except StopIteration:
            continue
        carry, section = [""] * dc, ""
        for row in ws.iter_rows(min_row=3, values_only=True):
            cells = [str(c).strip() if c is not None else "" for c in row]
            if not any(cells):
                continue
            detail = cells[dc] if dc < len(cells) else ""
            if cells[0] and not detail and not any(cells[1:dc + 1]):
                section, carry = cells[0], [""] * dc
                continue
            if not detail:
                continue
            for i in range(dc):
                v = cells[i] if i < len(cells) else ""
                if v:
                    carry[i] = v
                    for j in range(i + 1, dc):
                        carry[j] = ""
            levels = [c for c in carry if c]
            for depth in range(len(levels), 0, -1):
                k = (ws.title, _key(section), *(_key(x) for x in levels[:depth]))
                if depth == len(levels) or k not in idx:
                    idx[k] = detail
    return idx


def segments(text: str) -> list[str]:
    """把父级原文切成语义段：优先按编号项，其次按分号/换行。"""
    parts = re.split(r"(?:^|[；;\n])\s*\d+[、.．)）]\s*", text)
    if len(parts) <= 1:
        parts = re.split(r"[；;\n]", text)
    return [p.strip() for p in parts if len(p.strip()) >= 6]


def _norm(t: str) -> str:
    return re.sub(r"[\s，。、；：（）()【】\[\]0-9．.,;:]+", "", re.sub(r"^支持", "", t or ""))


def overlap(fragment: str, segment: str) -> float:
    """残片与分段的字符重合度 —— 残片的字有多少落在该段里。"""
    f, s = set(_norm(fragment)), set(_norm(segment))
    return len(f & s) / len(f) if f else 0.0


def locate(item, idx: dict[tuple, str]) -> str | None:
    sheet = SHEET_OF.get(item.path.system)
    if sheet is None and item.path.system.startswith("多角色"):
        sheet = "4.多角色业务应用"
    if sheet is None:
        return None
    section = (item.path.system.split("-", 1)[1]
               if sheet == "4.多角色业务应用" and "-" in item.path.system else "")
    levels = [x for x in (item.path.l1, item.path.l2, item.path.l3) if x]
    for depth in range(len(levels), 0, -1):
        k = (sheet, _key(section), *(_key(x) for x in levels[:depth]))
        if k in idx:
            return idx[k]
    return None


def restore(bom: Bom, idx: dict[tuple, str],
            only: set[str] | None) -> tuple[list[dict[str, Any]], Counter]:
    log: list[dict[str, Any]] = []
    stats: Counter = Counter()
    for it in bom.active():
        if only is not None and it.id not in only:
            continue
        parent = locate(it, idx)
        if parent is None:
            stats["无法回源"] += 1
            continue
        segs = segments(parent)
        if not segs:
            stats["源文无可用分段"] += 1
            continue
        best = max(segs, key=lambda s: overlap(it.description or "", s))
        score = overlap(it.description or "", best)
        if score < MATCH_FLOOR:
            # 对不齐任何分段 —— 该残片是从某段中间切断的，本不该独立存在。
            # 给它补描述等于把切分错误固化下来。
            it.tags = sorted(set(it.tags) | {"fragment-merge-pending"})
            stats["对不齐-标记待合并"] += 1
            log.append({"id": it.id, "action": "merge-pending",
                        "current": it.description, "parent": parent[:120],
                        "score": round(score, 2)})
            continue
        if len(best) > len(it.description or ""):
            log.append({"id": it.id, "action": "restored",
                        "before": it.description, "after": best,
                        "score": round(score, 2)})
            it.description = best if best.endswith("。") else best + "。"
            stats["已回源恢复"] += 1
        else:
            stats["原描述已不短于源分段"] += 1
    return log, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="从源工作簿回源恢复被切分的描述")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--version", required=True)
    ap.add_argument("--only", type=Path, help="限定处理的条目 id CSV（含 id 列）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    idx = build_detail_index(args.source)
    only = None
    if args.only:
        with args.only.open(encoding="utf-8-sig") as f:
            only = {r["id"] for r in csv.DictReader(f)}

    log, stats = restore(bom, idx, only)
    for k, n in stats.most_common():
        print(f"  {k:<18} {n:5d}")

    if args.dry_run:
        print("  [dry-run] 未写入")
        return
    bom.version = args.version
    bom.validate()
    bom.save(args.bom)
    out = args.bom / "restore-desc-report.csv"
    if log:
        with out.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=sorted({k for r in log for k in r}))
            w.writeheader()
            w.writerows(log)
        print(f"  明细 → {out}")


if __name__ == "__main__":
    main()
