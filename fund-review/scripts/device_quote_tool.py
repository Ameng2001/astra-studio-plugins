"""device_quote_tool —— 设备配置录入工具表。一个园一个 sheet，选场景→选设备→填数量。

## 它是什么

给方案/销售用的**录入工具**，不是送审件：

    选子场景  → 自动带出一级场景
    选设备    → 自动带出型号 / 性能参数 / 单位 / 单价（设备下拉只列该场景下的）
    填数量、备注 → 金额自动算

数据源是 `bom/devices/`（第一层目录），录入结果就是第二层的配置。
填完回读，重出甲附形态的配置表。

## 为什么这里可以用活公式

本项目的铁律是「数值由引擎算定，Excel 不承担计算」，那条来自 P0：
1,200 行明细里 SUMIF 被合并单元格击穿，少算 ¥421.6 万且不报错。

**但那条铁律管的是送审件。** 这张是输入工具 —— 人在里面选和填，
没有活公式就没法「选了设备自动带出型号」，工具就不成立。
分界线是：**工具表的数不作数，送审金额一律由引擎按回读结果重算。**
所以工具表页眉写明「本表金额仅供录入时参考」。

## 联动怎么做的

设备下拉要「只列该场景下的设备」。Excel 内联候选有 255 字符上限，
而智慧教学场景一个场景的候选串就 294 字符 —— 只能用区域引用。
做法：隐藏页按场景码分块排好设备，每块起一个定义名 `SC_S01`…，
下拉公式 `=INDIRECT("SC_"&<场景码>)`。场景码取自 taxonomy.yaml 的声明值。

用法：
    python3 device_quote_tool.py --root <项目根> [--out <xlsx>]
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from openpyxl.utils import get_column_letter as CL
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

import gov_sheet as gs

BATCH = "06_200所基础园配置"
BLANK_ROWS = 40          # 每个园留的空白录入行

#: 录入表列：(标题, 宽, 角色)　角色 input=黄（人填）/ calc=绿（公式带出）/ None=普通
COLS = [
    ("序号", 6, None),
    ("子场景", 22, "input"),
    ("一级场景", 14, "calc"),
    ("设备名称", 30, "input"),
    ("参考品牌型号", 26, "calc"),
    ("性能参数", 40, "calc"),
    ("单位", 7, "calc"),
    ("数量", 9, "input"),
    # **单价在各园页是只读的**，查「00_本商机报价」那一页。
    # 价格是**每个物料一个**，不是每一行一个：357 行只有 89 个物料，
    # 按行填等于同一个价要填最多 9 遍，还得自己保证几遍填的一样 ——
    # 填不一致时表上看不出来，而金额已经错了。
    ("市场单价(元)", 13, "calc"),
    ("金额(元)", 14, "calc"),
    # 成本两列**只在丙附**（内部件，文件名标着「勿送审」）。
    # 甲/甲附/乙 读 materials.yaml，其中不含成本价；成本价另存
    # bom/devices/cost-reference.yaml，只有本模块读它。
    ("成本单价(元)·内部", 15, "calc"),
    ("成本金额(元)·内部", 16, "calc"),
    # 「配置口径」**只读**，与人填的「备注」分开两列：备注是方案人员写给
    # 编制人看的（例外、说明），口径是目录告诉方案人员的（这台设备按什么配）。
    # 混一列的话，人一改就把口径覆盖了，而覆盖掉的是判断依据。
    ("配置口径", 30, "calc"),
    ("备注", 30, "input"),
    ("场景码", 8, None),          # 辅助列，隐藏
    ("口径键", 8, None),          # 辅助列，隐藏：场景|设备
]
IDX = {c[0]: i + 1 for i, c in enumerate(COLS)}


def load(root: Path) -> dict[str, Any]:
    d = root / "bom" / "devices"
    return {n: yaml.safe_load((d / f"{n}.yaml").read_text(encoding="utf-8"))
            for n in ("taxonomy", "materials", "catalog")}


#: rule.kind → 人话。方案人员要的是「这台按什么配」，不是 kind 字符串。
_RULE_CN = {
    "per_garden": "每园 {qty} 台",
    "per_class": "每班 {qty} 台",
    "per_child": "每人 {qty} 台",
    "per_room": "每教室 {qty} 台",
    "per_facility": "每点位 {qty} 台",
}


def _rule_text(e: dict) -> str:
    """把 catalog 条目的配置口径翻成一句话。

    **不猜**：解析不出来的照实写「待补口径」并把源表备注附上 ——
    方案人员看到原文还能自己判断，看到一个编出来的口径就只能照着错。
    """
    def one(r: dict) -> str:
        k = r.get("kind")
        if k == "composite":
            return " + ".join(one(x) for x in (r.get("parts") or []))
        tpl = _RULE_CN.get(k)
        if not tpl:
            return ""
        s = tpl.format(qty=(f"{r.get('qty'):g}" if r.get("qty") is not None else "N"))
        if r.get("at"):
            s += f"（{r['at']}）"
        return s
    # 人确认过的口径优先于源表解析结果
    rc = e.get("rule_confirmed") or {}
    if rc.get("kind"):
        s = one({"kind": {"每园 N": "per_garden", "每班 N": "per_class",
                          "每人 N": "per_child", "每点位 N": "per_facility"}
                 .get(rc["kind"], rc["kind"]),
                 "qty": rc.get("qty"), "at": rc.get("at")})
        if s:
            return s + "　（已确认）"
    parts, notes = [], []
    for pl in e.get("placements") or []:
        s = one(pl.get("rule") or {})
        if s and s not in parts:
            parts.append(s)
        n_ = (pl.get("note") or "").strip()
        if n_ and n_ not in notes:
            notes.append(n_)
    if parts:
        return " / ".join(parts)
    # 解析不出来：写明「待补」，并附源表原文（截断，够判断即可）
    why = ""
    for pl in e.get("placements") or []:
        r = pl.get("rule") or {}
        if r.get("kind") == "internal_only":
            return "**内部口径，不对外**"
        if r.get("reason"):
            why = r["reason"]
            break
    txt = "**待补口径**" + (f"（{why}）" if why else "")
    if notes:
        txt += "　源表备注：" + "；".join(notes)[:60]
    return txt


#: 名称→显示名。**同名不同料号时才加型号后缀**，其余保持原名。
#: 丙附整条链路拿名字当键（下拉 / VLOOKUP 型号单价 / 口径表 / 回读反查料号），
#: 同名物料会让这四处全部失配 —— 实测睡眠垫 HA0/HA1 同名，下拉去重成一条，
#: 选中后 VLOOKUP 返回排序靠前的 HA0，**根本选不到 HA1**；
#: 电子班牌与智能手环那两组单价差 3,500 / 280 元，选错直接影响金额而表上看不出。
#: 全表统一加后缀会让下拉项都变长，故只给同名的加，并在下方加守卫：
#: 出现新的同名组而没能消歧时**硬失败**，不静默退化。
def display_names(mats: list[dict]) -> dict[str, str]:
    """料号 → 显示名。同名的加「｜型号」，型号也缺就加料号。"""
    cnt: dict[str, int] = {}
    for m in mats:
        cnt[m["name"]] = cnt.get(m["name"], 0) + 1
    out, used = {}, {}
    for m in mats:
        n = m["name"]
        if cnt[n] > 1:
            # 消歧优先级：型号 → OA 系统名 → 料号。
            # 「幼视宝算法」四条的型号全填「无」，区分信息在 OA 名里
            # （基础平台及算法服务 / 园区智慧管理 / 老师不当行为观察 / 儿童饮水），
            # 退到料号能让构建过去，但下拉里四条只差一串编码，人选不出来。
            md = str(m.get("model") or "").strip()
            oa = str(m.get("oa_name") or "").strip()
            if md and not md.startswith("**") and md not in ("无", "-"):
                n = f"{n}｜{md}"
            elif oa:
                n = f"{n}｜{oa}"
            else:
                n = f"{n}｜{m['code']}"
        out[m["code"]] = n
        used.setdefault(n, []).append(m["code"])
    dup = {n: c for n, c in used.items() if len(c) > 1}
    if dup:
        raise SystemExit(
            f"消歧后仍有重名：{dup}\n"
            f"  丙附用名字当键（下拉/VLOOKUP/口径表/回读反查料号），重名会让这几处"
            f"全部失配，且失配的表现是「带出了另一台设备的型号和单价」，表上看不出来。\n"
            f"  请在飞书物料主数据里把型号填全或改名消歧。")
    return out


def _hidden_sources(wb, tax: list[dict], mats: list[dict],
                    cat: list[dict], disp: dict[str, str] | None = None,
                    cost_ref: dict | None = None) -> None:
    """隐藏页：场景表 / 设备表（按场景码分块）/ 物料表。并起定义名。"""
    # ---- 场景表 ----
    ws = wb.create_sheet("_场景表")
    ws.append(["子场景", "场景码", "一级场景"])
    seen = {s["scene"]: s for s in tax}
    scenes = [e["scene"] for e in cat]
    order, got = [], set()
    for s in scenes:
        if s not in got:
            got.add(s); order.append(s)
    # **按「一级场景 → 场景码」排，不按中文字序。**
    # 中文 Unicode 排序会把不同一级场景的项完全打散（AI助教/科学教育、
    # 健康体测/健康保育、兴趣潜能/科学教育…），选场景的人得在十几项里来回找。
    # 「未归类」永远排最后 —— 它是待办不是选项。
    def _key(s: str):
        m = seen.get(s) or {"code": "S99", "l1": "**待补**"}
        last = 1 if (s.startswith("未归类") or m["l1"] == "**待补**") else 0
        code = str(m["code"] or "")
        # 场景码有 "S04" 与 "20" 两种写法（飞书上不统一），
        # 取数字部分排序，取不到的排在本组末尾。
        num = "".join(ch for ch in code if ch.isdigit())
        return (last, str(m["l1"]), int(num) if num else 999, s)
    order_sorted = sorted(order, key=_key)
    for s in order_sorted:
        t = seen.get(s) or {"code": "S99", "l1": "**待补**"}
        ws.append([s, t["code"], t["l1"]])
    n = ws.max_row
    wb.defined_names.add(DefinedName("场景表", attr_text=f"_场景表!$A$2:$C${n}"))
    # 下拉**只能指一列**。「场景表」是三列（子场景/场景码/一级场景），
    # Excel 的列表校验会把整个区域逐格展开 —— 下拉里就成了
    # 「安全接送场景 · S01 · 健康保育 · 健康体测场景 · S04 · …」，
    # 60 个条目里只有 20 个是真场景名，选的人无从下手。
    # 另起一个只含 A 列的定义名给校验用；VLOOKUP 仍用三列的那个。
    wb.defined_names.add(DefinedName("场景名", attr_text=f"_场景表!$A$2:$A${n}"))

    # ---- 设备表：同一场景的设备**必须连续**，定义名才指得住 ----
    ws = wb.create_sheet("_设备表")
    ws.append(["场景码", "设备名称"])
    disp = disp or {}
    _dn = lambda e: disp.get(e["material"], e["name"])
    by_scene: dict[str, list[str]] = defaultdict(list)
    for e in cat:
        by_scene[e["scene"]].append(_dn(e))
    r = 2
    for s in order_sorted:
        code = (seen.get(s) or {"code": "S99"})["code"]
        names = sorted(set(by_scene[s]))
        first = r
        for nm in names:
            ws.cell(r, 1, code); ws.cell(r, 2, nm); r += 1
        wb.defined_names.add(DefinedName(
            f"SC_{code}", attr_text=f"_设备表!$B${first}:$B${r - 1}"))

    # ---- 成本表：显示名 → 成本单价（内部件，只在丙附）----
    ws = wb.create_sheet("_成本表")
    ws.append(["设备名称", "成本单价(元)"])
    for m in sorted(mats, key=lambda x: disp.get(x["code"], x["name"])):
        c_ = (cost_ref or {}).get(m["code"])
        if c_:
            ws.append([disp.get(m["code"], m["name"]), c_])
    wb.defined_names.add(DefinedName(
        "成本表", attr_text=f"_成本表!$A$2:$B${max(2, ws.max_row)}"))

    # ---- 口径表：（场景|设备）→ 配置口径一句话 ----
    ws = wb.create_sheet("_口径表")
    ws.append(["键", "配置口径"])
    for e in cat:
        ws.append([f"{e['scene']}|{_dn(e)}", _rule_text(e)])
    wb.defined_names.add(DefinedName(
        "口径表", attr_text=f"_口径表!$A$2:$B${ws.max_row}"))

    # ---- 物料表 ----
    ws = wb.create_sheet("_物料表")
    ws.append(["设备名称", "参考品牌型号", "性能参数", "单位", "单价(元)", "物料编码"])
    for m in sorted(mats, key=lambda x: disp.get(x["code"], x["name"])):
        ref = m["model"]
        if m.get("brand"):
            ref = f"{m['brand']} {ref}".strip()
        if m.get("ref_vendors"):
            ref += "；参考厂家：" + "、".join(m["ref_vendors"])
        ws.append([disp.get(m["code"], m["name"]), ref, m["spec"], m["unit"],
                   m["price_yuan"], m["code"]])
    wb.defined_names.add(DefinedName(
        "物料表", attr_text=f"_物料表!$A$2:$F${ws.max_row}"))
    for n_ in ("_场景表", "_设备表", "_物料表", "_口径表", "_成本表"):
        wb[n_].sheet_state = "hidden"


def _price_sheet(wb, codes: list[str], mats: list[dict], deal_prices: dict,
                 cost_ref: dict | None = None,
                 qty_of: dict | None = None,
                 disp: dict[str, str] | None = None,
                 agreement: dict | None = None) -> None:
    """「00_本商机报价」—— 每个物料一行，价格**一处一填**。

    为什么不按行填：本商机 357 行只有 89 个物料，同一台设备最多出现 9 次。
    按行填意味着同一个价要填 9 遍，还得自己保证几遍填的一样 ——
    填不一致时表上看不出来，而金额已经错了。

    为什么不填回 BOM：同一台设备在不同商机可以报不同价。BOM 里那个是
    产品级**参考价**，跨商机通用；本商机报多少是商机级的事，
    与「配置数量不放 BOM」同一条理由。
    """
    ws = wb.create_sheet("00_本商机报价")
    ws["A1"] = "本商机对外报价　每个物料一行，一处一填、全表生效"
    ws["A1"].font = gs.TITLE_FONT
    ws["A2"] = ("黄格＝请填本商机的对外报价；留空则按「参考单价」计。"
                "**不要填 0** —— 0 会被当成「报价 0 元」，而那是待核价的意思。"
                "两个都空的设备进【甲附】「待核价设备（列而不计）」：列出来证明"
                "它不是被漏掉的，但不计金额，因为无价可计。"
                "　参考单价来自产品级目录，只读，并排放着是为了让你看见差多少。")
    ws["A2"].font = gs.SUB_FONT
    # ⚠ 成本列**只在丙附**。丙附是内部件（文件名标着「勿送审」），
    # 甲/甲附/乙 读的是 materials.yaml，那里没有成本价。
    # 三个价格列**并排放**：定价的人要同时看参考价、报价、成本才能定，
    # 隔开放等于让他左右横跳。列名上的「·内部」是提示，靠的不是位置。
    HEAD = [("设备名称", 32), ("参考品牌型号", 26), ("单位", 8),
            ("参考单价(元)", 14), ("市场单价(元)", 14), ("成本单价(元)·内部", 16),
            ("成本价口径·内部", 30),
            ("本商机数量", 12), ("成本小计(元)·内部", 18), ("备注", 30)]
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEAD))
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(HEAD))
    for i, (h, w) in enumerate(HEAD, 1):
        c = ws.cell(3, i, h)
        c.font, c.fill, c.border, c.alignment = (
            gs.HEADER_FONT, gs.HEADER_FILL, gs.BORDER, gs.CENTER)
        ws.column_dimensions[CL(i)].width = w
    by_code = {m["code"]: m for m in mats}
    rows = sorted(codes, key=lambda c: (disp or {}).get(
        c, by_code[c]["name"] if c in by_code else c))
    for k, code in enumerate(rows, start=4):
        m = by_code.get(code) or {}
        ref = m.get("price_yuan") or None
        _ct = (cost_ref or {}).get(code)      # 已是本商机有效成本（协议价优先）
        _q = (qty_of or {}).get(code)
        _a = (agreement or {}).get(code)
        # 成本价口径：协议价的写明它从哪来、动了多少 —— 「6,900」本身不说明
        # 它是谈过的价还是目录价，而这两者在复盘时是两回事。
        if _a:
            _b = float(_a["bom_price_declared"])
            _d = (float(_a["agreement_price"]) - _b) / _b * 100 if _b else 0.0
            _o = f"协议价（源表 BOM 价 {_b:,.2f}，{_d:+.1f}%）"
        else:
            _o = "BOM 成本价" if _ct is not None else ""
        vals = [(disp or {}).get(code, m.get("name", code)),
                m.get("model", ""), m.get("unit", ""),
                ref, deal_prices.get(code), _ct, _o, _q,
                (round(_ct * _q, 2) if (_ct and _q) else None), ""]
        for i, v in enumerate(vals, 1):
            c = ws.cell(k, i, v)
            c.border, c.font = gs.BORDER, gs.BODY_FONT
            if i == 5:
                c.fill = gs.INPUT_FILL
            elif i in (4, 6, 7, 9):
                c.fill = gs.CALC_FILL
            if i in (4, 5, 6, 9):
                c.number_format = "#,##0.00"
            if i == 8:
                c.number_format = "#,##0"
        if ref is None and deal_prices.get(code) is None:
            ws.cell(k, 10, "**待核价** —— 无参考价，须填本商机报价").font = gs.BODY_FONT
        elif _ct is None:
            ws.cell(k, 10, "⚠ 无成本价 —— 内部成本合计未含本行").font = gs.BODY_FONT
    # 内部总成本合计行
    _last = 3 + len(rows)
    if cost_ref:
        r_ = _last + 1
        _nc = sum(1 for c2 in rows if not (cost_ref or {}).get(c2))
        ws.cell(r_, 1,
                "内部成本合计（仅供定价参考，不出现在任何送审件）"
                + (f"　⚠ 未含 {_nc} 个无成本价的物料，本合计**不是全口径成本**"
                   if _nc else "")).font = gs.BOLD_FONT
        for i in (8, 9):
            c = ws.cell(r_, i, f"=SUM({CL(i)}4:{CL(i)}{_last})")
            c.font, c.fill, c.border = gs.BOLD_FONT, gs.TOTAL_FILL, gs.BORDER
            c.number_format = "#,##0" if i == 8 else "#,##0.00"
        for i in (1, 2, 3, 4, 5, 6, 7, 10):
            ws.cell(r_, i).fill = gs.TOTAL_FILL
            ws.cell(r_, i).border = gs.BORDER
        _na = sum(1 for c2 in rows if (agreement or {}).get(c2))
        ws.cell(_last + 3, 1,
                "⚠ 成本三列是**内部参考**：本册标注「勿送审」，不随送审件提交。"
                "甲/甲附/乙 读的是产品目录 materials.yaml，其中不含成本价 —— "
                "成本价单独存在 bom/devices/cost-reference.yaml，"
                "只有本册的生成器读它。"
                + (f"　【协议价】其中 {_na} 个物料按本商机谈定的**协议成本价**计，"
                   "已覆盖 BOM 成本价，明细见「91_成本协议价」。"
                   if _na else "")).font = gs.SUB_FONT
    ws.freeze_panes = "A4"
    # 定义名指向 A:E（设备名称 → 参考单价 → 市场单价），供各园页 VLOOKUP。
    # 列序：1 设备名称 / 2 参考品牌型号 / 3 单位 / 4 参考单价 / 5 市场单价
    # 定义名仍指 A:E（名称…市场单价），列序变了但这五列的相对位置没变。
    wb.defined_names.add(DefinedName(
        "报价表",
        attr_text=f"'00_本商机报价'!$A$4:$E${max(4, 3 + len(rows))}"))


def _agreement_sheet(wb, agreement: dict, bom_cost: dict,
                     qty_of: dict, disp: dict) -> None:
    """「91_成本协议价」—— 本商机谈定的协议成本价，逐条对账。

    单列一页而不是只改个数：协议价改的是**成本**，成本改的是毛利，
    而毛利是拿去定价的。改了什么、相对什么改的、改后成本差多少，
    要能一眼查回源表；只把 6,900 写进成本列，下次没人知道它谈过。
    """
    ws = wb.create_sheet("91_成本协议价")
    ws["A1"] = "本商机成本协议价（内部参考 · 勿送审）"
    ws["A1"].font = gs.TITLE_FONT
    ws["A2"] = ("按料号覆盖产品级 BOM 成本价。协议价是**商机级**数据 —— "
                "别的商机谈的价不一样，故不回写产品目录，与市场单价同一条理由。"
                "　【对账】「源表 BOM 价」是协议表自己声明的基准价，"
                "「我方 BOM 成本价」是产品目录里的值：两者不一致说明双方的成本口径"
                "可能不同（如计费周期、是否含配套），须业务侧确认后再采信协议价。")
    ws["A2"].font = gs.SUB_FONT
    HEAD = [("物料编码", 15), ("设备名称", 30), ("品牌型号", 26),
            ("源表 BOM 价(元)", 15), ("我方 BOM 成本价(元)", 17),
            ("协议价(元)", 13), ("涨跌", 10),
            ("本商机数量", 12), ("成本影响(元)", 15), ("对账结论", 40)]
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEAD))
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(HEAD))
    for i, (h, w) in enumerate(HEAD, 1):
        c = ws.cell(3, i, h)
        c.font, c.fill, c.border, c.alignment = (
            gs.HEADER_FONT, gs.HEADER_FILL, gs.BORDER, gs.CENTER)
        ws.column_dimensions[CL(i)].width = w
    _rows = sorted(agreement.items(), key=lambda kv: disp.get(kv[0], kv[0]))
    for k, (code, a) in enumerate(_rows, start=4):
        _b = float(a["bom_price_declared"])
        _g = float(a["agreement_price"])
        _own = bom_cost.get(code)
        _q = qty_of.get(code)
        # 成本影响以**我方 BOM 成本价**为基准 —— 那才是本册改协议价之前实际在算的数。
        _base = _own if _own is not None else _b
        _imp = (_g - _base) * _q if _q else None
        if _own is None:
            _v = "⚠ 我方目录无成本价 —— 协议价为本册首个成本口径"
        elif abs(_own - _b) < 0.01:
            _v = "对得上"
        else:
            _v = (f"⚠ 不一致：我方 {_own:,.2f} vs 源表 {_b:,.2f}"
                  f"（差 {_own - _b:+,.2f}）—— 口径待确认，本册已按协议价计")
        vals = [code, disp.get(code, a.get("name") or code), a.get("model") or "",
                _b, _own, _g, (_g - _base) / _base if _base else None,
                _q, (round(_imp, 2) if _imp is not None else None), _v]
        for i, v in enumerate(vals, 1):
            c = ws.cell(k, i, v)
            c.border, c.font = gs.BORDER, gs.BODY_FONT
            if i in (4, 5, 6, 9):
                c.number_format = "#,##0.00"
            elif i == 7:
                c.number_format = "+0.0%;-0.0%;0.0%"
            elif i == 8:
                c.number_format = "#,##0"
            if i in (4, 5, 7, 9):
                c.fill = gs.CALC_FILL
    r = 4 + len(_rows)
    ws.cell(r, 1, "合计").font = gs.BOLD_FONT
    for i in (8, 9):
        c = ws.cell(r, i, f"=SUM({CL(i)}4:{CL(i)}{r - 1})")
        c.font, c.fill, c.border = gs.BOLD_FONT, gs.TOTAL_FILL, gs.BORDER
        c.number_format = "#,##0" if i == 8 else "#,##0.00"
    for i in list(range(1, 8)) + [10]:
        ws.cell(r, i).fill = gs.TOTAL_FILL
        ws.cell(r, i).border = gs.BORDER
    _bad = sum(1 for c_, a in _rows
               if bom_cost.get(c_) is not None
               and abs(bom_cost[c_] - float(a["bom_price_declared"])) >= 0.01)
    ws.cell(r + 2, 1,
            f"⚠ 共 {len(_rows)} 个物料按协议价计"
            + (f"，其中 {_bad} 个与我方 BOM 成本价对不上，已在「对账结论」逐条标出 —— "
               "这几条的成本影响金额随口径而变，采信前请业务侧确认。"
               if _bad else "，与我方 BOM 成本价全部对得上。")
            + "　「成本影响」= (协议价 − 我方 BOM 成本价) × 本商机数量，"
            "即本次改价使内部成本合计变动的金额。").font = gs.SUB_FONT
    ws.freeze_panes = "A4"


def _entry_sheet(wb, name: str, rows: list[dict], is_batch: bool) -> int:
    ws = wb.create_sheet(name)
    ws["A1"] = f"{name}　设备配置录入"
    ws["A1"].font = gs.TITLE_FONT
    ws["A2"] = ("⚠ 本表为**录入工具**，金额仅供录入时参考；送审金额一律由引擎"
                "按本表回读结果重算。黄格＝请填，绿格＝自动带出（勿改）。"
                "　【单价】本页的「市场单价」是查出来的，**不在这里填** —— "
                "价格是每个物料一个，填在「00_本商机报价」页，一处一填、全表生效。"
                + ("　本页是 200 所批次：数量填的是**这一批的总量**，不是单园量。"
                   if is_batch else ""))
    ws["A2"].font = gs.SUB_FONT
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(COLS))
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(COLS))

    hr = 3
    for i, (t, w, role) in enumerate(COLS, 1):
        c = ws.cell(hr, i, t)
        c.font, c.fill, c.border, c.alignment = (
            gs.HEADER_FONT, gs.HEADER_FILL, gs.BORDER, gs.CENTER)
        ws.column_dimensions[CL(i)].width = w
    ws.column_dimensions[CL(IDX["场景码"])].hidden = True
    ws.column_dimensions[CL(IDX["口径键"])].hidden = True

    FILL = {"input": gs.INPUT_FILL, "calc": gs.CALC_FILL}
    B, D = CL(IDX["子场景"]), CL(IDX["设备名称"])
    SC = CL(IDX["场景码"])
    first = hr + 1
    last = first + len(rows) + BLANK_ROWS - 1
    for k, r in enumerate(range(first, last + 1)):
        src = rows[k] if k < len(rows) else None
        ws.cell(r, IDX["序号"], f'=IF({D}{r}="","",COUNTA($D${first}:{D}{r}))')
        ws.cell(r, IDX["子场景"], src["scene"] if src else None)
        ws.cell(r, IDX["设备名称"], src["name"] if src else None)
        ws.cell(r, IDX["数量"], src["qty"] if src else None)
        ws.cell(r, IDX["备注"], src["note"] if src else None)
        # 联动：全部走 IFERROR，未选之前留空而不是一片 #N/A
        ws.cell(r, IDX["场景码"],
                f'=IFERROR(VLOOKUP({B}{r},场景表,2,FALSE),"")')
        # 口径按（场景 × 设备）查 —— 同一台设备在不同场景口径不同
        # （PAD X9 在智慧教学是「每教室1台+体育老师1台」，在别的场景可能是每园）。
        # 只按设备名查会把两套口径压成一套，而压掉的那套不会有任何地方报错。
        ws.cell(r, IDX["口径键"], f'={B}{r}&"|"&{D}{r}')
        # 查不到**不回落到按设备名查** —— 那会显示另一个场景的口径，
        # 一个看起来对、实际错的值。改为明确提示：该场景下目录里没有这台设备。
        # 这种情况的实质是两层数据对不齐（第二层引用了第一层没有的组合），
        # 让它显式出现在方案人员眼前，比悄悄留空或悄悄显示错值都好。
        _kk = f'{CL(IDX["口径键"])}{r}'
        ws.cell(r, IDX["配置口径"],
                f'=IF({D}{r}="","",'
                f'IFERROR(VLOOKUP({_kk},口径表,2,FALSE),'
                f'"⚠ 该场景下目录无此设备，口径待补"))')
        ws.cell(r, IDX["一级场景"],
                f'=IFERROR(VLOOKUP({B}{r},场景表,3,FALSE),"")')
        for col, idx in (("参考品牌型号", 2), ("性能参数", 3), ("单位", 4)):
            ws.cell(r, IDX[col],
                    f'=IFERROR(VLOOKUP({D}{r},物料表,{idx},FALSE),"")')
        # 市场单价查报价表：填了取填的，没填落回参考价。
        # 报价表列序：1 名称 / 4 参考单价 / 5 市场单价。
        # 填了市场单价取它，没填落回参考单价，都没有留空（→ 待核价）。
        ws.cell(r, IDX["市场单价(元)"],
                f'=IFERROR(IF(VLOOKUP({D}{r},报价表,5,FALSE)<>"",'
                f'VLOOKUP({D}{r},报价表,5,FALSE),'
                f'VLOOKUP({D}{r},报价表,4,FALSE)),"")')
        _q, _p = CL(IDX["数量"]), CL(IDX["市场单价(元)"])
        ws.cell(r, IDX["金额(元)"],
                f'=IF(OR({_q}{r}="",{_p}{r}=""),"",'
                f'ROUND({_q}{r}*{_p}{r},2))')
        # 成本按显示名查 —— 与市场单价同一个键，同名物料已在 display_names
        # 里消歧，不会串行。查不到留空（该物料没有成本价），不填 0：
        # 0 会让成本金额看起来是「不要钱」，而实际是「没有成本数据」。
        _cu = CL(IDX["成本单价(元)·内部"])
        ws.cell(r, IDX["成本单价(元)·内部"],
                f'=IFERROR(VLOOKUP({D}{r},成本表,2,FALSE),"")')
        ws.cell(r, IDX["成本金额(元)·内部"],
                f'=IF(OR({_q}{r}="",{_cu}{r}=""),"",'
                f'ROUND({_q}{r}*{_cu}{r},2))')
        for t, _, role in COLS:
            c = ws.cell(r, IDX[t])
            c.border = gs.BORDER
            c.font = gs.BODY_FONT
            if role:
                c.fill = FILL[role]
            if t in ("数量",):
                c.number_format = "#,##0"
            if t in ("市场单价(元)", "金额(元)",
                     "成本单价(元)·内部", "成本金额(元)·内部"):
                c.number_format = "#,##0.00"

    tr = last + 1
    ws.cell(tr, IDX["子场景"], "合计").font = gs.BOLD_FONT
    ws.cell(tr, IDX["数量"],
            f"=SUM({CL(IDX['数量'])}{first}:{CL(IDX['数量'])}{last})")
    ws.cell(tr, IDX["金额(元)"],
            f"=SUM({CL(IDX['金额(元)'])}{first}:{CL(IDX['金额(元)'])}{last})")
    for i in range(1, len(COLS) + 1):
        c = ws.cell(tr, i)
        c.font, c.fill, c.border = gs.BOLD_FONT, gs.TOTAL_FILL, gs.BORDER
    ws.cell(tr, IDX["数量"]).number_format = "#,##0"
    ws.cell(tr, IDX["金额(元)"]).number_format = "#,##0.00"

    dv_s = DataValidation(type="list", formula1="场景名", allow_blank=True,
                          showErrorMessage=True, errorTitle="子场景不在目录里",
                          error="请从下拉中选择。目录见 bom/devices/taxonomy.yaml；"
                                "确实缺场景的，先补目录再录入。")
    ws.add_data_validation(dv_s)
    dv_s.add(f"{B}{first}:{B}{last}")
    # 设备下拉按所选场景联动。内联候选有 255 字符上限，单场景最长 294，
    # 所以必须走区域引用 —— 定义名在隐藏页按场景码分块起好了。
    # formula1 里**不带前导等号** —— xlsx 的 <formula1> 存的是表达式本身。
    # 带上会写成 `==INDIRECT(...)`，LibreOffice 会吞掉一个，Excel 未必认。
    dv_d = DataValidation(type="list", formula1=f'INDIRECT("SC_"&${SC}{first})',
                          allow_blank=True, showErrorMessage=True,
                          errorTitle="该设备不在所选场景下",
                          error="设备下拉只列所选子场景下的设备。"
                                "若该设备确实要进这个场景，先在 bom/devices 目录里加。")
    ws.add_data_validation(dv_d)
    dv_d.add(f"{D}{first}:{D}{last}")

    ws.freeze_panes = f"A{hr + 1}"
    ws.auto_filter.ref = f"A{hr}:{CL(len(COLS))}{last}"
    return tr


def emit(root: Path, deal_dir: Path, out: Path,
         baseline: dict[str, str] | None = None) -> dict[str, Any]:
    """从**第二层配置**预填，不从目录。

    闭环是 `device-config.yaml → 丙附 → 同步 → device-config.yaml`。
    预填源若是目录（第一层），人改完同步进配置，下次重出又从目录预填 ——
    改动就丢了。必须让配置当真源、丙附当它的可编辑视图，重出才幂等。
    """
    D = load(root)
    mats, tax = D["materials"]["materials"], D["taxonomy"]["scenes"]
    cat = D["catalog"]["entries"]
    cfg = yaml.safe_load((deal_dir / "device-config.yaml").read_text(encoding="utf-8"))
    # 成本参考：**只有本模块（出丙附）读它**。缺文件就不出成本列，不报错 ——
    # 没有成本数据不该挡住方案人员填价。
    _cp = root / "bom" / "devices" / "cost-reference.yaml"
    cost = (yaml.safe_load(_cp.read_text(encoding="utf-8"))
            if _cp.exists() else None)
    # 商机级**协议成本价**覆盖产品级 BOM 成本价。分两层的理由与市场单价相同：
    # 同一台设备在不同商机谈到的价不一样，谈定的价属于商机，不回写产品目录。
    # 覆盖后 cost["prices"] 即「本商机有效成本」，下游（成本列、毛利）全部照此算，
    # 不在各处再判一次谁优先 —— 判两次就会有一处判错。
    _ag_p = deal_dir / "cost-agreement.yaml"
    agr = (yaml.safe_load(_ag_p.read_text(encoding="utf-8"))
           if _ag_p.exists() else None)
    _agr = (agr or {}).get("prices") or {}
    _bom_cost = dict((cost or {}).get("prices") or {})   # 覆盖前的产品级成本，供对账
    if _agr and cost is not None:
        _eff = dict(_bom_cost)
        for _c, _v in _agr.items():
            _eff[_c] = float(_v["agreement_price"])
        cost = {**cost, "prices": _eff}
    # 显示名：同名不同料号的加「｜型号」。预填/下拉/报价页/口径表全用它，
    # 回读（quote_sync）按同一函数反查料号 —— 一处定义，两处共用。
    disp = display_names(mats)
    name_of = dict(disp)
    per_garden: dict[str, list[dict]] = defaultdict(list)
    for t in cfg["targets"]:
        for it in t["items"]:
            nm = name_of.get(it["material"])
            if nm is None:
                raise SystemExit(
                    f"device-config.yaml 引用了目录里没有的物料 {it['material']!r}"
                    f"（{t['id']}）—— 预填不出设备名，先修配置或补目录")
            per_garden[t["id"]].append({"scene": it["scene"], "name": nm,
                                        "qty": it["qty"], "note": it.get("note", ""),
                                        "unit_price": it.get("unit_price")})
    gardens = [t["id"] for t in cfg["targets"]]
    # 本商机用到的物料（去重）—— 报价页的行就是它们。
    used_codes: list[str] = []
    _seen = set()
    for t in cfg["targets"]:
        for it in t["items"]:
            if it["material"] not in _seen:
                _seen.add(it["material"]); used_codes.append(it["material"])
    deal_prices = cfg.get("prices") or {}

    wb = gs.new_workbook()
    ws = wb.create_sheet("00_使用说明")
    ws["A1"] = "设备配置录入工具　使用说明"
    ws["A1"].font = gs.TITLE_FONT
    for i, t in enumerate([
        "",
        "① 每个园（或批次）一个 sheet，已按现有配置预填 —— 是改，不是从零填。",
        "② 选「子场景」→ 一级场景自动带出；选「设备名称」→ 型号/参数/单位/单价自动带出。",
        "③ 设备下拉**只列所选子场景下的设备**。要加新设备，先在 bom/devices 目录里加，",
        "   不要在本表硬打 —— 硬打的名字带不出型号和单价，金额会是空的。",
        "④ 只填黄格（子场景/设备/数量/备注）。绿格是公式，改了会断。",
        "⑤ **单价填在「00_本商机报价」页，不在各园页填** —— 价格是每个物料一个，",
        "   一处一填、全表生效。各园页的「市场单价」是查出来的，只读。",
        "⑥ 200 所批次页的「数量」填的是**这一批的总量**，不是单园量。",
        "",
        "⚠ 本表金额仅供录入时参考。送审金额一律由引擎按本表回读结果重算 ——",
        "   工具表用活公式是为了能联动，送审件的数不能出自 Excel。",
        "",
        "填完回传，跑 device_quote_tool.py --read 回读，重出甲附形态的配置表。",
    ], start=2):
        ws.cell(i, 1, t).font = gs.SUB_FONT
    ws.column_dimensions["A"].width = 110

    _cost_ref = (cost or {}).get("prices") or {}
    _qty_of: dict = {}
    for t_ in cfg["targets"]:
        for it in t_["items"]:
            _qty_of[it["material"]] = _qty_of.get(it["material"], 0) + it["qty"]
    _price_sheet(wb, used_codes, mats, deal_prices, _cost_ref, _qty_of, disp,
                 _agr)
    _hidden_sources(wb, tax, mats, cat, disp,
                    (cost or {}).get("prices") or {})
    totals = {}
    kinds = {t["id"]: t.get("kind", "garden") for t in cfg["targets"]}
    for g in gardens:
        tr = _entry_sheet(wb, g, per_garden[g], kinds.get(g) == "batch")
        totals[g] = tr

    ws = wb.create_sheet("90_汇总")
    ws["A1"] = "各园/批次合计"
    ws["A1"].font = gs.TITLE_FONT
    # 成本三列**只在丙附**（内部件）。甲/甲附/乙 读 materials.yaml，其中无成本价。
    _cr = (cost or {}).get("prices") or {}
    # **毛利只在可比口径上算。** 「可比金额」= 有成本价那部分条目的市场金额。
    # 拿全额金额减不完整的成本，毛利会虚高 —— 实测差三倍（38.4% vs 12.6%），
    # 因为睡眠垫 440 万有市场价却没有成本价。页脚写一句「毛利偏高」不够：
    # 数字本身会被拿去定价，一个偏高三倍的毛利率比不显示更危险。
    # **可比 = 市场价与成本价俱全。** 少任何一边都不进毛利计算：
    #   · 有市场价没成本价 → 成本缺失，毛利虚高
    #   · 有成本价没市场价（待核价）→ 收入按 0 计，毛利虚低
    # 后者更隐蔽：06 园有 257.75 万成本对应 0 元收入，把毛利率从 ~48% 砸到 10.9%，
    # 而那只是「还没定价」，不是「亏了」。两边都要剔。
    # 版面只留 6 列。「可比金额 / 待核价成本 / 无成本对照金额」是毛利的口径依据，
    # **隐藏而不删** —— 毛利仍按可比口径算（分子分母同源），三列一删公式就断，
    # 且价格变动后它们会重新变得不为零，那时需要它们在原位。
    HEAD = ["园所/批次", "台数", "金额(元)"] + (
        ["可比成本(元)·内部", "毛利(元)·内部", "毛利率·内部",
         "可比金额(元)·内部",
         "待核价成本(元)·内部", "无成本对照金额(元)·内部"] if _cr else [])
    HIDE = ("可比金额(元)·内部", "待核价成本(元)·内部", "无成本对照金额(元)·内部")
    for i, t in enumerate(HEAD, 1):
        c = ws.cell(3, i, t)
        c.font, c.fill, c.border, c.alignment = (
            gs.HEADER_FONT, gs.HEADER_FILL, gs.BORDER, gs.CENTER)
        if t in HIDE:
            ws.column_dimensions[CL(i)].hidden = True
    # 列号按 HEAD 取，不写死 —— 改列序时公式跟着走。
    _C = {t: CL(i) for i, t in enumerate(HEAD, 1)}
    # 逐园成本 = Σ(该园各条 成本单价 × 数量)。**只算有成本价的条目** ——
    # 无成本价的按 0 计会让毛利虚高，那是最会误导定价的方向。
    #
    # **单价必须与各园页同一口径**：各园页取「填了市场单价用它，没填落回参考价」。
    # 本页原先只取 price_yuan（产品级参考价），商务在丙附把市场价填低 25–45% 之后
    # 就成了两套价 —— 症状是「可比金额 > 金额」（子集大于全集，逻辑上不可能），
    # 且已定价的条目被当成待核价（甲本报 0 项、本页报 654.7 万）。
    _by = {m["code"]: m for m in mats}
    _dp = cfg.get("prices") or {}

    def _unit(code):
        """本商机有效单价：报价优先，落回产品级参考价，都没有 → 0（待核价）。"""
        v = _dp.get(code)
        if v in (None, ""):
            v = (_by.get(code) or {}).get("price_yuan")
        return float(v or 0)

    _cost_by_g, _skip_by_g, _cmp_by_g = {}, {}, {}
    _nocmp_by_g, _pend_by_g = {}, {}
    for t_ in cfg["targets"]:
        _s = _cmp = _no = _pend = 0.0
        _n = 0
        for it in t_["items"]:
            c_ = _cr.get(it["material"])
            _p = _unit(it["material"])
            _amt = _p * it["qty"]
            if c_ and _p:                       # 两边俱全 → 进毛利
                _s += c_ * it["qty"]
                _cmp += _amt
            elif c_:                            # 有成本没市场价 = 待核价
                _pend += c_ * it["qty"]
                _n += 1
            else:                               # 有市场价没成本价
                _no += _amt
                _n += 1
        _cost_by_g[t_["id"]], _skip_by_g[t_["id"]] = _s, _n
        _cmp_by_g[t_["id"]], _nocmp_by_g[t_["id"]] = _cmp, _no
        _pend_by_g[t_["id"]] = _pend
    for k, g in enumerate(gardens, start=4):
        ws.cell(k, 1, g).border = gs.BORDER
        ws.cell(k, 2, f"='{g}'!{CL(IDX['数量'])}{totals[g]}").border = gs.BORDER
        ws.cell(k, 3, f"='{g}'!{CL(IDX['金额(元)'])}{totals[g]}").border = gs.BORDER
        ws.cell(k, 2).number_format = "#,##0"
        ws.cell(k, 3).number_format = "#,##0.00"
        if _cr:
            _cmp, _cst = _C["可比金额(元)·内部"], _C["可比成本(元)·内部"]
            ws.cell(k, 4, round(_cost_by_g.get(g, 0.0), 2)).border = gs.BORDER
            # 毛利 = 可比金额 − 成本，**分母也用可比金额**，两边同口径。
            ws.cell(k, 5, f"=IF(OR({_cmp}{k}=\"\",{_cst}{k}=\"\"),\"\","
                           f"{_cmp}{k}-{_cst}{k})").border = gs.BORDER
            ws.cell(k, 6, f"=IF(OR({_cmp}{k}=\"\",{_cmp}{k}=0),\"\","
                           f"({_cmp}{k}-{_cst}{k})/{_cmp}{k})").border = gs.BORDER
            ws.cell(k, 7, round(_cmp_by_g.get(g, 0.0), 2)).border = gs.BORDER
            ws.cell(k, 8, round(_pend_by_g.get(g, 0.0), 2)).border = gs.BORDER
            ws.cell(k, 9, round(_nocmp_by_g.get(g, 0.0), 2)).border = gs.BORDER
            for i in (4, 5, 7, 8, 9):
                ws.cell(k, i).number_format = "#,##0.00"
            ws.cell(k, 6).number_format = "0.0%"
            for i in (4, 5, 6, 7, 8, 9):
                ws.cell(k, i).fill = gs.CALC_FILL
    r = 4 + len(gardens)
    ws.cell(r, 1, "合计").font = gs.BOLD_FONT
    for i in (2, 3) + ((4, 5, 7, 8, 9) if _cr else ()):
        c = ws.cell(r, i, f"=SUM({CL(i)}4:{CL(i)}{r - 1})")
        c.font, c.fill, c.border = gs.BOLD_FONT, gs.TOTAL_FILL, gs.BORDER
        c.number_format = "#,##0" if i == 2 else "#,##0.00"
    if _cr:
        _cmp, _cst = _C["可比金额(元)·内部"], _C["可比成本(元)·内部"]
        c = ws.cell(r, 6, f"=IF(OR({_cmp}{r}=\"\",{_cmp}{r}=0),\"\","
                          f"({_cmp}{r}-{_cst}{r})/{_cmp}{r})")
        c.font, c.fill, c.border = gs.BOLD_FONT, gs.TOTAL_FILL, gs.BORDER
        c.number_format = "0.0%"
    ws.cell(r, 1).fill = gs.TOTAL_FILL
    ws.cell(r, 1).border = gs.BORDER
    for i, w in ((1, 24), (2, 12), (3, 16), (4, 18), (5, 18), (6, 12),
                 (7, 18), (8, 20), (9, 22)):
        ws.column_dimensions[CL(i)].width = w
    if _cr:
        _tot_skip = int(sum(_skip_by_g.values()))
        _t_cmp = sum(_cmp_by_g.values())
        _t_no = sum(_nocmp_by_g.values())
        _t_pend = sum(_pend_by_g.values())
        # 覆盖率、缺口金额一律当期算 —— 写死的数字会在价格变动后变成假陈述。
        # 金额 = 可比金额 + 无成本对照金额（待核价条目单价为 0，不进金额）。
        _cov = _t_cmp / (_t_cmp + _t_no) * 100 if (_t_cmp + _t_no) else 0.0
        ws.cell(r + 2, 1,
                "⚠ 成本相关列为**内部参考**，本册标注「勿送审」，不随送审件提交。"
                "　【口径】毛利只在**市场价与成本价俱全**的条目上算，两边都要有："
                "有市场价没成本价会让毛利虚高，有成本价没市场价（待核价）则按 0 元"
                "收入计、把毛利率砸下去，那是「还没定价」不是「亏了」。两者均不参与"
                f"毛利计算，本次共 {_tot_skip} 条配置行。"
                f"　本次可比口径覆盖金额的 {_cov:.1f}%"
                + ("（无成本对照 {:,.0f} 元、待核价成本 {:,.0f} 元，"
                   "两项明细在右侧隐藏列）".format(_t_no, _t_pend)
                   if (_t_no or _t_pend) else
                   "，全部条目市场价与成本价俱全，毛利率即全口径毛利率。")
                + "　【市场单价口径】与各园页一致：填了本商机报价用报价，"
                "没填落回产品级参考价。"
                ).font = gs.SUB_FONT

    if baseline:
        ws = wb.create_sheet("_基线")
        ws.append(["键", "值"])
        for k, v in baseline.items():
            ws.append([k, v])
        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 60
        ws.sheet_state = "hidden"

    if _agr:
        _agreement_sheet(wb, _agr, _bom_cost, _qty_of, disp)
    # 顺序：说明 → 各园 → 汇总（隐藏页排最后）
    order = (["00_使用说明", "00_本商机报价"] + list(gardens)
             + ["90_汇总"] + (["91_成本协议价"] if _agr else [])
             + ["_场景表", "_设备表",
                "_物料表", "_口径表", "_成本表"]
             + (["_基线"] if baseline else []))
    wb._sheets.sort(key=lambda s: order.index(s.title))
    gs.save(wb, out)
    return {"gardens": len(gardens), "rows": sum(len(v) for v in per_garden.values()),
            "scenes": len({e["scene"] for e in cat}), "mats": len(mats)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--deal", type=Path, required=True)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    out = a.out or (a.deal.parent / "out" / "丙附_硬件配置录入.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    r = emit(a.root, a.deal.parent, out)
    print(f"录入工具表 → {out}")
    print(f"  {r['gardens']} 个园/批次页，预填 {r['rows']} 行，"
          f"每页另留 {BLANK_ROWS} 行空白")
    print(f"  联动数据源：{r['scenes']} 个子场景 / {r['mats']} 个物料（隐藏页）")


if __name__ == "__main__":
    main()
