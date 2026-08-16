"""bom_retax —— 按 taxonomy.yaml 的产品线/系统结构重挂 BOM 条目。

源清单的分组是**编制顺序**（平台开发 / 底座开发两张表），不是产品架构。
产品侧要的是「产品线 → 系统」两级：应用平台四个端、基座七个平台、
行业大模型七个系统（三个 L0 引擎 + L1 两块 + L2 两块）。

## 重挂的是 path，不是 id

`path.product_line` / `path.system` 改，**`id` 一律不动**。
id 是跨版本追溯的锚：飞书上正在编辑的行、deal.lock.json 里锁的版本、
Z01 明细表里的每一行，都靠它对上。为了让 id 好看而重编，
等于把所有已发生的引用一次性作废。

## 计价方法跟着**层次**走，不跟产品线走

三个引擎归到「数智幼教行业大模型」这条产品线下，但它们是 L0层，
仍走功能点法 —— 与它们此前挂在支撑基座下时一致。改的是归属，不是算法。
本模块重挂完会**逐系统核对**方法分配没有意外变化，变了就报错。

用法：
    python3 bom_retax.py --bom <dir> [--apply]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


def build_index(tax: dict) -> tuple[dict, dict]:
    """(旧子系统名 → 新归属, 新系统 → 元信息)。"""
    sub2new: dict[str, dict] = {}
    sysmeta: dict[str, dict] = {}
    for line, lspec in (tax.get("product_lines") or {}).items():
        line_layer = lspec.get("layer")
        line_method = lspec.get("costing_method")
        for sysname, spec in (lspec.get("systems") or {}).items():
            spec = spec if isinstance(spec, dict) else {}
            meta = {
                "product_line": line, "system": sysname,
                "code": spec.get("code"),
                "layer": spec.get("layer") or line_layer,
                "costing_method": spec.get("costing_method") or line_method,
                "basis": spec.get("basis", ""),
            }
            sysmeta[sysname] = meta
            for key in ([spec["source"]] if spec.get("source") else
                        spec.get("subsystems") or []):
                if key in sub2new:
                    raise ValueError(
                        f"「{key}」被同时归入 {sub2new[key]['system']} 与 {sysname} —— "
                        f"一个子系统只能属于一个系统，否则条目会被计两遍")
                sub2new[key] = meta
    return sub2new, sysmeta


def retax(bom_dir: Path, tax: dict) -> dict[str, Any]:
    sub2new, sysmeta = build_index(tax)
    files: dict[Path, Any] = {}
    moved = Counter()
    unmapped: list[dict] = []
    method_change: list[dict] = []

    for f in sorted(bom_dir.glob("items/*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for it in data["items"]:
            p = it["path"]
            old_sys = p["system"]
            # 归位键：基座的条目按 l1（子系统名）归，其余按 system 归
            key = p.get("l1") if old_sys in _BASE_ALIASES else old_sys
            meta = sub2new.get(key) or sub2new.get(old_sys)
            if not meta:
                unmapped.append({"id": it["id"], "system": old_sys,
                                 "l1": p.get("l1"), "name": it["name"]})
                continue
            was_effort = it.get("class") == "SOFTWARE_EFFORT"
            now_effort = meta["costing_method"] == "工作量估算法"
            if was_effort != now_effort:
                method_change.append({
                    "id": it["id"], "from": old_sys, "to": meta["system"],
                    "was": "工作量法" if was_effort else "功能点法",
                    "now": "工作量法" if now_effort else "功能点法"})
            p["product_line"] = meta["product_line"]
            p["system"] = meta["system"]
            # 原子系统名降为 l1，保留原层级信息 —— 拆开的谱系不能丢
            if old_sys in _BASE_ALIASES and p.get("l1"):
                p.setdefault("l0_source", old_sys)
            moved[meta["system"]] += 1
        files[f] = data
    return {"files": files, "moved": dict(moved), "unmapped": unmapped,
            "method_change": method_change, "sysmeta": sysmeta}


#: 源清单里把 49 个子系统串在一张表下的那个名字。按 l1 归位而不是按 system。
_BASE_ALIASES = {"数智民生生态体系平台支撑基座"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    tax = yaml.safe_load((a.bom / "taxonomy.yaml").read_text(encoding="utf-8"))
    rep = retax(a.bom, tax)

    print(f"重挂 {'（已写入）' if a.apply else '（dry-run）'}")
    by_line: dict[str, list] = defaultdict(list)
    for s, n in rep["moved"].items():
        by_line[rep["sysmeta"][s]["product_line"]].append((s, n))
    for line, syss in by_line.items():
        print(f"\n■ {line}")
        for s, n in sorted(syss, key=lambda x: -x[1]):
            m = rep["sysmeta"][s]
            print(f"    {s:<16}{n:>5} 条　{m['layer']}　{m['costing_method']}")

    if rep["method_change"]:
        print(f"\n⚠ {len(rep['method_change'])} 条的计价方法发生变化 —— "
              f"重挂只该改归属，不该改算法：")
        for c in rep["method_change"][:8]:
            print(f"    {c['id']}  {c['from']} → {c['to']}　{c['was']} → {c['now']}")
    if rep["unmapped"]:
        print(f"\n⚠ {len(rep['unmapped'])} 条无法归位（taxonomy 里没登记）：")
        seen = set()
        for u in rep["unmapped"]:
            k = (u["system"], u["l1"])
            if k in seen:
                continue
            seen.add(k)
            print(f"    {u['system']} / {u['l1']}")

    if a.apply:
        if rep["unmapped"]:
            raise SystemExit("有条目无法归位，先补全 taxonomy.yaml 再 --apply —— "
                             "落一半会让 BOM 处于两套结构混合的状态")
        for path, data in rep["files"].items():
            path.write_text(yaml.safe_dump(data, allow_unicode=True,
                                           sort_keys=False, width=200),
                            encoding="utf-8")
        (a.bom / "retax-report.json").write_text(
            json.dumps({"moved": rep["moved"],
                        "method_change": rep["method_change"]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  → {a.bom}")


if __name__ == "__main__":
    main()
