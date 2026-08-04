"""quote_generate — 三层合成 → 报价输出物。

产出（P4 范围）：
  01-建设期采购清单.xlsx   按标准科目树组织，每行带 BOM id 与条款出处
  02-功能点测算表.xlsx     逐条目 UFP → AFP → 工作量 → 费用，可复算
  05-编制说明.md           计数方法、参数取值与出处、假设与边界
  deal.lock.json           版本锁：BOM 版本 + 标准包 + 交付配置

运营期清单与 TCO 属 P5（交付方式）范围，本阶段不产出。

**Excel 只是渲染产物。** 所有数值由引擎算定后写入静态值 ——
从根上消除 P0 那类 SUMIF/VLOOKUP 因单元格布局失效的缺陷。
另附「复算公式」列供人工核验，但它不参与计算。

用法：
    python3 quote_generate.py --bom <dir> --pack <dir> --out <deal_dir> \
        [--deal-id <id>] [--as-of <bom_version>] [--include-placeholders]
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl

import excel_styler
from bom_schema import FP_COUNTED_CLASSES, Bom
from costing_engine import (PLACEHOLDER_TAG, CostingEngine, DealConfig,
                            DeliveryContext, lock, snapshot)
from delivery_matrix import DeliveryPlan
from ops_model import OpsModel, tco
from standard_pack import StandardPack


def emit_procurement_list(result: dict[str, Any], pack: StandardPack,
                          path: Path) -> None:
    """01 建设期采购清单 —— 按区域标准的科目树组织。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "建设期采购清单"
    ws.append([f"{result['deal']} 建设期采购清单"])
    ws.append([f"编制依据：{pack.data['standard_doc']}　|　"
               f"BOM {result['bom_version']}　|　标准包 {pack.pack_id}　|　"
               f"项目类型 {result['project_type']}　|　计数方法 {result['counting_method']}"])
    ws.append([])
    ws.append(["科目", "明细", "金额（元）", "计算依据", "标准出处"])

    sw = result["software_dev"]
    ws.append(["一、软件开发费用", "", sw["total"],
               f"功能点法：{sw['ufp_total']} UFP → {sw['afp_total']} 调整后功能点 "
               f"→ {sw['effort_total']} 人月", "三.(一) p.5-8"])
    for s in sw["systems"]:
        ws.append(["", f"{s['system']}（{s['dev_category']}）", s["cost"],
                   f"{s['ufp']} UFP × 系数 → {s['effort_man_months']} 人月 × "
                   f"¥{s['man_month_rate']:,.2f}/人月", ""])

    hw = result["hardware"]
    if hw["rows"]:
        ws.append(["二、硬件设备购置费", "", hw["total"],
                   f"{len(hw['rows'])} 项，按询价基线；{hw['quotes_required_count']} 项需三家询价",
                   "三.(三) p.10-11"])
        for r in hw["rows"]:
            ws.append(["", f"{r['name']}（{r['qty']}{r['unit']}）", r["amount"],
                       r["pricing_basis"], ""])

    ws.append(["三、其他建设费用", "",
               result["totals"]["other_fees"], "", "三.(四) p.11-13"])
    for f in result["other_fees"]:
        note = f["blocked_reason"] or (
            f"以 {'+'.join(f['base'])} {f['base_wan']:,.2f} 万元为基数，"
            f"{'超额累进' if f['method'] == 'progressive' else '费率'}")
        ws.append(["", f["name"], f["amount_yuan"], note,
                   f"p.{(f.get('citation') or {}).get('page', '')} "
                   f"{(f.get('citation') or {}).get('section', '')}"])

    ws.append([])
    ws.append(["合计", "", result["totals"]["construction_total"], "", ""])

    if result["scope"]["excluded"]:
        ws.append([])
        ws.append(["【范围说明】未计入本清单的条目：" +
                   "；".join(f"{k} {v} 条" for k, v in result["scope"]["excluded"].items())])

    wb.save(path)
    excel_styler.style_workbook(path)


def emit_fp_worksheet(bom: Bom, result: dict[str, Any], pack: StandardPack,
                      deal: DealConfig, path: Path) -> None:
    """02 功能点测算表 —— 逐条目可复算。

    不依赖单元格布局：数值全部由引擎算定后写入，另附「复算公式」文本列。
    """
    from costing_engine import CostingEngine

    engine = CostingEngine(bom, pack, deal)
    items, _ = engine.in_scope()
    method = deal.counting_method
    size_f = pack.factor("size_change", method)
    reuse_f = pack.factor("reuse", deal.reuse_level)

    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "参数"
    ws.append(["参数", "取值", "出处"])
    for label, dotted in [
        ("规模变更因子", f"factors.size_change.values.{method}"),
        ("复用度调整因子", f"factors.reuse.values.{deal.reuse_level}"),
        ("软件开发生产率（人时/FP）", "rates.productivity_hours_per_fp"),
        ("人月折算系数（人时/人月）", "rates.man_hours_per_month"),
        ("基准人月费率（元/人月）", "rates.base_man_month_rate"),
    ]:
        v, c = pack.value(dotted)
        ws.append([label, v, str(c)])
    weights, w_cite = pack.value(f"fp_counting.{method}.weights")
    ws.append(["功能点权重", "、".join(f"{k}={n}" for k, n in weights.items()),
               str(w_cite)])

    det = wb.create_sheet("功能点明细")
    det.append(["条目ID", "系统", "名称", "类型", "未调整功能点", "规模变更因子",
                "复用度因子", "应用类型因子", "调整后功能点", "复算公式"])
    for i in items:
        if i.cls not in FP_COUNTED_CLASSES or not i.nesma:
            continue
        w = pack.fp_weight(method, i.nesma.type)
        app_f = pack.factor("app_type", i.app_type or "业务处理")
        afp = engine.profile.adjusted_fp(
            w, pack=pack, counting_method=method,
            reuse_level=deal.reuse_level, app_type=i.app_type or "业务处理")
        det.append([i.id, i.path.system, i.name, i.nesma.type, w,
                    size_f, reuse_f, app_f, afp,
                    f"={w}*{size_f}*{reuse_f}*{app_f}"])

    smy = wb.create_sheet("测算汇总")
    smy.append(["系统", "开发类别", "条目数", "未调整功能点", "调整后功能点",
                "开发工作量（人月）", "人月费率（元）", "软件开发费用（元）", "类型分布"])
    for s in result["software_dev"]["systems"]:
        smy.append([s["system"], s["dev_category"], s["items"], s["ufp"], s["afp"],
                    s["effort_man_months"], s["man_month_rate"], s["cost"],
                    str(s["by_type"])])
    sw = result["software_dev"]
    smy.append(["合计", "", "", sw["ufp_total"], sw["afp_total"],
                sw["effort_total"], "", sw["total"], ""])

    wb.save(path)
    excel_styler.style_workbook(path)


def emit_notes(bom: Bom, result: dict[str, Any], pack: StandardPack,
               deal: DealConfig, path: Path) -> None:
    """05 编制说明 —— 方法、参数出处、假设与边界。"""
    d = pack.data
    L = [f"# {result['deal']} 编制说明", "",
         f"编制日期：{date.today().isoformat()}　|　"
         f"BOM {result['bom_version']}　|　标准包 {pack.pack_id}", "",
         "## 一、编制依据", "",
         f"《{d['standard_doc']}》，采用 NESMA {deal.counting_method}"
         f"（GB/T 42588）进行功能规模测算。", "",
         "## 二、测算公式", "",
         "```",
         "调整后功能点 = 功能点合计 × 规模变更因子 × 复用度调整因子 × 应用类型调整因子",
         "开发工作量   = 调整后功能点 × 软件开发生产率 ÷ 人月折算系数",
         "人月费率     = 基准人月费率 × 开发类别调整系数",
         "软件开发费用 = 开发工作量 × 人月费率",
         "```", "",
         f"—— {d['standard_doc']} p.5-8", "",
         "## 三、参数取值与出处", "",
         "| 参数 | 取值 | 出处 |", "|---|---|---|"]

    for label, dotted, unit in [
        ("功能点权重", f"fp_counting.{deal.counting_method}.weights", ""),
        ("规模变更因子", f"factors.size_change.values.{deal.counting_method}", ""),
        ("复用度调整因子", f"factors.reuse.values.{deal.reuse_level}", ""),
        ("软件开发生产率", "rates.productivity_hours_per_fp", " 人时/功能点"),
        ("人月折算系数", "rates.man_hours_per_month", " 人时/人月"),
        ("基准人月费率", "rates.base_man_month_rate", " 元/人月"),
    ]:
        v, c = pack.value(dotted)
        # dict 字面量不能直接进正式文件
        shown = ("、".join(f"{k}={n}" for k, n in v.items())
                 if isinstance(v, dict) else f"{v}{unit}")
        L.append(f"| {label} | {shown} | {c} |")

    L += ["", "## 四、项目类型与复用度", "",
          f"本项目按**{result['project_type']}项目**编制，复用度调整因子取 "
          f"**{pack.factor('reuse', deal.reuse_level)}**。", "",
          "> " + (d["factors"]["reuse"].get("note") or "").strip().replace("\n", "\n> "), "",
          "BOM 中记录的 `maturity`（供应商产品成熟度）用于内部评估增量工时，"
          "**不参与造价** —— 对甲方而言不存在既有系统，全部为新建。", "",
          "## 五、测算结果", "",
          "| 科目 | 金额（元） |", "|---|---:|",
          f"| 软件开发费用 | {result['totals']['software_dev']:,.2f} |",
          f"| 硬件设备购置费 | {result['totals']['hardware_purchase']:,.2f} |",
          f"| 其他建设费用 | {result['totals']['other_fees']:,.2f} |",
          f"| **合计** | **{result['totals']['construction_total']:,.2f}** |", "",
          f"功能点合计 {result['software_dev']['ufp_total']}，"
          f"调整后 {result['software_dev']['afp_total']}，"
          f"开发工作量 {result['software_dev']['effort_total']} 人月。", ""]

    blocked = [f for f in result["other_fees"] if f["blocked_reason"]]
    if blocked:
        L += ["### 未计列的费用项", ""]
        for f in blocked:
            L.append(f"- **{f['name']}**：{f['blocked_reason']}")
        L.append("")

    L += ["## 六、假设与边界", ""]
    ph = [i for i in snapshot(bom, deal.as_of_bom_version) if PLACEHOLDER_TAG in i.tags]
    for k, v in result["scope"]["excluded"].items():
        if k == "占位未确认":
            L.append(f"- **{v} 条占位条目待共创确认，未计入本次报价** —— "
                     f"这些条目对应「每个模型/智能体是否有独立的规则配置与结果查询入口」"
                     f"这一未决问题，确认后需重新测算")
        else:
            L.append(f"- 未计入 {v} 条条目：{k}")
    elif_note = [i for i in snapshot(bom, deal.as_of_bom_version)
                 if i.nesma and not (i.nesma.rationale or "").strip()]
    if elif_note:
        L.append(f"- BOM 中 {len(elif_note)} 条功能点尚未填写类型判定理由，"
                 f"本次报价基于 `draft` 状态的 BOM")

    ns = d.get("delivery_mode_support", {})
    oos = [k for k, v in ns.items() if v.get("status") in ("not_in_scope", "forbidden")]
    if oos:
        L += ["", "### 本标准不覆盖的范围", "",
              f"以下交付形态在《{d['standard_doc']}》中无对应科目或属负面清单，"
              f"**不计入本建设项目预算**：", ""]
        for k in oos:
            L.append(f"- **{k}**：{ns[k].get('note') or ns[k].get('reason')}")
        L.append("")
        L.append("涉及这些内容的费用需另行立项申报。")

    L += ["", "## 七、可复现性", "",
          "本次报价的全部输入已锁定在 `deal.lock.json`（BOM 版本 + 标准包版本 + "
          "交付配置）。任何时点可凭该文件精确重算，结果逐位一致。", "",
          "> Excel 输出为渲染产物，数值由引擎算定后写入静态值，不依赖单元格公式 —— "
          "避免合并单元格、留空等布局问题导致汇总失效。"]

    path.write_text("\n".join(L), encoding="utf-8")


def emit_ops_list(ops_lines, violations, path: Path) -> None:
    """03 运营期费用清单 —— 每行带 payee 与是否计入本次采购。

    in_scope=false 的行**必须列出但不计入**：漏列会被财评认为方案不完整
    （"运维怎么办"答不上来）；计入则超出本标准科目范围会被划掉。
    列而不计并说明另行立项，才是正确做法。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "运营期费用清单"
    ws.append(["运营期费用清单"])
    ws.append(["in_scope=是 的行计入本次采购预算；=否 的行须另行立项或由客户直付，"
               "此处列出以保证方案完整"])
    ws.append([])
    ws.append(["成本元素", "名称", "范围", "交付形态", "年度金额（元）",
               "付款对象", "计入本次采购", "测算依据", "说明"])
    for l in ops_lines:
        ws.append([l.element, l.element_name, l.scope, l.mode, l.annual_yuan,
                   l.payee, "是" if l.in_scope else "否", l.basis, l.note])
    ws.append([])
    ws.append(["合计（全部）", "", "", "",
               sum(l.annual_yuan for l in ops_lines), "", "", "", ""])
    ws.append(["其中计入本次采购", "", "", "",
               sum(l.annual_yuan for l in ops_lines if l.in_scope), "", "", "", ""])

    if violations:
        vs = wb.create_sheet("一致性检查")
        vs.append(["规则", "级别", "说明"])
        for v in violations:
            vs.append([v.rule, v.severity, v.message])
    wb.save(path)
    excel_styler.style_workbook(path)


def emit_tco(comparisons: list[dict], path: Path) -> None:
    """04 TCO 对比 —— 对客户最有说服力的一张表。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TCO对比"
    ws.append(["交付方式 TCO 对比"])
    ws.append(["建设期为一次性投入；运营期按年计，5 年累计"])
    ws.append([])
    payees = sorted({p for c in comparisons for p in c["tco5"]["annual_by_payee"]})
    ws.append(["方案", "说明", "建设期（元）", "年度经常性（元）",
               *[f"　其中付{p}" for p in payees], "3年TCO（元）", "5年TCO（元）"])
    for c in comparisons:
        t3, t5 = c["tco3"], c["tco5"]
        ws.append([c["name"], c["description"], t5["construction"], t5["annual_total"],
                   *[t5["annual_by_payee"].get(p, 0) for p in payees],
                   t3["tco"], t5["tco"]])
    ws.append([])
    best = min(comparisons, key=lambda c: c["tco5"]["tco"])
    ws.append([f"5 年 TCO 最低：{best['name']}（¥{best['tco5']['tco']:,.2f}）"])

    gaps = [g for c in comparisons for g in c.get("gaps", [])]
    if gaps:
        gs = wb.create_sheet("待核价与缺口")
        gs.append(["方案", "类型", "说明"])
        for c in comparisons:
            for g in c.get("gaps", []):
                gs.append([c["name"], g["kind"], g["note"]])
    wb.save(path)
    excel_styler.style_workbook(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成报价输出物")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--deal-id", default="报价")
    ap.add_argument("--as-of", help="按指定 BOM 版本时点重算（历史报价复现）")
    ap.add_argument("--include-placeholders", action="store_true")
    ap.add_argument("--project-type", default="新建")
    ap.add_argument("--reuse-level", default="新建")
    ap.add_argument("--modes", type=Path, help="delivery-modes/modes.yaml")
    ap.add_argument("--internal-cost", type=Path,
                    help="delivery-modes/internal-cost-model.yaml（敏感，仅本地）")
    ap.add_argument("--delivery-plan", type=Path, help="本商机采用的交付方案")
    ap.add_argument("--scenarios", type=Path, nargs="*", default=[],
                    help="用于 TCO 对比的场景预设")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    pack = StandardPack.load(args.pack)
    deal = DealConfig(deal_id=args.deal_id, project_type=args.project_type,
                      reuse_level=args.reuse_level,
                      include_placeholders=args.include_placeholders,
                      as_of_bom_version=args.as_of)

    delivery = None
    dplan = None
    if args.delivery_plan and args.modes:
        import yaml as _yaml
        modes_doc = _yaml.safe_load(args.modes.read_text(encoding="utf-8"))
        internal = (_yaml.safe_load(args.internal_cost.read_text(encoding="utf-8"))
                    if args.internal_cost else {})
        base_items, _ = CostingEngine(bom, pack, deal).in_scope()
        dplan = DeliveryPlan.load(args.modes, args.delivery_plan)
        delivery = DeliveryContext(assigned=dplan.assign(base_items),
                                   modes=modes_doc["modes"], internal=internal)

    result = CostingEngine(bom, pack, deal, delivery).run()
    out = args.out / "out"
    out.mkdir(parents=True, exist_ok=True)

    emit_procurement_list(result, pack, out / "01-建设期采购清单.xlsx")
    emit_fp_worksheet(bom, result, pack, deal, out / "02-功能点测算表.xlsx")
    emit_notes(bom, result, pack, deal, out / "05-编制说明.md")
    (args.out / "deal.lock.json").write_text(
        json.dumps(lock(result, bom, pack, deal), ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out / "costing-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if delivery is not None:
        base_items, _ = CostingEngine(bom, pack, deal).in_scope()
        om = OpsModel.load(args.modes, args.internal_cost)
        ops = om.expand(base_items, delivery.assigned)
        violations = dplan.check(base_items, delivery.assigned, pack)
        emit_ops_list(ops, violations, out / "03-运营期费用清单.xlsx")

        comparisons = []
        for sp in args.scenarios:
            import yaml as _yaml
            doc = _yaml.safe_load(Path(sp).read_text(encoding="utf-8"))
            p2 = DeliveryPlan.load(args.modes, sp)
            a2 = p2.assign(base_items)
            dc2 = DeliveryContext(a2, delivery.modes, delivery.internal)
            r2 = CostingEngine(bom, pack, deal, dc2).run()
            ops2 = om.expand(base_items, a2)
            cons2 = r2["totals"]["construction_total"]
            gaps = [{"kind": "待核价", "note": f"{g['system']} 的 {g['element']}：{g['note']}"}
                    for g in r2["pending_pricing"]]
            gaps += [{"kind": f"一致性-{v.rule}", "note": v.message}
                     for v in p2.check(base_items, a2, pack) if v.severity == "fail"]
            comparisons.append({
                "name": doc.get("name", Path(sp).stem),
                "description": doc.get("description", ""),
                "tco3": tco(cons2, ops2, 3), "tco5": tco(cons2, ops2, 5),
                "gaps": gaps})
        if comparisons:
            emit_tco(comparisons, out / "04-TCO对比.xlsx")
            (out / "tco-comparison.json").write_text(
                json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8")

        margins = [m for m in om.margin_check() if m["below_threshold"]]
        for m in margins:
            print(f"  ⚠ 订阅毛利预警：{m['kind']} 毛利 {m['margin']:.1%} "
                  f"低于阈值 {m['threshold']:.0%}（内部信息，未写入对外文档）")

    t = result["totals"]
    print(f"BOM {result['bom_version']} × {pack.pack_id} × {deal.project_type}项目")
    print(f"  范围 {result['scope']['items']} 条"
          + (f"（排除 {result['scope']['excluded']}）" if result["scope"]["excluded"] else ""))
    print(f"  软件开发 ¥{t['software_dev']:,.2f}　硬件 ¥{t['hardware_purchase']:,.2f}　"
          f"其他 ¥{t['other_fees']:,.2f}")
    print(f"  建设期合计 ¥{t['construction_total']:,.2f}")
    print(f"  输出 → {out}")


if __name__ == "__main__":
    main()
