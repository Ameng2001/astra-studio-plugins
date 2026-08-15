"""device_config_build —— 引导出商机级的硬件配置 `device-config.yaml`（第二层）。

## 它在链路里的位置

    bom/devices/            第一层 · 产品级：场景树 / 物料主数据 / 场景×设备目录
        ↓ 本脚本引导（一次性）
    deals/<id>/device-config.yaml     第二层 · 商机级：哪个园配哪些设备、配多少
        ↓ 引擎
    甲附（送审）             逐台清单，静态值
        ↑ 同步
    丙附（内部）             人填的录入表

首版从目录引导；之后由丙附同步维护，**不再回头读源工作簿**。

## 为什么只放「选了什么、配多少」

设备的型号、参数、单位、单价都是**物料属性**，不随商机变 —— 它们在第一层的
`materials.yaml`。本文件只引物料编码。复制一份参数进来的话，第一层改了价、
第二层还是旧价，而两份都长得完全正常。

## 引导必须与源工作簿对账

引导是一次性的，对错这一次就定了终身。所以**独立重读源工作簿**逐园所对账，
对不上直接拒绝写文件 —— 引导漏行不会有任何地方报错，只是这个商机从此少几台设备。

用法：
    python3 device_config_build.py --root <项目根> --deal <deal.yaml>
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from bom_build_devices import _independent_tally

BATCH_HINT = "所"          # 批次页名里带「200所」这类字样


def build(root: Path, deal_dir: Path, src: Path) -> dict[str, Any]:
    dev = root / "bom" / "devices"
    cat = yaml.safe_load((dev / "catalog.yaml").read_text(encoding="utf-8"))["entries"]
    mats = {m["code"]: m for m in yaml.safe_load(
        (dev / "materials.yaml").read_text(encoding="utf-8"))["materials"]}

    per: dict[str, list[dict]] = defaultdict(list)
    for e in cat:
        for p in e["placements"]:
            for g, q in sorted(p["qty_by_garden"].items()):
                if not q:
                    continue
                item = {"material": e["material"], "scene": e["scene"], "qty": q}
                if p["note"]:
                    item["note"] = p["note"]
                per[g].append(item)

    targets = []
    for g in sorted(per):
        is_batch = BATCH_HINT in g and any(ch.isdigit() for ch in g)
        t: dict[str, Any] = {"id": g, "kind": "batch" if is_batch else "garden"}
        if is_batch:
            # 批次不是一个园。数量填的是**这一批的总量**，不是单园量 ——
            # 不写清楚的话，下一个人会拿它去乘园所数。
            t["count"] = 200
            t["count_note"] = "园所数；items 里的数量已是全批总量，勿再乘"
        t["items"] = sorted(per[g], key=lambda x: (x["scene"], x["material"]))
        targets.append(t)

    # ---- 对账：独立重读源工作簿 ----
    src_q, src_money = _independent_tally(src)
    got_q: dict[str, float] = {}
    got_money = 0.0
    for t in targets:
        for it in t["items"]:
            got_q[t["id"]] = got_q.get(t["id"], 0.0) + it["qty"]
            got_money += (mats[it["material"]]["price_yuan"] or 0) * it["qty"]
    diff = {g: (src_q.get(g, 0), got_q.get(g, 0)) for g in set(src_q) | set(got_q)
            if abs(src_q.get(g, 0) - got_q.get(g, 0)) > 1e-9}
    if diff or abs(src_money - got_money) > 1:
        raise SystemExit(
            f"引导结果与源工作簿对不上，已拒绝写文件：\n"
            f"  数量差异（园所: 源表 vs 配置）：{diff}\n"
            f"  金额：源表 {src_money:,.2f} vs 配置 {got_money:,.2f}\n"
            f"  引导是一次性的，错一次就定终身 —— 漏行不会有任何地方报错，"
            f"只是这个商机从此少几台设备。")

    pend = sum(1 for t in targets for it in t["items"]
               if not (mats[it["material"]]["price_yuan"] or 0))
    return {"targets": targets, "qty": sum(got_q.values()), "money": got_money,
            "items": sum(len(t["items"]) for t in targets), "pending": pend}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--deal", type=Path, required=True)
    ap.add_argument("--force", action="store_true",
                    help="覆盖已有的 device-config.yaml（会丢掉丙附同步进来的内容）")
    a = ap.parse_args()
    deal = yaml.safe_load(a.deal.read_text(encoding="utf-8"))
    out = a.deal.parent / "device-config.yaml"
    if out.exists() and not a.force:
        raise SystemExit(
            f"{out} 已存在。引导只该跑一次 —— 之后由丙附同步维护。\n"
            f"确实要重新引导（会丢掉同步进来的改动）请加 --force。")
    src = a.root / deal["sources"]["hardware"]
    r = build(a.root, a.deal.parent, src)
    doc = {
        "version": "0.1.0",
        "deal": deal["deal_id"],
        "source_bom": "bom/devices",
        "bootstrapped_from": deal["sources"]["hardware"],
        "note": ("第二层 · 硬件配置：哪个园/批次的哪个场景配哪些设备、配多少。\n"
                 "设备的型号/参数/单位/单价是**物料属性**，在第一层 "
                 "bom/devices/materials.yaml，本文件只引物料编码 —— "
                 "复制一份进来的话，第一层改了价第二层还是旧价，而两份都长得正常。\n"
                 "首版由 device_config_build.py 从目录引导并与源工作簿逐园所对账；"
                 "之后由丙附（硬件配置录入）同步维护，不再回头读源工作簿。\n"),
        "targets": r["targets"],
    }
    out.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                                  default_flow_style=False, width=100),
                   encoding="utf-8")
    print(f"硬件配置（第二层）→ {out}")
    print(f"  {len(r['targets'])} 个园/批次，{r['items']} 条配置，"
          f"{r['qty']:,.0f} 台，¥{r['money']:,.2f}")
    print(f"  其中 {r['pending']} 条为待核价物料（单价 0，列而不计）")
    print(f"  对账通过：与源工作簿 {deal['sources']['hardware']} 逐园所一致")


if __name__ == "__main__":
    main()
