"""quote_init —— 起一个新商机：建目录、写骨架、出一本空的丙附等着填。

## 它产出什么，不产出什么

**产出**：`deals/<id>/` 下的 `deal.yaml` 骨架、`costing-method-split.yaml`、
空的 `device-config.yaml`，以及一本按声明的园所/批次开好页、挂好联动下拉的丙附。

**不产出**送审套表。新商机的硬件还没配、软件源表还没给，这时候出一版
「工程总投资 ¥0」的甲本没有意义，只会让人以为流程走完了。

## 硬件配置为什么从空开始

照抄上一个商机的设备配置，最省事，也最危险：新商机的丙附一打开就是满的，
人会以为「已经配好了」，只在上面改几笔 —— 而那些没改的行是**上一个项目的**
园所规模、场景选择、增补批次。这类错误在总额上完全看不出来。

所以园所/批次由 `--target` 显式声明，设备一条不预填。丙附是空表带下拉，
填的人知道自己在从零配置。

软件侧不同：计价方法按层次分配、标准包取值都是产品级/标准级的事实，
换个商机不变，所以可以从参考商机沿用。

用法：
    python3 quote_init.py --root . --deal-id 2027-xxx-preschool \\
        --project-name "XX市学前教育平台建设项目" --build-unit "XX市教育局" \\
        --pack standard-packs/liuzhou-2020 \\
        --target 01_示范园 --target 02_中心园 \\
        --target "06_100所基础园配置:batch:100" \\
        [--from deals/2026-liuzhou-preschool/deal.yaml]
"""
from __future__ import annotations

import argparse
import shutil
from datetime import date as _date
from pathlib import Path
from typing import Any

import yaml

TODO = "**待填**"


def _sha_file(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "—"


def _sha_fp(p: Path) -> str:
    """指纹范围 = 同步会写的范围（fp_method_settings），不是整个 deal.yaml。"""
    import hashlib
    if not p.exists():
        return "—"
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    blob = yaml.safe_dump(d.get("fp_method_settings") or {},
                          allow_unicode=True, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def parse_target(spec: str) -> dict[str, Any]:
    """`名称` 或 `名称:batch:园所数`。"""
    parts = spec.split(":")
    t: dict[str, Any] = {"id": parts[0].strip(), "kind": "garden", "items": []}
    if len(parts) >= 2 and parts[1].strip() == "batch":
        t["kind"] = "batch"
        t["count"] = int(parts[2]) if len(parts) > 2 and parts[2].strip() else 0
        t["count_note"] = "园所数；items 里的数量是全批总量，勿再乘"
        if not t["count"]:
            raise SystemExit(f"批次 {t['id']} 没给园所数：写成 `名称:batch:200`")
    elif len(parts) >= 2:
        raise SystemExit(f"--target {spec!r} 格式不对：`名称` 或 `名称:batch:园所数`")
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--deal-id", required=True)
    ap.add_argument("--project-name", default=TODO)
    ap.add_argument("--build-unit", default=TODO)
    ap.add_argument("--pack", default=TODO, help="standard-packs/<省市>")
    ap.add_argument("--prefix", default=TODO, help="文件名前缀，如「数智幼教」")
    ap.add_argument("--target", action="append", default=[],
                    help="园所或批次，可重复。`名称` / `名称:batch:园所数`")
    ap.add_argument("--from", dest="ref", type=Path,
                    help="参考商机的 deal.yaml —— 沿用软件侧的计价方法分配等产品级设定")
    a = ap.parse_args()

    d = a.root / "deals" / a.deal_id
    if d.exists():
        raise SystemExit(f"{d} 已存在。新商机请换 id；要重置现有商机请手动清理。")
    if not a.target:
        raise SystemExit(
            "至少要给一个 --target。园所/批次是商机决策，没有默认值 —— "
            "留空的话丙附一页都开不出来。")
    targets = [parse_target(x) for x in a.target]
    dup = {t["id"] for t in targets if [x["id"] for x in targets].count(t["id"]) > 1}
    if dup:
        raise SystemExit(f"--target 里有重名：{sorted(dup)}")

    d.mkdir(parents=True)
    today = _date.today().strftime("%Y%m%d")

    # ---- deal.yaml 骨架 ----
    deal = {
        "deal_id": a.deal_id,
        "project_name": a.project_name,
        "build_unit": a.build_unit,
        "baseline": a.pack,
        "as_of": _date.today().isoformat(),
        "costing_method": TODO + "（如：混合口径（功能点估算法 + 工作量估算法））",
        "fp_method_settings": {
            "productivity_ratio": 1.0,
            "app_type_factors": {},
        },
        "doc": {"prefix": a.prefix, "version": "0.1.0",
                "date": today, "status": "送审版"},
        "sources": {
            "software": TODO + "：raw-input/<软件估算表>.xlsx",
            "hardware": TODO + "：raw-input/<设备报价表>.xlsx（仅在 "
                               "hardware_source: workbook 时需要）",
            "hardware_price_column": "市场单价",
            "hardware_source": "device-config",
            "hardware_passthrough": False,
        },
    }
    if a.ref and a.ref.exists():
        ref = yaml.safe_load(a.ref.read_text(encoding="utf-8"))
        # 只沿用**产品级/标准级**的设定。商机级的一律不抄 ——
        # 抄过来的项目名、源表路径、硬件配置都是上一个项目的，
        # 而它们在总额上完全看不出来。
        for k in ("baseline",):
            if ref.get(k) and a.pack == TODO:
                deal[k] = ref[k]
        if ref.get("doc", {}).get("prefix") and a.prefix == TODO:
            deal["doc"]["prefix"] = ref["doc"]["prefix"]
    head = (f"# {a.deal_id} —— 商机级决策（第三层）。\n"
            f"# 由 quote_init 生成骨架。标 {TODO} 的必须补齐后才能出表。\n"
            f"#\n"
            f"# 硬件配置**不在这里** —— 在 device-config.yaml，由丙附录入后同步。\n"
            f"# 那份是空的：新商机的设备配置没有默认值，照抄上一个商机会让人\n"
            f"# 以为已经配好了，而没改的行是上一个项目的园所规模与场景选择。\n\n")
    (d / "deal.yaml").write_text(
        head + yaml.safe_dump(deal, allow_unicode=True, sort_keys=False,
                              default_flow_style=False, width=100),
        encoding="utf-8")

    # ---- 计价方法分配：产品级，可从参考商机沿用 ----
    split_src = (a.ref.parent / "costing-method-split.yaml") if a.ref else None
    if split_src and split_src.exists():
        shutil.copy(split_src, d / "costing-method-split.yaml")
        split_note = f"已从 {split_src.parent.name} 沿用"
    else:
        (d / "costing-method-split.yaml").write_text(
            "# 计价方法分配：哪些子系统走功能点法、哪些走工作量法。\n"
            "# 判据是**该方法测不测得到这项工作**，不是哪种算得多。\n"
            f"version: '0.1.0'\ndefault_method: 功能点法\n"
            f"effort_method:\n  systems: []   # {TODO}\n",
            encoding="utf-8")
        split_note = f"骨架（{TODO}）"

    # ---- device-config.yaml：声明园所/批次，设备一条不填 ----
    cfg = {
        "version": "0.1.0",
        "deal": a.deal_id,
        "source_bom": "bom/devices",
        "note": ("第二层 · 硬件配置。**新商机从空开始** —— 园所/批次已按 "
                 "--target 声明，设备一条不预填。\n"
                 "请打开丙附（硬件配置录入）逐条选场景、选设备、填数量，"
                 "再跑 quote_sync --write 同步回本文件。\n"
                 "设备的型号/参数/单位/单价是物料属性，在第一层 "
                 "bom/devices/materials.yaml，本文件只引物料编码。\n"),
        "targets": targets,
    }
    (d / "device-config.yaml").write_text(
        yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False,
                       default_flow_style=False, width=100), encoding="utf-8")

    # ---- 出一本空丙附 ----
    made = None
    if (a.root / "bom" / "devices" / "catalog.yaml").exists():
        import device_quote_tool as dqt
        import gov_sheet as gs
        out = d / "out"
        out.mkdir(exist_ok=True)
        made = out / gs.doc_name(
            deal["doc"]["prefix"], "丙附", "硬件配置录入",
            deal["doc"]["version"], today, "内部版·勿送审")
        dqt.emit(a.root, d, made,
                 baseline={"生成时间": today, "版本": deal["doc"]["version"],
                           "deal_id": a.deal_id, "标准包": str(deal["baseline"]),
                           # 写**真指纹**，不写占位 —— 占位会让首次同步一律
                           # 判成冲突。指纹只覆盖同步会写的范围，所以之后去
                           # 补 deal.yaml 里的项目名不会把同步堵住。
                           "fp_method_settings.sha": _sha_fp(d / "deal.yaml"),
                           "device-config.yaml.sha": _sha_file(d / "device-config.yaml"),
                           "说明": "本册由 quote_init 生成。"
                                   "quote_sync 用这些指纹做冲突检测。"})

    print(f"新商机 → {d}")
    print(f"  deal.yaml                 骨架，{TODO} 项待补")
    print(f"  costing-method-split.yaml {split_note}")
    print(f"  device-config.yaml        {len(targets)} 个园所/批次，"
          f"设备 0 条（**有意为空**）")
    if made:
        print(f"  {made.name}")
    print("\n下一步：")
    print(f"  1. 补齐 deal.yaml 里标 {TODO} 的项（项目名、建设单位、标准包、软件源表）")
    print("  2. 打开丙附逐条录入设备配置（选场景 → 选设备 → 填数量）")
    print("  3. quote_sync.py --write     同步回 device-config.yaml")
    print("  4. quote_generate_liuzhou.py 出套表")
    print("\n  ⚠ 现在还出不了套表：硬件一条没配，软件源表也没给。")


if __name__ == "__main__":
    main()
