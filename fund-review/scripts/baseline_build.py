"""baseline_build — 第二层：BOM × 区域标准包 → 区域基准。

三层里的第二层。它只回答一个问题：
**「这套产品在这个省、按全定制口径，值多少钱」**。

不知道交付形态、不知道范围、不知道客户是谁 —— 一切商机决策属第三层。
因此同一个 (BOM 版本 × 标准包) 只需算一次，所有商机复用；
也因此**两个 baseline 直接 diff 就是跨区域分析**，不需要另一条对比路径。

与 bom_build 对称：一个建 BOM，一个建区域基准。

产物：
    baseline.json        逐条目 UFP/AFP/因子 + 分系统汇总 + 科目框架（进 git）
    baseline.lock.json   BOM 版本 + 标准包版本 + 计数方法 + 输入哈希（进 git）
    区域基准清单.xlsx     参数页（带 citation）+ 明细 + 汇总（不进 git，重跑即得）
    citations.md         本次用到的每个取值 → 页码/章节/原文

用法：
    python3 baseline_build.py --bom <dir> --pack <dir> --out <baselines/xxx>
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl

import excel_styler
from bom_schema import Bom, Vocabulary, is_fp_counted
from costing_engine import CostingEngine, DealConfig, snapshot
from standard_pack import StandardPack


def default_dirname(pack: StandardPack, bom_version: str) -> str:
    """`<pack_id>@bom-<version>` —— 目录名本身就说清了这份基准是什么的函数。"""
    return f"{pack.pack_id}@bom-{bom_version}"


def build(bom: Bom, pack: StandardPack, counting_method: str,
          as_of: str | None = None) -> dict[str, Any]:
    """算区域基准。

    刻意**不接 DeliveryContext** —— 传了就不是基准了。全部条目按定制开发口径计，
    购置类条目按其 spec 列出但不判形态（形态是第三层的事）。
    """
    deal = DealConfig(deal_id="__baseline__", counting_method=counting_method,
                      # 基准含占位条目：它们是「已知的未知」，基准要完整。
                      # 是否计入报价由第三层决定。
                      include_placeholders=True,
                      as_of_bom_version=as_of)
    eng = CostingEngine(bom, pack, deal)
    items = snapshot(bom, as_of)
    result = eng.run()

    method = counting_method
    size_f = pack.factor("size_change", method)

    detail = []
    for i in items:
        if not is_fp_counted(i):
            continue
        w = pack.fp_weight(method, i.nesma.type)
        lvl = eng.reuse_level(i)
        detail.append({
            "id": i.id, "system": i.path.system,
            "l1": i.path.l1, "l2": i.path.l2, "l3": i.path.l3, "l4": i.path.l4,
            "name": i.name, "type": i.nesma.type, "ufp": w,
            "size_change": size_f,
            "maturity": i.maturity, "reuse_level": lvl,
            "reuse_factor": pack.factor("reuse", lvl),
            "app_type": i.app_type, "app_type_local": pack.factor_label("app_type", i.app_type or "业务处理"),
            "app_factor": pack.factor("app_type", i.app_type or "业务处理"),
            "dev_category": i.dev_category,
            "afp": eng.profile.adjusted_fp(
                w, pack=pack, counting_method=method,
                reuse_level=lvl, app_type=i.app_type or "业务处理"),
            "placeholder": "placeholder" in i.tags,
        })

    # 购置类条目：列出标的与科目，**不定价、不判形态**
    purchase = [{
        "id": i.id, "system": i.path.system, "name": i.name, "class": i.cls,
        "subject": i.spec.get("subject"), "subject_code": i.spec.get("subject_code"),
        "pricing_model": i.spec.get("pricing_model"),
        "unit": i.spec.get("unit"), "qty": i.spec.get("qty"),
        "reference_unit_price_yuan": i.spec.get("reference_unit_price_yuan"),
    } for i in items if i.spec.get("subject")]

    return {
        "kind": "region_baseline",
        "generated": date.today().isoformat(),
        "bom_version": as_of or bom.version,
        "pack_id": pack.pack_id,
        "standard_doc": pack.data["standard_doc"],
        "formula_profile": pack.formula_profile,
        "counting_method": method,
        "basis": "全定制开发口径、全量条目（含占位）—— 不含任何商机决策",
        "software_dev": result["software_dev"],
        "hardware": result["hardware"],
        "other_fees": result["other_fees"],
        "detail": detail,
        "purchase_subjects": purchase,
        "subject_framework": pack.data.get("delivery_mode_support", {}),
        "rates": {k: (v.get("value") if isinstance(v, dict) else v)
                  for k, v in (pack.data.get("rates") or {}).items()},
    }


def make_lock(bl: dict[str, Any], bom: Bom, pack: StandardPack,
              bom_dir: Path, pack_dir: Path) -> dict[str, Any]:
    """版本锁。第三层的 deal.lock.json 会引它，形成 deal → baseline → BOM 的链。

    哈希的是**输入文件**而不是产物 —— 产物可以重算，输入变了才需要重算。
    """
    def digest(paths: list[Path]) -> str:
        h = hashlib.sha256()
        for p in sorted(paths):
            h.update(p.read_bytes())
        return h.hexdigest()[:16]

    return {
        "kind": "baseline_lock",
        "generated": bl["generated"],
        "bom": {"version": bl["bom_version"],
                "items_sha256_16": digest(sorted((bom_dir / "items").glob("*.yaml"))),
                "vocabulary_sha256_16": digest([bom_dir / "vocabulary.yaml"])
                if (bom_dir / "vocabulary.yaml").exists() else None},
        "standard_pack": {"pack_id": pack.pack_id,
                          "formula_profile": pack.formula_profile,
                          "effective_from": pack.data.get("effective_from"),
                          "sha256_16": digest([pack_dir / "pack.yaml"
                                               if pack_dir.is_dir() else pack_dir])},
        "counting_method": bl["counting_method"],
        # 记路径不是为了偷懒，是为了让第三层能**自己找回**这份基准的输入并校验哈希 ——
        # 否则 --baseline 只是个名字，第三层仍要人工保证 --pack 传对了。
        "source_paths": {"bom": str(bom_dir), "pack": str(pack_dir)},
        "totals": {"ufp": bl["software_dev"]["ufp_total"],
                   "afp": bl["software_dev"]["afp_total"],
                   "software_dev": bl["software_dev"]["total"]},
        "reproduce": ("python3 baseline_build.py --bom <dir> --pack <dir> "
                      "--counting-method %s [--as-of %s]"
                      % (bl["counting_method"], bl["bom_version"])),
    }


def emit_xlsx(bl: dict[str, Any], pack: StandardPack, path: Path) -> None:
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "参数"
    ws.append([f"{pack.pack_id} 区域基准 · BOM {bl['bom_version']}"])
    ws.append([bl["standard_doc"]])
    ws.append([])
    ws.append(["参数", "取值", "出处"])
    method = bl["counting_method"]
    for label, dotted in [
        ("规模变更因子", f"factors.size_change.values.{method}"),
        ("软件开发生产率（人时/FP）", "rates.productivity_hours_per_fp"),
        ("人月折算系数（人时/人月）", "rates.man_hours_per_month"),
        ("基准人月费率（元/人月）", "rates.base_man_month_rate"),
    ]:
        try:
            v, c = pack.value(dotted)
            ws.append([label, v, str(c)])
        except Exception as e:      # 本包没有这个维度（如广东无开发类别）
            ws.append([label, "—", f"本标准无此项：{e}"])
    weights, w_cite = pack.value(f"fp_counting.{method}.weights")
    ws.append(["功能点权重", "、".join(f"{k}={n}" for k, n in weights.items()), str(w_cite)])

    det = wb.create_sheet("功能点明细")
    det.append(["条目ID", "系统", "一级模块", "名称", "类型", "未调整功能点",
                "规模变更因子", "产品成熟度", "复用度档位", "复用度因子",
                "应用类型", "本标准称谓", "应用类型因子", "调整后功能点", "占位", "复算公式"])
    for d in bl["detail"]:
        det.append([d["id"], d["system"], d["l1"], d["name"], d["type"], d["ufp"],
                    d["size_change"], d["maturity"], d["reuse_level"], d["reuse_factor"],
                    d["app_type"], d["app_type_local"], d["app_factor"], d["afp"],
                    "是" if d["placeholder"] else "",
                    f'={d["ufp"]}*{d["size_change"]}*{d["reuse_factor"]}*{d["app_factor"]}'])

    smy = wb.create_sheet("基准汇总")
    smy.append(["系统", "开发类别", "条目数", "未调整功能点", "调整后功能点",
                "开发工作量（人月）", "人月费率（元）", "软件开发费用（元）", "类型分布"])
    for s in bl["software_dev"]["systems"]:
        smy.append([s["system"], s["dev_category"], s["items"], s["ufp"], s["afp"],
                    s["effort_man_months"], s["man_month_rate"], s["cost"], str(s["by_type"])])
    sw = bl["software_dev"]
    smy.append(["合计", "", "", sw["ufp_total"], sw["afp_total"], sw["effort_total"],
                "", sw["total"], ""])

    if bl["purchase_subjects"]:
        pu = wb.create_sheet("采购标的")
        pu.append(["条目ID", "系统", "名称", "类别", "科目", "科目号",
                   "计价方式", "单位", "数量", "参考单价"])
        for r in bl["purchase_subjects"]:
            pu.append([r["id"], r["system"], r["name"], r["class"], r["subject"],
                       r["subject_code"], r["pricing_model"], r["unit"], r["qty"],
                       r["reference_unit_price_yuan"]])

    wb.save(path)
    excel_styler.style_workbook(path)


def emit_citations(pack: StandardPack, path: Path) -> None:
    cites = pack.citations_index()
    by_vol: dict[str, list] = defaultdict(list)
    for c in cites:
        by_vol[c.get("volume") or "（本册）"].append(c)
    L = [f"# {pack.pack_id} 条款引用索引", "",
         f"{pack.data['standard_doc']}", "",
         f"共 {len(cites)} 条，分布于 {len(by_vol)} 册。", ""]
    for vol, cs in sorted(by_vol.items()):
        L += [f"## {vol}", "", "| 参数路径 | 页 | 章节 | 原文 |", "|---|---|---|---|"]
        for c in cs:
            L.append(f"| `{c['path']}` | {c.get('page','')} | {c.get('section','')} | "
                     f"{str(c.get('quote','')).replace('|','｜')[:80]} |")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="第二层：BOM × 区域标准包 → 区域基准")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--out", type=Path,
                    help="默认 baselines/<pack_id>@bom-<version>")
    ap.add_argument("--counting-method", default="估算功能点法")
    ap.add_argument("--as-of", help="按指定 BOM 版本时点重算")
    ap.add_argument("--allow-deprecated-pack", action="store_true",
                    help="允许加载已退役的标准包 —— 仅用于历史复算")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    pack = StandardPack.load(args.pack, allow_deprecated=args.allow_deprecated_pack)

    # 选定标准包时就报词表缺口 —— 不要等生成到第 800 行才 PackError
    for group, cov in pack.vocabulary_coverage(Vocabulary.load(args.bom)).items():
        if cov.get("missing"):
            print(f"  ⚠ {pack.pack_id} 的 {group} 未覆盖 BOM 词表：{cov['missing']}")
        elif cov.get("unmapped"):
            used = {getattr(i, group) for i in bom.active()} & set(cov["unmapped"])
            if used:
                print(f"  ⚠ {pack.pack_id} 声明无对应的分类 {sorted(used)} 本次 BOM 用到了")

    bl = build(bom, pack, args.counting_method, args.as_of)
    out = args.out or Path("baselines") / default_dirname(pack, bl["bom_version"])
    out.mkdir(parents=True, exist_ok=True)

    (out / "baseline.json").write_text(
        json.dumps(bl, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "baseline.lock.json").write_text(
        json.dumps(make_lock(bl, bom, pack, args.bom, args.pack),
                   ensure_ascii=False, indent=2), encoding="utf-8")
    emit_xlsx(bl, pack, out / "区域基准清单.xlsx")
    emit_citations(pack, out / "citations.md")

    sw = bl["software_dev"]
    print(f"{pack.pack_id} × BOM {bl['bom_version']} × {args.counting_method}")
    print(f"  功能点 {sw['ufp_total']} UFP → {sw['afp_total']} 调整后 "
          f"→ {sw['effort_total']} 人月")
    print(f"  软件开发费（全定制口径）¥{sw['total']:,.2f}")
    print(f"  明细 {len(bl['detail'])} 条　采购标的 {len(bl['purchase_subjects'])} 条")
    print(f"  输出 → {out}")


if __name__ == "__main__":
    main()
