"""quote_generate_shandong —— 按山东标准出编报套表。

## 山东与另外两省的三处结构差异

    柳州   工程费用 + 其他费用 + 预备费；软件开发可选功能点法或工作量法
    广东   五类服务费；运维列入预算；硬件按年分摊
    山东   四类建设支出（软件开发 / 软件和服务购置 / 硬件购置 / 其他建设费用）；
           运维**不列入**；硬件一次性购置；**软件开发只能用功能点法**

其中第三条是本出表器最重要的约束，也是它不能由广东出表器加开关得到的原因：

**山东不接受工作量估算法。** 标准 p.7 明文只允许 NESMA 预估/估算功能点法，
报表「规模估算方法」格更硬 —— 只接受「预估功能点」或「估算功能点」两个答案
（p.45 表3-2 填报说明）。所以走工作量法的条目（`SOFTWARE_EFFORT`，本仓里是
行业大模型那几段：知识工程/数据工程/行业垂直模型/行业智能体）**不得计入
软件开发分项支出**。

它们并没有消失，落点是 二.（四）**数据模型购置**：标准 p.9 要求数据模型的
询价报价单载明「预计搭建模型所需投入工作量（采用人月计量）」—— 也就是说
在山东「按人月报模型」是合规的，只是报在购置费里，不是软件开发费里。
本出表器把这几段人月原样带进表4 并标为**待询价**，而不是替它编一个单价。

## 表结构的出处

附件3「省级政务信息化建设项目支出预算相关附表」，列序逐列记在
`pack.yaml` 的 `sheet_spec`，与标准原样一致 —— 财评是照着格式核的。

本出表器出：表2 总预算、表3-1 软件开发分项、表3-2 功能点计数明细、
表4 软件和服务购置、表5 硬件设备购置、表6 其他建设费用。

用法：
    python3 quote_generate_shandong.py --deal deals/<id>/deal.yaml --out out/
"""
from __future__ import annotations

import argparse
import glob
from pathlib import Path

import openpyxl
import yaml

import gov_sheet as gs
from gov_sheet import Col, GovSheet
from nesma_weights import xlround
from standard_pack import StandardPack, get_profile
from quote_generate_liuzhou import _latest_baseline, read_device_config

PACK_ID = "shandong-2024"

#: 报表「规模估算方法」格只接受这两个答案（p.45 表3-2 填报说明）。
FORM_METHODS = {"预估功能点法": "预估功能点", "估算功能点法": "估算功能点"}

#: 负面清单里按品名排除的通用资产 —— 只做提示，不自动删行。
#: 自动删会让「这台设备去哪了」变成没人能回答的问题；提示让人来判。
GENERIC_ASSETS = ("电脑", "笔记本", "台式机", "扫描仪", "打印机", "复印机")


def _effort_items(root: Path) -> dict[str, list[dict]]:
    """按子系统收走工作量法的条目与其 effort_basis 人月。"""
    out: dict[str, list[dict]] = {}
    for f in glob.glob(str(root / "bom" / "items" / "*.yaml")):
        for i in (yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}).get("items", []):
            if i.get("class") != "SOFTWARE_EFFORT" or i.get("deprecated_in"):
                continue
            b = i.get("effort_basis") or {}
            out.setdefault(i["path"]["system"], []).append({
                "name": i["name"], "man_months": b.get("man_months") or 0.0,
                "basis": b.get("basis", ""), "l1": (i.get("path") or {}).get("l1", "")})
    return out


# ---------------------------------------------------------------- 表3-1


def emit_dev_detail(wb, systems: list[dict], pack: StandardPack,
                    counting_method: str) -> None:
    """附表3-1 软件开发分项预算表。

    标准是**每个子系统一张**（表头有「系统名称/子系统名称」）。这里合成一张、
    每个子系统一个分组块 —— 18 个子系统开 18 个 sheet 没人翻得动。正式报批
    须按标准拆表，这一点写在表尾说明里，不靠口头交代。
    """
    size = pack.factor("size_change", counting_method)
    prod = pack.rate("productivity_hours_per_fp")
    hpm = pack.rate("man_hours_per_month")
    base_rate = pack.rate("base_man_month_rate")
    s = GovSheet(
        wb, "附表3-1 软件开发分项预算",
        title="附表 3-1：软件开发分项预算表",
        subtitle=f"编制依据：{pack.data['standard_doc']} 三.(一)2；"
                 f"规模调整因子 {size}（{counting_method}）、"
                 f"基准生产率 {prod} 人时/功能点、人月折算 {hpm} 人时/人月、"
                 f"基准人月费率 {base_rate:,.0f} 元/人月（济南）",
        columns=[
            Col("估算过程项", "text", width=26),
            Col("估算取值", "text", width=16),
            Col("单位", "text", width=12),
            Col("取值说明", "text", width=60),
        ],
        seq=False, clause_required=False)

    for x in systems:
        eff_reuse = (x["afp"] / (x["ufp"] * size * x["app_factor"])
                     if x["ufp"] and x["app_factor"] else 1.0)
        s.row({"估算过程项": f"【{x['system']}】", "估算取值": "",
               "单位": "", "取值说明": f"应用类型：{x['app_type']}；"
                                        f"开发类别：{x['dev_category']}"})
        for item, val, unit, why in [
            ("1.功能点合计", f"{x['ufp']:,.0f}", "功能点",
             "表3-2 中未调整功能点数之和"),
            ("规模调整因子", f"{size}", "——",
             f"CSBMK {pack.data['csbmk_baseline']}，{counting_method}取值"),
            ("复用度调整因子", f"{eff_reuse:.4f}", "——",
             "按模块 UFP 加权；新建项目默认 1（复用度低），"
             "升级改造中既有功能优化完善不超过 0.66"),
            ("应用类型调整因子", f"{x['app_factor']}", "——",
             f"应用类型调整因子参数表（表2）「{x['app_type']}」"),
            ("2.调整后功能点合计", f"{x['afp']:,.2f}", "功能点",
             "功能点合计×规模调整因子×复用度调整因子×应用类型调整因子"),
            ("3.基准生产率", f"{prod}", "人时/功能点",
             f"CSBMK {pack.data['csbmk_baseline']} 电子政务领域 P50"),
            ("人月折算系数", f"{hpm}", "人时/人月", "每天 8 小时、每月 21.75 天"),
            ("4.调整后工作量", f"{x['effort_man_months']:,.2f}", "人月",
             "调整后功能点数×基准生产率/人月折算系数"),
            ("5.基准人月费率", f"{base_rate / 1e4:,.4f}", "万元/人月",
             "CSBMK 济南市基准人月费率"),
            ("开发类别调整系数", f"{x['dev_coef']}", "——",
             f"开发类别调整系数表（表3）「{x['dev_category']}」"),
            ("6.人月费率", f"{x['man_month_rate'] / 1e4:,.4f}", "万元/人月",
             "基准人月费率×开发类别调整系数"),
            ("7.软件开发分项支出预算", f"{x['cost'] / 1e4:,.2f}", "万元",
             "调整后工作量×人月费率"),
        ]:
            s.row({"估算过程项": item, "估算取值": val,
                   "单位": unit, "取值说明": why})

    s.note("⚠ 标准原格式为**每个子系统一张表3-1**。本表把各子系统合成一张便于内部核对，"
           "正式报批须按标准逐子系统拆表。")
    s.finish()


# ---------------------------------------------------------------- 表3-2


def emit_fp_count(wb, detail: list[dict], effort_only: set[str],
                  pack: StandardPack, counting_method: str) -> None:
    """附表3-2 新建项目软件功能点计数明细表。"""
    s = GovSheet(
        wb, "附表3-2 功能点计数明细",
        title="附表 3-2：新建项目软件功能点计数明细表",
        subtitle=f"规模估算方法：{FORM_METHODS[counting_method]}"
                 f"（本格只接受「预估功能点」或「估算功能点」，标准 p.45 填报说明）",
        columns=[
            Col("子系统", "text", width=18),
            Col("一级模块", "text", width=18),
            Col("二级模块", "text", width=18),
            Col("功能点计数项名称", "text", width=34),
            Col("功能点描述", "text", width=40),
            Col("功能点计数项类别", "text", width=14),
            Col("未调整功能点数", "fp", width=14, sum=True),
            Col("规模调整因子", "rate", width=12),
            Col("复用程度调整因子", "rate", width=14),
            Col("应用类型调整因子", "rate", width=14),
            Col("调整后功能点数", "fp", width=14, sum=True),
            Col("备注", "text", width=20),
        ],
        clause_required=False)
    ufp = afp = 0.0
    for r in detail:
        if r["system"] in effort_only:
            continue                       # 工作量法的条目不进功能点计数表
        ufp += r["ufp"]
        afp += r["afp"]
        s.row({
            "子系统": r["system"], "一级模块": r.get("l1") or "",
            "二级模块": r.get("l2") or "", "功能点计数项名称": r["name"],
            "功能点描述": (r.get("description") or "")[:200],
            "功能点计数项类别": r["type"],
            "未调整功能点数": r["ufp"], "规模调整因子": r["size_change"],
            "复用程度调整因子": r["reuse_factor"],
            "应用类型调整因子": r["app_factor"],
            "调整后功能点数": r["afp"],
            "备注": "占位" if r.get("placeholder") else "",
        })
    s.total(expect={"未调整功能点数": ufp, "调整后功能点数": afp})
    s.note("功能点合计＝未调整功能点数之和；调整后功能点合计＝经过复用调整后的功能点数之和。")
    s.note("不计数规则已在计数阶段施加：信息安全/授权/登录等标准功能、"
           "基础软件适配工作量、菜单结构（用户不可自维护的）均不纳入计数"
           "（标准 三.(三)4）。")
    s.finish()


# ---------------------------------------------------------------- 表4


def emit_purchase(wb, effort_systems: list[dict], pack: StandardPack) -> float:
    """附表4 软件和服务购置分项支出预算表。

    本仓当前只有「数据模型购置」有内容 —— 行业大模型那几段。**它们有人月、
    没单价**：单价要靠三家询价，工程侧编不出来。所以金额留空并标待询价，
    人月原样带进「主要内容或性能指标需求」列充当举证口径。
    """
    s = GovSheet(
        wb, "附表4 软件和服务购置",
        title="附表 4：软件和服务购置分项支出预算表",
        subtitle=f"编制依据：{pack.data['standard_doc']} 三.(二)；"
                 f"数据模型询价报价单须载明「预计搭建模型所需投入工作量"
                 f"（采用人月计量）」（p.9 三.(二)2(2)）",
        columns=[
            Col("名称及类别", "text", width=30),
            Col("主要内容或性能指标需求", "text", width=52),
            Col("单价（万元）", "money", width=14),
            Col("数量", "int", width=10),
            Col("预算金额（万元）", "money", width=16, sum=True),
        ],
        clause_required=False)
    total_mm = 0.0
    s.row({"名称及类别": "一、软件产品购置", "主要内容或性能指标需求":
           "本商机不涉及 —— 无成品软件购置", "单价（万元）": 0,
           "数量": 0, "预算金额（万元）": 0})
    s.row({"名称及类别": "二、数据资源购置", "主要内容或性能指标需求":
           "本商机不涉及", "单价（万元）": 0, "数量": 0, "预算金额（万元）": 0})
    s.row({"名称及类别": "三、数据服务购置", "主要内容或性能指标需求":
           "本商机不涉及", "单价（万元）": 0, "数量": 0, "预算金额（万元）": 0})
    s.row({"名称及类别": "四、数据模型购置", "主要内容或性能指标需求":
           "以下各段按人月举证，单价待三家询价确定", "单价（万元）": 0,
           "数量": 0, "预算金额（万元）": 0})
    for x in effort_systems:
        mm = sum(i["man_months"] or 0 for i in x["items"])
        total_mm += mm
        s.row({
            "名称及类别": f"　{x['system']}",
            "主要内容或性能指标需求":
                f"{len(x['items'])} 项模型/语料/知识工程要素，"
                f"预计投入工作量 {mm:,.2f} 人月（BOM effort_basis 净工作量口径）",
            "单价（万元）": 0, "数量": 0, "预算金额（万元）": 0,
        })
    s.total(expect={"预算金额（万元）": 0.0})
    s.note(f"⚠ **本表金额为 0 是「待询价」，不是「不涉及」。**数据模型购置共 "
           f"{total_mm:,.2f} 人月已列明，但山东按购置费口径计价，单价须由三个或以上"
           f"同级别不同品牌厂商加盖公章的询价报价单确定（三.(二)2(2)），"
           f"工程侧不得自行折算。")
    s.note("⚠ 这几段在广东/柳州走工作量估算法直接出金额；山东**不接受工作量法**"
           "（三.(一)2 及表3-2 填报说明），故改列购置费。同一批内容两地科目不同，"
           "跨省比较金额没有意义。")
    return 0.0


# ---------------------------------------------------------------- 表5


def emit_hardware(wb, hw: dict | None, pack: StandardPack) -> tuple[float, list[str]]:
    """附表5 硬件设备购置分项支出预算表。"""
    warn: list[str] = []
    s = GovSheet(
        wb, "附表5 硬件设备购置",
        title="附表 5：硬件设备购置分项支出预算表",
        subtitle=f"编制依据：{pack.data['standard_doc']} 三.(三)；"
                 f"单价 1 万元以上或单一类型总价 4 万元以上须三家询价；"
                 f"购置费应含至少三年原厂质保",
        columns=[
            Col("名称及类别", "text", width=34),
            Col("主要内容或性能指标需求", "text", width=44),
            Col("单价（万元）", "money", width=14),
            Col("数量", "int", width=10),
            Col("预算金额（万元）", "money", width=16, sum=True),
        ],
        clause_required=False)
    total = 0.0
    if not hw:
        s.row({"名称及类别": "（无）", "主要内容或性能指标需求":
               "本商机尚未建 device-config.yaml", "单价（万元）": 0,
               "数量": 0, "预算金额（万元）": 0})
    else:
        agg: dict[str, dict] = {}
        for r in hw["priced"]:
            k = r.get("code") or r["name"]
            a = agg.setdefault(k, {"name": r["name"],
                                   "model": r.get("model") or r.get("spec") or "",
                                   "price": r.get("unit_price") or 0.0,
                                   "qty": 0, "total": 0.0})
            a["qty"] += r["qty"]
            a["total"] += r["total"]
        for k, a in sorted(agg.items(), key=lambda kv: -kv[1]["total"]):
            total += a["total"]
            if any(g in a["name"] for g in GENERIC_ASSETS):
                warn.append(f"「{a['name']}」疑似通用资产设备")
            s.row({
                "名称及类别": a["name"],
                "主要内容或性能指标需求": a["model"] or "⚠ 未填参考机型",
                "单价（万元）": xlround(a["price"] / 1e4, 4),
                "数量": a["qty"],
                "预算金额（万元）": xlround(a["total"] / 1e4, 2),
            })
        s.total(expect={"预算金额（万元）": xlround(total / 1e4, 2)})
    s.note("⚠ 购置费中不得包含设备租赁费用、政务云资源使用费、硬件设备集成实施费，"
           "以及电脑、扫描仪、打印机等通用资产设备（三.(三)）。"
           "本表未自动删行 —— 逐条筛选须由业务侧确认。")
    if warn:
        s.note("⚠ 疑似触碰负面清单，请逐条确认：" + "；".join(warn))
    s.finish()
    return total, warn


# ---------------------------------------------------------------- 表6


def emit_other_fees(wb, pack: StandardPack, prof, *, sw_dev_wan: float,
                    sw_purchase_wan: float, hw_wan: float,
                    dev_categories: set[str]) -> tuple[float, dict[str, float]]:
    """附表6 其他建设费用分项支出预算表。"""
    eligible = dev_categories & {"大型行业软件开发", "基于统一平台的软件开发"}
    design = prof.other_fee(pack, "design", sw_dev_wan)
    test = prof.other_fee(pack, "third_party_test",
                          sw_dev_wan + sw_purchase_wan + hw_wan)
    int_sw = xlround(sw_purchase_wan * 0.08, 2) if eligible else 0.0
    int_hw = xlround(hw_wan * 0.05, 2) if eligible else 0.0

    s = GovSheet(
        wb, "附表6 其他建设费用",
        title="附表 6：其他建设费用分项支出预算表",
        subtitle=f"编制依据：{pack.data['standard_doc']} 三.(四)",
        columns=[
            Col("名称及类别", "text", width=26),
            Col("计费基数（万元）", "money", width=16),
            Col("预算金额（万元）", "money", width=16, sum=True),
            Col("测算过程说明", "text", width=70),
        ],
        clause_required=False)
    s.row({"名称及类别": "一、设计费", "计费基数（万元）": sw_dev_wan,
           "预算金额（万元）": design,
           "测算过程说明": "以软件开发支出费用为基数，超额累进（表4）"})
    s.row({"名称及类别": "二、系统集成费", "计费基数（万元）": 0,
           "预算金额（万元）": xlround(int_sw + int_hw, 2),
           "测算过程说明": (
               f"成品软件集成费 ≤8%×{sw_purchase_wan:,.2f} = {int_sw:,.2f}；"
               f"硬件设备集成费 ≤5%×{hw_wan:,.2f} = {int_hw:,.2f}。"
               f"⚠ **按标准上限计取** —— 标准原文为「按不高于 X% 的比例计取」，"
               f"实际比例须按集成工作难度确定后调整"
               if eligible else
               "⚠ 不计取 —— 系统集成费仅适用于「大型行业软件开发」与"
               "「基于统一平台的软件开发」两类开发类别，本商机不属于")})
    s.row({"名称及类别": "三、第三方软件测试费",
           "计费基数（万元）": xlround(sw_dev_wan + sw_purchase_wan + hw_wan, 2),
           "预算金额（万元）": test,
           "测算过程说明": "以软件开发+软件购置+硬件购置为基数，超额累进（表5）"})
    s.row({"名称及类别": "四、其他费用", "计费基数（万元）": 0,
           "预算金额（万元）": 0,
           "测算过程说明": "⚠ 等级保护测评费、密码应用测评费、分级保护测评费"
                            "按主管部门要求执行，**不纳入建设项目预算编制范围**"
                            "（五.(一)4）—— 与柳州把密评/等保列入其他费用不同"})
    total = xlround(design + int_sw + int_hw + test, 2)
    s.total(expect={"预算金额（万元）": total})
    s.finish()
    return total, {"design": design, "integration": xlround(int_sw + int_hw, 2),
                   "test": test}


# ---------------------------------------------------------------- 表2


def emit_total(wb, deal: dict, pack: StandardPack, *, systems: list[dict],
               purchase_wan: float, hw_wan: float, other_wan: float,
               other_parts: dict, notes: list[str]) -> float:
    sw_wan = xlround(sum(x["cost"] for x in systems) / 1e4, 2)
    total = xlround(sw_wan + purchase_wan + hw_wan + other_wan, 2)
    s = GovSheet(
        wb, "附表2 项目总预算表",
        title="附表 2：项目总预算表",
        subtitle=f"{deal.get('project_name', '')}　"
                 f"编制依据：{pack.data['standard_doc']}"
                 f"（{pack.data['effective_from']} 起施行）",
        columns=[
            Col("序号", "text", width=8),
            Col("名称及类别", "text", width=36),
            Col("预算金额（万元）", "money", width=18),
            Col("备注", "text", width=58),
        ],
        seq=False, rollup=True, clause_required=False)

    s.row({"序号": "一", "名称及类别": "软件开发分项支出",
           "预算金额（万元）": sw_wan,
           "备注": f"{len(systems)} 个子系统，明细见附表3-1／3-2"})
    for i, x in enumerate(sorted(systems, key=lambda v: -v["cost"]), 1):
        s.row({"序号": f"（{i}）", "名称及类别": f"　{x['system']}",
               "预算金额（万元）": xlround(x["cost"] / 1e4, 2),
               "备注": f"调整后 {x['afp']:,.2f} 功能点 / {x['effort_man_months']:,.2f} 人月"})
    s.row({"序号": "二", "名称及类别": "软件和服务购置支出",
           "预算金额（万元）": purchase_wan,
           "备注": "⚠ 待询价，非不涉及 —— 数据模型购置已列人月，见附表4"})
    s.row({"序号": "三", "名称及类别": "硬件购置支出",
           "预算金额（万元）": hw_wan, "备注": "明细见附表5；须逐条过负面清单"})
    s.row({"序号": "四", "名称及类别": "其他建设费用支出",
           "预算金额（万元）": other_wan, "备注": "明细见附表6"})
    s.row({"序号": "（一）", "名称及类别": "　设计费",
           "预算金额（万元）": other_parts["design"], "备注": "超额累进"})
    s.row({"序号": "（二）", "名称及类别": "　系统集成费",
           "预算金额（万元）": other_parts["integration"], "备注": "按标准上限计取"})
    s.row({"序号": "（三）", "名称及类别": "　第三方软件测试费",
           "预算金额（万元）": other_parts["test"], "备注": "超额累进"})
    s.row({"序号": "（四）", "名称及类别": "　其他费用",
           "预算金额（万元）": 0, "备注": "等保/密评不纳入建设预算"})
    s.row({"序号": "", "名称及类别": "合　计", "预算金额（万元）": total,
           "备注": "⚠ 本标准为**限额标准**，算出的是上限，不是应报金额"})
    for n in notes:
        s.note(n)
    s.finish()
    return total


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()

    gs.assert_roles_parseable()
    deal = yaml.safe_load(a.deal.read_text(encoding="utf-8"))
    root = a.deal.parent.parent.parent
    pack = StandardPack.load(root / deal["baseline"])

    if pack.pack_id != PACK_ID:
        raise SystemExit(
            f"本出表器只出山东科目的表，但 deal.baseline 指向 {pack.pack_id}。\n"
            f"  科目体系与标准包绑定 —— 金额算得出来但科目不合规，且表上看不出来。")

    project_type = deal.get("project_type", "新建")
    if project_type != "新建":
        raise SystemExit(
            f"deal.project_type={project_type}，须出附表3-3 升级改造项目功能点计数明细表。\n"
            f"  该表要求「全面、完整填报拟升级改造系统所有功能点，"
            f"包含此次项目未进行任何升级改造的功能点」（p.46 填报说明1）——\n"
            f"  那是现状系统的**全量**清单，不是本次交付范围，BOM 里没有这份数据。\n"
            f"  须业务侧先提供既有系统功能点清单，本出表器才能出 3-3。")

    counting_method = deal.get("counting_method", "估算功能点法")
    if counting_method not in FORM_METHODS:
        raise SystemExit(f"counting_method={counting_method} 不是山东允许的两法之一")

    prof = get_profile(pack.formula_profile)
    bl = _latest_baseline(root, pack)

    effort_only: set[str] = set()
    split_p = a.deal.parent / "costing-method-split.yaml"
    if split_p.exists():
        split = yaml.safe_load(split_p.read_text(encoding="utf-8"))
        effort_only = {x["name"] for x in split["effort_method"]["systems"]}
    elif deal.get("effort_only"):
        effort_only = set(deal["effort_only"])

    size = pack.factor("size_change", counting_method)
    systems = []
    for x in bl["software_dev"]["systems"]:
        if x["system"] in effort_only:
            continue
        det = [r for r in bl["detail"] if r["system"] == x["system"]]
        app_factor = det[0]["app_factor"] if det else 1.0
        app_type = det[0].get("app_type_local") or det[0].get("app_type") or ""
        systems.append({**x, "app_factor": app_factor, "app_type": app_type,
                        "dev_coef": pack.factor("dev_category", x["dev_category"])})

    items_by_sys = _effort_items(root)
    effort_systems = [{"system": s, "items": items_by_sys.get(s, [])}
                      for s in sorted(effort_only)]

    hw = None
    if (a.deal.parent / "device-config.yaml").exists():
        hw = read_device_config(root, a.deal.parent)

    out = a.out or (a.deal.parent / "out-shandong")
    out.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    sw_wan = xlround(sum(x["cost"] for x in systems) / 1e4, 2)
    purchase_wan = emit_purchase(wb, effort_systems, pack)
    hw_yuan, hw_warn = emit_hardware(wb, hw, pack)
    hw_wan = xlround(hw_yuan / 1e4, 2)
    other_wan, other_parts = emit_other_fees(
        wb, pack, prof, sw_dev_wan=sw_wan, sw_purchase_wan=purchase_wan,
        hw_wan=hw_wan, dev_categories={x["dev_category"] for x in systems})

    notes = [
        "⚠ 本标准为**限额标准** —— 算出的是支出预算上限，不是应报金额。"
        "实际报价可低于限额，高于则须单独举证。",
        f"⚠ 二、软件和服务购置支出为 0 是「待询价」而非「不涉及」："
        f"{len(effort_systems)} 段模型/知识工程共 "
        f"{sum(sum(i['man_months'] or 0 for i in x['items']) for x in effort_systems):,.2f} "
        f"人月已在附表4 列明，单价须三家询价确定。",
        "⚠ 运维费不在本套表内 —— 山东标准仅覆盖建设期，运维须独立立项"
        "（与广东相反：广东把运维列入预算按一年测算）。",
        "⚠ 等级保护测评费、密码应用测评费、分级保护测评费不纳入建设项目预算"
        "编制范围（五.(一)4）。",
        "⚠ 标准全文未规定含税口径与金额取整规则，正式报批前须向省财政厅确认"
        "（见 pack.yaml 的 _v2_gaps）。",
    ]
    if hw_warn:
        notes.append("⚠ 硬件疑似触碰负面清单：" + "；".join(hw_warn))

    emit_dev_detail(wb, systems, pack, counting_method)
    emit_fp_count(wb, bl["detail"], effort_only, pack, counting_method)
    total = emit_total(wb, deal, pack, systems=systems, purchase_wan=purchase_wan,
                       hw_wan=hw_wan, other_wan=other_wan,
                       other_parts=other_parts, notes=notes)

    order = ["附表2 项目总预算表", "附表3-1 软件开发分项预算", "附表3-2 功能点计数明细",
             "附表4 软件和服务购置", "附表5 硬件设备购置", "附表6 其他建设费用"]
    wb._sheets = [wb[n] for n in order if n in wb.sheetnames]

    d = deal.get("doc") or {}
    fn = (f"{d.get('prefix', '套表')}_山东编报_{d.get('version', '0.1.0')}_"
          f"{d.get('date', '')}_{d.get('status', '试算版')}.xlsx")
    wb.save(out / fn)
    print(f"山东编报套表 → {out / fn}")
    print(f"  一 软件开发分项支出    {sw_wan:>12,.2f} 万元　{len(systems)} 个子系统")
    print(f"  二 软件和服务购置支出  {purchase_wan:>12,.2f} 万元　⚠ 待询价")
    print(f"  三 硬件购置支出        {hw_wan:>12,.2f} 万元")
    print(f"  四 其他建设费用支出    {other_wan:>12,.2f} 万元")
    print(f"  合计（限额上限）       {total:>12,.2f} 万元")
    for n in notes:
        print(f"  {n.splitlines()[0][:100]}")


if __name__ == "__main__":
    main()
