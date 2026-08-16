"""quote_generate_liuzhou —— 按柳州标准（工作量估算法）出送审套表。

与 `quote_generate.py` 的关系：那条链路是 **BOM → 功能点 → 金额**，
本模块是 **人月 → 金额**。不是同一条链路的开关，是两条链路：

    quote_generate.py       BOM 逐条 NESMA 判型 → UFP → 调整后 FP → 人月 → 金额
    quote_generate_liuzhou  源表逐行人月 × 表1 阶段单价 → 金额

柳州标准 p.10 明文两法择一（「软件开发费可按照工作量估算法或功能点估算法
进行估算」），本项目按工作量法编制，理由见 `docs/柳州标准适用性核查.md`。

## 本模块的职责边界

**不重新定价。** 人月来自源表，是业务侧的估算结果；本模块做的是：

  1. 逐行校验单价是否等于表1 注3 的阶段单价（1.9/1.9/1.7/1.6/1.4）
  2. 逐行重算 金额 = ROUND(人月 × 单价, 4)，与源表对账
  3. 其他费用按表9/11/12/13 逐项复算上限，超限的标出来
  4. 汇总与明细勾稽，对不上的标出来
  5. 功能点法交叉校验，出倍差
  6. **列而不计**：市场价为 0 的硬件逐条列出，标「待核价」而不是 ¥0

第 6 条是本项目的老问题：¥0 与「还没定价」在表上必须能分辨，
被排除的条目必须列出来证明它不是被漏掉的。

用法：
    python3 quote_generate_liuzhou.py --deal deals/<id>/deal.yaml --out out/
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import openpyxl
import yaml

import gov_sheet as gs
from gov_sheet import Col, GovSheet, formula
from nesma_weights import xlround
from standard_pack import StandardPack
from standard_pack import get_profile

_ROOT: Path = Path('.')

STAGES = ["需求分析", "系统设计", "软件开发（编码）", "系统测试", "实施部署"]


# ============================================================
# 读源
# ============================================================

def source_alias(bom_dir: Path) -> dict[str, str]:
    """源表系统名 → taxonomy 里的系统名。

    源清单的分组是编制顺序，产品架构调过之后两边名字不一样了
    （「数智幼教行业大模型数据建设」→「数据工程」）。映射写在 taxonomy 的
    `source:` 字段里 —— **不在这里硬编码**，否则改一次架构要改两处，
    而漏改的表现是工作量法那一段静默变成 0。
    """
    tax = yaml.safe_load((bom_dir / "taxonomy.yaml").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for ls in (tax.get("product_lines") or {}).values():
        for sysname, spec in (ls.get("systems") or {}).items():
            if isinstance(spec, dict) and spec.get("source"):
                out[spec["source"]] = sysname
    return out


def read_software(path: Path, pack: StandardPack,
                  alias: dict[str, str] | None = None) -> dict[str, Any]:
    """读估算表，逐行校验单价并重算金额。

    源表的金额单元格是 `=ROUND(D*F,4)` 的公式，这里用 data_only 读的是
    LibreOffice/Excel 存下来的缓存值。**重算一遍再比**，不信缓存 ——
    公式改了没重算的工作簿看起来一切正常。
    """
    import bom_build_preschool as bp   # 复用序号解析，不重写一套

    wb = openpyxl.load_workbook(path, data_only=True)
    rates = (pack.data.get("effort_method") or {}).get("stage_rates") or {}

    systems: dict[str, dict] = {}
    issues: list[dict] = []

    for sn in ("平台开发", "底座开发"):
        leaves, stages = bp.parse_sheet(wb[sn], sn)
        for lf in leaves:
            if lf["system"] == "平台开发":
                lf["system"] = lf["subsystem"]
                lf["subsystem"] = lf["func"]
        for st in stages:
            sysname = st["system"] if st["system"] != "平台开发" else "柳州市学前教育数智服务平台"
            sysname = (alias or {}).get(sysname, sysname)
            s = systems.setdefault(sysname, {"stages": {}, "leaves": []})
            mm, up, amt = st["man_months"], st["unit_price_wan"], st["amount_wan"]
            if mm is None or up is None:
                continue
            std = rates.get(st["stage"], {}).get("value")
            recomputed = xlround(mm * up, 4)
            if std is not None and abs(up - std) > 1e-9:
                issues.append({
                    "kind": "单价偏离表1", "system": sysname, "stage": st["stage"],
                    "detail": f"源表 {up} 万元/人月，表1 注3 为 {std}",
                })
            if abs(recomputed - (amt or 0)) > 0.0001:
                issues.append({
                    "kind": "金额与人月×单价对不上", "system": sysname,
                    "stage": st["stage"],
                    "detail": f"源表 {amt}，重算 {recomputed}",
                })
            s["stages"][st["stage"]] = {
                "man_months": mm, "unit_price_wan": up,
                "amount_wan": recomputed, "source_amount_wan": amt,
                "std_rate": std,
            }
        for lf in leaves:
            sysname = (alias or {}).get(lf["system"], lf["system"])
            systems.setdefault(sysname, {"stages": {}, "leaves": []})
            # 平台的末级挂到合并后的系统名下，但保留原子系统作为分组
            systems[sysname]["leaves"].append(lf)

    issues += _reconcile_summary(wb, systems)
    return {"systems": systems, "issues": issues}


#: 源表总表里的软件行 → 明细表里的系统名。**显式映射**，
#: 靠名字模糊匹配会在改名时静默失配，而失配的表现是「没有勾稽问题」。
_SUMMARY_ALIAS = {
    "柳州市学前教育数智服务平台": "柳州市学前教育数智服务平台",
    "数智民生生态体系平台支撑基座（L0层）": "数智民生生态体系平台支撑基座",
    "数智幼教行业大模型-知识工程（L1层）": "知识工程",
    "数智幼教行业大模型-数据建设（L1层）": "数据工程",
    "数智幼教行业垂直模型（L2层）": "行业垂直模型",
    "数智幼教场景智能体（L2层）": "行业智能体",   # 左=源表总表原名，右=产品新名
}


def _reconcile_summary(wb, systems: dict) -> list[dict]:
    """源表「总表」的软件行 vs 明细表算出来的数。

    源表总表那几格是**硬编码常量**，不是指向明细表的链接 —— 明细改了总表
    不会跟着动，且看不出来。这正是本项目要拦的形状：不报错，只是数错了。
    """
    if "总表" not in wb.sheetnames:
        return [{"kind": "源表缺总表", "system": "-", "stage": "-",
                 "detail": "无法做汇总与明细的勾稽"}]
    ws = wb["总表"]
    out: list[dict] = []
    seen = set()
    for r in range(1, ws.max_row + 1):
        name = ws.cell(r, 2).value
        if not isinstance(name, str) or name.strip() not in _SUMMARY_ALIAS:
            continue
        key = _SUMMARY_ALIAS[name.strip()]
        seen.add(key)
        stated = ws.cell(r, 3).value
        blk = systems.get(key)
        if blk is None:
            out.append({"kind": "总表有、明细无", "system": name, "stage": "-",
                        "detail": f"总表列 {stated}，明细表中找不到对应系统"})
            continue
        detail = xlround(sum(v["amount_wan"] for v in blk["stages"].values())
                         * 10000, 2)
        if not isinstance(stated, (int, float)):
            continue
        if abs(detail - stated) > 1.0:
            # 几元的差是逐阶段取四位再汇总的舍入残差，不是漏项；
            # 几十万的差是总表那一格根本没跟着明细动。两者要分开，
            # 否则真问题淹在噪声里 —— 但**都要列出来**，不设静默阈值。
            rounding = abs(detail - stated) < 100
            out.append({
                "kind": ("总表与明细有舍入差" if rounding
                         else "总表与明细对不上"),
                "system": name, "stage": "-",
                "detail": (f"总表 ¥{stated:,.0f}，明细表算得 ¥{detail:,.2f}，"
                           f"差 ¥{stated - detail:,.2f}。总表该格是硬编码常量，"
                           f"不是指向明细表的链接"),
                "delta_yuan": xlround(stated - detail, 2),
            })
    missing = set(_SUMMARY_ALIAS.values()) - seen
    if missing:
        out.append({"kind": "明细有、总表无", "system": "、".join(sorted(missing)),
                    "stage": "-", "detail": "明细表中有该系统，源表总表未列"})
    return out


def read_hardware(path: Path, price_col: str) -> dict[str, Any]:
    """读 Z03，只取指定价格列。

    ⚠️ 该工作簿含成本单价/成本总价/渠道单价/渠道总价与毛利率 ——
    与 internal-cost-model.yaml 同级敏感，**一列都不进送审表**。
    本函数只读 `price_col` 与其对应的总价列，别的价格列碰都不碰。
    """
    total_col = price_col.replace("单价", "总价")
    forbidden = {"成本单价", "成本总价", "渠道单价", "渠道总价", "市场毛利率"}
    if price_col in forbidden or total_col in forbidden:
        raise ValueError(f"{price_col} 是内部成本口径，不得用于送审表")

    wb = openpyxl.load_workbook(path, data_only=True)
    # 数据表是 01_～06_ 的园所/批次表。其余表**逐张登记**，不靠「名字像不像」——
    # 靠模式匹配会在源表新增一张表时静默漏读，而合计看起来仍然正常。
    NON_DATA = {"质检总览", "价格测算总览", "88_物料主数据字典", "99_场景命名对照"}
    sheets = [s for s in wb.sheetnames if re.match(r"^0[1-9]_", s)]
    unexpected = set(wb.sheetnames) - set(sheets) - NON_DATA
    if unexpected:
        raise ValueError(
            f"{path.name} 出现未登记的 sheet {sorted(unexpected)} —— "
            f"它是设备明细还是别的？请显式判定后加入 sheets 或 NON_DATA，"
            f"不默认跳过（跳过会漏计且合计看起来正常）")
    priced: list[dict] = []
    pending: list[dict] = []

    for sn in sheets:
        ws = wb[sn]
        h = {ws.cell(2, c).value: c for c in range(1, ws.max_column + 1)}
        need = ["设备名称", "品牌型号", "单位", "数量", price_col, total_col,
                "一级场景", "子场景/用途", "是否自研", "物料编码"]
        miss = [k for k in need if k not in h]
        if miss:
            raise ValueError(f"{sn} 缺列 {miss}")
        for r in range(3, ws.max_row + 1):
            name = ws.cell(r, h["设备名称"]).value
            if name is None:
                continue
            unit_price = ws.cell(r, h[price_col]).value or 0
            qty = ws.cell(r, h["数量"]).value or 0
            total = ws.cell(r, h[total_col]).value or 0
            rec = {
                "园所": sn, "场景": ws.cell(r, h["一级场景"]).value,
                "子场景": ws.cell(r, h["子场景/用途"]).value,
                "name": name, "model": ws.cell(r, h["品牌型号"]).value,
                "code": ws.cell(r, h["物料编码"]).value,
                "自研": ws.cell(r, h["是否自研"]).value,
                "unit": ws.cell(r, h["单位"]).value, "qty": qty,
                "unit_price": unit_price, "total": total,
            }
            # 数量 > 0 却没有单价 —— 这不是「免费」，是「还没定价」。
            # 计 ¥0 会让它在合计里消失且看不出来，正是本项目的头号缺陷形态。
            (pending if (unit_price == 0 and qty) else priced).append(rec)

    for p in priced:
        want = xlround(p["unit_price"] * p["qty"], 2)
        if abs(want - p["total"]) > 0.01:
            p["mismatch"] = want
    return {"priced": priced, "pending": pending, "sheets": sheets}


# ============================================================
# 出表
# ============================================================

def _col(sheet, name: str) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(sheet._by_name[name])


def emit_fp_worksheet(wb, pack: StandardPack, fpset: dict,  # noqa: C901
                      fp_systems: list[dict], dnl: dict | None = None) -> float:
    """Z01/01 功能点测算表 —— 逐子系统的**送审值**，静态。

    静态是有意的。活公式放在【丙本】的 02_二层试算，那张表可以随便改；
    这一张是送审口径，数由引擎算定 —— 两者的差直接印在试算表上。
    """
    rates = {"p": pack.rate("productivity_hours_per_fp"),
             "h": pack.rate("man_hours_per_month"),
             "r": pack.rate("base_man_month_rate")}
    s = GovSheet(
        wb, "01_功能点测算表",
        title="软件开发费测算表（一）功能点估算法",
        subtitle=(f"编制依据：{pack.data['doc_no']} 三.(一)2.1.2　"
                  f"功能点数 = UFP × 软件类别调整因子 × 复用系数；"
                  f"定制软件开发费 = 功能点数 × {rates['p']} 人时/功能点 ÷ "
                  f"{rates['h']:g} 人时/人月 × ¥{rates['r']:,.0f}/人月"),
        columns=[
            Col("子系统", "text", width=30),
            Col("条目数", "int", width=8),
            Col("未调整功能点(UFP)", "fp", width=16, sum=True, role="fp"),
            Col("软件类别", "text", width=18, cell_role="locked"),
            Col("类别因子", "rate", width=9, cell_role="locked"),
            Col("复用系数", "rate", width=9, cell_role="locked"),
            Col("复用度档位", "text", width=10, cell_role="locked"),
            # 混档子系统才有内容：注2 允许按功能模块判复用度，同一子系统内
            # 可以分几组各乘各的。不写出是哪几个模块，这一行的减项就没法核。
            Col("复用度适用模块", "text", width=26, cell_role="locked"),
            Col("调整后功能点", "fp", width=13, sum=True),
            Col("工作量(人月)", "fp", width=12, sum=True),
            Col("人月费率(元)", "money", width=13, cell_role="locked"),
            Col("金额(元)", "money", width=14, sum=True, role="amount"),
            Col("功能点类型分布", "text", width=32),
        ],
        clause_required=True)
    s.group("■ 按 2.1.2 功能点估算法计列（平台 + L0 支撑基座）",
            clause="三.(一)2.1.2")
    total = afp_t = mm_t = 0.0
    for x in sorted(fp_systems, key=lambda y: -y["cost"]):
        total += x["cost"]; afp_t += x["fp"]; mm_t += x["effort_man_months"]
        grps = x.get("groups") or []
        # 子系统行持有金额（取整在这一层做一次）；混档时下面挂**分解行**，
        # 只说明「哪些模块按哪档、贡献多少功能点」，**不带金额也不进合计** ——
        # 让分解行各持一份金额就得逐档取整，而那个取整在标准里没有对应的
        # 计价单元，人月那一步会攒出讲不清的差（市平台实测 170 元）。
        s.row({
            "子系统": x["system"], "条目数": x["items"],
            "未调整功能点(UFP)": x["ufp"], "软件类别": x["app_type"],
            "类别因子": x["app_type_factor"],
            "复用系数": (x["reuse"] if len(grps) <= 1 else None),
            "复用度档位": (x.get("reuse_level") if len(grps) <= 1 else "分档↓"),
            "复用度适用模块": ("" if len(grps) <= 1 else f"下分 {len(grps)} 档，见次行"),
            "调整后功能点": x["fp"],
            "工作量(人月)": x["effort_man_months"],
            "人月费率(元)": x["man_month_rate"], "金额(元)": x["cost"],
            "功能点类型分布": gs.flatten_counts(x.get("by_type") or {}),
        }, clause="三.(一)2.1.2 表3")
        if len(grps) > 1:
            for g in grps:
                s.row({
                    "子系统": f"　　└ {g['reuse_level']}档",
                    "复用系数": g["reuse_factor"],
                    "复用度档位": g["reuse_level"],
                    "复用度适用模块": "、".join(g["modules"])[:120],
                    "功能点类型分布": (f"{g['ufp']:,} UFP × 类别因子 "
                                       f"{x['app_type_factor']} × {g['reuse_factor']}"
                                       f" = {g['contrib']:,.2f} 功能点"),
                }, clause="三.(一)2.1.2 表3 注2")
    for cat, (val, basis) in sorted(fpset["app_type"].items()):
        if any(x["app_type"] == cat for x in fp_systems):
            s.note(f"【软件类别取值依据·{cat} = {val}】"
                   + " ".join(str(basis).split()))
    # 上面那条是**取值**的依据（为什么取 1.0 / 1.2）；下面这组是**归类**的依据
    # （这个子系统凭什么属于这一类）。两者是不同的问题，评审会分开问：
    # 「你凭什么把 AI 计算平台判成应用集成」和「你凭什么取 1.2」。
    _tax = _taxonomy_systems(_ROOT)
    _cls = [(x["system"], x["app_type"],
             " ".join(str((_tax.get(x["system"]) or {}).get("app_type_basis") or "").split()))
            for x in sorted(fp_systems, key=lambda y: -y["cost"])]
    if any(b for _, _, b in _cls):
        s.note("【软件类别归类依据·表3 注1「按主体功能的类型取值」】逐子系统：")
        for sysname, cat, basis in _cls:
            if basis:
                s.note(f"　· {sysname} = {cat}　{basis}")
    _pend = [n for n, _, b in _cls if "待复核" in b]
    if _pend:
        s.note(f"⚠ 上列 {len(_pend)} 个子系统的归类**继承自拆分前的单体「支撑基座」**，"
               f"未针对本子系统单独判定，须产品侧确认：{'、'.join(_pend)}。"
               f"（本次仍按继承值计列；若改判会影响类别因子的适用区间。）")
    s.note(f"【生产率】{rates['p']} 人时/功能点 × {fpset['productivity_ratio']}　"
           + " ".join(str(fpset["productivity_basis"]).split())
           + "（p.14 ②「根据实际情况可上下浮动20%」）")
    # 复用度取值依据逐条列出。表3 注2 的默认是 1（复用度低），偏离默认的每一项
    # 都要能指着说「凭什么打这个折」—— 这次偏离的方向是降价，没出处的折扣
    # 在评审那里是「随意定价」，比不打折更被动。
    _dev = [(x, g) for x in sorted(fp_systems, key=lambda y: -y["cost"])
            for g in (x.get("groups") or [])
            if abs(g["reuse_factor"] - 1.0) > 1e-9]
    if _dev:
        s.note("【复用度取值依据·表3 注2】新建项目缺省取 1（复用度低），"
               "以下按实际情况调整（p.14 注2「根据实际情况进行调整」；"
               "注2 原文为「已有软件系统**或功能模块**」，故判断单位到功能模块）：")
        for x, g in _dev:
            where = x["system"] if len(x.get("groups") or []) == 1 else (
                f"{x['system']} · {'、'.join(g['modules'])[:60]}")
            s.note(f"　· {where} = {g['reuse_level']}（{g['reuse_factor']}）　"
                   + " ".join(str(g.get("reuse_basis") or "").split()))
    _mix = [x for x in fp_systems if len(x.get("groups") or []) > 1]
    if _mix:
        s.note(f"说明：{len(_mix)} 个子系统内部按功能模块分档计列"
               f"（{'、'.join(x['system'] for x in _mix)}），"
               f"各组各乘各的复用系数后求和 —— 逐组见上表带「·档」的行。")
    _pol = next((x.get("reuse_policy") for x in fp_systems), "")
    _und = [x["system"] for x in fp_systems if not x.get("maturity")]
    s.note(f"说明：复用系数按表3 注2 逐子系统取值，映射档「{_pol}」；"
           "成熟度取自 BOM taxonomy 的 maturity（产品事实，非本商机判定）。")
    if _und:
        s.note(f"说明：其中 {len(_und)} 个子系统尚未在 BOM 登记产品成熟度、"
               f"按默认档（复用度低 = 1）计列：{'、'.join(_und)}。")
    # 标了成熟度但没开按实际成熟度调整 —— 必须说出来。不说的话，
    # 表上「existing 却取 1.0」看着像个 bug，而它其实是标准缺省。
    # 模块级声明存在、但开关没开 —— 必须说出来。不说的话，
    # 表上「BOM 里标了成熟却取 1.0」看着像个 bug，而它其实是标准缺省。
    _held = [x["system"] for x in fp_systems
             if x.get("declared_modules")
             and all(abs(g["reuse_factor"] - 1.0) <= 1e-9
                     for g in (x.get("groups") or []))]
    if _held:
        s.note(f"说明：BOM 已逐模块登记复用度的子系统有 {len(_held)} 个"
               f"（{'、'.join(_held)}），本次仍按表3 注2 前半句的新建缺省档"
               f"「复用度低 = 1」计列，**未按实际复用情况下调** —— "
               f"即本段未就复用向甲方让价。若需按注2 后半句调整，"
               f"由 deal.yaml 的 fp_method_settings.reuse.apply_maturity 显式开启。")
    s.note("说明：复用度**逐功能模块**声明（表3 注2「已有软件系统或功能模块」），"
           "未声明的模块一律按缺省「低 = 1」计列 —— 不设子系统级默认，"
           "以免将来新增的模块自动继承折扣。")
    # 直接非人力成本 —— 公式里的加数项。**取 0 也要写出来**：
    # 它是标准公式的一段，不印会让人以为漏了，也看不出我们是主动没计。
    if dnl and dnl["value"] > 0:
        total += dnl["value"]          # 计入功能点法段合计，随 fp_total 上浮到甲本
        s.row({"子系统": "直接非人力成本（另计）", "软件类别": "—",
               "复用度档位": "—",
               "复用度适用模块": " ".join(str(dnl["basis"]).split())[:120],
               "金额(元)": dnl["value"]}, clause=dnl["clause"])
        s.note(f"【直接非人力成本】计列 ¥{dnl['value']:,.2f}　"
               + " ".join(str(dnl["basis"]).split()))
        s.note("　标准 p.14 ⑤：一般情况不进行计列（通常为 0），特殊情况需要计列时"
               "应明确说明原因及测算依据。⚠ 专用设备费/专用软件费不得计入本项 ——"
               "它们在硬件设备购置费与软件产品购置费科目。")
    else:
        s.note("说明：**直接非人力成本取 0**。标准 p.14 ⑤ 明文「一般情况不进行计列"
               "（通常为 0），特殊情况需要计列时应明确说明原因及测算依据」；"
               "本项目按一般情况处理，未计列办公费、差旅费、培训费等。"
               "（p.14 ④ 已明确 1.7 万元/人月的人月费率**不包含**直接非人力成本，"
               "故本项可另计而本次主动不计。）")
        # 主动把「为什么不计差旅」和「为什么系统集成费取分散档」绑成一个逻辑。
        # 两处都不说的话，评审问任一边都得临时找说法；说了，另一边就是答案。
        s.note("说明：本项目 205 个实施点的现场部署差旅，已在**系统集成费**按表9"
               "「施工地点较为分散」档计取时予以考虑（该档的适用理由即"
               "「200 所基础园遍布柳州全市」，与实施点分散是同一事实；"
               "表9 注2 明确系统集成工作含安装上架、系统配置、调试、测试），"
               "**故不在本项重复计列**。另：标准 2.4.3 对差旅费的界定为"
               "「临时到常驻地以外地区」，本项目实施团队常驻柳州，"
               "市域内园所现场实施不构成该定义下的差旅。")
    s.note("说明：柳州标准 p.10 三.(一)2.1「软件开发费…费用包含需求分析、设计、"
           "编码、测试、部署实施以及项目管理、培训等」—— 本段单价已含全部五个阶段，"
           "**不再另计**需求分析/系统设计/系统测试/实施部署费用。")
    s.note("说明：逐条目的功能点计数见 02_功能点明细；各列取值的标准出处与区间见"
           "03_计价参数。**本表为举证件，送审金额以【甲本】01_软件开发费汇总 为准。**")
    s.total(expect={"金额(元)": xlround(total, 2),
                    "调整后功能点": xlround(afp_t, 2),
                    "工作量(人月)": xlround(mm_t, 2)})
    s.finish()
    return total


#: 未判型条目的「为什么没数出功能点」。按共创状态分，不写通稿。
_UNTYPED_REASON = {
    "★待补描述": "待补功能描述 —— 现有描述为能力罗列（如「支持组织架构管理、"
                 "职位管理」），识别不出用户可辨的基本过程（NESMA 要求"
                 "「对用户有意义、自成完整事务」）。描述补齐后重新判型。",
    "技术说明待复核": "描述为技术实现说明而非用户可见的业务功能，"
                      "须业务侧复核该条目对用户呈现为什么操作，再据以判型。",
}


def _split_basis(text: str) -> tuple[str, str, str]:
    """把 BOM 的「工作量·测算依据」拆成 公式 / 测算构成 / 分档规则 三段。

    这三段本来就是分行写在同一个字段里的，挤在一格里看不出结构；分开之后
    每一行评审能自己乘一遍。**不做任何数值解析** —— 只按行首标签归位，
    认不出的行原样落到「测算构成」，宁可重复也不丢。

    ⚠ 本体工程那段有一行「单位工作量（导出值）= 人月÷数量 = 15.7÷50 =
    0.314人月/条」：那是**先有人月再除出来的**，不是定价依据。它在丙本 03
    仍是引擎的输入（qty × per_unit_mm），但在举证册里没有举证价值 ——
    列出来只会招来「0.314 怎么定的」，而唯一诚实的回答是「除出来的」。
    八类要素的逐项展开才是真依据，所以这一行不进乙本。
    """
    if not text:
        return "", "", ""
    formula, comp, tier = [], [], []
    for ln in str(text).splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("公式："):
            formula.append(s.split("：", 1)[1].strip())
        elif s.startswith(("工作量·人月 =", "工作量·人月=")):
            formula.append(s)
        elif s.startswith(("本行实例：", "本行构成：", "计算：")):
            comp.append(s.split("：", 1)[1].strip())
        elif s.startswith(("口径说明：", "口径说明:", "单价（人月/条）：")):
            tier.append(s.split("：", 1)[1].strip())
        elif s.startswith("单位工作量（导出值）"):
            continue
        else:
            comp.append(s)
    return "\n".join(formula), "\n".join(comp), "\n".join(tier)


def _untyped_reason(item: dict) -> str:
    st = item.get("coauthor_status") or ""
    base = _UNTYPED_REASON.get(st)
    if base is None:
        # 出现新状态就报错，不要兜底成一句空话 —— 兜底会让新增的一类问题
        # 混进旧类别里，表面上「每条都有理由」。
        raise gs.GovSheetError(
            f"[02_功能点明细] 条目 {item['id']} 的共创状态 {st!r} 未登记"
            f"未判型理由；请在 _UNTYPED_REASON 里补一条，"
            f"不要让它套用别的状态的说法。已登记：{sorted(_UNTYPED_REASON)}")
    adv = " ".join(str(item.get("coauthor_advice") or "").split())
    return (base + ("　建议：" + adv[:120] if adv else ""))[:300]


def emit_fp_detail(wb, root: Path, pack: StandardPack,
                   effort_only: set[str], fpset: dict, ufp_expect: int,
                   reuse_of: dict[str, float] | None = None) -> None:
    """Z01/02 功能点明细 —— 逐条目。

    没有这张表，「3,153 个功能点」就是一个无法核对的数。评审问
    「你这 3,153 怎么数出来的」，答案必须是一行一行摆出来，
    而不是「引擎算的」。

    未判型的条目也要列 —— **列而不计**：它们在范围内、功能点记 0、
    标「待补功能描述」。静默漏掉与记 0 在表上必须能分辨。
    """
    import glob as _glob
    W = {"ILF": 10, "ELF": 7, "EI": 4, "EO": 5, "EQ": 4}
    typed, untyped = [], []
    for f in sorted(_glob.glob(str(root / "bom" / "items" / "*.yaml"))):
        for i in yaml.safe_load(Path(f).read_text(encoding="utf-8"))["items"]:
            # **必须与基准同口径**：基准走 `Bom.active()`，滤掉 status=deprecated
            # （「列而不计」的条目）。这里不滤的话，02 明细会把它们算进 UFP，
            # 而 01 测算表不算 —— 实测差 8 UFP（B7 两条「引入Maven依赖」各 EI=4），
            # 由那道逐条累加 vs 子系统合计的勾稽当场逮住。
            # 两处各自扫 yaml 而不共用一个取数函数，口径就会这样悄悄分家。
            if (i["path"]["system"] in effort_only
                    or i.get("class") == "SOFTWARE_EFFORT"
                    or i.get("status") == "deprecated"):
                continue
            (typed if "nesma" in i else untyped).append(i)

    cat_of = {}          # 子系统 → 软件类别，用于逐行标注类别因子
    s = GovSheet(
        wb, "02_功能点明细",
        title="功能点计数明细（NESMA 估算功能点法·逐条目）",
        subtitle="UFP = 10×ILF + 7×EIF + 4×EI + 5×EO + 4×EQ（p.13 三.(一)2.1.2①）；"
                 "调整后功能点 = UFP × 软件类别因子 × 复用系数（p.14 表3）",
        columns=[
            Col("条目ID", "text", width=17),
            Col("产品线", "text", width=20),
            Col("子系统", "text", width=16),
            Col("功能模块", "text", width=18),
            Col("功能点名称", "text", width=26),
            Col("计数项类别", "text", width=9, cell_role="locked"),
            Col("未调整功能点", "fp", width=11, sum=True, role="fp"),
            Col("软件类别", "text", width=16, cell_role="locked"),
            Col("类别因子", "rate", width=8, cell_role="locked"),
            Col("复用系数", "rate", width=8, cell_role="locked"),
            Col("调整后功能点", "fp", width=11, sum=True),
            Col("复算式", "text", width=26),
            Col("判定依据", "text", width=70),
        ],
        clause_required=True)
    # 复用系数**逐子系统**取，不是全表一个常量。写死成一个数的话，某个子系统
    # 一旦按复用度高计列，这张明细的调整后功能点合计就和 01 测算表分家，
    # 而两张表各自内部都是自洽的 —— 差额只有交叉核对时才看得见。
    reuse_of = reuse_of or {}
    got = 0
    afp_by_sys: dict[str, float] = {}
    for i in sorted(typed, key=lambda x: (x["path"]["product_line"],
                                          x["path"]["system"], x["id"])):
        sysname = i["path"]["system"]
        if sysname not in cat_of:
            cat_of[sysname] = _sys_app_type(root, sysname)
        cat = cat_of[sysname]
        fac = fpset["app_type"][cat][0]
        w = W[i["nesma"]["type"]]
        got += w
        s.row({"条目ID": gs.raw(i["id"]),
               "产品线": i["path"]["product_line"], "子系统": sysname,
               "功能模块": i["path"].get("l1") or "",
               "功能点名称": i["name"],
               "计数项类别": i["nesma"]["type"], "未调整功能点": w,
               "软件类别": cat, "类别因子": fac,
               "复用系数": (reuse := (reuse_of.get(sysname) or {}).get(
                   i["path"].get("l1") or "（未分模块）", 1.0)),
               # **逐条目不取整。** 标准的取整发生在子系统层
               # （功能点数 = 该子系统 UFP × 类别因子 × 复用系数，取整一次），
               # 这里再各取一次整，794 次舍入攒起来就和 01 测算表分家 ——
               # 因子全取 1.0 时两者恰好相等，所以这个差在开始打复用度折扣
               # 之前是看不见的。金额跟着 01 走，本表是分解，不是第二套算法。
               "调整后功能点": (afp_i := w * fac * reuse),
               "复算式": f"{w}×{fac}×{reuse}",
               "判定依据": " ".join(
                   str(i["nesma"].get("rationale") or "").split())[:280]},
              clause="三.(一)2.1.2① NESMA 表6")
        afp_by_sys[sysname] = afp_by_sys.get(sysname, 0.0) + afp_i
    # Σ(w·因子·复用) 与 (Σw)·因子·复用 在实数上恒等，浮点上应在 1e-6 内。
    # 超了说明某个子系统内混用了不同的因子 —— 那正是「一部分条目按别的
    # 类别/复用度计价且看不出来」的形状。
    for sysname, tot in afp_by_sys.items():
        # 复用系数按功能模块取，所以「按子系统一次算」也得逐模块乘再求和。
        rmap = reuse_of.get(sysname) or {}
        want = fpset["app_type"][cat_of[sysname]][0] * sum(
            W[i["nesma"]["type"]] * rmap.get(i["path"].get("l1") or "（未分模块）", 1.0)
            for i in typed if i["path"]["system"] == sysname)
        if abs(tot - want) > 1e-6:
            raise gs.GovSheetError(
                f"[02_功能点明细] 子系统「{sysname}」逐条目调整后功能点累加 "
                f"{tot:.6f}，按模块分档算得 {want:.6f} —— "
                f"同一功能模块内的类别因子或复用系数不一致")
    if got != ufp_expect:
        raise gs.GovSheetError(
            f"[02_功能点明细] 逐条目累加 UFP = {got:,}，"
            f"01_功能点测算表 的子系统合计 = {ufp_expect:,}，差 {got - ufp_expect:,}。\n"
            f"  两张表读的是同一批条目，对不上说明筛选口径分了家 —— "
            f"**不要改容差**，先查 effort_only / class 过滤条件。")

    if untyped:
        import collections as _c
        by_st = _c.Counter(i.get("coauthor_status") or "(无)" for i in untyped)
        s.group(f"■ 列而不计：范围内、尚未判型的 {len(untyped)} 条（"
                + "；".join(f"{k} {v} 条" for k, v in by_st.most_common()) + "）",
                clause="三.(一)2.1.2①")
        for i in sorted(untyped, key=lambda x: (x["path"]["system"], x["id"])):
            s.row({"条目ID": gs.raw(i["id"]),
                   "产品线": i["path"]["product_line"],
                   "子系统": i["path"]["system"],
                   "功能模块": i["path"].get("l1") or "",
                   "功能点名称": i["name"],
                   "计数项类别": "待补", "未调整功能点": 0,
                   "调整后功能点": 0, "复算式": "—",
                   # 理由跟着条目自己的共创状态走。此前这里对 134 条写同一句
                   # 「描述为能力罗列」—— 其中 12 条根本不是那个毛病，
                   # 评审读到第一条不符的就会不信整张表。
                   "判定依据": _untyped_reason(i)},
                  clause="三.(一)2.1.2①")
        s.note(f"　上列 {len(untyped)} 条**在建设范围内但当前记 0 功能点**，"
               f"即本次报价未向甲方收取这部分。它们不是被删掉的条目 —— "
               f"记 0 与漏列在表上必须分得清，故列出。")
    if any(v != 1.0 for m in reuse_of.values() for v in m.values()):
        s.note("　【复用系数】按表3 注2 逐子系统取值，不是全表一个数；"
               "取值与依据见 01_功能点测算表 页末。")
    s.note("　【取整口径】本表逐条目**不取整**。标准的取整在子系统层一次完成"
           "（功能点数 = 该子系统 UFP × 类别因子 × 复用系数，见 01_功能点测算表），"
           "本表逐条再取一次整会攒出舍入差。所以本表合计与 01 的差不超过"
           "「子系统数 × 0.005」，金额以 01 为准。")
    s.total(expect={"未调整功能点": ufp_expect})
    s.finish()


# ============================================================
# 分册 —— 按**读者**切，不按费用科目切
# ============================================================
#
# 从前 Z00–Z04 是按科目切的，于是每一册里同时装着两种东西：
# 给评审看结论的（总表、轻度拆分）和给评审核过程的（逐条明细、参数、判定依据），
# 还混着第三种 —— 我方自己的决策与自查工具。
#
# 甲方样例的形态说明评审方期望「一本看完结构」：总表 + 按大类的轻度拆分，
# 一个工作簿四张表。但举证不能砍 —— 800 行功能点明细是「3,153 怎么来的」
# 的唯一答案。所以不是合并，是**分层**：送审册薄、举证册厚、内部件不出门。
#
# ⚠️ 丙本是**内部件**：里面的复核意见列着我方自算的超上限项，
# 二层试算能当场把金额调高 43%。它们对我们有用，但不该送到评审手里 ——
# 所以它的 status 与另两本不同，文件名上就写着勿送审。

BOOKS: dict[str, dict[str, Any]] = {
    "甲": {
        "topic": "送审册", "status": None,        # None = 用 deal 的 status
        # 业务维度：总的 → 分子系统的 → 设备的 → 其他费用。
        # 「按什么方法算出来的」是另一个维度，属举证，整体在乙本。
        "sheets": ["00_总表", "01_软件开发费汇总", "02_硬件设备购置费汇总",
                   "03_其他费用与预备费"],
    },
    "乙": {
        "topic": "计量举证册", "status": None,
        "sheets": ["01_功能点测算表", "02_功能点明细", "03_计价参数",
                   "04_工作量测算表", "05_工作量法功能明细",
                   ],
        # 06_工作量活动分解 已删（2026-08-09）：它拆的是「知识工程这个领域整体
        # 按活动怎么分」，而 05 里是 134 个具体交付物（幼儿膳食营养语料库 4.0024
        # 人月…）—— 两者不是一个对象，06 完全不解释 05 的人月，起不到举证作用。
        # 且活动构成/占比/人员类型 04 都已有列，06 独有的只剩「阶段拆分说明」
        # 几句注释，已并入 04 的备注。
        # ⚠ 更要紧的是：把活动占比套到 134 个交付物上也补不上真缺口 ——
        # 那套百分比是**按子系统的模板**，套一遍只是把同一组数抄 134 遍，
        # 行数涨信息不涨。人月本身是从源表倒算的（1.559×/1.870×），
        # 见编制说明「人月缺独立测算依据」一条。
    },
    "丙": {
        "topic": "内部工作底稿", "status": "内部版·勿送审",
        # 04_功能点法取值档位对比 已删（2026-08-09）：它的场景来自 deal.yaml
        # 的 fp_scenarios，是一份**独立于实际计价设置**的手写假设，改了
        # fp_method_settings 不会同步 —— 于是表上写着「本次送审采用『保守
        # （当前）』档」而那一档根本不是当前，四行金额全是按已废弃的假设算的。
        # 一份数据两处维护，必然过期，而过期没有任何东西会报错。
        # 档位决策已完成（类别因子取上界、依据已写），这张表的用途也用尽了。
        # 04_复核意见 与 05_工作量口径对账 已移出（2026-08-09）：丙本是**试算与
        # 录入工具**（改参数看影响、填量纲与复用度），质检是另一件事 ——
        # 一个是「我要改什么」，一个是「我方自己查出了什么问题」。混在一起，
        # 填表的人要在自查清单里翻找输入格，看质检的人要在参数面板里找结论。
        "sheets": ["01_试算参数", "02_二层试算", "03_复用度模块清单"],
    },
    "丁": {
        "topic": "内部质检册", "status": "内部版·勿送审",
        # **不送审。** 复核意见列的是我方自算的超上限项与待补项 ——
        # 送到评审手里等于替对方把核减清单先写好。
        "sheets": ["01_复核意见", "02_工作量口径对账"],
    },
}
#: 非路由分册：表名不固定（甲附按园所动态生成）或带活公式（丙附是录入工具），
#: 都进不了 BookRouter。**但仍要在册登记** —— 登记表是「这个套表有哪几册」的
#: 唯一出处，漏登记的册不会有任何地方提醒，只是某次出表少了一本。
UNROUTED_BOOKS: dict[str, dict[str, Any]] = {
    "甲附": {"topic": "硬件设备清单", "status": None,
             "note": "逐台清单，按园所/批次动态成表；由引擎从第二层配置生成"},
    "丙附": {"topic": "硬件配置录入", "status": "内部版·勿送审",
             "note": "录入工具，带联动活公式；人填后同步回 device-config.yaml"},
}
PASSTHROUGH_BOOK = {"no": "甲附", "topic": UNROUTED_BOOKS["甲附"]["topic"]}


class BookRouter:
    """按表名把 GovSheet 分派到所属分册的工作簿。

    只需要 `create_sheet(name)` —— GovSheet 拿到的 `wb` 只用这一个方法，
    所以这个对象可以直接顶替 Workbook 传进去，各 emit_* 一行不用改。

    **未登记的表名直接报错。** 这条是这个类存在的理由：真正会发生的失败
    不是分错册，是以后有人加了一张表，它默默进了送审册跟着发出去。
    """

    def __init__(self, books: dict[str, dict[str, Any]]):
        self.books = books
        self.owner: dict[str, str] = {}
        for b, spec in books.items():
            for n in spec["sheets"]:
                if n in self.owner:
                    raise gs.GovSheetError(
                        f"表名 {n!r} 同时登记在「{self.owner[n]}」与「{b}」两册；"
                        f"表名要全局唯一，否则跨册引用指向哪一本说不清")
                self.owner[n] = b
        self.wbs: dict[str, Any] = {}
        self.made: list[str] = []

    def create_sheet(self, name: str):
        b = self.owner.get(name)
        if b is None:
            raise gs.GovSheetError(
                f"表「{name}」没有登记到任何一册。\n"
                f"  新增一张表必须同时在 BOOKS 里指明它归哪一本 —— "
                f"不登记就默认进送审册的话，内部用的表会跟着发出去，"
                f"而且没有任何地方会报错。\n"
                f"  已登记：{sorted(self.owner)}")
        if name in self.made:
            raise gs.GovSheetError(f"表「{name}」被创建了两次")
        self.made.append(name)
        return self.wbs.setdefault(b, gs.new_workbook()).create_sheet(name)

    def finish(self) -> dict[str, Any]:
        """核对：登记了的都造出来了。返回 {册名: 工作簿}。"""
        missing = {b: [n for n in spec["sheets"] if n not in self.made]
                   for b, spec in self.books.items()}
        missing = {b: v for b, v in missing.items() if v}
        if missing:
            raise gs.GovSheetError(
                f"以下表登记了但没造出来：{missing}\n"
                f"  缺表的分册发出去，评审看到的是残本。"
                f"若某张表这一版确实不出，从 BOOKS 里删掉它的登记。")
        for b, wb in self.wbs.items():
            order = self.books[b]["sheets"]
            wb._sheets.sort(key=lambda ws: order.index(ws.title))
        return self.wbs


_OUT_RE = re.compile(r"_v([\d.]+)_(\d{8})\.xlsx$")


def _archive_previous(out: Path, date: str, version: str) -> None:
    """把上一版挪进 `out/<日期>_v<版本>/`。

    送审目录里两版并存，最大的风险不是占地方，是**发错版本** ——
    文件名只差几个字符，收件人不会逐字比。所以旧版不删（可能还要拿来对账），
    但要挪出手边。

    键是 **(日期, 版本)** 而不是只看日期：同一天出两版是常事，
    只按日期判会把当天的旧版留在根目录 —— 而那正是最容易发错的一种，
    两个文件名连日期都一样。

    不看 mtime：重新生成会刷新 mtime，但那不代表它是新版本。
    同一天重出同一版仍然原地覆盖，那是有意的。
    """
    if not out.is_dir():
        return
    moved: dict[str, list[str]] = {}
    for f in sorted(out.glob("*.xlsx")):
        m = _OUT_RE.search(f.name)
        if not m or (m.group(2), m.group(1)) == (date, version):
            continue
        d = out / f"{m.group(2)}_v{m.group(1)}"
        d.mkdir(exist_ok=True)
        target = d / f.name
        if target.exists():
            # 同名已归档：留在原地并报出来，不覆盖归档件 ——
            # 静默覆盖历史版本，就再也查不出当时送的是什么。
            print(f"  ⚠ {f.name} 已在 {d.name}/ 中存在，未移动（不覆盖归档件）")
            continue
        f.rename(target)
        moved.setdefault(d.name, []).append(f.name)
    for dt, names in sorted(moved.items()):
        print(f"  历史版本 {len(names)} 册 → out/{dt}/")


def emit_pricing_params(wb, pack: StandardPack, fpset: dict,
                        fp_systems: list[dict], *, name: str,
                        subtitle_tail: str,
                        deal_counting: str | None = None
                        ) -> tuple[Any, dict[str, int]]:
    """计价参数 —— 既是举证（每个系数的页码/条款/区间），也是试算的参数面板。

    与康养 Z02 的 `02_计价参数` 同构（同一套配色约定：灰=标准取值不得改，
    黄=本商机可试算）。柳州与山东的差别在两处，都要写出来而不是留空：

      · 无「规模变更因子」—— 柳州公式是 UFP × 类别因子 × 复用系数，
        没有这一环。硬套山东会让报价虚高 21%。
      · 无「开发类别系数」—— 人月费率固定 1.7 万，不按开发类别调。

    「本标准没有这个维度」也要印在表上，否则读的人会以为是漏印。

    返回 (sheet, {参数名: 行号})，供 04 用绝对地址 / VLOOKUP 区域引用。
    """
    s = GovSheet(
        wb, name,
        title="功能点估算法　计价参数与依据",
        subtitle="每个取值均标注标准原文页码与条款。"
                 "**灰格为标准取值，改动即偏离编制依据**；黄格为本商机可试算项。"
                 + subtitle_tail,
        columns=[
            Col("参数", "text", width=30),
            Col("取值", "rate", width=14, cell_role="locked"),
            Col("单位/说明", "text", width=30),
            Col("取值区间", "text", width=16),
        ],
        clause_required=True, clause_name="标准出处")
    rows: dict[str, int] = {}
    R = (pack.data.get("rates") or {})

    def cite(node) -> str:
        c = node.get("citation") or {}
        return f"p.{c.get('page')} {c.get('section')}"

    # 计数方法是**二择一的方法选择**，不是取值 —— 从前它隐含在代码里，
    # 表上看不出我们做过这个选择。标准 p.13 给了两法，UFP 差别很大。
    fc = (pack.data.get("fp_counting") or {})
    _m = (deal_counting or "估算功能点法")
    for k in ("预估功能点法", "估算功能点法"):
        node = fc.get(k) or {}
        w = node.get("weights") or {}
        c = node.get("citation") or {}
        rows[f"count:{k}"] = s.row(
            {"参数": ("　计数方法 · " + k + ("　【本项目采用】" if k == _m else "")),
             "取值": None,
             "单位/说明": "　".join(f"{a}={b}" for a, b in w.items())
                          + "　" + str(node.get("applies_to", ""))[:40],
             "取值区间": "两法二择一"},
            clause=f"p.{c.get('page')} {c.get('section')}")
    s.note(f"　【计数方法】标准 p.13 给出预估/估算两种计数方法，**二择一**。"
           f"本项目采用「{_m}」—— 它逐基本过程计数（EI/EO/EQ 都数），"
           f"举证颗粒度到单个功能点；预估法只数逻辑文件（35×ILF+15×EIF），"
           f"数得快但答不出「这个数怎么来的」。选择写在 deal.yaml 的 counting_method。")
    s.blank()

    n = R["productivity_hours_per_fp"]
    rows["prod"] = s.row(
        {"参数": "软件开发生产率",
         "取值": xlround(n["value"] * fpset["productivity_ratio"], 4),
         "单位/说明": f"人时/功能点　基准 {n['value']}（{n['basis']}）× "
                      f"浮动 {fpset['productivity_ratio']}",
         "取值区间": f"±20%（{n['value'] * 0.8:.3f} ~ {n['value'] * 1.2:.3f}）"},
        clause=cite(n), cell_roles={"取值": "input"})
    n = R["man_hours_per_month"]
    rows["hpm"] = s.row(
        {"参数": "人月折算系数", "取值": n["value"],
         "单位/说明": "人时/人月　174 = 21.75 × 8", "取值区间": "标准固定值"},
        clause=cite(n))
    n = R["base_man_month_rate"]
    rows["rate"] = s.row(
        {"参数": "基准人月费率", "取值": n["value"],
         "单位/说明": f"元/人月　{n.get('note', '')}", "取值区间": "标准固定值"},
        clause=cite(n))
    ru = (pack.data.get("factors") or {})["reuse"]
    # 复用系数不是全项目一个数，**逐子系统取**（注2 原文就是「已有软件系统
    # 或功能模块基础上」，粒度在模块）。这里只登记默认档与档位表；
    # 各子系统实际取哪一档见 01/02 的「复用度档位」列。
    _rused = sorted({g["reuse_level"] for x in fp_systems
                     for g in (x.get("groups") or [])})
    rows["reuse"] = s.row(
        {"参数": "复用系数（新建缺省档）", "取值": pack.factor("reuse", "新建"),
         "单位/说明": "注2 前半句：新建项目默认取 1（复用度低）。"
                      f"本次逐子系统取值，实际用到的档位：{'、'.join(_rused)}",
         "取值区间": "见下方复用度档位表"},
        clause=cite(ru))

    # 复用度档位表 —— 与下方「软件类别调整因子表」同构：档位是选项，系数由档位查表得出。
    # 从前这里只有一个裸系数，02 试算表的复用系数列就是个自由数字，
    # 人得自己记住 0.3333 对应「高」—— 填错一位小数不会有任何东西拦住。
    _RD = {"高": "在已有软件系统或功能模块基础上，本项目主体沿用",
           "中": "在已有软件系统或功能模块基础上优化完善、调整改造（注2 明文默认）",
           "低": "为本项目全新开发（注2 明文：新建项目默认取 1）"}
    s.blank()
    s.note("　【复用度调整系数表 · 表3 注2】01/02 逐行标注的「复用系数」由"
           "「复用度」查本表得出；试算表用 VLOOKUP 查本区间")
    rows["reuse_first"] = s._next
    _rvals = (ru.get("values") or {})
    for k in ("高", "中", "低"):
        rows[f"reuse:{k}"] = s.row(
            {"参数": f"　复用度 · {k}", "取值": _rvals.get(k),
             "单位/说明": ("【本项目用到】" if k in _rused else "") + _RD[k],
             "取值区间": "标准固定值"},
            clause=cite(ru))
    rows["reuse_last"] = s._next - 1

    at = (pack.data.get("factors") or {})["app_type"]
    ranges = at.get("value_range") or {}
    used = {x["app_type"] for x in fp_systems}
    s.blank()
    s.note("　【软件类别调整因子表 · 表3】01/02 逐行标注的「类别因子」取自此表；"
           "试算表用 VLOOKUP 查本区间")
    rows["app_first"] = s._next
    for k, (v, basis) in sorted(fpset["app_type"].items()):
        lo, hi = ranges.get(k, (v, v))
        note = at.get("ranges", {}).get(k, "")
        rows[f"app:{k}"] = s.row(
            {"参数": f"　软件类别 · {k}", "取值": v,
             "单位/说明": ("【本项目用到】" if k in used else "") + note,
             "取值区间": f"[{lo}, {hi}]"},
            clause=cite(at), cell_roles={"取值": "input"})
    rows["app_last"] = s._next - 1
    # 列位不猜：04 用它拼绝对地址与 VLOOKUP 区域
    rows["_kcol"], rows["_vcol"] = _col(s, "参数"), _col(s, "取值")
    for k, (v, basis) in sorted(fpset["app_type"].items()):
        if k in used and v > 1.0:
            s.note(f"　　·{k} 取 {v} > 1.0，依据："
                   + " ".join(str(basis).split()))

    # 本标准**没有**的两个维度 —— 空着不写，读的人会以为漏印
    s.blank()
    sc = (pack.data.get("factors") or {})["size_change"]
    s.note(f"　【规模变更因子】**本标准无此维度** —— "
           f"{' '.join(str(sc.get('note', '')).split())}。"
           f"（山东标准有，柳州没有；跨省复用模板时最容易多乘这一道）")
    dc = (pack.data.get("factors") or {})["dev_category"]
    s.note(f"　【开发类别系数】**本标准无此维度** —— "
           f"{' '.join(str(dc.get('note', '')).split())}。"
           f"试算表的人月费率直接取基准人月费率。")
    s.note("　【表3 注1】「凡取值超过1的，需列明具体取值依据」（p.14）—— "
           "黄格标的是本商机可调项，但改到 1.0 以上必须在 deal.yaml 的 "
           "fp_method_settings.app_type_factors 里写 basis，否则生成端拒绝出表。")
    s.finish()
    return s, rows


def _prefix_groups(names: list[str]) -> dict[str, str]:
    """把同前缀的模块名归组：取「与他人共享的最长前缀」（4→3→2 字）。

    源表的 l1 常把同一块代码切成几条（园平台的出勤概况/出勤明细/出勤设置
    多半是同一个考勤模块）。按 UFP 排序会把它们打散到表的各处，人就得
    翻来覆去比对，还容易给同一块代码填出互相矛盾的档位。
    """
    import collections
    best: dict[str, str] = {}
    for L in (4, 3, 2):
        cnt = collections.Counter(n[:L] for n in names if len(n) > L)
        for n in names:
            if len(n) > L and cnt[n[:L]] >= 2 and n not in best:
                best[n] = n[:L]
    return {n: best.get(n, "") for n in names}


def emit_reuse_modules(wb, root: Path, pack: StandardPack, fpset: dict,
                       fp_systems: list[dict], detail: list[dict],
                       sw: dict | None = None,
                       effort_only: set[str] | None = None,
                       ef: dict | None = None) -> None:
    """丙本 03 —— 逐功能模块的复用度填写表。

    ## 为什么在丙本，不是一个独立 xlsx

    丙本带 `_基线` 指纹：填过的那份若在生成之后被改过，同步会拒绝并让你先
    重出再搬改动。裸 xlsx 没有这层保护，而「填完被下一次重出覆盖」在本项目
    已经真实发生过。分册规则也对得上：丙＝软件侧工作底稿，丙附＝硬件录入。

    ## 但它写的是**产品级** BOM

    复用度是产品事实（`bom/taxonomy.yaml`），不是商机参数。所以
    `quote_sync --write` **不写它**，要额外的 `--write-bom` ——
    改了会影响所有商机，不能是一次「同步」的静默副作用。
    """
    tax = _taxonomy_systems(root)
    M2L = {"existing": "高", "partial": "中", "new": "低", None: "低"}
    rvals = ((pack.data.get("factors") or {}).get("reuse") or {}).get("values") or {}
    by_sys: dict[str, dict[str, tuple[int, int]]] = {}
    for d in detail:
        m = d.get("l1") or "（未分模块）"
        n, u = by_sys.setdefault(d["system"], {}).get(m, (0, 0))
        by_sys[d["system"]][m] = (n + 1, u + d["ufp"])

    s = GovSheet(
        wb, "03_复用度模块清单",
        title="工作量法 / 功能点法 参数填写表 —— 逐功能模块（FP）· 逐建设对象（工作量法）",
        subtitle="表3 注2 原文为「在已有软件系统**或功能模块**基础上进行优化完善或"
                 "调整改造的」—— 判断单位到功能模块，比软件类别（注1「按主体功能"
                 "取值」，按子系统）更细。黄格待填；**只需填与「当前」不同的行**。"
                 "⚠ 本表写的是**产品级** BOM（bom/taxonomy.yaml），改了影响所有商机，"
                 "故 quote_sync 需显式 --write-bom 才写。"
                 "工作量法子系统按**活动**列（表1 注3 的单价同样含复用系数），"
                 "其复用系数**尚未接入引擎**，先填后接。",
        columns=[
            Col("子系统", "text", width=16, cell_role="locked"),
            Col("软件类别", "text", width=18, cell_role="locked"),
            Col("类别因子", "rate", width=8, cell_role="locked"),
            Col("同前缀组", "text", width=10, cell_role="locked"),
            # 只有工作量法行用：把阶段占比与人工成本摆到表上。
            # 五阶段单价 1.9→1.4 差 35%，把 10 个百分点从实施部署挪到需求分析，
            # 同样的人月总量金额就变 —— 这个决定从前藏在 effort-wbs.yaml 的
            # 活动 stage 字段里，方案人员在表上看不见自己在决定什么。
            Col("阶段结构", "text", width=26, cell_role="locked"),
            Col("功能模块", "text", width=26, cell_role="locked"),
            Col("条目数", "int", width=7),
            Col("UFP", "int", width=7, sum=True),
            # ---- 工作量法专用：人月的**来源**参数 ----
            # 单位工作量**不是标准因子**（表1 注3 的三个可调量只有人工成本/
            # 风险系数/复用系数），它在更前面一层：量纲 × 单位工作量 → 人月，
            # 而人月怎么估标准明确不管（表1 注1 只给方法不给数）。
            # 从前它只能改 YAML —— 乙本 05 那三列虽然标成了 input，但
            # quote_sync 只读丙本和丙附，填了不生效也不报错，是个静默空转。
            # 录入面必须在丙本，与「数由引擎算定、人只在丙本填」的规矩一致。
            Col("可核查量纲", "text", width=16, cell_role="input"),
            Col("数量", "fp", width=8, cell_role="input"),
            Col("单位工作量(人月/单位)", "rate", width=16, cell_role="input"),
            Col("→工作量(人月)", "fp", width=13, sum=True, cell_role="locked"),
            Col("单位工作量依据", "text", width=44, cell_role="input"),
            Col("当前复用度", "text", width=10, cell_role="locked"),
            Col("复用度", "text", width=9, cell_role="input", choices=["高", "中", "低"]),
            Col("取值依据", "text", width=46, cell_role="input"),
            # 三档并列。从前只给低/高两列 + 一个「较低档可减」，那一列算的是
            # **全取高**的情形，中档只减 1/3 却被按 2/3 读 —— 我自己按那一列
            # 加总就多估了 10 万。列名要让人一眼看出算的是哪一档。
            Col("低=1.0 金额", "money", width=13, sum=True, cell_role="locked"),
            Col("中=2/3 金额", "money", width=13, sum=True, cell_role="locked"),
            Col("高=1/3 金额", "money", width=13, sum=True, cell_role="locked"),
            Col("取高可减", "money", width=12, sum=True, cell_role="locked"),
        ],
        clause_required=True)
    prof = get_profile(pack.formula_profile)
    for x in sorted(fp_systems, key=lambda y: -y["cost"]):
        mods = by_sys.get(x["system"]) or {}
        if not mods:
            continue
        # 未声明 = 低（表3 注2 缺省）。**不看子系统级 maturity** ——
        # 那一层已经取消，留着看会让「当前」显示成一个并不生效的值。
        cur_default = "低"
        _ndec = len((tax.get(x["system"]) or {}).get("modules") or {})
        s.group(f"■ {x['system']}　{x['app_type']}　类别因子 {x['app_type_factor']}"
                f"　{len(mods)} 个模块　已声明 {_ndec} 个，其余按缺省「低」",
                clause="三.(一)2.1.2 表3 注2")
        node_mods = ((tax.get(x["system"]) or {}).get("modules") or {})
        # 排序：同前缀相邻 → 组内按 UFP 降序 → 组间按组总 UFP 降序
        # → 「逻辑文件」永远排最后（它是数据结构 ILF/EIF，不是功能模块，
        #   判的是「表结构是不是沿用的」，与功能模块是两个问题）。
        pg = _prefix_groups(list(mods))
        gt: dict[str, int] = {}
        for _m, (_n, _u) in mods.items():
            k = pg[_m] or f"\x00{_m}"
            gt[k] = gt.get(k, 0) + _u

        def _key(kv):
            _m, (_n, _u) = kv
            k = pg[_m] or f"\x00{_m}"
            return (1 if _m == "逻辑文件" else 0, -gt[k], k, -_u)

        for m, (n, u) in sorted(mods.items(), key=_key):
            cur = M2L.get((node_mods.get(m) or {}).get("maturity"), cur_default)
            lo = prof.fp_cost(u, pack=pack, app_type=x["app_type"],
                              settings=fpset, reuse_level="低")["cost"]
            mid = prof.fp_cost(u, pack=pack, app_type=x["app_type"],
                               settings=fpset, reuse_level="中")["cost"]
            hi = prof.fp_cost(u, pack=pack, app_type=x["app_type"],
                              settings=fpset, reuse_level="高")["cost"]
            s.row({"子系统": x["system"], "软件类别": x["app_type"],
                   "类别因子": x["app_type_factor"],
                   "同前缀组": ("数据结构" if m == "逻辑文件" else (pg[m] or "")),
                   "功能模块": m,
                   "条目数": n, "UFP": u, "当前复用度": cur,
                   "复用度": cur,
                   # **依据要从 taxonomy 读回来。** 从前这里写死成空串，
                   # 于是每次重出都把已写好的依据抹掉，第二次 --write-bom
                   # 必报「取中但没写依据」—— 而人明明写过。
                   "取值依据": str((node_mods.get(m) or {}).get("maturity_basis") or ""),
                   "低=1.0 金额": lo, "中=2/3 金额": mid,
                   "高=1/3 金额": hi, "取高可减": lo - hi},
                  clause="三.(一)2.1.2 表3 注2")
    # ---- 工作量法子系统：单位是**活动**，不是功能模块 ----
    # 表1 注3 原文：单价 = 人工成本 × 风险系数 × 复用系数 —— 复用系数在
    # 工作量法里也是标准明文，不是「后续可能引入」。本次隐含取 1，
    # 但引擎尚未把它接进单价，所以这一段先填不算。
    if sw and effort_only:
        _rt = {k: v["value"] for k, v in
               (pack.data["effort_method"]["stage_rates"] or {}).items()}
        _wr = _wbs_roles(root)
        for sysname, blk in (sw.get("systems") or {}).items():
            if sysname not in effort_only or not blk.get("stages"):
                continue
            at = (tax.get(sysname) or {}).get("app_type") or "—"
            objs = [lf for lf in (blk.get("leaves") or [])
                    if (lf.get("effort_basis") or {}).get("man_months")]
            if not objs:
                continue
            _pct = {st: ((_wr.get(sysname) or {}).get(st) or {}).get("pct", 0.0)
                    for st in STAGES}
            # 该系统「一个人月」横跨五阶段的加权价 —— 由阶段占比与五个法定
            # 单价推出，**不是编出来的平均价**：Σ(阶段占比 × 阶段单价)。
            _blend = sum(_pct.get(st, 0.0) * _rt.get(st, 0.0) for st in STAGES)
            tot_mm = sum(float(lf["effort_basis"]["man_months"]) for lf in objs)
            tot_y = blk.get("obj_amount_wan", 0.0) * 10000
            _nd = sum(1 for lf in objs if (lf.get("reuse_level") or "低") != "低")
            s.group(f"■ {sysname}　2.1.1 工作量估算法　{at}"
                    f"（类别因子不适用，表3 属 2.1.2）　{len(objs)} 个建设对象"
                    f"　{tot_mm:,.2f} 人月　合计 ¥{tot_y:,.0f}",
                    clause="三.(一)2.1.1 表1 注3")
            pg2 = _prefix_groups([lf["name"] for lf in objs])
            for lf in sorted(objs, key=lambda z: (
                    pg2[z["name"]] or f"\x00{z['name']}",
                    -float(z["effort_basis"]["man_months"]))):
                eb = lf["effort_basis"]
                mm = float(eb["man_months"])
                o_i = (ef or {}).get("obj", {}).get((sysname, lf["name"])) or {}
                lo = xlround(mm * _blend * (ef or {}).get("risk", 1.0) * 10000, 2)
                # **「对象·」是给 quote_sync 的判别标记。** 这一列原来只用于
                # 分组展示，功能点法行填模块前缀。工作量法行若也填前缀，
                # 回写时会被当成功能点法的功能模块写进 taxonomy —— 那里没有
                # 这个名字，下一次出表直接拒绝。判别必须显式，不能靠猜。
                s.row({"子系统": sysname, "软件类别": at,
                       "同前缀组": "对象" + ("·" + pg2[lf["name"]]
                                             if pg2[lf["name"]] else ""),
                       "阶段结构": f"五阶段加权单价 {_blend:.4f} 万元/人月",
                       "功能模块": lf["name"],
                       "可核查量纲": eb.get("unit"),
                       "数量": eb.get("qty"),
                       "单位工作量(人月/单位)": eb.get("per_unit_mm"),
                       "→工作量(人月)": xlround(mm, 4),
                       "单位工作量依据": eb.get("basis"),
                       "当前复用度": o_i.get("level", "低"),
                       "复用度": o_i.get("level", "低"),
                       "取值依据": o_i.get("basis", ""),
                       "低=1.0 金额": lo,
                       "中=2/3 金额": xlround(lo * float(rvals.get("中", 1.0)), 2),
                       "高=1/3 金额": xlround(lo * float(rvals.get("高", 1.0)), 2),
                       "取高可减": xlround(lo * (1 - float(rvals.get("高", 1.0))), 2)},
                      clause="三.(一)2.1.1 表1 注3")

    s.total("合计")
    s.note("　【工作量法段的软件类别】照显子系统在 BOM 登记的类别（与丙本 02 一致），"
           "但「类别因子」列**留空** —— 表3「软件类别调整因子」挂在 2.1.2 功能点"
           "估算法 底下，2.1.1 的单价公式为「人工成本 × 风险系数 × 复用系数」，"
           "没有这一道。给这一段乘一道标准没有的系数会让报价虚高且答不出出处。")
    s.note("　【阶段结构】工作量法行的这一列给出该建设对象的**量纲 × 单位工作量**"
           "与加权单价。加权单价 = Σ(阶段占比 × 阶段单价)，阶段占比由 "
           "bom/effort-wbs.yaml 各活动的 stage 字段推出 —— 五阶段人工成本 "
           "1.9/1.9/1.7/1.6/1.4 相差 35%，**阶段归属直接改金额**；要调占比改那里，"
           "不在本表填。")
    s.note("　【工作量法段的判断单位是建设对象】表1 注3 原文「单价 = 人工成本 × "
           "风险系数 × 复用系数」，复用系数在工作量法里同样是标准明文，**已接入引擎**，"
           "填了即改金额。判断单位经两次下移定在建设对象：阶段是活动映射出来的派生桶；"
           "活动也不对 —— 同一个「入库、索引与检索配置」用在既有食材库上是沿用、"
           "用在新建富媒体库上是新做，判在活动上一次取值就把整系统的交付物一起打了折。"
           "建设对象是 05 的计量单元，也是「这一项有多少是新做的」唯一问得清楚的对象。"
           "**只有这一级生效**：effort-wbs.yaml 的活动 reuse 节点已退役，仍声明非「低」"
           "会直接拒绝出表，不静默忽略。")
    s.note(f"　【档位与系数】高 = {rvals.get('高')}　中 = {rvals.get('中')}　"
           f"低 = {rvals.get('低')}（低 = 为本项目全新开发，注2 缺省）")
    s.note("　【三档金额列】低/中/高三列并列给出该模块在各档位下的金额；"
           "「取高可减」只算**取高**的差额 —— 填「中」的行实际只减一半，"
           "不要拿这一列的合计当总减额。")
    s.note("　【只填差异】「复用度」列已预填当前值，与当前一致的行不用动。"
           "填了与当前不同的值才会被同步识别为改动。")
    s.note("　【依据必填】偏离缺省（≠低）而没有依据的，**引擎拒绝出表** —— "
           "与表3 注1 同构。这个方向是降价，没有出处的折扣在评审那里是"
           "「随意定价」，比不打折更被动。")
    s.note("　【判据】看的是**代码有没有既有实现**，不是模块名字像不像定制。"
           "名字通用的可能是为本项目重做的，名字像定制的可能来自既有产品。")
    s.note("　【排序】同前缀的模块排在一起（如出勤概况/出勤明细/出勤设置），"
           "便于成组判断 —— 源表的模块划分常把同一块代码切成几条，"
           "散在表里容易给同一块代码填出互相矛盾的档位。"
           "「同前缀组」列为空表示该模块无同前缀伙伴。")
    s.note("　【逻辑文件】多数子系统里 UFP 最大的一块是「逻辑文件」（ILF/EIF，"
           "即数据结构）。系统沿用则其数据结构一般也沿用；但若为本项目新增了"
           "大量字段或新表，应判低。这一项容易漏。")
    s.note("　【落实路径】填完跑 `quote_sync` 看差异 → `--write-bom` 写入 "
           "bom/taxonomy.yaml（各子系统取众数作默认档，少数派进 modules 例外）"
           "→ 重出套表。另需在 deal.yaml 打开 fp_method_settings.reuse.apply_maturity。")
    s.finish()


def emit_fp_whatif(wb, pack: StandardPack, fpset: dict,
                   fp_systems: list[dict], prows: dict[str, int],
                   engine_total: float, *, param_sheet: str,
                   root: Path | None = None, sw: dict | None = None,
                   effort_only: set[str] | None = None,
                   ef: dict | None = None) -> None:
    """二层试算 —— 活公式，引同册的试算参数表。

    ## 为什么这里可以用活公式

    本项目的铁律是「数值由引擎算定，Excel 不承担计算」，那条铁律来自
    SUMIF 被合并单元格击穿的事故。但这条链是 14 行以内的
    「参数 → 子系统汇总」直引用，没有跨表查找明细、没有合并单元格参与计算。
    每一条公式在写入时都由 `gov_sheet.formula()` 用 Python 复算过一遍。

    ## 与 01 的口径差

    引擎逐子系统 `xlround(...,2)`；本表同构地写 ROUND，所以两者应当逐行相等，
    差值列全 0。**差不是 0 就说明公式与引擎分家了**，直接印在表上。
    """
    P = f"'{param_sheet}'"
    kcol, vcol = prows["_kcol"], prows["_vcol"]
    a_prod = f"{P}!${vcol}${prows['prod']}"
    a_hpm = f"{P}!${vcol}${prows['hpm']}"
    a_rate = f"{P}!${vcol}${prows['rate']}"
    app_rng = (f"{P}!${kcol}${prows['app_first']}:${vcol}${prows['app_last']}")
    # 复用度档位表仍在 01_试算参数 上（供查阅），但 02 不再引它 ——
    # 复用度是第一层的判断，见 03_复用度模块清单。

    prod = xlround(pack.rate("productivity_hours_per_fp")
                   * fpset["productivity_ratio"], 4)
    hpm = pack.rate("man_hours_per_month")
    base_rate = pack.rate("base_man_month_rate")
    choices = sorted(fpset["app_type"])

    s = GovSheet(
        wb, "02_二层试算",
        title="第二层试算 · 算法参数",
        subtitle="试的是**算法参数**（第二层）。灰格取自区域标准包，"
                 "黄格「软件类别」其实是 BOM 里的条目属性（第一层）—— "
                 "两者改了要去不同地方落实，见页末说明。绿格全部为活公式；"
                 "**送审值以【甲本·送审册】01_软件开发费汇总 为准**。"
                 "⚠ 本表为内部决策工具，不随送审件提交。",
        columns=[
            Col("子系统", "text", width=30),
            Col("软件类别", "text", width=18, cell_role="input",
                choices=choices or None),
            Col("未调整功能点", "fp", width=12, sum=True),
            # 复用调整在**第一层**做完（逐功能模块，见 03_复用度模块清单），
            # 本表只试**第二层**参数（生产率、类别因子）。
            # 从前这里有「复用度」下拉，等于把第一层的判断搬到第二层来试，
            # 还逼得一个子系统拆成高/中/低三行 —— 那三行是举证的形态，
            # 属于乙本 01，不属于试算面板。
            Col("复用调整后功能点", "fp", width=14, sum=True, cell_role="locked"),
            Col("软件类别因子", "rate", width=12, cell_role="calc"),
            Col("调整后功能点", "fp", width=12, sum=True, cell_role="calc"),
            Col("工作量(人月)", "fp", width=12, sum=True, cell_role="calc"),
            Col("人月费率(元)", "money", width=13, cell_role="calc"),
            Col("软件开发费(元)", "money", width=15, sum=True, cell_role="calc"),
            Col("与送审值差(元)", "money", width=15, sum=True,
                cell_role="calc"),
        ])
    trial = 0.0
    for x in sorted(fp_systems, key=lambda y: -y["cost"]):
        r = s._next
        cat = x["app_type"]
        fac = fpset["app_type"][cat][0]
        c_cat = _col(s, "软件类别")
        c_ufp = _col(s, "未调整功能点")
        c_fac = _col(s, "软件类别因子")
        c_ru = _col(s, "复用调整后功能点")
        c_afp = _col(s, "调整后功能点")
        c_mm = _col(s, "工作量(人月)")
        c_rt = _col(s, "人月费率(元)")
        c_amt = _col(s, "软件开发费(元)")
        # 复用调整后 UFP = Σ各档（该档 UFP × 该档复用系数），第一层的结果。
        # 不取整 —— 取整放在乘完类别因子之后一次做，与引擎逐档 ROUND 的
        # 结果在本项目所有子系统上一致；万一哪天不一致，formula() 的自验会当场拦住。
        ufp_r = sum(g["ufp"] * g["reuse_factor"] for g in x["groups"])
        eng_fp, eng_mm, eng_cost = x["fp"], x["effort_man_months"], x["cost"]
        afp = xlround(ufp_r * fac, 2)
        mm = xlround(afp * prod / hpm, 2)
        cost = xlround(mm * base_rate, 2)
        trial += cost
        w = f"[02_二层试算] {x['system'][:16]}"
        s.row({
            "子系统": x["system"],
            "软件类别": cat,
            "未调整功能点": x["ufp"],
            "复用调整后功能点": xlround(ufp_r, 4),
            "软件类别因子": formula(
                f'=VLOOKUP("　软件类别 · "&{c_cat}{r},{app_rng},2,FALSE)',
                fac, x["app_type_factor"], where=f"{w} 类别因子"),
            "调整后功能点": formula(
                f"=ROUND({c_ru}{r}*{c_fac}{r},2)",
                afp, eng_fp, where=f"{w} 调整后功能点"),
            "工作量(人月)": formula(
                f"=ROUND({c_afp}{r}*{a_prod}/{a_hpm},2)",
                mm, eng_mm, where=f"{w} 工作量"),
            "人月费率(元)": formula(
                f"={a_rate}", base_rate, x["man_month_rate"],
                where=f"{w} 人月费率"),
            "软件开发费(元)": formula(
                f"=ROUND({c_mm}{r}*{c_rt}{r},2)", cost, eng_cost,
                where=f"{w} 软件开发费"),
            # 差额必须是活公式：试算改了参数金额会动，静态 0 会一直显示
            # 「与送审值一致」—— 那正是最误导人的形状。送审值写成字面量，
            # 它是本次出表时定死的数，不该跟着试算走。
            "与送审值差(元)": formula(
                f"=ROUND({c_amt}{r}-{eng_cost:.2f},2)",
                xlround(cost - eng_cost, 2), xlround(cost - eng_cost, 2),
                where=f"{w} 与送审值差额"),
        })
    # ---- 工作量法段：**只标注，不套类别因子** ----
    # 表3（软件类别调整因子）挂在 2.1.2 功能点估算法 底下；2.1.1 的单价公式是
    # 「人工成本 × 风险系数 × 复用系数」，**没有类别因子**。给这一段乘一道
    # 标准没有的系数，和跨省复用模板时多乘一道「规模变更因子」是同类错误。
    # 所以软件类别照显（产品事实），类别因子留空并在页末写明不适用；
    # 复用系数照显 —— 那个是表1 注3 明文有的。
    eff_trial = 0.0
    if sw and effort_only and root is not None:
        tax_all = _taxonomy_systems(root)
        # **归集视角是建设对象，不是阶段。** 这一段原来逐（子系统 × 阶段）出行，
        # 于是同一张表上，功能点法段列的是「子系统建了什么」，工作量法段列的是
        # 「这个子系统在哪个阶段花了多少」—— 两种维度并排，读表的人要在中途换脑筋。
        # 更实际的害处：复用度判在**建设对象**上（表1 注3，2026-08-09 已接入引擎），
        # 按阶段出行就没有一行对得上一个可判复用的单元，试算这一列无从下手。
        _rt_map = {k: v["value"] for k, v in
                   (pack.data["effort_method"]["stage_rates"] or {}).items()}
        _wbs_t = _wbs_roles(root)
        s.group("■ 2.1.1 工作量估算法段　行=建设对象　类别因子**不适用**"
                "（表3 属 2.1.2）；复用系数为表1 注3 明文，**已接入引擎**，"
                "改「复用度」即改送审值")
        for sysname, blk in (sw.get("systems") or {}).items():
            if sysname not in effort_only or not blk.get("stages"):
                continue
            at = (tax_all.get(sysname) or {}).get("app_type") or "—"
            _pct = {k: ((_wbs_t.get(sysname) or {}).get(k) or {}).get("pct", 0.0)
                    for k in STAGES}
            # 该系统「一个人月」横跨五阶段的加权价 = Σ(阶段占比 × 阶段单价)。
            # 是各部分之和，不是编出来的平均值 —— 乙本 04 的阶段小计可复核它。
            _blend = sum(_pct.get(k, 0.0) * _rt_map.get(k, 0.0) for k in STAGES)
            for lf in (blk.get("leaves") or []):
                _eb = lf.get("effort_basis") or {}
                mm = _eb.get("man_months")
                if not mm:
                    continue
                stage = lf["name"]
                r = s._next
                c_mm = _col(s, "工作量(人月)")
                c_rt = _col(s, "人月费率(元)")
                c_amt = _col(s, "软件开发费(元)")
                _oi = (ef or {}).get("obj", {}).get((sysname, lf["name"])) or {}
                rate = round(_blend * 10000 * (ef or {}).get("risk", 1.0)
                             * _oi.get("factor", 1.0), 2)
                eng_cost = xlround(
                    xlround(sum(mm * _pct.get(k, 0.0) * _rt_map.get(k, 0.0)
                                for k in STAGES)
                            * (ef or {}).get("risk", 1.0)
                            * _oi.get("factor", 1.0), 4) * 10000, 2)
                eff_trial += eng_cost
                w = f"[02_二层试算] {sysname[:10]}·{lf['name'][:8]}"
                s.row({
                    "子系统": f"{sysname}　·　{lf['name']}",
                    "软件类别": at,
                    "未调整功能点": None,
                    "工作量(人月)": mm,
                    "人月费率(元)": rate,
                    # **在万元上留 4 位再折算回元** —— 与引擎同构。
                    # 引擎的 amount_wan 是万元级 4 位小数（源表口径），
                    # 直接在元上 ROUND(...,2) 会与它差出几十元，而两个数
                    # 看起来都对，只有差额列会露馅。
                    "软件开发费(元)": formula(
                        f"=ROUND({c_mm}{r}*{c_rt}{r}/10000,4)*10000",
                        xlround(xlround(mm * rate / 10000, 4) * 10000, 2), eng_cost,
                        where=f"{w} 软件开发费"),
                    "与送审值差(元)": formula(
                        f"=ROUND({c_amt}{r}-{eng_cost:.2f},2)",
                        xlround(xlround(mm * rate / 10000, 4) * 10000 - eng_cost, 2),
                        xlround(xlround(mm * rate / 10000, 4) * 10000 - eng_cost, 2),
                        where=f"{w} 与送审值差额"),
                })

    s.total("试算合计")
    s.note("　【工作量法段的类别因子】表3「软件类别调整因子」挂在 2.1.2 功能点"
           "估算法 底下；2.1.1 的单价公式为「人工成本 × 风险系数 × 复用系数」，"
           "**无类别因子**。故该段「软件类别」仅作标注（产品事实，见"
           " bom/taxonomy.yaml），「软件类别因子」列留空 —— "
           "给这一段乘一道标准没有的系数会让报价虚高且答不出出处。")
    s.note("　【工作量法段的复用系数】表1 注3 明文含复用系数，判断单位为**建设对象**"
           "（2026-08-09 由 WBS 活动下移），**已接入引擎** —— 改 03_复用度模块清单"
           "的「复用度」列即改送审值，不再是「只在本表看得到影响」。本次 13 个本体、"
           "9 个语料库、28 个数据集等全部取「低」= 1，待方案侧逐条判定。")
    s.note("　【工作量法段的人月费率】该列是**五阶段加权价** = Σ(阶段占比 × 阶段单价)"
           " × 风险系数 × 复用系数。一条建设对象横跨五个阶段，五个阶段单价不同"
           "（1.9/1.9/1.7/1.6/1.4 万元/人月），行按建设对象出就必须有一个能乘的价。"
           "它是**各部分之和**、可由乙本 04 的阶段小计复核，不是编出来的平均值 —— "
           "阶段占比来自 bom/effort-wbs.yaml 的活动 stage 字段。")
    s.note(f"　【甲本】01_软件开发费汇总·功能点法段（送审值）：¥{engine_total:,.2f}　|　"
           f"本表试算合计：¥{trial:,.2f}　|　差 ¥{trial - engine_total:,.2f}")
    s.note("　本表与引擎同构（同样逐子系统 ROUND），差应恒为 0。"
           "**差不是 0 就是公式与引擎分家了**，不要放宽，去查算式。")
    s.note(f"　试算用法：改 {param_sheet} 的黄格 → 本表绿格自动重算 → "
           "看「软件开发费」合计的变化。也可直接改本表的两个下拉："
           "「软件类别」与「复用度」—— 两列的因子都由下拉查表得出，不手填数字。")
    s.note("　【复用系数列】改这一列只是试算。落实要改 **bom/taxonomy.yaml** 该子系统的 "
           "`maturity`（existing 成熟沿用 / partial 部分改造 / new 全新定制）"
           "并写 `maturity_basis` —— 「这个模块是不是我们已有的成熟产品」是产品事实，"
           "换个商机也成立，所以在第一层，不在 deal.yaml。"
           "只有本商机的例外才写 deal.yaml 的 fp_method_settings.reuse.overrides。")
    s.note("　⚠ 复用系数往下调是**降价**。表3 注2 的默认是 1（复用度低），"
           "偏离默认的每一项引擎都要求写依据，写不出就不出表 —— "
           "没有出处的折扣在评审那里是「随意定价」，比不打折更被动。")
    s.note("　【改了怎么落实】两类格子归属不同的层，落实路径不同：")
    s.note(f"　　{param_sheet} 的灰格（人月折算系数、基准人月费率、复用系数）= **第二层**，"
           "标准原文，**不该改** —— 试算只为看敏感度。若确认抄错了，"
           "改 standard-packs/liuzhou-2020/pack.yaml 并同步 citation。")
    s.note(f"　　{param_sheet} 的黄格（生产率浮动、软件类别因子）= **本商机决策**，"
           "落实要改 deals/<id>/deal.yaml 的 fp_method_settings，"
           "且超过 1.0 的必须同时写 basis（表3 注1）。")
    s.note("　　本表 B 列的软件类别 = **第一层**，BOM 里子系统的属性。"
           "若认为某个子系统分类不对，落实要改 bom/taxonomy.yaml，不是改这张表。")
    s.finish()


def _assert_same_params(a, b) -> None:
    """两份参数表必须逐格相同。

    复制出来的第二份最危险的地方不是它存在，是它**看起来一样但不是** ——
    评审拿举证件核，我们拿试算件调，两边差一个系数没人会发现。
    所以复制归复制，当场核对：参数名与取值两列全等，否则不出表。
    """
    def grid(ws):
        cols = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
        k, v = cols["参数"], cols["取值"]
        return [(ws.cell(r, k).value, ws.cell(r, v).value)
                for r in range(4, ws.max_row + 1)
                if ws.cell(r, k).value is not None]
    ga, gb = grid(a.ws), grid(b.ws)
    if ga != gb:
        diff = [(x, y) for x, y in zip(ga, gb) if x != y]
        raise gs.GovSheetError(
            f"两份计价参数表内容不一致：{a.ws.title} vs {b.ws.title}\n"
            f"  不同处（前 5）：{diff[:5]}\n"
            f"  行数 {len(ga)} vs {len(gb)}\n"
            f"  一份是举证件、一份是试算面板，它们必须是同一组数 —— "
            f"不一致意味着评审核的和我们调的不是一回事。")


#: 表1 的五个阶段，顺序即表内行序。
_T1_STAGES = ["需求分析", "系统设计", "软件开发（编码）", "系统测试", "实施部署"]


def _wbs_roles(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """从 bom/effort-wbs.yaml 推出每个系统每个阶段的**活动构成与人员类型**。

    ## 人员类型为什么要从 WBS 推，而不是按阶段写死

    表1 有「人员类型」列，但注3 把单价挂在**阶段**上，与人员类型无关 ——
    所以这一列填什么不改一分钱。既然不改钱，就更该填对：把知识工程的
    「资料收集与来源整理」标成「软件开发工程师」，与编制说明里
    「L1/L2 交付物是语料/数据/模型，功能点法按定义测不到」的论证互相打脸，
    评审读到两种说法会挑对我们更不利的那个。

    ## 只推结构，不推数量

    返回的是**该阶段内部**的活动占比与角色占比（归一化到该阶段），
    不含人天。WBS 的活动人天与送审表的阶段人月口径不同
    （差 1.56×/1.87×），把 WBS 人天摆进送审件会与阶段人月当场打架。
    数量的账在丙本单列，见 `emit_effort_reconcile`。

    返回 {系统: {阶段: {"pct": 该阶段占系统总量比,
                        "roles": [(角色, 阶段内占比)…],
                        "acts":  [(活动, 阶段内占比)…]}}}
    """
    f = root / "bom" / "effort-wbs.yaml"
    if not f.exists():
        return {}
    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for sysname, tpl in (doc.get("templates") or {}).items():
        acts = tpl.get("activities") or []
        tot = sum(a["pct"] for a in acts)
        if abs(tot - 1.0) > 1e-6:
            raise gs.GovSheetError(
                f"effort-wbs.yaml「{sysname}」活动占比合计 {tot:.4f} ≠ 1 —— "
                f"占比不满 1 的话，按它折算出来的阶段构成就少了一块，"
                f"而少的那块不会有任何地方报错。")
        by: dict[str, dict[str, float]] = {}
        for a in acts:
            st = a.get("stage")
            if st is None:
                raise gs.GovSheetError(
                    f"effort-wbs.yaml「{sysname}·{a['name']}」缺 stage —— "
                    f"活动挂不到表1 的阶段上，人员类型就无从归集")
            parts = {st: 1.0} if isinstance(st, str) else dict(st)
            if abs(sum(parts.values()) - 1.0) > 1e-9:
                raise gs.GovSheetError(
                    f"effort-wbs.yaml「{sysname}·{a['name']}」stage 拆分合计 "
                    f"{sum(parts.values())} ≠ 1")
            for stage, w in parts.items():
                if stage not in _T1_STAGES:
                    raise gs.GovSheetError(
                        f"effort-wbs.yaml「{sysname}·{a['name']}」的 stage "
                        f"{stage!r} 不是表1 的阶段；可选：{_T1_STAGES}")
                d = by.setdefault(stage, {"pct": 0.0, "r": {}, "a": {}})
                d["pct"] += a["pct"] * w
                d["r"][a["role"]] = d["r"].get(a["role"], 0.0) + a["pct"] * w
                d["a"][a["name"]] = d["a"].get(a["name"], 0.0) + a["pct"] * w
        out[sysname] = {
            st: {"pct": d["pct"],
                 "roles": sorted(((k, v / d["pct"]) for k, v in d["r"].items()),
                                 key=lambda kv: -kv[1]),
                 "acts": sorted(((k, v / d["pct"]) for k, v in d["a"].items()),
                                key=lambda kv: -kv[1])}
            for st, d in by.items()}
    return out


def _role_cell(comp: dict[str, Any] | None) -> str:
    """人员类型单元格：单一角色直接写；多角色写主导角色 + 占比。"""
    if not comp:
        # WBS 里这个阶段没有对应活动。**不兜底成一个像样的角色** ——
        # 兜底出来的「实施工程师」看起来完全正常，而它背后没有任何活动。
        return "待补（WBS 无对应活动）"
    rs = comp["roles"]
    if len(rs) == 1:
        return rs[0][0]
    return " / ".join(f"{r} {v:.0%}" for r, v in rs)


def _act_role(doc: dict, sysname: str, act: str) -> str:
    for a in (doc["templates"][sysname]["activities"]):
        if a["name"] == act:
            return a["role"]
    return ""


def emit_effort_reconcile(wb, root: Path, sw: dict, effort_only: set[str]) -> None:
    """【丙本】05 工作量口径对账 —— 内部件。

    活动分解里的人天，和送审表里的阶段人月，不是一个数：

        知识工程  WBS 1,004 人天 = 46.16 人月   送审 71.95 人月   1.559×
        行业智能体 WBS 4,543 人天 = 208.87 人月  送审 390.67 人月  1.870×

    倍数只有两个值（L1 一个、L2 一个），一位不差 —— 说明送审表的人月不是
    估出来的工作量，是**金额 ÷ 表1 单价**倒算的。根子在于表1 编码阶段
    1.7 万元/人月 ≈ 782 元/人天，低于这类工作的实际单价；要用表1 的费率
    凑到目标金额，人月就得放大。

    这张表**不进送审件**：它把「我方人月是倒算的」摆在明面上。
    但它必须存在 —— 评审自己也能除（活动分解一旦提供人天就能除出来），
    我们得先知道自己站在哪儿。
    """
    W_PER_MM = 21.75
    rows = []
    import glob as _glob
    agg: dict[str, float] = {}
    for f in sorted(_glob.glob(str(root / "bom" / "items" / "*.yaml"))):
        for i in yaml.safe_load(Path(f).read_text(encoding="utf-8"))["items"]:
            if i.get("class") != "SOFTWARE_EFFORT":
                continue
            e = i.get("effort") or {}
            agg[i["path"]["system"]] = agg.get(i["path"]["system"], 0.0) + (
                e.get("person_days") or 0.0)
    for sysname, blk in sw["systems"].items():
        if sysname not in effort_only or not blk["stages"]:
            continue
        mm = sum(v["man_months"] for v in blk["stages"].values())
        amt = blk.get("obj_amount_wan",
                      sum(v["amount_wan"] for v in blk["stages"].values()))
        pd = agg.get(sysname, 0.0)
        rows.append((sysname, pd, pd / W_PER_MM, mm, amt))

    s = GovSheet(
        wb, "02_工作量口径对账",
        title="工作量口径对账（内部）—— 业务侧活动分解人天 vs 我方自下而上测算人月",
        subtitle="⚠ 内部件，不随送审件提交。本表是**交叉验证**：两个相互独立的来源"
                 "（业务侧按活动拆的人天 / 我方按可核查量纲自下而上估的人月）"
                 "落在同一个量级上，才说明这批人月站得住。",
        columns=[
            Col("系统", "text", width=14),
            Col("活动分解人天", "fp", width=12, sum=True),
            Col("折人月", "fp", width=10, sum=True),
            Col("送审阶段人月", "fp", width=12, sum=True),
            Col("倍数", "rate", width=8),
            Col("送审金额(万元)", "money", width=13, sum=True),
            Col("金额÷活动人月", "rate", width=13),
            Col("说明", "text", width=52),
        ])
    for sysname, pd, wm, mm, amt in rows:
        s.row({"系统": sysname, "活动分解人天": xlround(pd, 1),
               "折人月": xlround(wm, 2), "送审阶段人月": xlround(mm, 4),
               "倍数": xlround(mm / wm, 3) if wm else 0,
               "送审金额(万元)": xlround(amt, 4),
               "金额÷活动人月": xlround(amt / wm, 3) if wm else 0,
               # 没有业务侧活动分解人天的系统（本体工程），送审人月**不是倒算来的**，
               # 是自下而上从五要素条目累加的 —— 本表的「倍数」对它没有意义，
               # 硬算会除零。这里不填 0 蒙混，明写它不参与本对账。
               "说明": (f"隐含 {amt * 10000 / (wm * W_PER_MM):,.0f} 元/人天"
                        f"（送审金额 ÷ 业务侧活动人天，仅供对照，"
                        f"非计价口径）") if wm else
                       "业务侧无活动分解人天。本系统送审人月为自下而上测算"
                       "（量纲 × 单位工作量，见【乙本】05），非金额倒算，不参与本对账"})
    s.total()
    s.note("　【本表怎么读】「倍数」= 我方测算人月 ÷ 业务侧活动人天折算的人月。"
           "接近 1 说明两个独立来源互相印证；显著偏离要能说出原因，"
           "不能只当作噪音。")
    s.note("　【已解决：倒算指纹】上一版这一列是齐刷刷的 1.559×/1.870×（L1 两系统"
           "一个值、L2 两系统另一个值，小数点后三位一致）—— 那不是巧合，是因为"
           "人月本身由对外金额倒算而来（`人月 = 报价 ÷ 17,000`，132 条零例外），"
           "整齐正是倒算的指纹，评审拿人月列乘 17,000 就能复现。"
           "2026-08-09 已改为自下而上按「可核查量纲 × 单位工作量」逐条测算，"
           "倍数随之散开，本表才真正具备对账意义。")
    s.note("　【偏离的解释】知识工程 0.585×、行业垂直模型 0.904× —— 我方测算**低于**"
           "业务侧活动人天，方向对我方不利但可接受：业务侧的活动人天含内部管理与"
           "返工冗余，而表1 单价已含项目管理与培训（p.10 三.(一)2.1），不另计。"
           "数据工程 1.053×、行业智能体 1.026× 落在 ±5% 内，视为互相印证。")
    s.note("　【仍不外发】业务侧活动人天一旦随件提交，评审可自行计算本表所有比值。"
           "【乙本】04 只给阶段占比不给人天 —— 现在两个来源已能互相印证，"
           "这条不再是遮掩，而是「两套口径不必都摆到送审件上」。")
    s.note("　【本体工程无对照】该系统是 2026-08-09 新增，业务侧从未做过活动分解，"
           "故无人天可比。它的人月全部来自五要素计数（见【乙本】05），"
           "是本套表中唯一没有第二来源交叉验证的一段。")
    s.finish()


def emit_software_summary(wb, tax: dict, pack: StandardPack, *,
                          fp_systems: list[dict], sw: dict,
                          effort_only: set[str], expect_wan: float,
                          dnl: dict | None = None) -> None:
    """【甲本】01 软件开发费 —— 按子系统，两法统一到「工作量 → 金额」。

    ## 为什么两法能并到一张表

    柳州标准 p.13 的功能点法公式原文是
    「定制软件开发费用＝功能点数×软件开发生产率基准/人月折算系数×
    软件开发基准人月费率+直接非人力成本」——
    `功能点数 × 6.51 ÷ 174` 这一段的量纲就是**人月**，标准只是没给它命名。
    所以两法在柳州都收敛到「工作量（人月）× 人月费率」，收敛点是标准自带的，
    不是我们凑的。

    ## 为什么没有「单价」列

    功能点法的人月费率是 1.7 万元/人月（标准 ④ 明文）；工作量法是五个
    阶段单价 1.9/1.9/1.7/1.6/1.4。强设一个单价列，工作量法那几行只能填
    加权后的 1.6745 —— **那个数不在标准的任何一处**。评审问出处，
    答案是「按各阶段金额占比加权」，而那个占比本身缺依据。
    与其把最弱的一环放到送审册最显眼的位置，不如不设这一列：
    单价在各自的方法表里（乙本 01 / 04），本表只回答「多少工作量、多少钱」。

    ## 分组与 00_总表 一一对应

    组行即总表「软件开发费」下的 8 个科目，组内是子系统。
    总表任何一行都能在本表展开，行序一致，可以对着看。
    """
    bucket = _make_bucket(tax)
    METHOD = {"fp": "2.1.2 功能点估算法", "eff": "2.1.1 工作量估算法"}
    CLAUSE = {"fp": "三.(一)2.1.2 表3", "eff": "三.(一)2.1.1 表1"}
    rows: dict[tuple, list] = {}
    for x in (fp_systems or []):
        rows.setdefault(bucket(x["system"]), []).append(
            {"sys": x["system"], "m": "fp", "mm": x["effort_man_months"],
             "yuan": x["cost"],
             "note": (
                 f"{x['ufp']:,} UFP × 类别因子 {x['app_type_factor']} × "
                 f"复用系数 {x['reuse']} → {x['fp']:,.2f} 功能点"
                 if x.get("reuse") is not None else
                 # 混档：不能填一个不存在的「平均复用系数」—— 那个数不在标准的
                 # 任何一处。写清按哪几档拆的，逐组明细在【乙本】01。
                 f"{x['ufp']:,} UFP × 类别因子 {x['app_type_factor']}，"
                 f"复用度按功能模块分 {len(x['groups'])} 档（"
                 + " + ".join(f"{g['reuse_level']} {g['ufp']:,}UFP×{g['reuse_factor']}"
                              for g in x["groups"])
                 + f"）→ {x['fp']:,.2f} 功能点")})
    if dnl and dnl["value"] > 0 and fp_systems:
        # 项目级加数，不属于任何子系统 —— 单出一行，挂在第一个功能点法科目下，
        # 备注写明它是标准公式的加数项，不是某个子系统的开发费。
        rows.setdefault(bucket(fp_systems[0]["system"]), []).append(
            {"sys": "直接非人力成本（另计）", "m": "fp", "mm": 0.0,
             "yuan": dnl["value"],
             "note": "标准公式加数项（p.14 ⑤），非某子系统开发费；"
                     + " ".join(str(dnl["basis"]).split())[:90]})
    for sysname, blk in sw["systems"].items():
        if sysname not in effort_only or not blk["stages"]:
            continue
        rows.setdefault(bucket(sysname), []).append(
            {"sys": sysname, "m": "eff",
             "mm": sum(v["man_months"] for v in blk["stages"].values()),
             "yuan": blk.get("obj_amount_wan", sum(
                 v["amount_wan"] for v in blk["stages"].values())) * 10000,
             "note": "五阶段（需求分析/系统设计/编码/系统测试/实施部署）"
                     "按表1 注3 阶段单价计列"})
    # 已声明属本次建设范围、但尚无内容的系统 —— 列而不计，与 00_总表 一致
    for line, ls in (tax.get("product_lines") or {}).items():
        for sysname, sp in (ls.get("systems") or {}).items():
            sp = sp if isinstance(sp, dict) else {}
            if sp.get("status") == "待补内容":
                rows.setdefault(bucket(sysname), []).append(
                    {"sys": sysname, "m": "eff", "mm": 0.0, "yuan": 0.0,
                     "note": "**待补建设内容与工作量，本次未计入金额**；"
                             "已确认属本次建设范围 —— 列而不计，以证明其非遗漏"})

    grand = sum(r["yuan"] for g in rows.values() for r in g)
    s = GovSheet(
        wb, "01_软件开发费汇总",
        title="软件开发费　按子系统",
        subtitle="两种计价方法均收敛到「工作量（人月）× 人月费率」——"
                 "功能点法公式 p.13「功能点数×生产率基准/人月折算系数×基准人月费率」"
                 "的中间量即人月。计价方法按**层次**择用，见「计价方法」列；"
                 "各自的推导见【乙本·计量举证册】。",
        columns=[
            Col("费用名称", "text", width=34),
            # ↓ 这三列默认折叠：主线是「哪个子系统、多少工作量、多少钱」，
            #   层次/方法/推导是要追的时候才展开的一层。挨在一起才能成组。
            Col("层次", "text", width=10, cell_role="locked"),
            Col("计价方法", "text", width=18, cell_role="locked"),
            Col("推导要点", "text", width=54),
            Col("工作量(人月)", "fp", width=12, sum=True, role="effort"),
            Col("金额(元)", "money", width=14, sum=True, role="amount"),
            Col("占软件开发费", "pct", width=12),
            Col("备注", "text", width=26),
        ],
        clause_required=True, clause_name="备注", rollup=True)
    for key in sorted(rows):
        grp = sorted(rows[key], key=lambda r: -r["yuan"])
        s.group(f"{key[1]}　（{sum(r['yuan'] for r in grp):,.2f} 元）",
                clause="三.(一)2.1")
        for r in grp:
            s.row({"费用名称": r["sys"],
                   "层次": bucket.layer_of(r["sys"]),
                   "计价方法": METHOD[r["m"]],
                   "工作量(人月)": xlround(r["mm"], 2),
                   "金额(元)": xlround(r["yuan"], 2),
                   "占软件开发费": round(r["yuan"] / grand, 6) if grand else 0,
                   "推导要点": r["note"]},
                  clause=CLAUSE[r["m"]])
    s.note("　【为什么不设单价列】功能点法人月费率 1.7 万元/人月（p.14 ④）；"
           "工作量法为五个阶段单价 1.9/1.9/1.7/1.6/1.4（p.11 表1 注3）。"
           "两者的费率结构不同，合成一个单价会得到一个标准里没有的数。"
           "单价见【乙本·计量举证册】01_功能点测算表 与 04_工作量测算表。")
    s.note("　【计价方法按层次择用】应用层与 L0 层交付可运行的业务功能，"
           "有用户可识别的逻辑文件与基本过程，用功能点法；L1/L2 层交付语料、"
           "数据资产、模型与智能体，不产生 ILF/EIF/EI/EO/EQ，功能点法按定义"
           "测不到，用工作量法。标准 p.10「可按照工作量估算法或功能点估算法"
           "进行估算」，未要求全项目用同一种。")
    # 这句从前是**写死的**「计列为 0」，页码还写成 p.15（实为 p.14）——
    # 一旦真的计列，它就与同表那一行直接打架。改成跟着实际值走。
    if dnl and dnl["value"] > 0:
        s.note(f"　【直接非人力成本】计列 ¥{dnl['value']:,.2f}（{dnl['clause']}）。"
               f"标准明文「一般情况不进行计列（通常为 0），特殊情况需要计列时应"
               f"明确说明原因及测算依据」，依据见上行备注与【乙本】01。")
    else:
        s.note("　【直接非人力成本】按 p.14 ⑤「一般情况不进行计列（通常为 0）」，"
               "本项目计列为 0。")
    s.note("　【折叠列】「层次 / 计价方法 / 推导要点」三列默认折叠，"
           "点「费用名称」列上方的 [+] 展开。「备注」列填的是该行的标准条款出处 —— "
           "条款号本身即可分辨计价方法（2.1.1 工作量估算法 / 2.1.2 功能点估算法）。")
    s.total(expect={"金额(元)": xlround(expect_wan * 10000, 2)}, tolerance=1.0)
    s.group_columns("层次", "推导要点", collapsed=True)
    s.finish()


_CN_NUM = "一二三四五六七八九十"


def _cn(n: int) -> str:
    """1→一 … 20→二十。分组序号用，甲方模板与表7 例表都是这个形。"""
    if n <= 10:
        return _CN_NUM[n - 1]
    return "十" + (_CN_NUM[n - 11] if n < 20 else "") if n < 20 else "二十"


#: 物料字典里**只准读这几列**。供应商、渠道单价、★canonical单价 是内部数据，
#: 读都不读 —— 脱敏靠「没取过」比靠「取了再删」可靠。
_DICT_SAFE = ("核心参数", "品牌", "参考对比厂家")


def _material_dict(path: Path) -> dict[str, dict[str, str]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    if "88_物料主数据字典" not in wb.sheetnames:
        return {}
    ws = wb["88_物料主数据字典"]
    h = {ws.cell(2, c).value: c for c in range(1, ws.max_column + 1)}
    out = {}
    for r in range(3, ws.max_row + 1):
        code = ws.cell(r, h["物料编码"]).value
        if not code:
            continue
        out[str(code)] = {k: str(ws.cell(r, h[k]).value or "").strip()
                          for k in _DICT_SAFE if k in h}
    return out


def _ref_models(model: str, d: dict[str, str]) -> tuple[str, int]:
    """参考品牌型号栏 + 个数（本机型算 1 个）。表7 注3「一般不少于3个」。"""
    ref = d.get("参考对比厂家", "")
    parts = [x for x in re.split(r"[、,，/;；\s]+", ref) if x and x not in ("无", "-")]
    txt = model or ""
    if d.get("品牌"):
        txt = f"{d['品牌']} {txt}".strip()
    if parts:
        txt += "；参考厂家：" + "、".join(parts)
    return txt, len(parts) + (1 if model else 0)


def emit_hardware_summary(wb, hw: dict, pack: StandardPack,
                          expect_yuan: float,
                          root: Path | None = None) -> dict[str, Any]:
    """【甲本】02 硬件设备购置费汇总 —— 按场景分组、逐设备，园所作列。

    ## 形式

    列序抄甲方模板（序号 | 场景及设备 | … | 数量 | 单位 | 单价 | 金额 | 备注），
    但**补回表7 例表的「参考品牌型号」与「性能参数」**：甲方那张是形式参考，
    内容要对齐标准，而表7 注3 明文「参考品牌型号一般不少于3个」。
    甲方模板里没有这两列，照抄就等于把标准要求抄掉了。

    分组用**子场景**：甲方模板第一组写的是「安全接送场景」，而源表里
    「安全接送场景」正是子场景（其一级场景是「智慧管理」）。

    ## 数量后面按园所展开

    同一台设备在 6 个园所/批次各配多少，摆成 6 列。这是甲方要的那一层：
    00_总表 给到园所总额，甲附给到逐行明细，中间「这台设备在哪几个园配了几台」
    两头都看不出来。横向合计必须等于「数量」列 —— 逐行校验，对不上就不出表。
    """
    import collections as _c
    gardens = sorted({r["园所"] for r in hw["priced"]})
    dev: dict[tuple, dict] = {}
    for r in hw["priced"]:
        scene = str(r["子场景"] or "").strip()
        if scene in ("", "✓", "基准补录", "None"):
            scene = "未归类（源表场景名待补）"
        k = (scene, r["name"], str(r["model"] or ""))
        # 型号与性能参数直接取记录自带的 —— 它们来自第一层 materials.yaml，
        # 不再另读物料字典。两处读同一份数据，迟早会有一处忘了跟着改。
        d = dev.setdefault(k, {"qty": _c.Counter(), "unit": r["unit"],
                               "price": r["unit_price"], "code": str(r["code"]),
                               "total": 0.0, "自研": r["自研"],
                               "model": r.get("model") or "",
                               "spec": r.get("spec") or "",
                               "ref_count": r.get("ref_count", 0)})
        if abs(d["price"] - r["unit_price"]) > 1e-9:
            raise gs.GovSheetError(
                f"设备「{r['name']}」在不同园所单价不一致："
                f"{d['price']} vs {r['unit_price']} —— 一行一单价的汇总表放不下，"
                f"须先在源表统一或拆成两行（一码多价）")
        d["qty"][r["园所"]] += r["qty"]
        d["total"] += r["total"]

    scenes = _c.defaultdict(list)
    for k, v in dev.items():
        scenes[k[0]].append((k[1], k[2], v))
    order = sorted(scenes, key=lambda x: (x.startswith("未归类"),
                                          -sum(v["total"] for _, _, v in scenes[x])))

    s = GovSheet(
        wb, "02_硬件设备购置费汇总",
        title="硬件设备购置费　按场景 · 逐设备（园所配置分解）",
        subtitle=f"编制依据：{pack.data['doc_no']} 三.(一)2.5 表7　"
                 f"列序参照甲方模板，「参考品牌型号」「性能参数」两列按表7 例表补列"
                 f"（注3：参考品牌型号一般不少于3个）；"
                 f"逐行原始清单见【甲附·硬件设备清单】",
        columns=[
            Col("序号", "text", width=7),
            Col("场景及设备", "text", width=32),
            Col("参考品牌型号", "text", width=34),
            Col("性能参数", "text", width=40),
            Col("数量", "int", width=8, sum=True),
            *[Col(g.split("_", 1)[-1], "int", width=11, sum=True) for g in gardens],
            Col("单位", "text", width=7),
            Col("单价(万元)", "rate", width=11, cell_role="locked"),
            Col("金额(万元)", "money", width=12, sum=True, role="amount_wan"),
            Col("备注", "text", width=34),
        ],
        clause_required=True, seq=False, rollup=True)

    short = {g: g.split("_", 1)[-1] for g in gardens}
    total_wan = 0.0
    short_ref = 0
    short_ref_yuan = 0.0
    self_made = 0
    self_made_yuan = 0.0
    for gi, scene in enumerate(order, 1):
        items = sorted(scenes[scene], key=lambda x: -x[2]["total"])
        s.group(f"{scene}",
                {"序号": _cn(gi),
                 "金额(万元)": xlround(sum(v["total"] for _, _, v in items) / 10000, 4)},
                clause="三.(一)2.5 表7")
        for i, (name, model, v) in enumerate(items, 1):
            ref, nref = (v["model"] or model), v["ref_count"]
            qty = sum(v["qty"].values())
            note = []
            # **自研产品不打「待补参考品牌型号」。** 表7 注3 要的是市面比选机型，
            # 自研件没有可比机型（字典里这类的「参考对比厂家」写的就是
            # 「自研产品，无」）。硬挂一个它满足不了也不需要满足的要求，
            # 评审看到会当成缺陷 —— 而它真正的举证责任是**成本构成与定价依据**，
            # 反而更重。两条要求不能同时挂，否则等于把举证方向指错。
            if v["自研"] == "自研":
                note.append("自研产品，须提供成本构成与定价依据（表7 注3 的"
                            "参考品牌型号对自研件不适用）")
                self_made += 1
                self_made_yuan += v["total"]
            elif nref < 3:
                note.append(f"**待补**参考品牌型号（现 {nref} 个，注3 要求≥3）")
                short_ref += 1
                short_ref_yuan += v["total"]
            row = {"序号": str(i), "场景及设备": name,
                   "参考品牌型号": ref or "**待补**",
                   "性能参数": (v["spec"] or "**待补**")[:200],
                   "数量": qty, "单位": v["unit"],
                   "单价(万元)": xlround(v["price"] / 10000, 6),
                   "金额(万元)": xlround(v["total"] / 10000, 4),
                   "备注": "；".join(note)}
            row.update({short[g]: v["qty"].get(g, 0) or None for g in gardens})
            # 横向对账：园所列之和必须等于「数量」——不等就是分解掉了行
            if sum(v["qty"].values()) != qty:
                raise gs.GovSheetError(f"{name} 园所分解之和 ≠ 数量")
            s.row(row, clause="三.(一)2.5 表7")
            total_wan += v["total"] / 10000

    s.note(f"　【园所列】数量列 = 右侧 {len(gardens)} 个园所/批次之和，逐行校验。"
           f"「200所基础园配置」为 200 所园的批量配置，其数量口径与单个园所不同。")
    odd = sum(v["total"] for k, v in dev.items() if k[0].startswith("未归类"))
    if odd:
        s.note(f"　【未归类】{odd:,.2f} 元（占 {odd / (total_wan * 10000):.2%}）的设备，"
               f"源表「一级/子场景」列填的是「✓」或「基准补录」而非场景名 —— "
               f"那是业务侧的补录标记。**未并入其他场景**：并进去这笔钱就消失在"
               f"别人的合计里，漏掉与归错在表上必须分得开。须业务侧补场景归属。")
    _out = len(dev) - self_made
    s.note(f"　【参考品牌型号】表7 注3「参考品牌型号一般不少于3个」。"
           f"本表 {len(dev)} 项设备中外购 {_out} 项，其中 **{short_ref} 项不满足**"
           f"（{short_ref_yuan:,.2f} 元，占 {short_ref_yuan / (total_wan * 10000):.2%}），"
           f"已在「备注」列逐条标注。须业务侧补齐比选机型及价格依据。")
    if self_made:
        s.note(f"　【自研产品】另有 **{self_made} 项自研产品**"
               f"（{self_made_yuan:,.2f} 元，占 {self_made_yuan / (total_wan * 10000):.2%}），"
               f"**不适用表7 注3 的参考品牌型号**（无市面比选机型），"
               f"改按成本构成与定价依据举证 —— 举证责任不因此减轻，只是形式不同。"
               f"须业务侧提供各项的成本构成（物料/加工/研发摊销）与定价方法。")
    # 条数**从 hw 取**，不写死。写死的数字第三次咬人了：
    # 施工地点声明的 7.17%、这里的 114 项 —— 都在计费额/配置变动后成了假陈述。
    s.note(f"　【待核价设备】另有 {len(hw.get('pending') or [])} 项设备数量非零"
           f"但单价为 0，已在【甲附】「待核价设备（列而不计）」单列，"
           f"**未计入本表金额**。")
    _fee = _service_fees(root) if root else []
    if _fee:
        s.note(f"　【运营服务费】另有 {len(_fee)} 项按年/按周期收取的服务费"
               f"（{'、'.join(x['name'] for x in _fee)}）已从设备目录拆出，"
               f"**不计入本表金额**。依据 三.(三)：运维为独立预算科目，"
               f"不列入建设期预算。列此说明是为证明它们不是被遗漏 —— "
               f"源表原将其计在设备清单内。")
    s.total(expect={"金额(万元)": xlround(expect_yuan / 10000, 4)}, tolerance=0.001)
    # 园所分解默认折叠：本表的主线是「场景 × 设备 × 金额」，
    # 「这台设备在哪个园配了几台」是需要时才展开的一层。
    # 折叠后从「数量」直接接到「单位/单价/金额」，与甲方模板的列序一致。
    s.group_columns(short[gardens[0]], short[gardens[-1]], collapsed=True)
    s.finish()
    return {"devices": len(dev), "scenes": len(scenes),
            "short_ref": short_ref, "short_ref_yuan": short_ref_yuan,
            "self_made": self_made, "self_made_yuan": self_made_yuan}


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "—"


def _sha_fp_settings(p: Path) -> str:
    """只对 `fp_method_settings` 取指纹，不是整个 deal.yaml。

    冲突检测要防的是「同步把别人的改动覆盖掉」，所以指纹的范围必须
    **正好等于同步会写的范围**。对整个文件取指纹的话，改个项目名、
    提个版本号都会把同步堵住 —— 而那些字段同步根本不碰。
    """
    import hashlib
    if not p.exists():
        return "—"
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    blob = yaml.safe_dump(d.get("fp_method_settings") or {},
                          allow_unicode=True, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def stamp_baseline(wb, info: dict[str, str]) -> None:
    """给工作簿盖一张隐藏的 `_基线` 表：这一册是从哪一版输入生成的。

    同步（`quote_sync`）要靠它做冲突检测：人在丙/丙附里改的时候，
    别人可能同时改了 deal.yaml 或 device-config.yaml。没有基线的话，
    同步只能「后写覆盖先写」，而覆盖掉的那份不会有任何地方留下痕迹。

    盖在**每一册**上，不只丙/丙附 —— 拿到一份送审件想知道它出自哪版输入时，
    答案得在文件里，而不是靠翻 git。
    """
    ws = wb.create_sheet("_基线")
    ws.append(["键", "值"])
    for k, v in info.items():
        ws.append([k, v])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 60
    ws.sheet_state = "hidden"


def read_device_config(root: Path, deal_dir: Path) -> dict[str, Any]:
    """读第二层硬件配置，**返回与 `read_hardware` 完全相同的形状**。

    换源不换接口：下游二十来处用 `hw["priced"]` / `hw["pending"]` 的代码
    一行不用改。接口一变就得同时改二十处，那种改法漏一处不会报错，
    只会让某张表少一段。

    与 `read_hardware` 的差别只在**数据从哪来**：

        read_hardware        源工作簿（含成本/渠道/毛利列，必须脱敏）
        read_device_config   deals/<id>/device-config.yaml + bom/devices/

    后者的敏感数据在建目录时就没读进来 —— 脱敏靠「没取过」比靠「取了再删」可靠。
    """
    cfg_p = deal_dir / "device-config.yaml"
    cfg = yaml.safe_load(cfg_p.read_text(encoding="utf-8"))
    dev = root / "bom" / "devices"
    mats = {m["code"]: m for m in yaml.safe_load(
        (dev / "materials.yaml").read_text(encoding="utf-8"))["materials"]}
    l1_of = {s["scene"]: s["l1"] for s in yaml.safe_load(
        (dev / "taxonomy.yaml").read_text(encoding="utf-8"))["scenes"]}

    _deal_prices = cfg.get("prices") or {}
    priced: list[dict] = []
    pending: list[dict] = []
    sheets: list[str] = []
    for t in cfg["targets"]:
        sheets.append(t["id"])
        for it in t["items"]:
            m = mats.get(it["material"])
            if m is None:
                raise gs.GovSheetError(
                    f"{cfg_p.name} 引用了目录里没有的物料 {it['material']!r}"
                    f"（{t['id']} · {it['scene']}）。\n"
                    f"  第二层只能选第一层有的东西 —— 允许它过去的话，"
                    f"这条会带不出型号和单价，金额静默变 0。")
            ref = m["model"]
            if m.get("brand"):
                ref = f"{m['brand']} {ref}".strip()
            vendors = m.get("ref_vendors") or []
            if vendors:
                ref += "；参考厂家：" + "、".join(vendors)
            # 表7 注3「参考品牌型号一般不少于3个」：本机型算 1 个，加参考厂家数。
            # 算好了带走，不要让下游去反解字符串 —— 反解出来的数没人能核。
            # 优先用 materials 里人工维护的 ref_count（来自飞书「商务·参考机型数」）。
            # 回落到「本机型 + 厂家数」只在没有该字段时用 —— 那个算法会把
            # 「厂家1：型号1：价格1｜…」这类填写模板数成 9 个厂家。
            ref_count = m.get("ref_count")
            if ref_count is None:
                ref_count = (1 if m["model"]
                             and not m["model"].startswith("**") else 0) \
                    + len(vendors)
            # **本商机报价优先于产品级参考价。**
            # 同一台设备在不同商机可以报不同价 —— BOM 里的 price_yuan 是参考，
            # 填在丙附「00_本商机报价」页的才是本商机的对外报价。
            # 报价按**物料**存（cfg["prices"]），不按行 —— 357 行只有 89 个物料，
            # 按行存等于允许同一物料出现几个价，而不一致时表上看不出来。
            # 没填才落回参考价；两处都没有 → 待核价（列而不计）。
            price = _deal_prices.get(it["material"])
            price = (m["price_yuan"] or 0) if price in (None, "") else float(price)
            qty = it["qty"]
            rec = {
                "园所": t["id"], "kind": t.get("kind", "garden"),
                "场景": l1_of.get(it["scene"], "**未归类**"),
                "子场景": it["scene"],
                "name": m["name"], "model": ref, "code": m["code"],
                "spec": m.get("spec", ""), "自研": m.get("self_made", ""),
                "ref_count": ref_count,
                "unit": m["unit"], "qty": qty, "unit_price": price,
                "price_from": ("本商机"
                               if _deal_prices.get(it["material"]) not in (None, "")
                               else "产品级参考价"),
                "ref_price": m["price_yuan"] or 0,
                "total": xlround(price * qty, 2),
                "note": it.get("note", ""),
            }
            # 数量 > 0 却没有单价 —— 不是「免费」，是「还没定价」。
            # 计 ¥0 会让它在合计里消失且看不出来。
            (pending if (price == 0 and qty) else priced).append(rec)
    return {"priced": priced, "pending": pending, "sheets": sheets,
            "config": cfg}


def _service_fees(root: Path) -> list[dict]:
    """运营服务费清单（仅供甲本 02 出说明用，不参与任何金额）。"""
    f = root / "bom" / "devices" / "service-fees.yaml"
    if not f.exists():
        return []
    return (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("fees") or []


def emit_hardware_detail(wb, hw: dict, pack: StandardPack,
                         root: Path | None = None,
                         deal: dict | None = None) -> tuple[float, int]:
    """【甲附】硬件设备清单 —— 由引擎从第二层配置生成，逐台、静态。

    ## 与直通件的差别

    从前甲附是甲方 Z03 的脱敏另存。改成生成之后：

      · 补上了「性能参数」—— 表7 例表有这一列，直通件一直没有
      · 「参考品牌型号」带上参考厂家（表7 注3 要求不少于3个）
      · **不再需要脱敏** —— 成本/渠道/毛利/供应商在建目录时就没读进来，
        而不是读进来再删。脱敏最危险的失败是「以为删了」。

    ## 待核价的处理

    数量非零、单价为 0 的设备**不进各园所表的金额**，单列一页披露。
    列出是为证明它不是被漏掉的；不计金额是因为无价可计。
    """
    cfg = hw.get("config") or {}
    s0 = GovSheet(
        wb, "00_编制说明",
        title="硬件设备清单　编制说明",
        subtitle="本册由造价引擎按第二层硬件配置生成，非甲方源表转录。",
        columns=[Col("项", "text", width=20), Col("说明", "text", width=96)],
        seq=False)
    for k, v in [
        ("表式依据", f"{pack.data['doc_no']} 三.(一)2.5 表7「硬件设备购置预算支出表」。"
                     f"列含参考品牌型号、性能参数（表7 例表设有此二列）。"),
        ("数据来源", "设备目录（场景树 / 物料主数据 / 场景×设备）与本商机的设备配置，"
                     "均为本方受控数据；型号、性能参数、单位、单价取自物料主数据，"
                     "各园所配置数量取自本商机配置。"),
        ("金额口径", "金额 = 数量 × 单价，逐条计算后累加，非表内公式求得。"),
        ("待核价设备", "数量非零、单价为 0 的设备**不计入各园所表金额**，"
                       "单列「待核价设备（列而不计）」一页。列出是为证明其非遗漏，"
                       "不计金额是因无价可计。"),
        ("参考品牌型号", "表7 注3「参考品牌型号一般不少于3个」。未达 3 个的已在"
                         "「备注（用途）」列逐条标注，须补齐比选机型及价格依据。"),
        ("与汇总表的关系", "本册为逐台清单；按场景/园所的汇总见【甲本】"
                           "02_硬件设备购置费汇总，两者金额一致。"),
    ]:
        s0.row({"项": k, "说明": v})
    s0.note("　【格式说明】本册此前为甲方源表的转录件。改为引擎生成，"
            "系因表7 要求的「性能参数」在源表中缺失，且转录路径需对源表逐列脱敏；"
            "由受控目录生成可避免该环节。设备名称、型号、数量与单价均与此前一致。")
    s0.finish()

    total = 0.0
    for sn in hw["sheets"]:
        rows = [r for r in hw["priced"] if r["园所"] == sn]
        pend = [r for r in hw["pending"] if r["园所"] == sn]
        if not rows and not pend:
            continue
        kind = next((r["kind"] for r in rows + pend), "garden")
        s = GovSheet(
            wb, sn,
            title=f"{sn}　设备清单",
            subtitle=(f"编制依据：{pack.data['doc_no']} 三.(一)2.5 表7"
                      + ("　本页为批次：数量是全批总量，不是单园量。"
                         if kind == "batch" else "")
                      + "　单价为 0 的设备未列于本页，见「待核价设备（列而不计）」"),
            columns=[
                Col("一级场景", "text", width=14, cell_role="locked"),
                Col("子场景", "text", width=20, cell_role="locked"),
                Col("设备名称", "text", width=30),
                Col("参考品牌型号", "text", width=32),
                Col("性能参数", "text", width=40),
                Col("物料编码", "text", width=15),
                Col("是否自研", "text", width=9, cell_role="locked"),
                Col("单位", "text", width=7, cell_role="locked"),
                Col("数量", "int", width=9, sum=True),
                Col("单价(元)", "money", width=12, cell_role="locked"),
                Col("金额(元)", "money", width=14, sum=True, role="amount"),
                Col("备注（用途）", "text", width=30),
            ],
            clause_required=True)
        sub = 0.0
        cur = None
        for r in sorted(rows, key=lambda x: (x["场景"], x["子场景"], -x["total"])):
            if r["子场景"] != cur:
                cur = r["子场景"]
                s.group(f"{r['场景']}　·　{cur}", clause="三.(一)2.5 表7")
            note = [r["note"]] if r["note"] else []
            if r["自研"] == "自研":
                note.append("自研产品，须提供成本构成与定价依据")
            s.row({"一级场景": r["场景"], "子场景": r["子场景"],
                   "设备名称": r["name"],
                   "参考品牌型号": gs.raw(str(r["model"] or "**待补**")),
                   "性能参数": (r["spec"] or "**待补**")[:220],
                   "物料编码": gs.raw(str(r["code"] or "")),
                   "是否自研": r["自研"] or "**待补**",
                   "单位": r["unit"], "数量": r["qty"],
                   "单价(元)": r["unit_price"], "金额(元)": r["total"],
                   "备注（用途）": "；".join(note)},
                  clause="三.(一)2.5 表7")
            sub += r["total"]
        if pend:
            s.note(f"　本页另有 {len(pend)} 项设备数量非零但单价为 0，"
                   f"**未计入本页金额**，见「待核价设备（列而不计）」。")
        s.total(expect={"金额(元)": xlround(sub, 2),
                        "数量": sum(r["qty"] for r in rows)})
        s.finish()
        total += sub

    p = GovSheet(
        wb, "待核价设备（列而不计）",
        title="待核价设备清单 —— 已列入建设内容，未计入本次预算金额",
        subtitle=("本表条目在前述各园所表中数量非零但单价为 0，故**列出但不计金额**。"
                  "列出是为了证明它们不是被漏掉的；不计金额是因为无价可计。"
                  "定价后须并入相应园所表，并同步调整系统集成费与预备费的计费基数。"),
        columns=[
            Col("园所/批次", "text", width=18),
            Col("一级场景", "text", width=13),
            Col("子场景", "text", width=18),
            Col("分项名称", "text", width=28),
            Col("参考品牌型号", "text", width=26),
            Col("物料编码", "text", width=15),
            Col("单位", "text", width=7),
            Col("数量", "int", width=9, sum=True),
            Col("是否自研", "text", width=9),
            Col("状态", "text", width=10),
            Col("处置要求", "text", width=40),
        ],
        clause_required=True)
    for r in sorted(hw["pending"], key=lambda x: (x["园所"], x["子场景"], x["name"])):
        p.row({"园所/批次": r["园所"], "一级场景": r["场景"], "子场景": r["子场景"],
               "分项名称": r["name"],
               "参考品牌型号": gs.raw(str(r["model"] or "**待补**")),
               "物料编码": gs.raw(str(r["code"] or "")),
               "单位": r["unit"], "数量": r["qty"], "是否自研": r["自研"],
               "状态": gs.raw("待核价"),
               "处置要求": ("须提供参考品牌型号不少于 3 个及价格依据"
                            if r["自研"] != "自研"
                            else "自研产品，须提供成本构成与定价依据")},
              clause="三.(一)2.5 表7 注3")
    p.note(f"共 {len(hw['pending'])} 条待核价，**未计入**硬件设备购置费合计 "
           f"{total:,.2f} 元。")
    p.total(expect={"数量": sum(r["qty"] for r in hw["pending"])})
    p.finish()

    # ---- 运营服务费：与待核价同一形式的**披露页**，不进任何合计 ----
    # 放在甲附而不是单出一份文档：评审拿源清单对照时会发现设备条目少了，
    # 找去向的第一站就是这本。与「待核价设备」并列，两页回答的是同一类问题
    # ——「这些东西哪去了」，只是原因不同（一个无价可计，一个不属本预算科目）。
    _fees = _service_fees(root) if root else []
    if _fees:
        _fq = _service_fee_qty(root)
        _fp = _service_fee_price(root, deal)
        f = GovSheet(
            wb, "运营服务费（不计入建设期预算）",
            title="运营服务费清单 —— 按年/按周期收取，不列入建设期预算",
            subtitle=("柳州标准 三.(一)1 列举的预算科目**不含运维**；运维为 三.(三)，"
                      "独立预算科目、独立评审。本表条目在建设单位提供的源清单中"
                      "**原列在设备清单内**，本套表将其从硬件设备购置费拆出。"
                      "列出的用途有二：证明它们不是被遗漏；供建设单位编制运维预算时取用。"
                      "**工程总投资不含本表任何金额。**"),
            columns=[
                Col("服务名称", "text", width=28),
                Col("物料编码", "text", width=17),
                Col("计费周期", "text", width=10),
                Col("计费单位", "text", width=12),
                Col("数量", "int", width=8, sum=True),
                Col("单价(元)", "rate", width=11, cell_role="locked"),
                Col("年费(元)", "money", width=13, sum=True),
                Col("触发关系", "text", width=26),
                Col("判定依据 / 待办", "text", width=56),
            ],
            clause_required=True)
        KIND = {"per_device": "每台触发设备 1 份", "per_garden": "每园 1 份",
                "unknown": "**触发口径未定**"}
        _yr = 0.0
        for x in _fees:
            tr = x.get("trigger") or {}
            q = _fq.get(x["code"], 0)
            pr = _fp.get(x["code"])
            amt = (pr * q) if pr else None
            if amt:
                _yr += amt
            trg = KIND.get(tr.get("kind"), "**未定**")
            if tr.get("kind") == "per_device":
                trg += f"（{tr.get('material_name', '')}）"
            elif tr.get("kind") == "per_garden":
                trg += f"（{tr.get('scene', '')}）"
            f.row({"服务名称": x["name"],
                   "物料编码": gs.raw(x["code"]),
                   "计费周期": x.get("period", "按年"),
                   "计费单位": x.get("unit", ""),
                   "数量": q,
                   "单价(元)": pr,
                   "年费(元)": amt,
                   "触发关系": gs.raw(trg),
                   "判定依据 / 待办": (x.get("basis") or "")[:900]},
                  clause="三.(三)")
        _nop = [x["name"] for x in _fees if not _fp.get(x["code"])]
        f.note(f"　【合计】可估算部分年费 {_yr:,.2f} 元（{_yr / 10000:.2f} 万元/年），"
               f"按 3 年运营期计 {_yr * 3:,.2f} 元（{_yr * 3 / 10000:.2f} 万元）。")
        if _nop:
            f.note(f"　【未计价】{len(_nop)} 项无单价（{'、'.join(_nop)}）"
                   f"—— **不是不发生，是价格与计量口径均未确定**，"
                   f"未计入上述合计。")
        f.note("　【与硬件的边界】流量卡（含 3 年套餐的实体卡）经业务侧确认"
               "**包在设备上**，随设备一次性交付，不是经常性服务费，"
               "仍计入硬件设备购置费。单位「张/3年」指卡本身含 3 年套餐，"
               "与按年续费的服务性质不同。")
        f.note("　【触发关系】每项服务费挂在触发设备上，配置该设备才产生费用。"
               "运营费独立填报会出现「设备没配、费用却在」，而这种错在表上看不出来。")
        f.total(expect={"数量": sum(_fq.get(x["code"], 0) for x in _fees),
                        "年费(元)": xlround(_yr, 2)})
        f.finish()

    return xlround(total, 2), len(hw["pending"])


def _service_fee_qty(root: Path) -> dict[str, float]:
    """运营服务费的数量（按料号合计）。来自 service-fee-catalog.yaml。"""
    f = root / "bom" / "devices" / "service-fee-catalog.yaml"
    if not f.exists():
        return {}
    out: dict[str, float] = {}
    for e in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get(
            "entries") or []:
        out[e["material"]] = out.get(e["material"], 0.0) + sum(
            v or 0 for v in (e.get("gardens") or {}).values())
    return out


def _service_fee_price(root: Path, deal: dict) -> dict[str, float]:
    """运营服务费的市场单价。取自设备物料主数据 —— 与硬件同一份价源。

    这些料号仍在 materials.yaml 里（它们确实是可售物料），
    拆出去的只是**配置条目**，不是物料本身。
    """
    f = root / "bom" / "devices" / "materials.yaml"
    if not f.exists():
        return {}
    return {m["code"]: m["price_yuan"] for m in
            (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get(
                "materials") or [] if m.get("price_yuan")}


def _make_bucket(tax: dict):
    """子系统 → (排序键, 总表科目名)。**00_总表 与甲本 01 共用这一个函数。**

    两张表是同一笔钱的两个粒度，行序必须一致 —— 各写一份归并逻辑，
    迟早会有一版改了这边没改那边，而表面上两张表都「正常」。
    """
    meta = {}
    for line, ls in (tax.get("product_lines") or {}).items():
        for sysname, sp in (ls.get("systems") or {}).items():
            sp = sp if isinstance(sp, dict) else {}
            meta[sysname] = (line, sp.get("layer") or ls.get("layer"))

    def bucket(sysname):
        line, layer = meta.get(sysname, ("其他", ""))
        if line == "数智幼教行业大模型":
            if layer == "L0层":
                return (3, "数智幼教行业大模型-引擎（L0层）")
            return ({"知识工程": 4, "本体工程": 5, "数据工程": 6,
                     "行业垂直模型": 7, "行业智能体": 8}.get(sysname, 9),
                    f"数智幼教行业大模型-{sysname}（{layer}）")
        if line == "数智民生生态体系平台支撑基座":
            return (2, "数智民生生态体系平台支撑基座（L0层）")
        return (1, "柳州市学前教育数智服务平台")

    bucket.layer_of = lambda n: (meta.get(n) or ("", ""))[1] or "应用层"
    return bucket


def emit_software(wb, sw: dict, deal: dict, pack: StandardPack,  # noqa: C901
                  fp_systems: list[dict] | None = None,
                  effort_only: set[str] | None = None,
                  fpset: dict | None = None,
                  fp_detail: list[dict] | None = None,
                  dnl: dict | None = None,
                  deal_counting: str | None = None,
                  ef: dict | None = None) -> tuple[float, float]:
    """Z01 —— 软件开发费。**混合口径**：一段功能点法、一段工作量法。

    柳州标准 p.10 三.(一)2.1 两法并列，标准未要求全项目用同一种；
    但每一块用哪种、为什么，必须在表内声明（见 costing-method-split.yaml）。

    返回 (功能点法金额万元, 工作量法金额万元)。
    """
    cite = pack.data["effort_method"]["citation"]
    effort_only = effort_only or set()
    fp_total = 0.0

    fpset = fpset or {"app_type": {}, "productivity_ratio": 1.0,
                      "productivity_basis": ""}
    if fp_systems:
        # 功能点法这一段拆成四张表：测算 / 明细 / 参数 / 试算。
        # 从前只有一张子系统级汇总 —— 3,153 个功能点摊在哪些条目上无从核对，
        # 各个系数出自标准哪一页也只写在副标题里。评审要逐条核，得有明细；
        # 方案要试算，得有参数面板。
        fp_total = emit_fp_worksheet(wb, pack, fpset, fp_systems, dnl=dnl)
        emit_fp_detail(wb, _ROOT, pack, effort_only, fpset,
                       sum(x["ufp"] for x in fp_systems),
                       # 复用度到功能模块，所以按 (子系统, 模块) 传，不是按子系统。
                   reuse_of={x["system"]: {m: g["reuse_factor"]
                                           for g in x["groups"] for m in g["modules"]}
                             for x in fp_systems})
        # 计价参数出两份：乙本一份作举证（每个系数的页码/条款/区间），
        # 丙本一份作试算面板。**不是冗余，是被跨簿引用逼出来的**：
        # 活公式跨工作簿要写 `='[文件名.xlsx]表'!C4`，文件名带版本号，
        # 下一版必断。同一次生成的两份来自同一个 fpset，版本内不可能漂移，
        # 且下面当场逐格核对过。
        pe, prows_e = emit_pricing_params(
            wb, pack, fpset, fp_systems, name="03_计价参数",
            deal_counting=deal_counting,
            subtitle_tail="本表为**举证件**：数由引擎算定，不参与计算。"
                          "要改系数看影响，用【丙本】的试算表。")
        pt, prows_t = emit_pricing_params(
            wb, pack, fpset, fp_systems, name="01_试算参数",
            deal_counting=deal_counting,
            subtitle_tail="本表是【丙本】02_二层试算 的参数面板，"
                          "改这里试算表全表重算。**内容与【乙本】02_计价参数 逐格相同**，"
                          "那一份是举证件。")
        _assert_same_params(pe, pt)
        emit_fp_whatif(wb, pack, fpset, fp_systems, prows_t, fp_total,
                       param_sheet="01_试算参数",
                       root=_ROOT, sw=sw, effort_only=effort_only, ef=ef)
        emit_reuse_modules(wb, _ROOT, pack, fpset, fp_systems, fp_detail or [],
                           sw=sw, effort_only=effort_only, ef=ef)

    s = GovSheet(
        wb, "04_工作量测算表" if fp_systems else "01_软件开发费",
        title=("软件开发费预算表（二）工作量估算法" if fp_systems
               else "软件开发费预算表（工作量估算法）"),
        subtitle=(f"编制依据：{pack.data['standard_doc']}（{pack.data['doc_no']}）"
                  f" 三.(一)2.1.1 表1　单价 = 人工成本 × 风险系数 × 复用系数"
                  f"（p.{cite['page']} 表1 注3）。"
                  + (f"**风险系数取 {ef['risk']}**" if ef else "")
                  + ("；复用系数逐（子系统 × 阶段）取值，见「复用度」列"
                     if (ef and ef.get("apply_reuse")) else
                     "；复用系数本次一律取 1（复用度低，缺省）")),
        columns=[
            # **归集视角是「建设了什么」，不是「谁在干」。**
            # 从前行是五个阶段、主描述列是「人员类型」，整张表读下来是
            # 「知识工程雇了哪些角色、各干多少人月」—— 而评审要问的是
            # 「数据工程建了哪些数据、每一项多少钱」。人员类型按注3 根本
            # 不参与计价（单价按阶段取），让它占据主描述位就是把不计价的
            # 维度摆成了主维度。现在：行 = 建设对象，五阶段退成列，
            # 人员类型移到系统尾部的阶段小计区。
            Col("建设对象", "text", width=34),
            Col("工作量（人月）", "fp", width=13, sum=False, role="effort"),
            *[Col(f"　{st}", "fp", width=10) for st in STAGES],
            Col("×风险系数", "rate", width=10, cell_role="locked"),
            Col("×复用度", "text", width=9, cell_role="locked"),
            Col("×复用系数", "rate", width=10, cell_role="locked"),
            Col("金额(万元)", "money", width=14, sum=True, role="amount_wan"),
            Col("备注", "text", width=50),
        ],
        clause_required=True)

    wbs = _wbs_roles(_ROOT)
    _wdoc = yaml.safe_load(
        (_ROOT / "bom" / "effort-wbs.yaml").read_text(encoding="utf-8"))
    wbs_notes = {(sysn, a["name"]): a.get("stage_note", "")
                 for sysn, t in (_wdoc.get("templates") or {}).items()
                 for a in (t.get("activities") or [])}
    _rates = {k: v["value"] for k, v in
              (pack.data["effort_method"]["stage_rates"] or {}).items()}
    _risk = (ef or {}).get("risk", 1.0)
    total = 0.0
    obj_drift: list[str] = []
    for sysname, blk in sw["systems"].items():
        if not blk["stages"]:
            continue
        if effort_only and sysname not in effort_only:
            continue    # 该系统走功能点法，已在上一张表计列，此处不重复
        sub = blk.get("obj_amount_wan",
                      sum(v["amount_wan"] for v in blk["stages"].values()))
        s.group(f"■ {sysname}", {"金额(万元)": xlround(sub, 4)})

        # ---- 每个阶段的取数：单价、复用系数、活动构成 ----
        st_pct = {st: ((wbs.get(sysname) or {}).get(st) or {}).get("pct", 0.0)
                  for st in STAGES}
        st_f = {st: ((ef or {}).get("stage", {}).get((sysname, st)) or {})
                for st in STAGES}
        # 系统内各阶段的复用系数是否一致 —— 一致才能在对象行印一个数。
        _facs = {round(st_f[st].get("factor", 1.0), 6)
                 for st in STAGES if st_pct.get(st)}
        _uniform = len(_facs) <= 1
        _lv = {(st_f[st].get("levels") or ["低"])[0]
               for st in STAGES if st_pct.get(st)}

        # ---- 行 = 建设对象 ----
        objs = [lf for lf in (blk.get("leaves") or [])
                if (lf.get("effort_basis") or {}).get("man_months")]
        sum_obj = 0.0
        for lf in objs:
            eb = lf["effort_basis"]
            mm = float(eb["man_months"])
            cells = {st: xlround(mm * st_pct.get(st, 0.0), 4) for st in STAGES}
            # 金额逐阶段算再合并，**不折平均单价**：一条交付物横跨五个阶段，
            # 五个阶段单价不同（1.9/1.9/1.7/1.6/1.4），折一个「综合单价」
            # 出来就是编一个标准里没有的数。取整只在对象的金额上做一次。
            o_i = (ef or {}).get("obj", {}).get((sysname, lf["name"])) or {}
            o_f = o_i.get("factor", 1.0)
            amt = xlround(sum(
                mm * st_pct.get(st, 0.0) * _rates.get(st, 0.0)
                for st in STAGES) * _risk * o_f, 4)
            sum_obj += amt
            _q, _u, _p = eb.get("qty"), eb.get("unit"), eb.get("per_unit_mm")
            note = (f"{_q} {_u} × {_p} 人月/{_u}；测算依据见 05"
                    if (_q and _u and _p) else "**待补**测算依据，见 05")
            s.row({
                "建设对象": lf["name"],
                "工作量（人月）": xlround(mm, 4),
                **{f"　{st}": cells[st] for st in STAGES},
                "×风险系数": _risk,
                "×复用度": o_i.get("level", "低"),
                "×复用系数": o_f,
                "金额(万元)": amt,
                "备注": (note + ("　复用依据："
                                 + " ".join(str(o_i["basis"]).split())[:70]
                                 if o_i.get("basis") else "")),
            }, clause=f"三.(一)2.1.1 表1 注1（p.{cite['page']}）")
        total += sum_obj

        # ---- 尾部：阶段小计 / 阶段单价 / 阶段复用系数 / 人员类型 ----
        # 表1 的法定行是五个阶段。行换成建设对象后，五阶段仍须**逐列可加总**，
        # 否则这张表就脱离了表1 的结构。这三行就是与表1 的接缝。
        s.row({"建设对象": "　└ 阶段小计（人月）",
               "工作量（人月）": xlround(sum(
                   v["man_months"] for v in blk["stages"].values()), 4),
               **{f"　{st}": xlround((blk["stages"].get(st) or {}).get(
                   "man_months", 0.0), 4) for st in STAGES},
               "备注": "＝上列各对象按 WBS 活动模板分摊之和；分摊比例见下行"},
              clause=f"三.(一)2.1.1 表1（p.{cite['page']}）")
        s.row({"建设对象": "　└ ×阶段单价（万元/人月）",
               **{f"　{st}": _rates.get(st) for st in STAGES},
               "备注": "表1 注3：人工成本按阶段取，与人员类型无关"},
              clause="三.(一)2.1.1 表1 注3")
        if not _uniform:
            s.row({"建设对象": "　└ ×阶段复用系数",
                   **{f"　{st}": st_f[st].get("factor", 1.0) for st in STAGES},
                   "备注": "复用度判在 WBS 活动上，阶段系数 = Σ(活动阶段内占比 × 活动系数)"},
                  clause="三.(一)2.1.1 表1 注3")
        for st in STAGES:
            comp = (wbs.get(sysname) or {}).get(st)
            if not st_pct.get(st):
                continue
            if not comp:
                s.note(f"　⚠ {sysname} · {st}：WBS 无对应活动，人员类型无从归集，"
                       f"复用度按缺省「低 = 1」计列（见编制说明待处理事项）。")
                continue
            sn = [wbs_notes.get((sysname, a)) for a, _ in comp["acts"]]
            sn = [x for x in dict.fromkeys(sn) if x]
            s.note(f"　{st}（占 {st_pct[st]:.0%}）由 "
                   + "、".join(f"{a}({w:.0%})" for a, w in comp["acts"])
                   + f" 构成；承担人员 {_role_cell(comp)}"
                   + ("；" + "；".join(sn) if sn else ""))
            a_i = (ef or {}).get("acts", {}).get((sysname, st)) or []
            if st_f[st].get("mixed"):
                for x in a_i:
                    s.note(f"　　└ {x['act']}　复用度 {x['level']}（{x['factor']}）"
                           f"　占本阶段 {x['share']:.1%}　"
                           + " ".join(str(x["basis"]).split())[:60])

        # 对象口径与阶段口径的**取整漂移**：两条链路的取整层级不同
        # （对象口径在对象的金额上取一次，阶段口径在阶段金额上取一次）。
        # 差额必然很小，但**必须报出来**，否则读者按阶段小计乘单价复算会
        # 对不上，而对不上的原因看不见。
        if abs(sum_obj - sub) > 0.0001:
            obj_drift.append(f"{sysname} {(sum_obj - sub) * 10000:+,.0f} 元")
    if obj_drift:
        s.note("　【取整漂移】按建设对象逐条计价并合计，与按阶段计价合计相差："
               + "；".join(obj_drift)
               + "。差额来自取整层级不同（本表在每个对象的金额上取整一次，"
                 "阶段口径在每个阶段的金额上取整一次），**不是计价差异**。"
                 "本表合计以对象口径为准，甲本汇总同源。")

    # 这条原来在 06_工作量活动分解 里，删表时一并没了 —— 表头那句
    # 「说明性·不参与计价」太短，说不清为什么「不改钱反而更该填对」。
    s.note("说明：**本表按建设对象归集** —— 行是「建了哪些语料库/数据集/模型/"
           "智能体/本体要素」，五个阶段退成列。表1 的法定行是五阶段，故每个系统"
           "尾部保留「阶段小计（人月）」与「×阶段单价」两行作为与表1 的接缝，"
           "逐列可加总核对。")
    s.note("说明：**人员类型不进主栅格**。表1 设有该列，但注3 定义单价时写的是"
           "「人工成本：需求分析/系统设计阶段单价按照 1.9 万元/人月计算…」——"
           "**单价按阶段取，标准未给按人员类型的费率**，同阶段所有角色适用同一个"
           "人工成本，该列填什么都不改一分钱。让一个不计价的维度占据主描述位，"
           "整张表会读成「雇了哪些角色」而不是「建了什么、各多少钱」，故移到"
           "每个系统尾部的阶段构成说明里。")
    s.note("说明：不改钱**反而更该填对** —— 若把知识工程的「资料收集与来源整理」"
           "标成「软件开发工程师」，就与「L1/L2 交付物是语料/数据资产/模型，"
           "功能点法按定义测不到」这条论证自相矛盾，评审会挑对我方更不利的那个说法。"
           "各阶段的角色构成由 bom/effort-wbs.yaml 的活动 role 字段归集。")
    s.note("说明：**金额逐阶段算再合并，本表不设「综合单价」列**。一条交付物横跨"
           "五个阶段，五阶段单价不同（1.9/1.9/1.7/1.6/1.4），折一个平均单价出来"
           "就是编一个标准里没有的数。金额 = Σ阶段(该对象在本阶段的人月 × 阶段单价"
           " × 风险系数 × 该阶段复用系数)，取整只在对象的金额上做一次。")
    s.note("说明：柳州标准 p.10 三.(一)2.1「软件开发费按实际需求及任务量计算，"
           "开发周期一般不超过一年，费用包含需求分析、设计、编码、测试、部署实施"
           "以及项目管理、培训等」—— 上列五阶段即该费用的全部构成，不另计项目管理费与培训费。")
    s.note("说明：软件开发应包含终验通过后至少一年的免费维护服务（p.10 三.(一)2.1）；"
           "建设方应拥有新开发软件的完整知识产权（含源代码、说明文档、部署文档、测试文档）。")
    if fp_systems:
        s.note("说明：本表仅列**按 2.1.1 工作量估算法**计列的部分（L1 知识工程/"
               "数据建设、L2 专业模型/行业智能体）。其交付物为语料、数据资产、"
               "模型与智能体，功能点法测的是数据功能与事务功能，按定义测不到这些工作 —— "
               "故依标准 p.10「可按照工作量估算法或功能点估算法进行估算」择用工作量法。")
        s.note("说明：平台与 L0 支撑基座按 2.1.2 功能点估算法计列，见 01_功能点测算表，"
               "两段互不重叠。")
    s.total(expect={"金额(万元)": xlround(total, 4)})
    s.finish()

    # ---- 逐末级功能明细（表1 要求「按子系统、模块细化分层」）----
    d = GovSheet(
        wb, "05_工作量法功能明细" if fp_systems else "02_软件开发功能明细",
        title="工作量测算明细（逐末级交付物）",
        subtitle="依据 三.(一)2.1.1 表1 注1「系统应按子系统、模块进行细化分层」。"
                 "**本表回答「这条的工作量是怎么来的」**。五段工作量各有各的公式（词元数×单价 / Σ要素条数×各类单价 / 类目数×分档单价 / 基准+复杂度 / 基准+能力点数），故不设统一的「数量×单价」两列，改为逐项展开到可复算；"
                 "单价的构成（人工成本 × 风险系数 × 复用系数）见 04_工作量测算表。",
        columns=[
            Col("系统", "text", width=26),
            Col("子系统/功能", "text", width=22),
            Col("末级功能", "text", width=22),
            Col("需求描述", "text", width=60),
            # ---- 工作量的测算依据：本表的主线 ----
            # 参照的做法是「可核查量纲 × 单位工作量 = 工作量」
            # （如 3000 类数据资源 × 0.024 人月/类 = 72 人月）。
            # ⚠ 本项目**不能事后按规模/条数回填**：实测四个数据集规模差 30 倍
            # （100～3000 条）而人天只差 17%（71～83），隐含单产跨 33 倍 ——
            # 这批人天本来就不是按条数估的，按规模写一除就露馅，而规模数字
            # 就写在需求描述里，评审自己能除。BOM 给的方向是按**建设复杂度**填。
            # **只读。** 本表是送审的举证册，不是录入面；录入在丙本 03。
            # 标成 input 会让人以为填了有用 —— quote_sync 不读乙本。
            Col("可核查量纲", "text", width=18, cell_role="locked"),
            # 「数量 / 单位工作量」两列已删。这五段工作量是**五个不同的公式**，
            # 硬塞进「一个数量 × 一个单价」的格子里，四段是错的或空的：
            #   · 知识工程　量纲写「知识主题域」，数量列装的却是词元数
            #   · 本体工程　单价 0.314 是 15.7÷50 倒算的，依据里自称「导出值」
            #   · 垂直模型　单价列**是空的**（真实算法是 基准 + 两项复杂度）
            #   · 行业智能体 2.75 是整个中括号塌成一个数，能力点数不可见
            # 保留这两列等于制造一个统一口径的假象，而假象一除就露 ——
            # 评审拿 人月÷数量 一算，得到的正是那个我们说不出来源的数。
            Col("测算公式", "text", width=34, cell_role="locked"),
            Col("测算构成（逐项展开）", "text", width=52, cell_role="locked"),
            Col("工作量（人月）", "fp", width=13, sum=True),
            Col("分档规则", "text", width=46, cell_role="locked"),
            # 逐条的复杂度量化说明：取值规则（分档表）+ 本条落在哪一档 + 依据。
            # 「测算依据来源」给的是**公式**（人月 = 量纲 × 单位工作量），
            # 回答不了「为什么这条取 1 不取 3」—— 那句才是评审真正会问的，
            # 而它一直只躺在 BOM 里没出到送审册。
            Col("复杂度量化说明", "text", width=64, cell_role="locked"),
            Col("状态", "text", width=20, cell_role="locked"),
        ],
        clause_required=True)
    leaf_total = 0.0
    leaf_mm = 0.0
    for sysname, blk in sw["systems"].items():
        if effort_only and sysname not in effort_only:
            continue
        st_i = (ef or {}).get("stage", {}).get((sysname, "软件开发（编码）")) or {}
        for lf in blk["leaves"]:
            eb = (lf.get("effort_basis") or {})
            mm_leaf = eb.get("man_months")
            if mm_leaf is None:
                mm_leaf = lf["man_months"] or 0
            leaf_mm += mm_leaf
            _fml, _cmp, _tier = _split_basis(eb.get("basis") or "")
            _cf = (eb.get("counted_from") or "").strip()
            _cf_dup = bool(_cf) and _cf == (eb.get("basis") or "").strip()
            d.row({
                "系统": sysname, "子系统/功能": lf["subsystem"] or "",
                "末级功能": lf["name"],
                "需求描述": (lf["description"] or "")[:400],
                "可核查量纲": (eb.get("unit") or ""),
                # **全阶段人月**（量纲 × 单位工作量），不是编码阶段的一段。
                # 金额不在本表出：一条交付物横跨五个阶段，五个阶段单价不同，
                # 折一个「平均单价」出来就是编出一个标准里没有的数 ——
                # 与甲本 01 不设「单价」列同一条理由。金额见 04。
                "工作量（人月）": xlround(mm_leaf, 4),
                "测算公式": _fml,
                "测算构成（逐项展开）": _cmp,
                "分档规则": _tier,
                # C4 知识工程 / C6 数据工程 在飞书上**没有「说明」列**，
                # counted_from 装的是 basis 的副本 —— 原样出表会让这一列
                # 整列重复前面三列。同源就留空：空白说明「这批没有逐条
                # 量化说明」，重复则说明「有，但等于没有」，后者更误导。
                "复杂度量化说明": ("" if _cf_dup else (eb.get("counted_from")
                                                       or "")),
                "状态": (eb.get("status") or "**待补**"),
            }, clause="三.(一)2.1.1 表1 注1")
    # 勾稽口径换成**人月**：05 现在给的是逐交付物的**全阶段**人月，
    # 应与 04 五个阶段的人月合计相等（04 的阶段人月正是由它按模板分摊而来）。
    # 从前比的是「05 金额 vs 编码阶段金额」—— 那是旧链路（源表给阶段、
    # 末级恰好等于编码段）的产物，方向反了。
    stage_mm = sum(v["man_months"] for sn, b in sw["systems"].items()
                   if not effort_only or sn in effort_only
                   for v in b["stages"].values())
    # **只数本表实际出的行。** 不按 effort_only 过滤的话数的是全部系统的末级
    # （484 条），而本表只列工作量法那一段（134 条）—— 一个和表上行数对不上的
    # 计数，比不写更容易让人以为表漏了行。
    _lv = [lf for sn, b2 in sw["systems"].items()
           if not effort_only or sn in effort_only
           for lf in (b2.get("leaves") or [])]
    _all = len(_lv)
    _pend = sum(1 for lf in _lv if not ((lf.get("effort_basis") or {}).get("basis")))
    if _pend:
        d.note(f"⚠ **本表 {_all} 条中 {_pend} 条尚无工作量测算依据。** 标准只规定单价"
               f"（表1 注3），**不规定人月如何估算**；表1 注1 给的方法是「按子系统、"
               f"模块细化分层…参照本表测算」，即自下而上从可核查量纲累加。"
               f"本套表的人月来自源估算表，与既有报价的倒算比为 1.559×/1.870×，"
               f"**不构成独立测算**。见编制说明第八节第 1 条。")
        d.note("⚠ **不要按规模/条数回填依据。** 实测四个数据集规模差 30 倍"
               "（100～3000 条）而人天只差 17%（71～83），隐含单产跨 33 倍 —— "
               "这批人天本来就不是按条数估的；按规模写，评审拿需求描述里的规模数字"
               "一除就露馅。应按**建设复杂度**填「可核查量纲」（如资料来源数、"
               "文档格式种类、切分策略数、审校轮次、需专家复核比例），"
               "并在「测算依据来源」写明判断来源（对标项目 / 历史实测 / 专家判断，"
               "任选其一但要说得出来）。")
    d.note("　【单价的构成】表1 注3：单价 = 人工成本 × 风险系数 × 复用系数。"
           "人工成本按阶段固定（编码阶段 1.7 万元/人月）；复用度分档时本表的"
           "复用系数为该阶段的加权值 Σ(活动阶段内占比 × 活动系数)，"
           "逐活动明细见 04_工作量测算表 的分解行。")
    d.note(f"　【与 04 的勾稽】本表逐交付物合计 {xlround(leaf_mm,4):,.4f} 人月，"
           f"= 04_工作量测算表 五阶段人月合计 {xlround(stage_mm,4):,.4f}。"
           f"**推导方向：本表 → 04**：各交付物的全阶段人月按 bom/effort-wbs.yaml 的"
           f"活动模板分摊到表1 的五个阶段，04 再施加因子（人工成本 × 风险 × 复用）"
           f"得出金额。本表不出金额 —— 一条交付物横跨五个阶段、五个单价不同，"
           f"折一个「平均单价」就是编一个标准里没有的数。")
    d.total(expect={"工作量（人月）": xlround(leaf_mm, 4)})
    d.finish()
    if abs(leaf_mm - stage_mm) > 0.01:
        raise gs.GovSheetError(
            f"逐交付物人月合计 {leaf_mm:.4f} 与 04 五阶段人月合计 {stage_mm:.4f} "
            f"对不上，差 {leaf_mm - stage_mm:.4f} 人月 —— 分摊环节漏了或重了")
    return xlround(fp_total / 10000, 4), total


def emit_hardware(wb, hw: dict, deal: dict, pack: StandardPack) -> tuple[float, float]:
    """Z02 —— 硬件设备购置费，柳州表7 格式。返回 (计入金额, 待核价条数)。"""
    ev = pack.data["procurement_evidence"]["hardware"]
    s = GovSheet(
        wb, "01_硬件设备购置费",
        title="硬件设备购置预算支出表",
        subtitle=(f"编制依据：{pack.data['doc_no']} 三.(一)2.5 表7　"
                  f"{ev['brands_note']}；{ev['quota_rule']}"),
        columns=[
            Col("园所/批次", "text", width=16),
            Col("一级场景", "text", width=12),
            Col("分项名称", "text", width=28),
            Col("参考品牌型号", "text", width=20),
            Col("物料编码", "text", width=14),
            Col("单位", "text", width=6),
            Col("数量", "int", width=8, sum=True),
            Col("单价(元)", "money", width=12, role="unit_price"),
            Col("金额(元)", "money", width=14, sum=True, role="amount"),
            Col("备注（用途）", "text", width=24),
        ],
        clause_required=True)

    by_sheet: dict[str, list] = defaultdict(list)
    for p in hw["priced"]:
        by_sheet[p["园所"]].append(p)

    total = 0.0
    for sn in hw["sheets"]:
        rows = by_sheet.get(sn, [])
        if not rows:
            continue
        sub = sum(r["total"] for r in rows)
        s.group(f"■ {sn}", {"金额(元)": xlround(sub, 2)})
        for r in rows:
            note = r.get("备注") or ""
            if "mismatch" in r:
                note = f"⚠ 单价×数量={r['mismatch']:,.2f}，源表总价={r['total']:,.2f}"
            s.row({
                "园所/批次": sn, "一级场景": r["场景"],
                "分项名称": r["name"],
                # 型号是厂商给的自由文本标识（z202 / DRH-FZ420 …），不是枚举 ——
                # 显式 raw() 放行单列，而不是放宽全局的枚举守卫
                "参考品牌型号": gs.raw(str(r["model"] or "")),
                "物料编码": gs.raw(str(r["code"] or "")),
                "单位": r["unit"], "数量": r["qty"],
                "单价(元)": r["unit_price"], "金额(元)": xlround(r["total"], 2),
                "备注（用途）": note or (r["子场景"] or ""),
            }, clause="三.(一)2.5 表7")
            total += r["total"]
    s.total(expect={"金额(元)": xlround(total, 2)})
    s.finish()

    # ---- 待核价：列而不计 ----
    # ¥0 和「还没定价」在表上必须分得出来。这些条目**有数量、有用途**，
    # 计 0 会让它们在合计里消失且合计看起来正常 —— 那是本项目的头号缺陷形态。
    p = GovSheet(
        wb, "02_待核价设备（列而不计）",
        title="待核价设备清单 —— 已列入建设内容，未计入本次预算金额",
        subtitle="本表条目数量与用途明确但尚无报价，故**列出但不计金额**。"
                 "列出是为了证明它们不是被漏掉的；不计金额是因为无价可计。"
                 "定价后须并入 01 表并同步调整系统集成费与预备费基数。",
        columns=[
            Col("园所/批次", "text", width=16),
            Col("一级场景", "text", width=12),
            Col("分项名称", "text", width=28),
            Col("参考品牌型号", "text", width=20),
            Col("单位", "text", width=6),
            Col("数量", "int", width=8, sum=True),
            Col("是否自研", "text", width=10),
            Col("状态", "text", width=14),
            Col("处置要求", "text", width=40),
        ],
        clause_required=True)
    for r in hw["pending"]:
        p.row({
            "园所/批次": r["园所"], "一级场景": r["场景"],
            "分项名称": r["name"],
            "参考品牌型号": gs.raw(str(r["model"] or "")),
            "单位": r["unit"], "数量": r["qty"], "是否自研": r["自研"],
            "状态": gs.raw("待核价"),
            "处置要求": (f"须提供参考品牌型号不少于 3 个及价格依据"
                         if r["自研"] != "自研"
                         else "自研产品，须提供成本构成与定价依据"),
        }, clause="三.(一)2.5 表7 注3")
    p.note(f"共 {len(hw['pending'])} 条待核价，**未计入**硬件设备购置费合计 "
           f"{xlround(total,2):,.2f} 元。这些条目在源清单中数量非零，"
           f"若按 ¥0 并入合计，合计看起来正常但实际漏项 —— 故单列。")
    p.total(expect={"数量": sum(r["qty"] for r in hw["pending"])})
    p.finish()
    return total, len(hw["pending"])


def passthrough_hardware(src: Path, out_path: Path, rule_path: Path,
                         hw: dict, pack: StandardPack) -> tuple[float, int]:
    """硬件沿用源表原格式送审：脱敏另存 + 追加「待核价」披露页。

    「沿用原表」是业务决定，格式评审方看惯了，重排反增风险 —— 但沿用不等于
    原样发出去：源表把成本/渠道/毛利与对外报价并排放在同一张表。
    所以先过 `sanitize_workbook`（删列 → 整本复检 → 合计对账），再补一页。

    补的那一页是**不能省的**：源表里 114 条设备有数量、单价为 ¥0，
    并入合计后合计看起来完全正常，但硬件预算实际漏项。
    ¥0 与「还没定价」在表上必须分得出来 —— 这是本项目的头号缺陷形态。
    """
    import sanitize_workbook as sw_mod

    rule = yaml.safe_load(rule_path.read_text(encoding="utf-8"))
    total = xlround(sum(r["total"] for r in hw["priced"]), 2)
    sw_mod.sanitize(src, out_path, rule)

    # 合计对账：脱敏产物里的市场总价合计必须等于我们读到的
    got = sw_mod._sum_column(out_path, "市场总价",
                             rule.get("default_header_row", 2))
    if abs(got - total) > 0.01:
        out_path.unlink(missing_ok=True)
        raise gs.GovSheetError(
            f"脱敏产物「市场总价」合计 {got:,.2f}，引擎读到 {total:,.2f}，"
            f"差 {got - total:,.2f} —— 脱敏时动到了数据，已删除产物")

    wb = openpyxl.load_workbook(out_path)
    p = GovSheet(
        wb, "待核价设备（列而不计）",
        title="待核价设备清单 —— 已列入建设内容，未计入本次预算金额",
        subtitle=("本表条目在前述各园所表中数量非零但单价为 0，故**列出但不计金额**。"
                  "列出是为了证明它们不是被漏掉的；不计金额是因为无价可计。"
                  "定价后须并入相应园所表，并同步调整系统集成费与预备费的计费基数。"),
        columns=[
            Col("园所/批次", "text", width=16),
            Col("一级场景", "text", width=12),
            Col("分项名称", "text", width=28),
            Col("参考品牌型号", "text", width=20),
            Col("单位", "text", width=6),
            Col("数量", "int", width=8, sum=True),
            Col("是否自研", "text", width=10),
            Col("状态", "text", width=12),
            Col("处置要求", "text", width=40),
        ],
        clause_required=True)
    for r in hw["pending"]:
        p.row({
            "园所/批次": r["园所"], "一级场景": r["场景"],
            "分项名称": r["name"],
            "参考品牌型号": gs.raw(str(r["model"] or "")),
            "单位": r["unit"], "数量": r["qty"], "是否自研": r["自研"],
            "状态": gs.raw("待核价"),
            "处置要求": ("须提供参考品牌型号不少于 3 个及价格依据"
                         if r["自研"] != "自研"
                         else "自研产品，须提供成本构成与定价依据"),
        }, clause="三.(一)2.5 表7 注3")
    p.note(f"共 {len(hw['pending'])} 条待核价，**未计入**硬件设备购置费合计 "
           f"{total:,.2f} 元。")
    p.total(expect={"数量": sum(r["qty"] for r in hw["pending"])})
    p.finish()
    wb.save(out_path)
    return total, len(hw["pending"])


def _write_lock(path: Path, deal: dict, pack: StandardPack, *, sw: dict,
                hw: dict, sw_wan: float, hw_yuan: float, other: dict,
                grand: float, fp_cross: dict, src_software: Path,
                src_hardware: Path) -> None:
    """复算锁 —— 记下这一版是**什么的函数**，以及重跑它的命令。

    与康养的 `deal.lock.json` 同职责：没有它，三个月后没人能复现这一版的数。
    输入文件按内容哈希锁 —— 源表被人改了一格，哈希就变，复算时会发现。
    """
    import hashlib

    def sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]

    path.write_text(json.dumps({
        "deal_id": deal["deal_id"],
        "project_name": deal["project_name"],
        "as_of": deal["as_of"],
        "costing_method": deal["costing_method"],
        "costing_method_clause": deal["costing_method_clause"],
        "standard_pack": {
            "pack_id": pack.pack_id,
            "doc_no": pack.data["doc_no"],
            "formula_profile": pack.formula_profile,
            "effective_from": pack.data["effective_from"],
            "csbmk_baseline": pack.data["csbmk_baseline"],
        },
        "deal_decisions": {
            "fee_project_type": deal["fee_project_type"],
            "integration_layout": deal["integration_layout"],
            "fixed_fee_counts": deal.get("fixed_fee_counts"),
            "hardware_passthrough": bool(
                deal["sources"].get("hardware_passthrough")),
        },
        "inputs": {
            "software": {"path": deal["sources"]["software"],
                         "sha256_16": sha(src_software)},
            "hardware": {"path": deal["sources"]["hardware"],
                         "sha256_16": sha(src_hardware),
                         "price_column": deal["sources"]["hardware_price_column"]},
        },
        "totals": {
            "software_wan": xlround(sw_wan, 4),
            "hardware_yuan": xlround(hw_yuan, 2),
            "hardware_pending_items": len(hw["pending"]),
            "other_fees_wan": other["total_wan"],
            "grand_total_yuan": grand,
            "other_fees_ratio_excl_integration": round(other["capped_ratio"], 4),
        },
        "crosscheck_fp_method": fp_cross,
        "open_issues": sw["issues"],
        "reproduce": (
            f"python3 quote_generate_liuzhou.py "
            f"--deal deals/{deal['deal_id']}/deal.yaml"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def emit_other_fees(wb, deal: dict, pack: StandardPack, *,
                    software_wan: float, hardware_yuan: float,
                    listed: dict[str, float]) -> dict[str, Any]:
    """Z03 —— 其他费用，逐项按表9/11/12/13 复算上限。"""
    prof = get_profile(pack.formula_profile)
    eng_wan = software_wan
    hw_wan = hardware_yuan / 10000
    works_wan = eng_wan + hw_wan          # 工程费用 = 软件开发 + 硬件购置

    s = GovSheet(
        wb, "03_其他费用与预备费",
        title="其他费用与预备费预算表",
        subtitle=f"编制依据：{pack.data['doc_no']} 三.(一)2.6～2.9　"
                 f"计费额：工程费用 {works_wan:,.2f} 万元（软件开发 {eng_wan:,.2f} + "
                 f"硬件购置 {hw_wan:,.2f}）",
        columns=[
            Col("费用科目", "text", width=24),
            Col("计费额(万元)", "money", width=14),
            Col("计算方式", "text", width=46),
            Col("标准上限(万元)", "money", width=15, cell_role="locked"),
            Col("本次计列(万元)", "money", width=15, sum=True),
            Col("结论", "text", width=18),
        ],
        clause_required=True)

    rows: list[dict] = []
    total = 0.0

    def add(fee_id: str, base_wan: float, listed_wan: float, clause: str,
            **kw) -> None:
        nonlocal total
        cap = prof.other_fee(pack, fee_id, base_wan, **kw)
        spec = pack.fee(fee_id)
        how = _how(spec, base_wan, prof, kw)
        # 计列值可以写成 "上限" 而不是一个数：**核减到上限是个规则，不是某个数字**。
        # 写死数字的下场刚见过 —— 施工地点声明里焊着「171.82 ÷ 2,396.64 = 7.17%，
        # 在档内」，硬件一变就成了假陈述，而假的方向对我方有利。
        # 上限随计费额浮动，写死就等着下次计费额一动再错一次。
        reduced = False
        if isinstance(listed_wan, str):
            if listed_wan.strip() not in ("上限", "cap", "核减至上限"):
                raise gs.GovSheetError(
                    f"{fee_id} 的计列值「{listed_wan}」不认识 —— "
                    f"要么给数字，要么写「上限」")
            listed_wan, reduced = cap, True
        ok = listed_wan <= cap + 1e-9
        s.row({
            "费用科目": spec["name"], "计费额(万元)": xlround(base_wan, 2),
            "计算方式": how, "标准上限(万元)": cap,
            "本次计列(万元)": listed_wan,
            "结论": gs.raw("已核减至上限" if reduced else
                           ("未超上限" if ok else f"⚠ 超上限 {listed_wan - cap:,.2f} 万")),
        }, clause=clause)
        rows.append({"id": fee_id, "name": spec["name"], "cap": cap,
                     "listed": listed_wan, "ok": ok, "reduced": reduced})
        total += listed_wan

    add("integration", hw_wan, listed["integration"], "三.(一)2.6.1 表9",
        integration_layout=deal["integration_layout"])
    add("design", works_wan, listed["design"], "三.(一)2.6.2 表11",
        fee_project_type=deal["fee_project_type"])
    add("supervision", works_wan, listed["supervision"], "三.(一)2.6.3 表12")
    add("third_party_test", eng_wan, listed["third_party_test"],
        "三.(一)2.6.4 表13", fee_project_type="软件开发为主")
    add("dengbao_l3", 0.0, listed["dengbao_l3"], "三.(一)2.6.5",
        count=(deal.get("fixed_fee_counts") or {}).get("dengbao_l3", 1))

    for name, blk in (deal.get("external_standard_fees") or {}).items():
        amt = blk["amount_yuan"] / 10000
        s.row({
            "费用科目": name, "计费额(万元)": None,
            "计算方式": gs.raw("本标准未给费率表 —— 按柳州市相关文件标准执行"),
            "标准上限(万元)": None, "本次计列(万元)": amt,
            "结论": gs.raw("待补依据"),
        }, clause=blk["clause"])
        total += amt

    add("reserve", works_wan, listed["reserve"], "三.(一)2.9")

    s.total(expect={"本次计列(万元)": xlround(total, 2)})

    # ---- 10% 封顶检查 ----
    con = next(c for c in pack.data["constraints"] if c["id"] == "other_fees_cap")
    # 从 `rows` 取**解析后**的计列额，不从 `listed` 取原始值 ——
    # 后者可能是「上限」这个字符串，而 10% 封顶要的是钱。
    _lst = {r["id"]: r["listed"] for r in rows}
    capped = total - _lst["integration"] - _lst["reserve"]
    budget = works_wan + total
    ratio = capped / budget
    s.note(f"【{con['rule']}】（p.{con['citation']['page']} {con['citation']['section']}）："
           f"扣除系统集成费后的其他费用合计 {capped:,.2f} 万元 ÷ 预算总额 {budget:,.2f} 万元 "
           f"= {ratio:.2%}，"
           + ("未超 10% 上限。" if ratio <= con["max_ratio"] else "**已超 10% 上限**。"))
    # 占比**当场算**，不从 deal 的文字里抄。
    # 这一句原来整段写死在 `integration_layout_basis` 里（「171.82 ÷ 2,396.64
    # = 7.17%，在档内」）。硬件后来从 2,396.64 万降到 1,523.59 万，实际占比
    # 变成 11.28% —— 同一张表里，数据行写着「超上限 49.93 万」，声明行还在说
    # 「在档内」，而说反的方向恰好对我方有利。隔壁【项目类型声明】的注释
    # 专门写过「写死的数字会在计费额变动后变成假陈述」，这里自己踩了。
    _intg = next((r for r in rows if r["id"] == "integration"), None)
    _share = (_intg["listed"] / hw_wan) if (_intg and hw_wan) else 0.0
    _lo, _hi = deal.get("integration_layout_range") or (0.04, 0.08)
    s.note(f"【施工地点分布声明】本项目按「{deal['integration_layout']}」计取系统集成费。"
           + " ".join(deal["integration_layout_basis"].split())
           + f" 现列 {_intg['listed']:,.2f} 万 ÷ 采购总额 {hw_wan:,.2f} 万 = "
             f"{_share:.2%}，"
           # **复用表格行已经判好的 ok，不另算一遍比率。**
           # 原来这里拿 listed/hw_wan 和 8% 比，而表格行比的是 listed <= cap；
           # cap 是四舍五入到分的（1,523.59 × 8% = 121.8872 → 121.89），
           # 两条路在边界上必然分家 —— 核减到上限后占比算出 8.00008%，
           # 声明写成「已超出…须核减 0.00 万」，同一张表自相矛盾。
           # 同一件事只判一次，判在哪里都行，但不能判两次。
           + (f"在 {_lo:.0%}–{_hi:.0%} 档内。" if _intg["ok"] else
              f"**已超出 {_lo:.0%}–{_hi:.0%} 档**（上限 {_intg['cap']:,.2f} 万，"
              f"须核减 {_intg['listed'] - _intg['cap']:,.2f} 万）。"))
    # ⚠️ 不要复用 `ratio` —— 上面 10% 封顶检查用的就是这个名字，
    # 覆盖掉会让 `capped_ratio` 返回软件占比而不是费用占比（3.97% → 38.82%）。
    sw_share = eng_wan / works_wan if works_wan else 0
    s.note(f"【项目类型声明】表11/12/13 按「{deal['fee_project_type']}」取系数。"
           f"软件开发费 {eng_wan:,.2f} 万 ÷ 工程费用 {works_wan:,.2f} 万 = {sw_share:.1%}，"
           + ("超过" if sw_share > 0.30 else "**未达**")
           + "表12 注3「综合类项目指项目总金额中软件开发费用 30% 以上的项目」的门槛。"
           + " ".join(deal["fee_project_type_basis"].split()))
    s.finish()
    return {"rows": rows, "total_wan": xlround(total, 2),
            "capped_ratio": ratio, "works_wan": works_wan, "budget_wan": budget}


def _how(spec: dict, base_wan: float, prof, kw: dict) -> str:
    """把算法写成评审能自己加一遍的一句话。"""
    m = spec["method"]
    if m == "rate":
        by = spec.get("max_rate_by_layout")
        if by:
            lay = kw.get("integration_layout")
            return (f"采购总额 {base_wan:,.2f} × {by[lay]:.0%}"
                    f"（{lay}：{spec['rate_range_by_layout'][lay][0]:.0%}–"
                    f"{spec['rate_range_by_layout'][lay][1]:.0%}）")
        return f"{base_wan:,.2f} × {spec['max_rate']:.1%}"
    if m == "fixed":
        return f"基价 {spec['fixed_base']} 万元/系统 × 1 系统"
    if m == "interpolate":
        t = spec["table"]
        if base_wan < t[0][0] or base_wan > t[-1][0]:
            return f"计费额 {base_wan:,.2f} 万元超出分档表，按表外规则计取"
        for (a1, b1), (a2, b2) in zip(t, t[1:]):
            if a1 <= base_wan <= a2:
                base = prof.interpolate(base_wan, t)
                f, why = prof.fee_project_factor(
                    spec, kw.get("fee_project_type"), spec["id"])
                return (f"内插 {b1:g}+({base_wan:,.2f}−{a1:g})÷({a2:g}−{a1:g})"
                        f"×({b2:g}−{b1:g})={base:,.2f}，×{f:g}（{why}）")
    return m


def emit_summary(wb, deal: dict, pack: StandardPack, *,
                 software_wan: float, hardware_yuan: float,
                 other: dict, listed: dict,
                 fp_wan: float = 0.0, eff_wan: float = 0.0,
                 fp_systems_summary: list[dict] | None = None,
                 hardware_breakdown: list[tuple] | None = None,
                 pending_n: int = 0) -> float:
    """Z00 —— 总投资估算，科目按 三.(一)1 的预算构成。"""
    subj = pack.data["budget_subjects"]
    s = GovSheet(
        wb, "00_总表",
        title=f"{deal['project_name']} 投资估算总表",
        subtitle=(f"编制依据：{pack.data['standard_doc']}（{pack.data['doc_no']}）　"
                  f"预算构成：{subj['statement']}"
                  f"（p.{subj['citation']['page']} {subj['citation']['section']}）"),
        # 列组与序号规则**照客户给的模板**：序号 / 费用名称 / 投资估算(元) / 占比 / 备注。
        # 序号自己填「一 /（一）/ 1」且逐节重新起编，所以关掉自动序号。
        # 计价方法与标准条款并入备注 —— 各明细表仍保留独立的「标准条款」列，
        # 逐行追溯性不丢；总表是汇总件，形式服从甲方模板。
        columns=[
            Col("序号", "text", width=8),
            Col("费用名称", "text", width=34),
            Col("投资估算(元)", "money", width=18, sum=True, role="amount"),
            Col("占比", "pct", width=10),
            Col("备注", "text", width=62),
        ],
        seq=False, clause_required=False, rollup=True)

    sw = xlround(software_wan * 10000, 2)
    hw = xlround(hardware_yuan, 2)
    reserve = next((r for r in other["rows"] if r["id"] == "reserve"), None)
    oth_rows = [r for r in other["rows"] if r["id"] != "reserve"]
    res_yuan = xlround(reserve["listed"] * 10000, 2) if reserve else 0.0
    ext_yuan = {k: xlround(b["amount_yuan"], 2)
                for k, b in (deal.get("external_standard_fees") or {}).items()}
    oth = xlround(sum(xlround(r["listed"] * 10000, 2) for r in oth_rows)
                  + sum(ext_yuan.values()), 2)
    grand = xlround(sw + hw + oth + res_yuan, 2)

    def sub(no, name, yuan, note=""):
        """小计行：**不进合计**。父行与明细行同时计入 =SUM() 就是双倍。"""
        # 序号显式写进「序号」列 —— group() 的首参会落到「第一个非序号列」，
        # 而本表关了自动序号，第一个非序号列是「费用名称」，
        # 被显式传的 费用名称 挡掉，首参就静默丢了。
        return s.group(name, {"序号": no, "费用名称": name,
                              "投资估算(元)": yuan,
                              "占比": yuan / grand, "备注": note})

    def leaf(n, name, yuan, note):
        return s.row({"序号": str(n), "费用名称": name, "投资估算(元)": yuan,
                      "占比": yuan / grand, "备注": note})

    top = s.group("工程总投资", {"序号": "", "费用名称": "工程总投资"})
    sub("一", "工程费用", xlround(sw + hw, 2), "三.(一)1")
    sub("（一）", "软件开发费", sw,
        f"三.(一)2.1 {deal['costing_method']}；功能点法 {fp_wan:,.2f} 万"
        f"（平台+L0 基座）+ 工作量法 {eff_wan:,.2f} 万（L1/L2）")
    for n, x in enumerate(fp_systems_summary or [], 1):
        leaf(n, x["name"], x["yuan"], f"{x['clause']} {x['method']}；{x['note']}")
    # 待核价条数按当期算 —— 原先写死「114 项」，定价补齐后待核价归零，
    # 那句话就成了送审册上的假陈述。声明性文字里的数字一律由生成端计算。
    sub("（二）", "硬件设备购置费", hw,
        "三.(一)2.5 表7 参考品牌型号≥3 个 + 价格依据"
        + (f"；另有 {pending_n} 项待核价设备单列，未计入" if pending_n else
           "；本次无待核价设备，全部已定价并计入"))
    for n, (name, amt) in enumerate(hardware_breakdown or [], 1):
        leaf(n, name, xlround(amt, 2), "三.(一)2.5 表7")
    sub("二", "其他费用", oth,
        "三.(一)2.6　扣除系统集成费后的其他几项费用之和不得超过预算总额的 10%")
    n = 0
    for r in oth_rows:
        n += 1
        leaf(n, r["name"], xlround(r["listed"] * 10000, 2),
             f"三.(一)2.6 标准上限 {r['cap']:,.2f} 万元；"
             + ("未超上限" if r["ok"] else "⚠ 超上限，须核减或说明"))
    for name, blk in (deal.get("external_standard_fees") or {}).items():
        n += 1
        leaf(n, name, ext_yuan[name],
             f"{blk['clause']} 本标准未给费率表，按柳州市相关文件标准执行；待补依据")
    if reserve:
        sub("三", "预备费", res_yuan,
            "三.(一)2.9　独立于 2.6「其他费用」，不计入其 10% 封顶")
        leaf(1, "项目预备费", res_yuan,
             f"三.(一)2.9 工程费用 × 2%，标准上限 {reserve['cap']:,.2f} 万元；"
             + ("未超上限" if reserve["ok"] else "⚠ 超上限，须核减或说明"))

    trow = s.total("合计", expect={"投资估算(元)": grand})
    # 顶部的「工程总投资」指向底部已自验的合计行 —— **一个真相源**。
    # 源表把总投资放在最上方，形式照搬；但不另算一遍，否则两处会分叉。
    col = s._by_name["投资估算(元)"]
    from openpyxl.utils import get_column_letter as _cl
    c = s.ws.cell(top, col, f"={_cl(col)}{trow}")
    c.number_format = gs.FMT["money"]
    c.alignment = gs.RIGHT
    c.font, c.fill, c.border = gs.BOLD_FONT, gs.TOTAL_FILL, gs.BORDER
    p = s.ws.cell(top, s._by_name["占比"], 1.0)
    p.number_format, p.alignment = gs.FMT["pct"], gs.RIGHT
    if fp_wan:
        s.note(f"【计价方法声明】标准原文：「{deal['costing_method_quote']}」"
               f"（p.10 {deal['costing_method_clause']}）。本项目**按子系统分别择用**："
               f"平台与 L0 支撑基座按 2.1.2 功能点估算法计列 {fp_wan:,.2f} 万元；"
               f"L1 知识工程/数据建设、L2 专业模型/行业智能体按 2.1.1 工作量估算法"
               f"计列 {eff_wan:,.2f} 万元。判据为该方法测不测得到该项工作 —— "
               f"功能点法测的是数据功能与事务功能，模型训练、语料治理、提示工程"
               f"不产生 ILF/EIF/EI/EO/EQ，按定义测不到。两段互不重叠。")
    else:
        s.note(f"【计价方法声明】软件开发费按 {deal['costing_method']} 编制。"
               f"标准原文：「{deal['costing_method_quote']}」"
               f"（p.10 {deal['costing_method_clause']}）。")
    s.note("【上限语义】" + pack.data["upper_limit_semantics"]["statement"]
           + f"（p.{pack.data['upper_limit_semantics']['citation']['page']}）。")
    s.finish()
    return grand


def emit_review(wb, *, sw: dict, hw: dict, other: dict, fp_cross: dict,
                deal: dict, grand: float,
                listed: dict | None = None) -> None:
    """Z04 —— 复核意见与交叉校验。"""
    s = GovSheet(
        wb, "01_复核意见",
        title="编制复核意见 —— 需业务侧处理的事项",
        subtitle="本表由工具自动生成，逐条给出依据条款与影响金额。"
                 "「否决」项不处理无法通过财评；「提示」项须在编制说明中回应。",
        columns=[
            Col("级别", "text", width=10),
            Col("事项", "text", width=30),
            Col("说明", "text", width=64),
            Col("影响金额(元)", "money", width=15),
            Col("处置", "text", width=34),
        ],
        clause_required=True)

    for it in sw["issues"]:
        hard = it["kind"] in ("总表与明细对不上", "总表有、明细无", "明细有、总表无")
        s.row({"级别": gs.raw("否决" if hard else "提示"), "事项": it["kind"],
               "说明": f"{it['system']}{' / ' + it['stage'] if it['stage'] != '-' else ''}：{it['detail']}",
               "影响金额(元)": it.get("delta_yuan"),
               "处置": ("以明细表为准修正总表，或说明差额来源"
                        if hard else "核对源表")},
              clause="三.(一)2.1.1 表1" if not hard else "三.(一)1")

    if hw["pending"]:
        n = len(hw["pending"])
        s.row({"级别": gs.raw("否决"), "事项": "硬件设备未定价",
               "说明": (f"{n} 条设备在源清单中数量非零但无单价，"
                        f"已在【甲附·硬件设备清单】「待核价设备」单列、未计入合计。"
                        f"定价后硬件购置费、系统集成费基数、预备费基数均须重算。"),
               "影响金额(元)": None,
               "处置": "补参考品牌型号≥3 个及价格依据"},
              clause="三.(一)2.5 表7 注3")

    for r in other["rows"]:
        if not r["ok"]:
            s.row({"级别": gs.raw("否决"), "事项": f"{r['name']}超上限",
                   "说明": f"本次计列 {r['listed']:,.2f} 万元，标准上限 {r['cap']:,.2f} 万元",
                   "影响金额(元)": xlround((r["listed"] - r["cap"]) * 10000, 2),
                   "处置": "核减至上限，或按标准说明突破原因"},
                  clause="三.(一)2.6～2.9")

    fpc = fp_cross
    if fpc.get("fp_scope_source_wan"):
        gap = fpc["fp_scope_source_wan"] / fpc["fp_scope_wan"] if fpc["fp_scope_wan"] else 0
        s.row({"级别": gs.raw("否决"), "事项": "功能点法段与源表估算差距",
               "说明": (f"平台 + L0 支撑基座按功能点法计得 {fpc['fp_scope_wan']:,.2f} 万元"
                        f"（{fpc['fp_ufp']:,} 功能点 × ¥{fpc['per_fp']:.2f}/功能点），"
                        f"而源表按工作量法对同一范围列 {fpc['fp_scope_source_wan']:,.2f} 万元，"
                        f"相差 {gap:.1f} 倍。这一段功能点法本来适用，差距不来自方法，"
                        f"来自功能描述不足：{fpc['untyped']} 条基本过程是能力罗列"
                        f"（如「用户管理、员工管理、顾客管理」），数不出基本过程，"
                        f"其中 {fpc['untyped_base']} 条在 L0 基座。"),
               "影响金额(元)": xlround((fpc["fp_scope_source_wan"]
                                        - fpc["fp_scope_wan"]) * 10000, 2),
               "处置": "业务侧补功能描述后重跑，或按工作量法另行举证"},
              clause="三.(一)2.1.2")
    s.row({"级别": gs.raw("提示"), "事项": "工作量法段的测算依据",
           "说明": (f"L1 知识工程/数据建设、L2 专业模型/行业智能体按工作量估算法计列，"
                    f"人月合计 {fpc['effort_scope_mm']:,.1f}。标准未规定人月如何估算，"
                    f"财评通常会追问依据。该段无功能点对照可比 —— 这正是选用工作量法的"
                    f"理由（功能点法测不到模型训练、语料治理与提示工程），"
                    f"但也意味着人月必须自证。"),
           "影响金额(元)": None,
           "处置": "补逐子系统工作量分解说明（按语料条数/模型数/评测轮次等可核查量纲）与同类项目对标"},
          clause="三.(一)2.1.1")

    # ---- 柳州特有的两条重复计列约束 ----
    impl = {sn: b["stages"].get("实施部署", {}).get("amount_wan", 0)
            for sn, b in sw["systems"].items()}
    plat = next((v for k, v in impl.items() if "学前教育" in k), 0)
    sites = deal.get("implementation_sites")
    if plat and sites:
        per = plat * 10000 / sites
        s.row({"级别": gs.raw("提示"), "事项": "实施部署费可能不足",
               "说明": (f"平台实施部署 {plat:,.2f} 万元 ÷ {sites} 个实施点 = "
                        f"¥{per:,.0f}/点。表1 注2 明文「属于统一开发部署的软件…"
                        f"**各实施点只计算部署实施费用**」—— 即实施点的部署费是"
                        f"标准允许计列的。若园平台需逐园开户、配置、培训、验收，"
                        f"当前取值明显不足；若为多租户一次部署，则应在编制说明中写明，"
                        f"否则财评会反过来问「205 个园怎么只有 {plat:,.1f} 万实施费」。"
                        f"⚠️ 本条方向是**我方少计**，不处理不会被核减，但等于主动放弃"
                        f"标准允许计列的费用。"),
               "影响金额(元)": None,
               "处置": "确认园平台是多租户一次部署还是逐园实施；后者须按实施点重新测算"},
              clause="三.(一)2.1.1 表1 注2")

    listed = listed or {}
    sec = [("第三方测试（检测/测评）费", listed.get("third_party_test", 0)),
           ("信息安全等级保护测评费", listed.get("dengbao_l3", 0))]
    sec += [(k, v["amount_yuan"] / 10000)
            for k, v in (deal.get("external_standard_fees") or {}).items()]
    tot_sec = sum(v for _, v in sec)
    s.row({"级别": gs.raw("否决"), "事项": "四项测评无工作内容清单，无法证明不重复",
           "说明": (f"{'、'.join(f'{k} {v:,.2f} 万' for k, v in sec)}，合计 "
                    f"{tot_sec:,.2f} 万元。标准 2.7 要求「软件测评（测试）、"
                    f"风险评估、信息安全等级保护测评、密码测评等工作中存在重复"
                    f"工作内容的，重复部分只计算一次费用」。四项目前**均只有一个金额、"
                    f"无检测内容清单** —— 既证明不了重复，也证明不了不重复；"
                    f"而这四项天然重叠（都含漏洞扫描、配置核查、出具报告）。"),
           "影响金额(元)": None,
           "处置": "商务/技术侧提供四项各自的检测内容清单并标出交叉项；"
                   "与密码费、风评费的「待补依据」一并处理"},
          clause="三.(一)2.7")

    # taxonomy 里声明了但无内容的系统 —— **不进 Z00，进这里**。
    # 与「待核价硬件」不是同一回事：那些有名称、型号、数量，确定在建设范围内，
    # 只缺单价，所以「列而不计」；本系统连建什么都没有，在总表放一行 ¥0，
    # 评审问「这到底建不建」我们答不上来。
    tax = yaml.safe_load((_ROOT / "bom" / "taxonomy.yaml").read_text(encoding="utf-8"))
    for line, ls in (tax.get("product_lines") or {}).items():
        for sysname, sp in (ls.get("systems") or {}).items():
            sp = sp if isinstance(sp, dict) else {}
            if sp.get("status") != "待补内容":
                continue
            s.row({"级别": gs.raw("否决"), "事项": f"{sysname}：声明了但无建设内容",
                   "说明": (f"「{sysname}」在产品架构（bom/taxonomy.yaml）中列为 "
                            f"{line} 下的 {sp.get('layer','')} 系统，"
                            f"但源估算表无对应科目，当前 0 条条目、0 元，"
                            f"**未计入任何金额**。"
                            f"与【甲附】的待核价设备不同：那些有名称、型号、数量，"
                            f"确定在建设范围内、只缺单价，故「列而不计」；"
                            f"本系统的建设内容尚未填写，故无工作量可计。"
                            f"**业务侧已确认属本次建设范围**（2026-08-08），"
                            f"已在【甲本】00_总表 按「列而不计」列出 ¥0 行。"
                            + " ".join(str(sp.get("pending_note", "")).split())),
                   "影响金额(元)": None,
                   "处置": "产品侧补建设内容（本体的实体/关系/规则形式化范围）"
                           "与工作量后重新计列；填写前本项金额为 0，"
                           "总投资不含该部分"},
                  clause="三.(一)1")

    s.note("说明：本项目按子系统分别择用计价方法 —— 平台与 L0 支撑基座走 2.1.2 "
           "功能点估算法，L1/L2 走 2.1.1 工作量估算法，两段互不重叠。"
           "方法分配与逐条理由见 deals/<id>/costing-method-split.yaml。")
    s.finish()


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True, type=Path)
    ap.add_argument("--out", type=Path,
                    help="缺省为 <deal 目录>/out —— 出表是商机的产物，跟着 deal 走。"
                         "放在项目根上，多一个商机就互相覆盖")
    ap.add_argument("--listed", type=Path,
                    help="本次计列金额 yaml；缺省用源总表的数")
    a = ap.parse_args()

    gs.assert_roles_parseable()
    deal = yaml.safe_load(a.deal.read_text(encoding="utf-8"))
    root = a.deal.parent.parent.parent
    pack = StandardPack.load(root / deal["baseline"])

    # 计价方法分配（第三层决策）：哪些子系统走功能点法、哪些走工作量法
    split_path = a.deal.parent / "costing-method-split.yaml"
    fp_systems: list[dict] = []
    effort_only: set[str] = set()
    if split_path.exists():
        split = yaml.safe_load(split_path.read_text(encoding="utf-8"))
        effort_only = {x["name"] for x in split["effort_method"]["systems"]}
        bl = _latest_baseline(root, pack)
        prof = get_profile(pack.formula_profile)
        # 基准给的是 **UFP**（计数，与商机无关）；类别因子/生产率是**商机决策**，
        # 在这里施加，不在基准里。基准的 cost 用的是包默认值，不能直接拿来用。
        fpset = prof.resolve_fp_settings(pack, deal.get("fp_method_settings"))
        _in_fp = [x for x in bl["software_dev"]["systems"]
                  if x["system"] not in effort_only]
        # 复用度逐子系统解析（第一层 maturity → 第二层映射 → 第三层覆盖）。
        # 从前这里不传 reuse_level，fp_cost 默认「新建」=1.0 —— 一个有默认值的
        # 参数不传，不会报错，只会让整段永远按最保守档计价而清单上看不出来。
        ru = resolve_reuse(root, pack, deal, [x["system"] for x in _in_fp],
                           detail=bl.get("detail"))
        fp_systems = []
        for x in _in_fp:
            info = ru[x["system"]]
            at = _sys_app_type(root, x["system"])
            # **逐组算、再求和。** 同一子系统内复用度可以按功能模块分档
            # （注2「已有软件系统或功能模块」），各组各乘各的。
            # 拿整块 UFP 配一个档位算，混档的子系统就少算/多算一截，
            # 而汇总表上看不出来。
            # **取整一次，在子系统这一层。** 标准的「功能点数」是针对一块开发
            # 内容的（表3 公式里因子乘的是该块 UFP），复用度分档是我们内部的
            # 计算手段，不是标准的计价单元。逐档各取一次整，会在人月那一步
            # 攒出差（市平台实测 9.50+3.44+3.54=16.48 vs 一次取整 16.49，
            # 折 170 元），而那个差没有标准依据可讲。
            cat_v = fpset["app_type"][at][0]
            grps = [{**g, "contrib": g["ufp"] * cat_v * g["reuse_factor"]}
                    for g in info["groups"]]
            c = prof.fp_cost(sum(g["ufp"] * g["reuse_factor"] for g in grps),
                             pack=pack, app_type=at, settings=fpset,
                             reuse_level="低")      # 复用已在上一步逐档乘完
            tot_fp, tot_mm, tot_yuan = c["fp"], c["effort_man_months"], c["cost"]
            fp_systems.append({
                **x, **info, "groups": grps,
                "app_type": at, "app_type_factor": fpset["app_type"][at][0],
                "app_type_basis": fpset["app_type"][at][1],
                "productivity": c["productivity"],
                "man_month_rate": c["man_month_rate"],
                "fp": tot_fp, "effort_man_months": tot_mm, "cost": tot_yuan,
                "reuse": info["reuse_factor"],
            })
        got = {x["system"] for x in bl["software_dev"]["systems"]}
        stray = effort_only & got
        if stray:
            raise ValueError(
                f"基准里出现了本该走工作量法的系统 {sorted(stray)} —— "
                f"BOM 里这些条目的 class 应为 SOFTWARE_EFFORT 而非 SOFTWARE_FP，"
                f"否则同一段工作会被功能点法与工作量法各计一遍")

    sw = read_software(root / deal["sources"]["software"], pack,
                       source_alias(root / "bom"))
    # 硬件的源**显式声明**，不靠「文件在不在」来切。
    # 靠文件存在性切换的话，配置文件被改名/挪走会静默回落到源工作簿 ——
    # 两条链路的数不一样，而且不会有任何地方报错。
    hw_src = deal["sources"].get("hardware_source", "workbook")
    if hw_src not in ("device-config", "workbook"):
        raise ValueError(f"sources.hardware_source={hw_src!r} 未知；"
                         f"可选：device-config | workbook")
    hw_from_config = hw_src == "device-config"
    if hw_from_config:
        cfg_p = a.deal.parent / "device-config.yaml"
        if not cfg_p.exists():
            raise SystemExit(
                f"deal.yaml 声明 hardware_source: device-config，"
                f"但 {cfg_p} 不存在。\n"
                f"  先跑 device_config_build.py 引导，或把声明改回 workbook。"
                f"**不自动回落** —— 两条链路的数不一样。")
        hw = read_device_config(root, a.deal.parent)
    else:
        hw = read_hardware(root / deal["sources"]["hardware"],
                           deal["sources"]["hardware_price_column"])

    hw_yuan = sum(r["total"] for r in hw["priced"])

    # 计列额是**商机级**的，长在工具里就会被所有商机共用一份。
    # 优先读 deals/<id>/listed-fees.yaml；没有才回落到这份历史默认值。
    _lf = a.deal.parent / "listed-fees.yaml"
    listed = (yaml.safe_load(a.listed.read_text(encoding="utf-8")) if a.listed
              else yaml.safe_load(_lf.read_text(encoding="utf-8")) if _lf.exists()
              else {"integration": 171.8238, "design": 80.0, "supervision": 40.0,
                    "third_party_test": 25.0, "dengbao_l3": 10.0, "reserve": 100.0})

    d = deal["doc"]
    out = a.out or (a.deal.parent / "out")
    prefix, ver, date, status = d["prefix"], d["version"], d["date"], d["status"]
    _archive_previous(out, date, ver)

    # 所有 GovSheet 表都往路由器里造，由它按 BOOKS 分派到甲/乙/丙三本。
    globals()["_ROOT"] = root
    # 逐末级交付物的**工作量测算依据**。BOM 里每条 SOFTWARE_EFFORT 都带
    # coauthor_status/coauthor_advice，132 条全是「★待补工作量依据」——
    # 把这个状态接到 05 上，评审才看得见「这一段的工作量还没有测算依据」。
    _eb: dict[str, dict] = {}
    import glob as _glob
    for _f in _glob.glob(str(root / "bom" / "items" / "*.yaml")):
        for _i in yaml.safe_load(Path(_f).read_text(encoding="utf-8"))["items"]:
            if _i.get("class") != "SOFTWARE_EFFORT":
                continue
            _b = _i.get("effort_basis") or {}
            _eb[_i["name"]] = {
                "unit": _b.get("unit", ""), "qty": _b.get("qty"),
                "per_unit_mm": _b.get("per_unit_mm"),
                # man_months 漏了这一行，05 就静默回落到源表的旧人月，
                # 而勾稽校验会报「分摊环节漏了或重了」—— 错在取数，不在分摊。
                "man_months": _b.get("man_months"),
                "basis": _b.get("basis", ""),
                # 和 man_months 同一类坑：这里漏一个键，05 对应的那一列就
                # **整列静默为空**，表还是出得来。counted_from 是本体工程
                # 那 24 条最实的举证（要素明细，426~1447 字），漏了看不出来。
                "counted_from": _b.get("counted_from", ""),
                "status": _i.get("coauthor_status", ""),
                "advice": _i.get("coauthor_advice", "")}
    # ---- 工作量法子系统的末级行：**从 BOM 重建，不靠名字去匹配源表** ----
    # 原来是拿源估算表的末级行，按**名字**去 `_eb` 里认领 effort_basis。
    # 只要 BOM 的条目名与源表不一致（改名、拆细、换来源），认领就失败：
    # 05 少列那些行、人月少算，而 04 由 BOM 汇总驱动、数是全的 ——
    # 两张表当场差一截。实测 C4/C6 按飞书共创版覆盖后改了条目名，
    # 差 108.02 人月，由那道「逐交付物 vs 五阶段」的勾稽逮住。
    #
    # 名字是**展示字段**，不是键。用它做连接，任何一次改名都会静默丢数据。
    # BOM 才是工作量法的取数源（effort_from_bom），末级行直接由它生成。
    _by_sys_items: dict[str, list[dict]] = {}
    for _f in _glob.glob(str(root / "bom" / "items" / "*.yaml")):
        for _i in yaml.safe_load(Path(_f).read_text(encoding="utf-8"))["items"]:
            if _i.get("class") == "SOFTWARE_EFFORT" and _i.get("status") != "deprecated":
                _by_sys_items.setdefault(_i["path"]["system"], []).append(_i)
    for _sn2, _b2 in sw["systems"].items():
        if _sn2 in effort_only and deal.get("effort_from_bom", True):
            _b2["leaves"] = [
                {"system": _sn2, "subsystem": (_i2["path"].get("l1") or ""),
                 "func": (_i2["path"].get("l2") or ""), "name": _i2["name"],
                 "description": _i2.get("description") or "",
                 "man_months": None, "unit_price_wan": None,
                 "amount_wan": None, "source_amount_wan": None,
                 # **直接用该条目自己的 effort_basis**，不再按名字去 `_eb` 查。
                 # `_eb` 是 name→basis 的字典，同名条目只会留下最后一条 ——
                 # 实测「数据工程 · 用户健康档案-个人维度基础指标」有两条
                 # （10.50 与 12.00 人月），查表后两行都拿到 12.00，
                 # 05 比 04 多 1.50 人月。末级行既然已由 BOM 条目生成，
                 # basis 就在手边，绕一次名字查表纯属自找。
                 "effort_basis": {
                     "unit": (_i2.get("effort_basis") or {}).get("unit", ""),
                     "qty": (_i2.get("effort_basis") or {}).get("qty"),
                     "per_unit_mm": (_i2.get("effort_basis") or {}).get("per_unit_mm"),
                     "man_months": (_i2.get("effort_basis") or {}).get("man_months"),
                     "basis": (_i2.get("effort_basis") or {}).get("basis", ""),
                     "counted_from": (_i2.get("effort_basis")
                                      or {}).get("counted_from", ""),
                     "status": _i2.get("coauthor_status", ""),
                     "advice": _i2.get("coauthor_advice", "")}}
                for _i2 in _by_sys_items.get(_sn2, [])]
        else:
            for _lf in _b2.get("leaves") or []:
                if _lf.get("name") in _eb:
                    _lf["effort_basis"] = _eb[_lf["name"]]
    # ---- 工作量法：**由 BOM 的逐交付物测算驱动阶段人月** ----
    # 从前是反的：源表给阶段人月 → 04 展示 → 05 的末级「恰好」加总等于编码阶段，
    # 于是 05 成了 04 的附属而不是来源。标准表1 的结构正相反：子系统/功能/子模块
    # 挂在「三、软件开发（编码）」之下，建设内容是推导的起点。
    # 现在：05 逐交付物（量纲 × 单位工作量 → 人月）→ 按 WBS 活动模板分摊到五阶段
    # → 04 施加因子（人工成本 × 风险 × 复用）→ 金额。
    if effort_only and deal.get("effort_from_bom", True):
        _tpl = yaml.safe_load(
            (root / "bom" / "effort-wbs.yaml").read_text(encoding="utf-8"))["templates"]
        _by_sys: dict[str, float] = {}
        _bom_items: dict[str, list[dict]] = {}
        _miss: list[str] = []
        for _f in _glob.glob(str(root / "bom" / "items" / "*.yaml")):
            for _i in yaml.safe_load(Path(_f).read_text(encoding="utf-8"))["items"]:
                if _i.get("class") != "SOFTWARE_EFFORT":
                    continue
                _b = _i.get("effort_basis") or {}
                if not _b.get("man_months"):
                    _miss.append(_i["name"]); continue
                _by_sys[_i["path"]["system"]] = (
                    _by_sys.get(_i["path"]["system"], 0.0) + float(_b["man_months"]))
                _bom_items.setdefault(_i["path"]["system"], []).append(_i)
        if _miss:
            raise gs.GovSheetError(
                f"{len(_miss)} 条工作量法条目没有 effort_basis.man_months，"
                f"无法自下而上汇总：{_miss[:5]}…\n"
                f"  工作量法段现由 BOM 的逐交付物测算驱动（deal.effort_from_bom）。"
                f"缺一条就少一条，而少的那条不会有任何地方报错 —— 故直接拒绝出表。")
        # **BOM 有工作量、但计价方法分配里没登记 → 拒绝出表。**
        # 从前这里和下面的「源表无此系统」合并成一句 `continue`，于是新加的
        # 子系统（本体工程 61.62 人月）被静默丢掉，总投资一分不变、无任何提示。
        _unreg = sorted(set(_by_sys) - set(effort_only))
        if _unreg:
            raise gs.GovSheetError(
                f"BOM 里这些子系统有 SOFTWARE_EFFORT 工作量，但 "
                f"costing-method-split.yaml 的 effort_method.systems 没登记：{_unreg}。\n"
                f"  不登记就不会计入工作量法段，而少的那一段不会有任何地方报错。")
        for _sn, _mm_tot in _by_sys.items():
            # **源表没有的子系统就地建条目**，而不是跳过。源估算表是历史报价，
            # 新增子系统（本体工程）本来就不在里面；跳过 = 静默归零。
            if _sn not in sw["systems"]:
                sw["systems"][_sn] = {"stages": {}, "leaves": [
                    {"system": _sn,
                     "subsystem": (_i2["path"].get("l1") or ""),
                     "func": (_i2["path"].get("l2") or ""),
                     "name": _i2["name"],
                     "description": _i2.get("description") or "",
                     "man_months": None, "unit_price_wan": None,
                     "amount_wan": None, "source_amount_wan": None}
                    for _i2 in _bom_items.get(_sn, [])]}
                for _lf2 in sw["systems"][_sn]["leaves"]:
                    if _lf2["name"] in _eb:
                        _lf2["effort_basis"] = _eb[_lf2["name"]]
            _t = _tpl.get(_sn)
            if not _t:
                raise gs.GovSheetError(
                    f"{_sn} 有交付物测算但 effort-wbs.yaml 无活动模板，"
                    f"无法把人月分摊到表1 的五个阶段")
            _stw: dict[str, float] = {}
            for _a in _t["activities"]:
                _st2 = _a["stage"]
                if isinstance(_st2, str):
                    _stw[_st2] = _stw.get(_st2, 0.0) + _a["pct"]
                else:
                    for _k, _w in _st2.items():
                        _stw[_k] = _stw.get(_k, 0.0) + _a["pct"] * _w
            _rates = {k: v["value"] for k, v in
                      (pack.data["effort_method"]["stage_rates"] or {}).items()}
            _new = {}
            for _st3, _w2 in _stw.items():
                _mm = xlround(_mm_tot * _w2, 4)
                _old = (sw["systems"][_sn]["stages"].get(_st3) or {})
                _new[_st3] = {
                    "man_months": _mm,
                    "unit_price_wan": _rates[_st3],
                    "amount_wan": xlround(_mm * _rates[_st3], 4),
                    "source_amount_wan": _old.get("source_amount_wan"),
                    "std_rate": _rates[_st3],
                }
            sw["systems"][_sn]["stages"] = _new
    # 功能点法交叉校验 —— **必须放在阶段人月由 BOM 重建之后**。
    # 原来在读 listed 那一段就调用了，取到的是重建**之前**的阶段值：
    # 丁本第 9 条印着「人月合计 779.0」，而甲/乙用的是 757.55，差 21.26 人月。
    # 甲乙没错（05↔04 的勾稽守卫会拦住不一致），错的是丁本这一句 ——
    # 一份自查册报着和送审册不同的数，比不报更容易把人带偏。
    fp_cross = _fp_crosscheck(root, pack, sw, effort_only, fp_systems)

    dnl = resolve_direct_non_labor(pack, deal)
    ef = resolve_effort_factors(root, pack, deal, sw, effort_only)
    # **把两个因子施加到金额上。** 下游（eff_wan / 乙本04 / 甲本 / 总表）
    # 全部读 amount_wan，在这里改一处即可；分散到各出表点改，必然漏一处，
    # 而漏掉的那处金额看起来完全正常。
    for _sn, _blk in (sw.get("systems") or {}).items():
        if _sn not in effort_only:
            continue
        for _st, _v in (_blk.get("stages") or {}).items():
            # **不覆写 unit_price_wan。** 它的含义是「人工成本」，下游按这个
            # 含义在用：05_工作量法功能明细 直接印它、「偏离表1 标准单价」的
            # 检查拿它比表1。覆写含义而不改下游，05 就会印出一个表1 里没有的
            # 单价（实测 1.264081）且不解释来源，04 还会把我们自己调的价
            # 报成「偏离表1」。另起字段，含义不动。
            _r = ef["stage"].get((_sn, _st), {}).get("factor", 1.0)
            _v["risk_factor"] = ef["risk"]
            _v["reuse_factor"] = _r
            _v["effective_price_wan"] = xlround(
                _v["unit_price_wan"] * ef["risk"] * _r, 6)
            _v["amount_wan"] = xlround(
                _v["man_months"] * _v["effective_price_wan"], 4)
        # **末级明细也要跟着乘。** 05_工作量法功能明细 是「编码」阶段的逐末级
        # 展开，与阶段表有勾稽校验；只乘阶段不乘末级，两边当场差一个因子
        # （实测 58.70 万）。这类「改了一处忘了另一处」正是那道校验的用处。
        _cr = ef["stage"].get((_sn, "软件开发（编码）"), {}).get("factor", 1.0)
        for _lf in (_blk.get("leaves") or []):
            if _lf.get("unit_price_wan") is not None:
                _lf["risk_factor"] = ef["risk"]
                _lf["reuse_factor"] = _cr
                _lf["effective_price_wan"] = xlround(
                    _lf["unit_price_wan"] * ef["risk"] * _cr, 6)

    # ---- 计价单元 = **建设对象**，取整只在这一层做一次 ----
    # 04 的归集视角是「建了什么、每项多少钱」，行就是建设对象；那么金额的
    # 取整层级必须跟着下移到对象，否则表里会有两套数：按对象行加总一个数、
    # 按阶段列加总另一个数（实测差 16 元）。同一个 人月×单价 矩阵按行舍入
    # 与按列舍入本来就不等 —— 不是谁算错了，是必须**选定一个方向**。
    # 选对象方向：它是 05 逐交付物测算的直接产物，阶段是 WBS 模板分摊出来的
    # 派生容器。派生量不该反过来定权威值。
    _rates_o = {k: v["value"] for k, v in
                (pack.data["effort_method"]["stage_rates"] or {}).items()}
    _wbs_o = _wbs_roles(root)
    for _sn, _blk in (sw.get("systems") or {}).items():
        if _sn not in effort_only or not _blk.get("stages"):
            continue
        _pct = {st: ((_wbs_o.get(_sn) or {}).get(st) or {}).get("pct", 0.0)
                for st in STAGES}
        _acc = 0.0
        for _lf in (_blk.get("leaves") or []):
            _mm = (_lf.get("effort_basis") or {}).get("man_months")
            if not _mm:
                continue
            _rf = (ef.get("obj") or {}).get(
                (_sn, _lf["name"]), {}).get("factor", 1.0)
            _acc += xlround(sum(
                float(_mm) * _pct.get(_st, 0.0) * _rates_o.get(_st, 0.0)
                for _st in STAGES) * ef["risk"] * _rf, 4)
        # 没有逐对象测算的系统（源表直给阶段人月）回落到阶段口径 ——
        # 不静默：回落会被 05 的「141 条全部已填」检查暴露出来。
        _blk["obj_amount_wan"] = xlround(_acc, 4) if _acc else sum(
            v["amount_wan"] for v in _blk["stages"].values())

    # **必须在施加因子之后再算。** 放在前面算的是未调因子的数，与
    # emit_software 返回的 eff_wan 差一个因子，那道校验会当场报
    # （实测 1426.08 vs 1304.47）。
    sw_effort_wan = sum(
        b.get("obj_amount_wan",
              sum(v["amount_wan"] for v in b["stages"].values()))
        for sn, b in sw["systems"].items()
        if (not effort_only or sn in effort_only) and b.get("stages"))
    tax = yaml.safe_load((root / "bom" / "taxonomy.yaml").read_text(encoding="utf-8"))
    wb = BookRouter(BOOKS)
    fp_wan, eff_wan = emit_software(wb, sw, deal, pack, fp_systems, effort_only,
                                    fpset if split_path.exists() else None,
                                    fp_detail=(bl.get("detail") if fp_systems else None),
                                    dnl=dnl,
                                    deal_counting=deal.get("counting_method"),
                                    ef=ef)
    if effort_only:
        emit_effort_reconcile(wb, root, sw, effort_only)
    if abs(eff_wan - sw_effort_wan) > 0.01:
        raise gs.GovSheetError(
            f"工作量法金额 {eff_wan} 与预算 {sw_effort_wan} 不符")
    sw_wan = xlround(fp_wan + eff_wan, 4)

    emit_software_summary(wb, tax, pack, fp_systems=fp_systems, sw=sw,
                          effort_only=effort_only, expect_wan=sw_wan, dnl=dnl)

    # 甲附 硬件 —— 直通件（甲方原格式脱敏另存），不由 GovSheet 造，故不进路由
    z02 = out / gs.doc_name(prefix, PASSTHROUGH_BOOK["no"],
                            PASSTHROUGH_BOOK["topic"], ver, date, status)
    if hw_from_config:
        # 由引擎从第二层配置生成 —— 不再脱敏直通。
        # 成本/渠道/毛利/供应商在建 bom/devices 时就没读进来，
        # 而不是读进来再删；脱敏最危险的失败是「以为删了」。
        hwb = gs.new_workbook()
        hw_total, n_pending = emit_hardware_detail(hwb, hw, pack, root, deal)
        gs.save(hwb, z02)
    elif deal["sources"].get("hardware_passthrough"):
        hw_total, n_pending = passthrough_hardware(
            root / deal["sources"]["hardware"], z02,
            a.deal.parent / "hardware-sanitize.yaml", hw, pack)
    else:
        hwb = gs.new_workbook()
        hw_total, n_pending = emit_hardware(hwb, hw, deal, pack)
        gs.save(hwb, z02)

    hw_sum = emit_hardware_summary(wb, hw, pack, hw_total, root)

    # 其他费用
    other = emit_other_fees(wb, deal, pack, software_wan=sw_wan,
                            hardware_yuan=hw_total, listed=listed)

    # 总表
    # 软件开发费在总表下**按产品线/层次归并**，不平铺子系统 ——
    # 源表总表在软件开发费下就是 6 行（平台 / L0 基座 / 知识工程 / 数据建设 /
    # 专业模型 / 智能体）。子系统明细本来就在 Z01，总表平铺 18 行既多又乱，
    # 而且按金额排序会把同一产品线的行拆散。
    _bucket = _make_bucket(tax)

    agg = {}
    for x in (fp_systems or []):
        k = _bucket(x["system"])
        acc = agg.setdefault(k, {"yuan": 0.0, "n": 0, "ufp": 0, "mm": 0.0,
                               "method": "功能点估算法",
                               "clause": "三.(一)2.1.2 表3"})
        acc["yuan"] += x["cost"]
        acc["n"] += 1
        acc["ufp"] += x["ufp"]
    for sn, b in sw["systems"].items():
        if sn not in effort_only or not b["stages"]:
            continue
        k = _bucket(sn)
        acc = agg.setdefault(k, {"yuan": 0.0, "n": 0, "ufp": 0, "mm": 0.0,
                               "method": "工作量估算法",
                               "clause": "三.(一)2.1.1 表1"})
        acc["yuan"] += b.get("obj_amount_wan", sum(
            v["amount_wan"] for v in b["stages"].values())) * 10000
        acc["n"] += 1
        acc["mm"] += sum(v["man_months"] for v in b["stages"].values())

    # 已确认属本次建设范围、但内容尚未填写的系统 —— **列而不计**：
    # 列出来是为了证明它不是被漏掉的（与【甲附】的 114 条待核价设备同一处理），
    # 不计金额是因为还没有建设内容可计。备注含「待补」，
    # gov_sheet 会用蓝色斜体渲染，与真正的 ¥0 在视觉上分得开。
    pending_sys = []
    for line, ls in (tax.get("product_lines") or {}).items():
        for sysname, sp in (ls.get("systems") or {}).items():
            sp = sp if isinstance(sp, dict) else {}
            if sp.get("status") == "待补内容":
                pending_sys.append((_bucket(sysname), sysname, line,
                                    sp.get("layer", "")))

    # 待补内容的系统与有金额的系统**合并进同一个排序**再输出 ——
    # 之前是排完序再 append，本体工程（L1）就掉到了 L2 后面。
    # 层次顺序 L0 → L1 → L2 是读表的人默认的心智模型，乱了就要回头找。
    rows_by_order: dict[tuple, dict] = {}
    for (order, name), acc in agg.items():
        note = (f"{acc['n']} 个子系统，{acc['ufp']:,} 功能点" if acc["ufp"]
                else f"{acc['mm']:,.1f} 人月")
        rows_by_order[(order, name)] = {
            "name": name, "yuan": xlround(acc["yuan"], 2),
            "method": acc["method"], "clause": acc["clause"],
            "note": f"{note}；子系统明细见 01_软件开发费汇总"}
    # 待补系统落进**已有金额的科目**时，setdefault 什么也不做 —— 那条系统
    # 就从总表上消失了，而总表是评审最先看的一张。本体工程当初能出来，
    # 只是因为它独占一个科目；新增一个落进「支撑基座」的待补系统就被吞了。
    # 「列而不计」的整个意义是让「在范围内但没计价」看得见，吞掉就等于漏列。
    pend_in_bucket: dict[tuple, list[str]] = {}
    for (order, name), sysname, line, layer in pending_sys:
        if (order, name) in rows_by_order:
            pend_in_bucket.setdefault((order, name), []).append(sysname)
            continue
        rows_by_order[(order, name)] = {
            "name": name, "yuan": 0.0,
            "method": "工作量估算法", "clause": "三.(一)2.1.1 表1",
            "note": "**待补建设内容与工作量，本次未计入金额**。"
                    "已确认属本次建设范围，内容填写后另行计列 —— "
                    "列而不计，以证明其非遗漏"}
    for key, names in pend_in_bucket.items():
        r = rows_by_order[key]
        r["note"] += (f"；另有 {len(names)} 个子系统**待补建设内容、本次未计入金额**"
                      f"（{'、'.join(names)}）—— 已确认属本次建设范围，"
                      f"列而不计，以证明其非遗漏")
    if dnl["value"] > 0:
        # 项目级加数项，不属于任何子系统科目 —— 在总表单出一行。
        # 不出这一行的话，总表分科目累加与引擎总额差一个它，而那个差
        # 表里写的是 =SUM() 活公式，评审点开就看得见。
        rows_by_order[(98, "直接非人力成本（另计）")] = {
            "name": "直接非人力成本（另计）", "yuan": xlround(dnl["value"], 2),
            "method": "2.1.2⑤", "clause": dnl["clause"],
            "note": "标准公式加数项，不属于任何子系统；"
                    + " ".join(str(dnl["basis"]).split())[:80]}
    fp_sum = [rows_by_order[k] for k in sorted(rows_by_order)]

    hw_bd = sorted(((k, sum(r["total"] for r in v))
                    for k, v in __import__("itertools").groupby(
                        sorted(hw["priced"], key=lambda r: r["园所"]),
                        key=lambda r: r["园所"])), key=lambda x: x[0])
    grand = emit_summary(wb, deal, pack, software_wan=sw_wan,
                         hardware_yuan=hw_total, other=other, listed=listed,
                         fp_wan=fp_wan, eff_wan=eff_wan,
                         fp_systems_summary=fp_sum, hardware_breakdown=hw_bd,
                         pending_n=len(hw.get("pending") or []))
    emit_review(wb, sw=sw, hw=hw, other=other, fp_cross=fp_cross,
                deal=deal, grand=grand, listed=listed)

    # 分册落盘 —— finish() 会核对「登记了的都造出来了」
    baseline = {
        "生成时间": f"{date}",
        "版本": ver,
        "deal_id": deal["deal_id"],
        "标准包": pack.pack_id,
        "fp_method_settings.sha": _sha_fp_settings(a.deal),
        "device-config.yaml.sha": _sha(a.deal.parent / "device-config.yaml"),
        "硬件源": hw_src,
        "说明": "本册由造价引擎按上列输入生成。quote_sync 用这些指纹做冲突检测 —— "
                "同步时若输入已被别人改过，指纹对不上，拒绝覆盖。",
    }
    books_out: list[tuple[str, Path]] = []
    for b, bwb in wb.finish().items():
        spec = BOOKS[b]
        f = out / gs.doc_name(prefix, b, spec["topic"], ver, date,
                              spec["status"] or status)
        stamp_baseline(bwb, baseline)
        gs.save(bwb, f, sheets_expected=spec["sheets"])
        books_out.append((b, f))

    # 丙附 —— 硬件配置录入工具。带联动活公式，不走 GovSheet 路由。
    if hw_from_config:
        import device_quote_tool as dqt
        spec = UNROUTED_BOOKS["丙附"]
        f = out / gs.doc_name(prefix, "丙附", spec["topic"], ver, date,
                              spec["status"] or status)
        dqt.emit(root, a.deal.parent, f, baseline=baseline)
        books_out.append(("丙附", f))
    books_out.append(("甲附", z02))

    # 每一册都落了盘吗 —— 登记表是唯一出处，漏一册不会有别的地方提醒
    want = set(BOOKS) | {b for b in UNROUTED_BOOKS
                         if b != "丙附" or hw_from_config}
    got = {b for b, _ in books_out}
    if want - got:
        raise gs.GovSheetError(
            f"以下分册登记了但没出：{sorted(want - got)}　"
            f"（已出：{sorted(got)}）")

    (out / "generate-report.json").write_text(json.dumps({
        "deal": deal["deal_id"], "pack": pack.pack_id,
        "costing_method": deal["costing_method"],
        "software_wan": xlround(sw_wan, 4),
        "software_fp_method_wan": fp_wan, "software_effort_method_wan": eff_wan,
        "hardware_yuan": xlround(hw_total, 2),
        "other_fees_wan": other["total_wan"], "grand_total_yuan": grand,
        "hardware_pending": n_pending,
        "other_fees_ratio": round(other["capped_ratio"], 4),
        "fp_crosscheck": fp_cross,
        "software_issues": sw["issues"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_lock(a.deal.parent / "deal.lock.json", deal, pack, sw=sw, hw=hw,
                sw_wan=sw_wan, hw_yuan=hw_total, other=other, grand=grand,
                fp_cross=fp_cross, src_software=root / deal["sources"]["software"],
                src_hardware=root / deal["sources"]["hardware"])

    print(f"送审套表 → {out}")
    print(f"  软件开发费   {sw_wan:>12,.2f} 万元　（混合口径）")
    print(f"    功能点法   {fp_wan:>12,.2f} 万元　平台 + L0 支撑基座")
    print(f"    工作量法   {eff_wan:>12,.2f} 万元　L1 知识工程/数据建设 + L2 模型/智能体")
    print(f"  硬件设备购置 {hw_total/10000:>12,.2f} 万元　待核价 {n_pending} 条未计入")
    print(f"  其他费用     {other['total_wan']:>12,.2f} 万元　"
          f"扣集成费后占预算总额 {other['capped_ratio']:.2%}（上限 10%）")
    print(f"  工程总投资   {grand/10000:>12,.2f} 万元")
    print(f"\n  功能点法段 {fp_cross['fp_scope_wan']:,.2f} 万 vs 源表同范围 "
          f"{fp_cross['fp_scope_source_wan']:,.2f} 万　差 "
          f"{fp_cross['fp_scope_source_wan']/max(fp_cross['fp_scope_wan'],1e-9):.1f}×"
          f"（{fp_cross['untyped']} 条未判型，其中基座 {fp_cross['untyped_base']} 条）")


def resolve_effort_factors(root: Path, pack: StandardPack, deal: dict,
                           sw: dict, effort_only: set[str]) -> dict:
    """工作量法的两个可调因子（表1 注3）：风险系数与复用系数。

        单价 = 人工成本 × 风险系数 × 复用系数

    人工成本按阶段固定（1.9/1.9/1.7/1.6/1.4），不可调；另外两个可调而从前
    都硬编码成 1，连读都没读。

    · 风险系数：标准写「**一般**取 1」，与「直接非人力成本一般为 0」同一句式。
      标准未给区间与适用情形，偏离 1 全靠自建依据 → >1 必须写依据。
      项目级 —— 标准没有把它挂在任何结构上。

    · 复用系数：表1 注3 只说「根据开发内容确定」，**未给档位表**；本包借用
      表3 注2 的高⅓/中⅔/低1，属推断。

      **粒度是建设对象（BOM 的 SOFTWARE_EFFORT 条目），不是阶段、也不是活动。**

      先是阶段 —— 阶段是活动经 `stage` 字段映射出来的派生容器：知识工程的
      「编码」装的是语料切分，数据工程的「编码」装的是数据标注，工种与角色
      都不同，问「这个阶段是不是复用的」等于问一个我们自己攒出来的桶。

      后改活动 —— 仍不对。同一个活动作用在不同交付物上，复用程度本来就不同：
      「入库、索引与检索配置」用在既有食材库上是沿用，用在新建富媒体库上是新做。
      判在活动上，一次取值就把整个系统的所有交付物一起打了折。

      现在判在**建设对象**上：它就是 05 的计量单元，也是「这一项有多少是新做的」
      这个问题唯一问得清楚的对象。

      **只能有一级生效。** 对象级与活动级同时乘就是双重打折 —— 活动级的
      `reuse` 节点已退役（保留字段作沿革），若仍声明非「低」，直接拒绝出表，
      不静默忽略：被忽略的那个折扣不会有任何地方报错。

      曾以「WBS 活动人天与阶段人月口径不同（差 1.56×/1.87×）」否掉活动级 ——
      **那个理由不成立**：这里只用活动在阶段内的**占比**（`_wbs_roles` 早已
      算好，乙本 06 的人员类型归集一直在用），口径不一致的是绝对人天，
      占比不受影响。

      阶段无对应活动时（如行业智能体的实施部署，见编制说明第 10 条），
      按缺省「低」并在表上标出，不继承任何东西。
    """
    em = pack.data.get("effort_method") or {}
    rf_node = em.get("risk_factor") or {}
    cfg = (deal.get("effort_method_settings") or {})
    risk = float((cfg.get("risk_factor") or {}).get("value", rf_node.get("value", 1.0)))
    risk_basis = str((cfg.get("risk_factor") or {}).get("basis") or "").strip()
    if risk <= 0:
        raise ValueError("风险系数必须为正数")
    if risk > 1.0 and not risk_basis:
        c = rf_node.get("citation") or {}
        raise ValueError(
            f"风险系数取 {risk}，但没有依据。\n"
            f"  标准 p.{c.get('page')} {c.get('section')}：「风险系数**一般**取 1」——"
            f"「一般」说明可以不是 1，但标准**未给取值区间与适用情形**，"
            f"偏离 1 完全靠自建依据。\n"
            f"  请在 deal.yaml 的 effort_method_settings.risk_factor.basis 写明"
            f"风险来源与测算方式。\n"
            f"  ⚠ 这是加价方向 —— 无依据的加价比无依据的降价更容易被整条核掉。")

    vals = ((pack.data.get("factors") or {}).get("reuse") or {}).get("values") or {}
    doc = yaml.safe_load((root / "bom" / "effort-wbs.yaml").read_text(encoding="utf-8"))
    decl = {(sysn, a["name"]): (a.get("reuse") or {})
            for sysn, t in (doc.get("templates") or {}).items()
            for a in (t.get("activities") or [])}
    stray = set(decl) - set(decl)          # 占位：模板即来源，不可能有孤儿
    apply_reuse = bool(((deal.get("fp_method_settings") or {}).get("reuse") or {})
                       .get("apply_maturity"))
    wbs = _wbs_roles(root)

    acts_out: dict[tuple, list] = {}       # (系统, 阶段) → [{活动, 占比, 档位, 系数, 依据}]
    stage_out: dict[tuple, dict] = {}      # (系统, 阶段) → 该阶段的**加权**复用系数
    for sysname, blk in (sw.get("systems") or {}).items():
        if sysname not in effort_only:
            continue
        comp = wbs.get(sysname) or {}
        for stage in (blk.get("stages") or {}):
            acts = (comp.get(stage) or {}).get("acts") or []
            rows = []
            for aname, share in acts:
                d0 = decl.get((sysname, aname)) or {}
                _dl = str(d0.get("level") or "低")
                if _dl != "低":
                    raise ValueError(
                        f"effort-wbs.yaml「{sysname} · {aname}」仍声明复用度"
                        f"「{_dl}」，但工作量法的复用度已下移到**建设对象**"
                        f"（bom/items/*.yaml 的 reuse 节点）。\n"
                        f"  两级同时生效会双重打折；活动级已退役。\n"
                        f"  请把该判断改挂到对应的建设对象上，或将本节点改回「低」。")
                lvl, basis = "低", ""
                if lvl not in vals:
                    raise ValueError(
                        f"effort-wbs.yaml {sysname} · {aname} 的复用度档位"
                        f"「{lvl}」不在标准包 factors.reuse.values：{sorted(vals)}")
                fac = float(vals[lvl])
                if abs(fac - 1.0) > 1e-9 and not basis:
                    raise ValueError(
                        f"工作量法「{sysname} · {aname}」取复用度「{lvl}」"
                        f"（系数 {fac}），但没有依据。\n"
                        f"  表1 注3「复用系数根据开发内容确定」；本包借用表3 注2 "
                        f"档位（属推断），偏离缺省必须列明依据。\n"
                        f"  填在【丙本】03_复用度模块清单，经 quote_sync --write-bom "
                        f"写入 bom/effort-wbs.yaml 该活动的 reuse 节点。")
                rows.append({"act": aname, "share": share, "level": lvl,
                             "factor": fac, "basis": basis})
            acts_out[(sysname, stage)] = rows
            # 阶段的有效复用系数 = Σ(活动阶段内占比 × 该活动系数)。
            # 这是**各部分之和**，不是编出来的平均值 —— 金额精确等于逐活动求和。
            _lv = sorted({r["level"] for r in rows})
            if not rows:
                _fac = 1.0
            elif len(_lv) == 1:
                # 单一档位就直接取该档的标准值 —— 走加权求和会因为占比的
                # 浮点误差印出 0.9999999999999999，送审表上那种数看着像算错了。
                _fac = float(vals[_lv[0]])
            else:
                _fac = round(sum(r["share"] * r["factor"] for r in rows), 6)
            stage_out[(sysname, stage)] = {
                "factor": _fac,
                "mixed": len(_lv) > 1,
                "no_acts": not rows,
                "levels": _lv or ["低"]}
    # ---- 复用度：逐**建设对象**取值（表1 注3 借表3 注2 档位） ----
    obj_out: dict[tuple, dict] = {}
    import glob as _g
    _seen: set[tuple] = set()
    for _f in _g.glob(str(root / "bom" / "items" / "*.yaml")):
        for _i in yaml.safe_load(Path(_f).read_text(encoding="utf-8"))["items"]:
            if _i.get("class") != "SOFTWARE_EFFORT":
                continue
            _sn = _i["path"]["system"]
            if _sn not in effort_only:
                continue
            _r0 = _i.get("reuse") or {}
            _lv = str(_r0.get("level", "低")) if apply_reuse else "低"
            _bs = str(_r0.get("basis") or "").strip()
            if _lv not in vals:
                raise ValueError(
                    f"{_sn} · {_i['name']} 的复用度档位「{_lv}」不在标准包 "
                    f"factors.reuse.values：{sorted(vals)}")
            _fc = float(vals[_lv])
            if abs(_fc - 1.0) > 1e-9 and not _bs:
                raise ValueError(
                    f"工作量法「{_sn} · {_i['name']}」取复用度「{_lv}」"
                    f"（系数 {_fc}），但没有依据。\n"
                    f"  表1 注3「复用系数根据开发内容确定」；本包借用表3 注2 "
                    f"档位（属推断），偏离缺省必须列明依据。\n"
                    f"  填在【丙本】03_复用度模块清单，经 quote_sync --write-bom "
                    f"写入 bom/items 该条目的 reuse 节点。")
            obj_out[(_sn, _i["name"])] = {
                "level": _lv, "factor": _fc, "basis": _bs}
            _seen.add((_sn, _i["name"]))
    return {"risk": risk, "risk_basis": risk_basis,
            "risk_clause": f"p.{(rf_node.get('citation') or {}).get('page')} "
                           f"{(rf_node.get('citation') or {}).get('section')}",
            "acts": acts_out, "stage": stage_out, "obj": obj_out,
            "apply_reuse": apply_reuse,
            "tiers_note": str(em.get("reuse_tiers_source") or "")}


def resolve_direct_non_labor(pack: StandardPack, deal: dict) -> dict:
    """直接非人力成本 —— 公式的**加数项**，不是因子（p.14 ⑤）。

    标准把它排除在 1.7 万人月费率之外（p.14 ④「不包含直接非人力成本」），
    所以它确实可以另计；但同一条又明文「一般情况不进行计列（通常为 0），
    特殊情况需要计列时应明确说明原因及测算依据」。

    默认 0。要计列必须写依据 —— 与表3 注1/注2 同构。这一项是**加价**方向，
    没有依据的加价比没有依据的降价更容易被整条核掉。

    ⚠️ 「采购费」里的专用设备费/专用软件费已在硬件设备购置费与软件产品购置费
    科目里，重复计列会被 negative_list 拦。本项实际能计的是办公费、差旅费、
    培训费这几类。
    """
    node = (pack.data.get("rates") or {}).get("direct_non_labor_cost") or {}
    cfg = ((deal.get("fp_method_settings") or {}).get("direct_non_labor_cost") or {})
    val = float(cfg.get("value", node.get("value", 0)) or 0)
    basis = str(cfg.get("basis") or "").strip()
    if val < 0:
        raise ValueError("直接非人力成本不能为负 —— 它是加数项，不是折扣")
    if val > 0 and not basis:
        raise ValueError(
            f"直接非人力成本计列 ¥{val:,.2f}，但没有依据。\n"
            f"  标准 p.14 ⑤：「一般情况不进行计列（通常为 0），"
            f"特殊情况需要计列时应**明确说明原因及测算依据**」。\n"
            f"  请在 deal.yaml 的 fp_method_settings.direct_non_labor_cost.basis "
            f"写明原因与测算依据（例如实施差旅：实施点数 × 人次 × 单次标准）。\n"
            f"  ⚠ 专用设备费/专用软件费不得计入本项 —— 它们在硬件设备购置费与"
            f"软件产品购置费科目，重复计列会被 negative_list 拦。")
    c = node.get("citation") or {}
    return {"value": val, "basis": basis,
            "clause": f"p.{c.get('page')} {c.get('section')}",
            "quote": str(node.get("quote") or c.get("quote") or "")}


def _taxonomy_systems(root: Path) -> dict[str, dict]:
    """taxonomy 里所有子系统节点，展平成 {子系统名: 节点}。"""
    tax = yaml.safe_load((root / "bom" / "taxonomy.yaml").read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for line, ls in (tax.get("product_lines") or {}).items():
        for name, node in ((ls or {}).get("systems") or {}).items():
            node = dict(node or {})
            node["_product_line"] = line
            node["_layer"] = node.get("layer") or (ls or {}).get("layer")
            out[name] = node
    return out


def resolve_reuse(root: Path, pack: StandardPack, deal: dict,
                  systems, detail: list[dict] | None = None) -> dict[str, dict]:
    """复用度（表3 注2）—— **粒度到功能模块**，不是子系统。

    ## 为什么这一个因子可以比软件类别细

    两条注的措辞不一样，粒度是标准给的，不是我们挑的：

      注1（软件类别）「原则上按照**主体功能**的类型取值」        → 一块内容一个值
      注2（复用度）  「在已有软件系统**或功能模块**基础上…」      → 判断单位到模块

    所以软件类别按子系统取（见 `_sys_app_type`），复用度可以按 `path.l1`
    功能模块取。柳州公式 `功能点数 = UFP × 类别因子 × 复用系数` 依然成立 ——
    同一子系统内按复用度分组，各组各乘各的，再求和。

    ## 三层各管一段

      第一层 BOM taxonomy —— **只在 `modules:` 上逐模块声明，没有子系统级默认。**
        没声明的模块一律取「低」= 1.0，也就是表3 注2 的缺省
        （「新建项目的复用度调整系数默认取值为1」）。

        为什么不设子系统默认：默认会被**将来新增的模块**继承。134 条未判型
        条目补齐后会多出若干模块，若子系统默认是 existing，这些全新写的功能
        就自动拿到 1/3 —— 不报错、不提示，报价少一截而清单上看不出来。
        取「低」则永远保守：判错只会多算，不会少算。
        （yaml 变长不是问题 —— 这一段由 quote_sync --write-bom 机器写，
        人只在丙本 03 的表格里填。）
      第二层 标准包 —— `aliases_by_project_type` 把 maturity 映射到本地档位。
      第三层 deal.yaml —— project_type、apply_maturity 开关、本商机例外覆盖。

    **偏离缺省（系数 ≠ 1.0）必须带依据，否则拒绝出表。** 方向是降价，
    没有出处的折扣在评审那里是「随意定价」，比不打折更被动。

    返回 {子系统: {"groups": [...], "reuse_level"/"reuse_factor"（单一档位时）...}}
    """
    node = (pack.data.get("factors") or {}).get("reuse") or {}
    values = node.get("values") or {}
    rset = ((deal.get("fp_method_settings") or {}).get("reuse") or {})
    ptype = rset.get("project_type") or deal.get("project_type") or "新建"
    key = f"{ptype}·按实际成熟度调整" if rset.get("apply_maturity") else ptype
    tables = node.get("aliases_by_project_type") or {}
    table = tables.get(key)
    if not table:
        raise ValueError(
            f"标准包 factors.reuse.aliases_by_project_type 没有「{key}」，"
            f"已登记：{sorted(tables)}")
    ov = rset.get("overrides") or {}
    tax = _taxonomy_systems(root)
    stray = set(ov) - set(systems)
    if stray:
        raise ValueError(
            f"deal.yaml fp_method_settings.reuse.overrides 里的子系统 {sorted(stray)} "
            f"不在本次功能点法范围内 —— 覆盖了个不存在的名字不会报错，只会静默不生效")

    # 各子系统的功能模块 UFP。没有 detail 就退回子系统整块（单组）。
    mod_ufp: dict[str, dict[str, int]] = {}
    mod_n: dict[str, dict[str, int]] = {}
    for d in (detail or []):
        m = d.get("l1") or "（未分模块）"
        mod_ufp.setdefault(d["system"], {}).setdefault(m, 0)
        mod_ufp[d["system"]][m] += d["ufp"]
        mod_n.setdefault(d["system"], {}).setdefault(m, 0)
        mod_n[d["system"]][m] += 1

    DEFAULT_LVL = table.get("new")          # 未声明 = 全新 = 表3 注2 缺省

    def _lvl_basis(mat, basis, src):
        lvl = table.get(mat or "new")
        if lvl not in values:
            raise ValueError(f"复用度档位「{lvl}」不在标准包 factors.reuse.values：{sorted(values)}")
        return lvl, float(values[lvl]), str(basis or "").strip(), src

    out: dict[str, dict] = {}
    for name in systems:
        t = tax.get(name) or {}
        # 未声明的模块一律走缺省档，**不继承任何子系统级取值**
        sys_lvl, sys_fac, sys_basis, sys_src = _lvl_basis(
            "new", "", "未声明（按表3 注2 缺省取低）")
        o = ov.get(name) or {}
        if o:
            sys_lvl = o.get("level", sys_lvl)
            sys_fac = float(values[sys_lvl])
            sys_basis = str(o.get("basis") or "").strip()
            sys_src = f"deal.yaml 覆盖（{ptype}）"
        mods = (t.get("modules") or {})
        stray_m = set(mods) - set(mod_ufp.get(name, {}))
        if stray_m:
            raise ValueError(
                f"taxonomy「{name}」的 modules 里有 {sorted(stray_m)}，"
                f"BOM 里没有这个功能模块（path.l1）—— "
                f"写错名字不会报错，只会静默不生效。\n"
                f"  本子系统的功能模块：{sorted(mod_ufp.get(name, {}))}")
        # 按档位归组：例外模块各归各档，其余归子系统默认档
        groups: dict[str, dict] = {}
        for mod, u in sorted(mod_ufp.get(name, {}).items()):
            mo = mods.get(mod) or {}
            if mo:
                lvl, fac, basis, src = _lvl_basis(
                    mo.get("maturity"), mo.get("maturity_basis"),
                    f"taxonomy modules.{mod} maturity={mo.get('maturity')}")
            else:
                lvl, fac, basis, src = sys_lvl, sys_fac, sys_basis, sys_src
            g = groups.setdefault(lvl, {"reuse_level": lvl, "reuse_factor": fac,
                                        "reuse_basis": basis, "reuse_source": src,
                                        "ufp": 0, "items": 0, "modules": []})
            g["ufp"] += u
            g["items"] += mod_n.get(name, {}).get(mod, 0)
            g["modules"].append(mod)
        if not groups:                      # 0 UFP 的待补子系统
            groups[sys_lvl] = {"reuse_level": sys_lvl, "reuse_factor": sys_fac,
                               "reuse_basis": sys_basis, "reuse_source": sys_src,
                               "ufp": 0, "items": 0, "modules": []}
        for g in groups.values():
            if abs(g["reuse_factor"] - 1.0) > 1e-9 and not g["reuse_basis"]:
                where = (f"子系统「{name}」" if g["modules"] == sorted(mod_ufp.get(name, {}))
                         or not g["modules"]
                         else f"子系统「{name}」的功能模块 {g['modules']}")
                raise ValueError(
                    f"{where} 取复用度「{g['reuse_level']}」"
                    f"（系数 {g['reuse_factor']}），但没有依据。\n"
                    f"  表3 注2 的缺省是 1（复用度低），偏离缺省必须列明依据。\n"
                    f"  依据写在 bom/taxonomy.yaml 该子系统的 maturity_basis，"
                    f"或 modules.<模块名>.maturity_basis，"
                    f"或 deal.yaml fp_method_settings.reuse.overrides.{name}.basis。\n"
                    f"  这次偏离的方向是降价 —— 没出处的折扣评审当「随意定价」看。")
        gl = sorted(groups.values(), key=lambda g: -g["ufp"])
        one = gl[0] if len(gl) == 1 else None
        out[name] = {
            "groups": gl, "reuse_policy": key,
            # 子系统级 maturity 已不参与计价；保留只为 0 模块的待补子系统留个位置
            "maturity": t.get("maturity"),
            "declared_modules": sorted(mods),
            # 单一档位时保持与从前完全一致的键，下游 20 余处调用不用改；
            # 混合档位时置 None，逼下游显式处理而不是拿第一组当全部。
            "reuse_level": one["reuse_level"] if one else None,
            "reuse_factor": one["reuse_factor"] if one else None,
            "reuse_basis": one["reuse_basis"] if one else "",
            "reuse_source": one["reuse_source"] if one else "逐功能模块分档",
        }
    return out


def _sys_app_type(root: Path, system: str) -> str:
    """该子系统的软件类别（表3）。**权威值在 taxonomy 的子系统节点上。**

    ## 为什么在子系统这一层，不在条目上

    柳州公式是 `功能点数 = UFP × 软件类别调整因子 × 复用系数` —— 因子乘的是
    UFP，也就是**一块内容的合计数**，因子的粒度由公式位置定死在这一层。
    表3 注1 给的两条出路（「原则上按主体功能的类型取值」/「多种类型功能占比
    比较均衡的可取各类型调整因子平均值」）产出也都是「一块内容一个因子」——
    标准没有「逐个功能点各乘各的」这条路。

    依据也必须落在这一层：注1 要求取值超过 1 的列明依据，而依据是
    「这个子系统的主体功能属于哪一类」，一个子系统一句话。挂到条目上就是
    同一句话抄几百遍，改的时候必漏一部分，而漏掉的那部分看不出来。

    ## 条目上的 app_type 是什么

    是**举证与对账**用的，不参与计价。它的真实用处是证明主体功能是什么、
    以及算占比看均不均衡（注1 第二条的前提）。但当前 926 条的值是 build 时
    从系统级查找表盖上去的，不是逐条判定的结果 —— 拿它去举证等于用结论证明
    结论，所以这里只做一致性核对，**不允许它影响价格**。

    不一致时报告而非取众数：取众数会让一部分条目按别的类别计价，且看不出来。
    """
    tax = _taxonomy_systems(root)
    node = tax.get(system)
    if node is None:
        raise ValueError(
            f"taxonomy.yaml 里没有子系统「{system}」—— 基准里有它，产品树里没有")
    at = node.get("app_type")
    if not at:
        # **不回落到条目。** 回落等于把「没人判过」当成「判过了」，
        # 而这一列直接乘在金额上。
        raise ValueError(
            f"子系统「{system}」在 bom/taxonomy.yaml 里没有登记 app_type。\n"
            f"  软件类别按子系统取值（表3 注1「按主体功能的类型取值」），"
            f"权威值在 taxonomy 的子系统节点，不从条目倒推。\n"
            f"  请补 app_type 与 app_type_basis（依据是「该子系统主体功能属于表3 哪一类」）。")
    seen = _item_app_types(root, system)
    stray = seen - {at}
    if stray:
        raise ValueError(
            f"子系统「{system}」登记的软件类别是「{at}」，但条目里还出现了 "
            f"{sorted(stray)}。\n"
            f"  条目级 app_type 是举证用的，不参与计价；但两者不一致说明"
            f"「主体功能是什么」这个判断本身有分歧。\n"
            f"  要么按注1「主体功能」统一，要么在编制说明里按注1 第二条说明"
            f"占比均衡、取各类因子平均值 —— 不要让它悄悄按登记值算过去。")
    return at


def _item_app_types(root: Path, system: str) -> set[str]:
    """该子系统下条目登记的软件类别集合（举证用，不参与计价）。"""
    import glob
    out: set[str] = set()
    for f in glob.glob(str(root / "bom" / "items" / "*.yaml")):
        for i in yaml.safe_load(Path(f).read_text(encoding="utf-8"))["items"]:
            if i["path"]["system"] == system and i.get("app_type"):
                out.add(i["app_type"])
    return out


def _latest_baseline(root: Path, pack: StandardPack) -> dict:
    """取本包最新的基准产物。

    **按 BOM 版本排序取最新**，不用 `next(glob)` —— 那个取到的是文件系统
    给的第一个，可能是几版之前的旧基准，而金额看起来完全正常。
    """
    ver = (root / "bom" / "VERSION").read_text(encoding="utf-8").strip()
    want = root / "baselines" / f"{pack.pack_id}@bom-{ver}" / "baseline.json"
    if not want.exists():
        raise FileNotFoundError(
            f"缺基准 {want} —— 先跑 baseline_build --bom bom --pack "
            f"standard-packs/{pack.pack_id} --out baselines/{pack.pack_id}@bom-{ver}")
    return json.loads(want.read_text(encoding="utf-8"))


def _fp_crosscheck(root: Path, pack: StandardPack, sw: dict,
                   effort_only: set[str] | None = None,
                   fp_systems: list[dict] | None = None) -> dict[str, Any]:
    """分段交叉校验。

    混合口径下「全项目倒推生产率」已经没有意义 —— 走功能点法那段的生产率
    按定义就是标准值 6.51，走工作量法那段根本没有功能点对照。
    有意义的是两件事：
      · 功能点法那段算出来的钱，与源表对同一范围的估算差多少（差距归因于描述质量）
      · 工作量法那段的人月有多少（它必须自证，因为没有对照）
    """
    import glob
    effort_only = effort_only or set()
    ufp = untyped = untyped_base = 0
    for f in glob.glob(str(root / "bom" / "items" / "*.yaml")):
        for i in yaml.safe_load(Path(f).read_text(encoding="utf-8"))["items"]:
            if (i["path"]["system"] in effort_only
                    or i.get("class") == "SOFTWARE_EFFORT"
                    or i.get("status") == "deprecated"):   # 与基准 active() 同口径
                continue
            if "nesma" in i:
                ufp += {"ILF": 10, "ELF": 7, "EI": 4, "EO": 5, "EQ": 4}[i["nesma"]["type"]]
            else:
                untyped += 1
                if "基座" in i["path"]["system"]:
                    untyped_base += 1

    per_fp = (pack.rate("productivity_hours_per_fp")
              / pack.rate("man_hours_per_month")
              * pack.rate("base_man_month_rate"))
    fp_wan = sum(x["cost"] for x in (fp_systems or [])) / 10000
    src_fp_wan = sum(v["amount_wan"] for sn, b in sw["systems"].items()
                     for v in b["stages"].values() if sn not in effort_only)
    eff_mm = sum(v["man_months"] for sn, b in sw["systems"].items()
                 for v in b["stages"].values() if sn in effort_only)
    return {"fp_ufp": ufp, "untyped": untyped, "untyped_base": untyped_base,
            "per_fp": round(per_fp, 2),
            "fp_scope_wan": round(fp_wan, 2),
            "fp_scope_source_wan": round(src_fp_wan, 2),
            "effort_scope_mm": round(eff_mm, 2)}


if __name__ == "__main__":
    main()
