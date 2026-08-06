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
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl

import gov_sheet
from bom_schema import Bom, Vocabulary, is_fp_counted
from costing_engine import CostingEngine, DealConfig, snapshot
from nesma_weights import xlround
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

    # 三档区间 —— 只在标准包给了浮动依据时出
    band = pack.productivity_band()
    band_out = None
    if band:
        prof = eng.profile
        afp_total = result["software_dev"]["afp_total"]
        hours_pm = pack.rate("man_hours_per_month")
        band_out = []
        for label, prod in band:
            effort = xlround(afp_total * prod / hours_pm, 2)
            # 费率按各系统加权 —— 与单点口径同源，不另起一套
            cost = 0.0
            for s in result["software_dev"]["systems"]:
                e = xlround(s["afp"] * prod / hours_pm, 2)
                cost += xlround(e * s["man_month_rate"], 2)
            band_out.append({"档": label, "生产率": round(prod, 4),
                             "工作量人月": effort, "软件开发费": xlround(cost, 2)})

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
        "productivity_band": band_out,
        "band_note": (None if band_out else
                      "本标准未规定生产率浮动区间，按 P50 单值计 —— "
                      "不替标准编造区间"),
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
    """区域基准清单 —— 与第三层送审包同一套结构规范（`gov_sheet`）。

    这两份表是同一批人对着看的。此前第二层还留着第三层已经修掉的四个毛病：
    `类型分布` 是 dict 的 repr、`产品成熟度` 是 `existing`/`new`、
    复算公式漏 `ROUND` 差半分、明细没有合计行。
    """
    method = bl["counting_method"]
    sw = bl["software_dev"]
    sub = (f"{bl['standard_doc']}　|　BOM {bl['bom_version']}　|　"
           f"标准包 {pack.pack_id}　|　计数方法 {method}")
    wb = gov_sheet.new_workbook()

    smy = gov_sheet.GovSheet(
        wb, "00_基准汇总", title=f"{pack.pack_id} 区域基准 · BOM {bl['bom_version']}",
        subtitle=sub + "　|　**含占位条目**，是否计入报价由第三层决定",
        rollup=True,                 # 它汇总 01_功能点明细，不能与之相加
        columns=[
            gov_sheet.Col("子系统", width=40),
            gov_sheet.Col("开发类别", width=18),
            gov_sheet.Col("条目数", "int", width=9, sum=True),
            gov_sheet.Col("未调整功能点", "fp", width=13, sum=True),
            gov_sheet.Col("调整后功能点", "fp", width=13, sum=True, role="fp"),
            gov_sheet.Col("开发工作量（人月）", "fp", width=15, sum=True),
            gov_sheet.Col("人月费率（元）", "money", width=15),
            gov_sheet.Col("软件开发费用（元）", "money", width=17, sum=True,
                          role="total"),
            gov_sheet.Col("功能点类型分布", width=30),
        ])
    for s in sw["systems"]:
        smy.row({"子系统": s["system"], "开发类别": s["dev_category"],
                 "条目数": s["items"], "未调整功能点": s["ufp"],
                 "调整后功能点": s["afp"],
                 "开发工作量（人月）": s["effort_man_months"],
                 "人月费率（元）": s["man_month_rate"],
                 "软件开发费用（元）": s["cost"],
                 "功能点类型分布": gov_sheet.flatten_counts(s["by_type"])})
    smy.total("合计", expect={"未调整功能点": sw["ufp_total"],
                              "调整后功能点": sw["afp_total"],
                              "软件开发费用（元）": sw["total"]})
    smy.finish()

    det = gov_sheet.GovSheet(
        wb, "01_功能点明细", title=f"{pack.pack_id} 区域基准 · 功能点明细",
        subtitle=sub + "　|　「复算式」为活公式，可现场点开核验",
        columns=[
            gov_sheet.Col("条目编号", width=18),
            gov_sheet.Col("子系统", width=34),
            gov_sheet.Col("一级模块", width=20),
            gov_sheet.Col("功能点名称", width=34, role="detail"),
            gov_sheet.Col("功能点类型", width=11),
            gov_sheet.Col("未调整功能点", "fp", width=13, sum=True),
            gov_sheet.Col("规模变更因子", "rate", width=13),
            gov_sheet.Col("产品成熟度", width=12),
            gov_sheet.Col("复用度档位", width=14),
            gov_sheet.Col("复用度因子", "rate", width=12),
            gov_sheet.Col("应用类型", width=14),
            gov_sheet.Col("本标准称谓", width=16),
            gov_sheet.Col("应用类型因子", "rate", width=13),
            gov_sheet.Col("调整后功能点", "fp", width=13, sum=True, role="fp"),
            gov_sheet.Col("占位条目", width=10),
            gov_sheet.Col("复算式", "fp", width=16),
        ])
    n_ph = 0
    for d in bl["detail"]:
        prod = d["ufp"] * d["size_change"] * d["reuse_factor"] * d["app_factor"]
        # 公式带 ROUND 才与引擎的 xlround 同语义 —— 裸乘积差半分（9.075 vs 9.08）
        if abs(xlround(prod, 2) - d["afp"]) > 0.0001:
            raise gov_sheet.GovSheetError(
                f"{d['id']}：复算式与引擎值不符 —— ROUND({prod:.6f}, 2) = "
                f"{xlround(prod, 2)}，引擎给 {d['afp']}")
        n_ph += bool(d["placeholder"])
        det.row({"条目编号": d["id"], "子系统": d["system"], "一级模块": d["l1"],
                 "功能点名称": d["name"], "功能点类型": d["type"],
                 "未调整功能点": d["ufp"], "规模变更因子": d["size_change"],
                 "产品成熟度": gov_sheet.label(d["maturity"]),
                 "复用度档位": d["reuse_level"], "复用度因子": d["reuse_factor"],
                 "应用类型": d["app_type"], "本标准称谓": d["app_type_local"],
                 "应用类型因子": d["app_factor"], "调整后功能点": d["afp"],
                 "占位条目": "是" if d["placeholder"] else "",
                 "复算式": f'=ROUND({d["ufp"]}*{d["size_change"]}'
                           f'*{d["reuse_factor"]}*{d["app_factor"]},2)'})
    # 第二层不做交付形态过滤，所以明细与汇总**必须逐位相等** ——
    # 这正是与第三层的分野：那里两者相差 917.08 FP（订阅形态列而不计），
    # 这里差一分钱都说明基准自己算岔了。锁死比写句说明有用。
    det.total("合计", expect={"未调整功能点": sw["ufp_total"],
                              "调整后功能点": sw["afp_total"]})
    det.note(f"　口径说明：本表 {det.seq_count} 条，其中 {n_ph} 条为占位"
             f"（「已知的未知」，基准要完整）。合计与 00_基准汇总逐位一致 —— "
             f"第二层不判交付形态，是否计入报价由第三层（quote_generate）裁定。")
    det.finish()

    par = gov_sheet.GovSheet(
        wb, "02_计价参数", title=f"{pack.pack_id} 区域基准 · 计价参数与依据",
        subtitle="每个取值均标注标准原文页码与条款，供逐项核对",
        columns=[gov_sheet.Col("参数", width=28),
                 gov_sheet.Col("取值", width=34),
                 gov_sheet.Col("标准出处", width=40)],
        clause_required=True, clause_name="标准出处")
    for lb, dotted in [
        ("规模变更因子", f"factors.size_change.values.{method}"),
        ("软件开发生产率（人时/FP）", "rates.productivity_hours_per_fp"),
        ("人月折算系数（人时/人月）", "rates.man_hours_per_month"),
        ("基准人月费率（元/人月）", "rates.base_man_month_rate"),
    ]:
        try:
            v, c = pack.value(dotted)
            par.row({"参数": lb, "取值": v}, clause=str(c))
        except Exception as e:      # 本包没有这个维度（如广东无开发类别）
            par.row({"参数": lb, "取值": "—"}, clause=f"本标准无此项：{e}")
    weights, w_cite = pack.value(f"fp_counting.{method}.weights")
    par.row({"参数": "功能点权重",
             "取值": "、".join(f"{k}={n}" for k, n in weights.items())},
            clause=str(w_cite))
    par.finish()

    if bl["purchase_subjects"]:
        pu = gov_sheet.GovSheet(
            wb, "03_采购标的", title=f"{pack.pack_id} 区域基准 · 采购标的",
            subtitle="**只列不定价、不判交付形态** —— 形态是第三层的事",
            columns=[
                gov_sheet.Col("条目编号", width=18),
                gov_sheet.Col("子系统", width=28),
                gov_sheet.Col("名称", width=34, role="detail"),
                gov_sheet.Col("类别", width=12),
                gov_sheet.Col("科目", width=30),
                gov_sheet.Col("科目号", width=14),
                gov_sheet.Col("计价方式", width=14),
                gov_sheet.Col("单位", width=8),
                gov_sheet.Col("数量", "int", width=9),
                gov_sheet.Col("参考单价（元）", "money", width=15),
            ])
        n_nop = 0
        for r in bl["purchase_subjects"]:
            price = r["reference_unit_price_yuan"]
            n_nop += price is None
            pu.row({"条目编号": r["id"], "子系统": r["system"], "名称": r["name"],
                    "类别": r["class"], "科目": r["subject"],
                    "科目号": r["subject_code"],
                    "计价方式": gov_sheet.label(r["pricing_model"] or "待定"),
                    "单位": r["unit"], "数量": r["qty"],
                    # 无价的写「待询价」而不是留空 —— ¥0 与「没有价格」
                    # 必须能区分，这是本项目反复吃亏的地方
                    "参考单价（元）": price if price is not None else "待询价"})
        pu.note(f"　共 {pu.seq_count} 项，其中 {n_nop} 项无参考单价（显示「待询价」）。"
                f"**空白与 ¥0 是两回事** —— 前者是价格未到位，后者是真的不要钱。")
        pu.finish()

    gov_sheet.save(wb, path)


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


def diff(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """两份基准的差异。**按变的是哪个轴分派** —— 两个轴不能同时变。

        同 BOM 版本、不同标准包 → 跨区域分析（差额全来自标准的系数与公式）
        同标准包、不同 BOM 版本 → 版本变更分析（差额全来自 BOM 改动）
        两者都变              → 拒绝：无法归因

    最后一条是这个函数最有用的部分。两个轴同时变时，金额差里有多少来自
    改了标准、多少来自改了 BOM，**分不开**；出一张看着很像回事的对比表
    只会让人得出错误结论。宁可拒绝。
    """
    same_pack = a["pack_id"] == b["pack_id"]
    same_bom = a["bom_version"] == b["bom_version"]
    if not same_pack and not same_bom:
        return [
            "# 拒绝对比：两个轴同时变了", "",
            f"- 标准包　{a['pack_id']} → {b['pack_id']}",
            f"- BOM 版本 {a['bom_version']} → {b['bom_version']}", "",
            "金额差里有多少来自改标准、多少来自改 BOM，**分不开**。",
            "出一张对比表只会让人得出错误结论。", "",
            "改法：先固定一个轴。",
            f"  跨区域比 → 两侧都用 BOM {b['bom_version']} 重建基准",
            f"  比版本变更 → 两侧都用 {b['pack_id']} 重建基准",
        ]
    if same_pack and not same_bom:
        return _diff_versions(a, b)
    return _diff_regions(a, b)


def _item_changes(a: dict[str, Any], b: dict[str, Any]) -> dict[str, list]:
    """逐条目比对两份基准的 detail。

    基准的 `detail` 是**当时那一刻的冻结记录** —— 这是能重建历史类型的
    唯一来源。`snapshot(bom, as_of)` 只能按 since/deprecated_in 过滤成员，
    条目**当时判成什么类型**它答不出来（改过 type 之后，历史快照给的是新值）。
    """
    da = {d["id"]: d for d in a["detail"]}
    db = {d["id"]: d for d in b["detail"]}
    out: dict[str, list] = {"added": [], "removed": [], "retyped": [],
                            "refactored": []}
    for iid in sorted(set(db) - set(da)):
        out["added"].append(db[iid])
    for iid in sorted(set(da) - set(db)):
        out["removed"].append(da[iid])
    for iid in sorted(set(da) & set(db)):
        x, y = da[iid], db[iid]
        if x["type"] != y["type"]:
            out["retyped"].append((x, y))
        elif x["ufp"] != y["ufp"] or x["afp"] != y["afp"]:
            out["refactored"].append((x, y))
    return out


def _diff_versions(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """同一标准包下两个 BOM 版本的差异 —— 差额全部来自 BOM 改动。

    存在的理由：0.17.0 → 0.18.0 那张对比表此前是**手工拼的**，
    在一次性脚本里硬编码了旧数字，没有任何东西验证它，却写进了 CHANGELOG。
    这与「送审表里印出来的数必须能复算」是同一条纪律 —— 自己的变更日志
    也不该例外。
    """
    ch = _item_changes(a, b)
    sa, sb = a["software_dev"], b["software_dev"]
    L = [f"# BOM 版本变更：{a['bom_version']} → {b['bom_version']}", "",
         f"标准包 `{a['pack_id']}` 固定，**差额全部来自 BOM 改动**。", "",
         "## 总量", "",
         "| 项 | 旧 | 新 | 差 |", "|---|---:|---:|---:|"]
    for k, label, fmt in (("ufp_total", "未调整功能点", ",.0f"),
                          ("afp_total", "调整后功能点", ",.2f"),
                          ("effort_total", "工作量（人月）", ",.2f"),
                          ("total", "软件开发费（元）", ",.2f")):
        d = sb[k] - sa[k]
        L.append(f"| {label} | {sa[k]:{fmt}} | {sb[k]:{fmt}} | {d:+{fmt}} |")
    if sa["total"]:
        L.append(f"\n软件开发费变动 **{(sb['total']/sa['total']-1):+.2%}**。")

    L += ["", "## 条目变动", "",
          "| 类别 | 条数 | ΔUFP |", "|---|---:|---:|"]
    d_add = sum(i["ufp"] for i in ch["added"])
    d_rm = -sum(i["ufp"] for i in ch["removed"])
    d_rt = sum(y["ufp"] - x["ufp"] for x, y in ch["retyped"])
    d_rf = sum(y["ufp"] - x["ufp"] for x, y in ch["refactored"])
    for label, n, d in (("新增", len(ch["added"]), d_add),
                        ("废弃", len(ch["removed"]), d_rm),
                        ("改判类型", len(ch["retyped"]), d_rt),
                        ("权重/因子变动", len(ch["refactored"]), d_rf)):
        if n:
            L.append(f"| {label} | {n} | {d:+,.0f} |")
    L.append(f"| **合计** | | **{d_add + d_rm + d_rt + d_rf:+,.0f}** |")
    # 对账：逐条目累加必须等于总量差，否则这张表是错的
    want = sb["ufp_total"] - sa["ufp_total"]
    got = d_add + d_rm + d_rt + d_rf
    if got != want:
        L += ["", f"> ⚠ **逐条目累加 {got:+,.0f} ≠ 总量差 {want:+,.0f}** —— "
                  f"本表不可信，请检查 detail 是否完整。"]

    if ch["retyped"]:
        by = defaultdict(list)
        for x, y in ch["retyped"]:
            by[(x["type"], y["type"])].append(y)
        L += ["", "### 改判类型", "", "| 改判 | 条数 | ΔUFP | 涉及子系统 |",
              "|---|---:|---:|---|"]
        for (t0, t1), rows in sorted(by.items(), key=lambda kv: -len(kv[1])):
            du = sum(r["ufp"] for r in rows) - sum(
                x["ufp"] for x, y in ch["retyped"] if x["type"] == t0
                and y["type"] == t1)
            syss = sorted({r["system"].split("：")[-1].split("（")[0][:14]
                           for r in rows})
            L.append(f"| {t0} → {t1} | {len(rows)} | {du:+,.0f} | "
                     f"{'、'.join(syss[:4])}{'…' if len(syss) > 4 else ''} |")

    for key, label in (("added", "新增"), ("removed", "废弃")):
        if not ch[key]:
            continue
        by_sys = Counter(i["system"].split("：")[-1].split("（")[0][:20]
                         for i in ch[key])
        L += ["", f"### {label}（{len(ch[key])} 条）", ""]
        for s, n in by_sys.most_common():
            L.append(f"- {s}：{n} 条")

    L += ["", "## 分子系统", "", "| 子系统 | 旧 UFP | 新 UFP | Δ | 旧金额 | 新金额 | Δ |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    ma = {s["system"]: s for s in sa["systems"]}
    mb = {s["system"]: s for s in sb["systems"]}
    for sysn in sorted(set(ma) | set(mb)):
        x, y = ma.get(sysn), mb.get(sysn)
        u0, u1 = (x["ufp"] if x else 0), (y["ufp"] if y else 0)
        c0, c1 = (x["cost"] if x else 0), (y["cost"] if y else 0)
        if u0 == u1 and c0 == c1:
            continue
        L.append(f"| {sysn[:36]} | {u0:,.0f} | {u1:,.0f} | {u1-u0:+,.0f} | "
                 f"{c0:,.2f} | {c1:,.2f} | {c1-c0:+,.2f} |")
    L += ["", "> 本表由 `baseline_build --diff` 生成，可直接粘进 CHANGELOG。",
          "> 数字取自两份 baseline.json 的冻结记录，不是手工抄的。"]
    return L


def _diff_regions(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """两份基准的差异 —— 这就是跨区域分析。

    第二层不含任何商机决策，所以两份基准**天生可比**：UFP 必须一致
    （BOM 零改动的证明），差额全部来自区域标准的系数与公式结构。
    """
    L = [f"# 区域基准对比", "",
         f"| | {a['pack_id']} | {b['pack_id']} |", "|---|---|---|",
         f"| 标准 | {a['standard_doc'][:40]} | {b['standard_doc'][:40]} |",
         f"| 公式档案 | {a['formula_profile']} | {b['formula_profile']} |",
         f"| BOM 版本 | {a['bom_version']} | {b['bom_version']} |", ""]

    sa, sb = a["software_dev"], b["software_dev"]
    same = sa["ufp_total"] == sb["ufp_total"]
    L += ["## 规模", "",
          f"UFP {sa['ufp_total']} vs {sb['ufp_total']} —— "
          + ("**一致**，即同一份 BOM 零改动出两地清单。" if same else
             "**不一致**，说明 BOM 或计数方法有差异，需先排查。"), ""]

    L += ["## 金额", "", "| 项 | A | B | 比值 |", "|---|---:|---:|---:|"]
    for k, label in (("afp_total", "调整后功能点"), ("effort_total", "工作量（人月）"),
                     ("total", "软件开发费（元）")):
        r = (sb[k] / sa[k]) if sa[k] else float("nan")
        L.append(f"| {label} | {sa[k]:,.2f} | {sb[k]:,.2f} | {r:.5f} |")
    L.append("")

    # 逐因子分解：取任一条目看两侧的因子链，再看费率
    da = {d["id"]: d for d in a["detail"]}
    db = {d["id"]: d for d in b["detail"]}
    k = next((i for i in da if i in db), None)
    if k:
        L += ["## 因子链（抽样一条，因子对全库同构）", "",
              "| 因子 | A | B | 比值 |", "|---|---:|---:|---:|"]
        for f, label in (("size_change", "规模变更"), ("reuse_factor", "复用度"),
                         ("app_factor", "应用类型")):
            va, vb = da[k][f], db[k][f]
            L.append(f"| {label} | {va} | {vb} | {(vb/va if va else float('nan')):.5f} |")
        L += ["", f"抽样条目 `{k}`；应用类型本地称谓：{da[k]['app_type_local']} / "
                  f"{db[k]['app_type_local']}", ""]

    ba, bb = a.get("productivity_band"), b.get("productivity_band")
    L += ["## 生产率区间", "",
          f"- {a['pack_id']}：" + (f"{len(ba)} 档" if ba else a.get("band_note", "单点")),
          f"- {b['pack_id']}：" + (f"{len(bb)} 档" if bb else b.get("band_note", "单点")), ""]

    fa = {kk: v.get("status") for kk, v in (a.get("subject_framework") or {}).items()}
    fb = {kk: v.get("status") for kk, v in (b.get("subject_framework") or {}).items()}
    rows = [(kk, fa.get(kk, "—"), fb.get(kk, "—")) for kk in sorted(set(fa) | set(fb))
            if fa.get(kk) != fb.get(kk)]
    if rows:
        L += ["## 科目支持差异", "", "| 交付形态 | A | B |", "|---|---|---|"]
        L += [f"| {r[0]} | {r[1]} | {r[2]} |" for r in rows]
        L += ["", "**这是换省时最容易出事的一栏** —— 某形态在一地有科目、"
                  "另一地 not_in_scope，报价会算得出来但落不了账。", ""]
    return L


def main() -> None:
    ap = argparse.ArgumentParser(description="第二层：BOM × 区域标准包 → 区域基准")
    ap.add_argument("--bom", type=Path)
    ap.add_argument("--pack", type=Path)
    ap.add_argument("--out", type=Path,
                    help="默认 baselines/<pack_id>@bom-<version>")
    ap.add_argument("--counting-method", default="估算功能点法")
    ap.add_argument("--as-of", help="按指定 BOM 版本时点重算")
    ap.add_argument("--allow-deprecated-pack", action="store_true",
                    help="允许加载已退役的标准包 —— 仅用于历史复算")
    ap.add_argument("--diff", nargs=2, type=Path, metavar=("A", "B"),
                    help="对比两份已生成的基准目录，不重新构建")
    args = ap.parse_args()

    if args.diff:
        a, b = (json.loads((d / "baseline.json").read_text(encoding="utf-8"))
                for d in args.diff)
        print("\n".join(diff(a, b)))
        return

    if not (args.bom and args.pack):
        ap.error("需要 --bom 与 --pack（或用 --diff 对比两份已生成的基准）")
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
