"""quote_generate_guangdong —— 按广东标准出编报套表。

## 为什么不是给柳州出表器加个开关

广东与柳州**不是同一套表的不同费率，是两套科目体系**：

    柳州   工程费用（软件开发 + 硬件购置 + 系统集成）+ 其他费用（设计/监理/
           评测，各按表11/12/13 的费率上限计取）+ 预备费（工程费用 × 2%）
    广东   软件开发服务费 / 运行维护服务费 / 系统业务运营服务费 /
           基础设施服务费 / 其他服务费 —— 五类服务费，**没有**工程费用、
           预备费这层建设项目口径，也没有按费率计取的设计费与系统集成费

三处口径差异，任何一处用开关糊过去都会出一张「看起来对、科目不合规」的表：

  1. **运维列不列入**。柳州三.(三) 明文「运维不列入建设期预算」；广东表1
     的 2 就是运行维护服务费，按一年测算。同一个项目两地预算口径不同。
  2. **硬件不是一次性购置**。广东走基础设施服务分册的专业基础设施服务，
     [设备采购成本 + Σ资金成本] / 分摊年限 + 年运维费，计算机设备类分摊
     年限 ≥6 年。柳州是一次性购置 + 系统集成费按 4%–8%。
  3. **工作量法是四阶段**（需求设计/编码/测试/部署），柳州五阶段；且广东
     人月单价统一 24,000 元不分阶段，柳州按阶段 1.9/1.9/1.7/1.6/1.4 万。

所以本模块自己组织科目与表，只从柳州出表器复用与科目无关的取数与判型。
计价一律走 `formula_profiles/guangdong_v1.py`，不在这里写公式。

## 表结构的出处

总则《粤财行〔2019〕82号》三、预算编报格式，表1 至表5（p7–13）。
**到表5 为止**，没有表6+；结构逐列记在 `pack.yaml` 的 `sheet_spec`。
本模块只生成本商机用得到的表，未覆盖的表在总表上标明「本商机不涉及」——
不涉及与漏了必须能分辨，这是柳州项目上反复出问题的地方。

## 分册

**册的划分与柳州一致，科目的划分不一致** —— 详见 `BOOK_META` / `build_books`。
出六册：甲（送审册）· 甲附（硬件设备清单）· 乙（计量举证册）为送审件；
丙（内部工作底稿）· 丙附（硬件配置录入）· 丁（内部质检册）为内部件·勿送审。

用法：
    python3 quote_generate_guangdong.py --deal deals/<id>/deal.yaml --out out/
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any

import openpyxl
import yaml

import gov_sheet as gs
from gov_sheet import Col, GovSheet
from nesma_weights import xlround
from standard_pack import StandardPack, get_profile

# 与科目无关的取数/判型，直接复用 —— 这些是 BOM 侧的事实，与哪个省无关
from quote_generate_liuzhou import (
    BookRouter, _archive_previous, _assert_same_params, _latest_baseline,
    _sha, _sha_fp_settings, _split_basis, _sys_app_type, read_device_config,
    resolve_reuse, stamp_baseline,
)
# 举证与试算这四张表**不重写，直接用柳州那套**。它们读的是 pack + fpset +
# baseline，没有一处写死柳州的科目 —— 差别只在条款措辞，由 cite 参数给。
# 重写一份的代价是两份逻辑各自漂移：改了取整口径只改一边，两地的明细
# 就会各自自洽却互不相等，而那种差只有交叉核对时才看得见。
from quote_generate_liuzhou import (
    emit_fp_detail as _lz_fp_detail,
    emit_fp_whatif as _lz_fp_whatif,
    emit_pricing_params as _lz_pricing_params,
    emit_reuse_modules as _lz_reuse_modules,
)

# ============================================================
# 分册登记
# ============================================================
#
# **册的划分与柳州一致，科目的划分不一致。** 这两件事常被当成一件：
# 「广东和柳州不是同一套科目」是真的（见本模块开头），但那说的是**表里装什么**；
# 装好的表**怎么打包成册、册怎么命名、哪几册不能出门**是另一回事 ——
# 它跟标准无关，跟「谁看这份文件」有关，而看的人两地是同一批：
# 评审看甲/甲附/乙，我方自己看丙/丙附/丁。
#
# 所以册号、册名、文件名格式、内部件的「勿送审」标记，一律沿用柳州的约定。
# 一个商机一套叫法，换个省要重学一遍册名，那是自找的事故。
#
# 与柳州的差别只在**册里装哪些表**：
#   甲    广东的编报格式表（表1 + 各分项表）—— 柳州装的是总表 + 三个费用汇总
#   甲附  非标准附表：硬件按场景逐条 + 网点店型汇总（广东无表7 这一张标准表）
#   乙    计价参数 + 4.2 分摊测算过程
#   丙/丁 内部件，同柳州

BOOK_META: dict[str, dict[str, Any]] = {
    "甲":   {"topic": "送审册", "status": None},
    "甲附": {"topic": "硬件设备清单", "status": None},
    "乙":   {"topic": "计量举证册", "status": None},
    "丙":   {"topic": "内部工作底稿", "status": "内部版·勿送审"},
    "丙附": {"topic": "硬件配置录入", "status": "内部版·勿送审"},
    "丁":   {"topic": "内部质检册", "status": "内部版·勿送审"},
}


def build_books(*, has_fp: bool, has_effort: bool,
                has_ops: bool, has_infra: bool) -> dict[str, dict[str, Any]]:
    """按本次实际会出的表登记分册。

    **条件表要在登记时就决定，不能造完再补登记。** BookRouter 的价值全在
    「没登记的表名直接报错」这一条 —— 允许运行中追加登记，等于把那道闸拆了。

    甲附不进路由：它的表名随店型批次动态生成，且是非标准附表，另建工作簿。
    """
    a = ["表1 项目预算总表"]
    if has_fp:
        a.append("表2-1 定制软件开发(功能点法)")
    if has_effort:
        a.append("表2-1 定制软件开发(工作量法)")
    if has_ops:
        a.append("表3-2 软件系统运维")
    b, c = [], ["04_商机决策登记"]
    if has_fp:
        b += ["02_功能点明细", "03_计价参数"]
        c = ["01_试算参数", "02_二层试算", "03_复用度模块清单"] + c
    if has_effort:
        b.append("05_工作量法功能明细")
    b.append("06_计价方法与口径")
    if has_infra:
        b.append("附 4.2 测算过程")
    return {
        "甲": {**BOOK_META["甲"], "sheets": a},
        "乙": {**BOOK_META["乙"], "sheets": b},
        "丙": {**BOOK_META["丙"], "sheets": c},
        "丁": {**BOOK_META["丁"], "sheets": ["01_复核意见"]},
    }


#: 广东工作量法的四阶段，顺序即表内列序（表2-1 方法二，总则 p9）。
#: 柳州是五阶段且各阶段单价不同；广东**不分阶段计价**，四阶段只是工程量
#: 的分解口径，人月单价统一 24,000。把它当成「柳州五阶段去掉一段」是错的。
STAGES_GD = ["需求设计", "编码", "测试", "部署"]


def _effort_items(root: Path) -> dict[str, list[dict]]:
    """按子系统收 BOM 里走工作量法的条目及其 effort_basis。

    取数源是 BOM，不是源估算表 —— 名字是展示字段不是键，靠名字去源表
    认领 effort_basis，任何一次改名都会静默丢行（柳州实测差过 108 人月）。
    """
    out: dict[str, list[dict]] = {}
    for f in glob.glob(str(root / "bom" / "items" / "*.yaml")):
        for i in (yaml.safe_load(Path(f).read_text(encoding="utf-8")) or {}).get("items", []):
            if i.get("class") != "SOFTWARE_EFFORT" or i.get("deprecated_in"):
                continue
            b = i.get("effort_basis") or {}
            out.setdefault(i["path"]["system"], []).append({
                "name": i["name"],
                "unit": b.get("unit", ""), "qty": b.get("qty"),
                "per_unit_mm": b.get("per_unit_mm"),
                "man_months": b.get("man_months"),
                "basis": b.get("basis", ""),
                "counted_from": b.get("counted_from", ""),
                "status": i.get("coauthor_status", ""),
            })
    return out


def _effort_issues(effort_systems: list[dict], pack: StandardPack
                   ) -> list[dict[str, str]]:
    """工作量法人月的**测算依据里引的是不是本标准的常数**。

    这一条是 05_工作量法功能明细 做出来当天就逮到的：康养 BOM 里 236 条
    SOFTWARE_EFFORT 的 basis 写着「人月 = N UFP × 6.51 人时/功能点 ÷ 174」，
    而 6.51 是**柳州**的生产率，广东是 6.65。人月是产品级 BOM 里的数，
    当初是照柳州包倒算出来的；换到广东包，条目一条没改、金额却应当变。

    **金额差只有 2%，正是最难发现的那一类** —— 总额看起来完全正常，
    没有任何一处会报错。露出来靠的是把依据原文印到举证册上：
    一句「取自柳州标准」印在广东的送审件里，评审一眼就看见。
    """
    import re as _re
    want = pack.rate("productivity_hours_per_fp")
    bad: dict[str, int] = {}
    mm_bad = 0.0
    for rec in effort_systems:
        for it in rec["items"]:
            b = str(it.get("basis") or "")
            for m in _re.finditer(r"([\d.]+)\s*人时/功能点", b):
                v = float(m.group(1))
                if abs(v - want) > 1e-9:
                    bad[f"{v} 人时/功能点"] = bad.get(f"{v} 人时/功能点", 0) + 1
                    mm_bad += float(it.get("man_months") or 0)
                break
    if not bad:
        return []
    used = "、".join(f"{k}（{n} 条）" for k, n in sorted(bad.items()))
    rate = pack.rate("base_man_month_rate")
    # 人月 ∝ 1/生产率：本包生产率更高（每功能点耗时更多）→ 人月应上调。
    # 写成 v/want - 1 是反的 —— 方向错的建议比没有建议更糟。
    v0 = float(next(iter(sorted(bad))).split()[0])
    delta = want / v0 - 1
    return [{
        "category": "工作量法", "title": "人月的测算依据引的是别的标准包的生产率",
        "severity": "阻断",
        "finding": f"{sum(bad.values())} 条 SOFTWARE_EFFORT 的 effort_basis 写着 {used}，"
                   f"本标准（{pack.pack_id}）是 {want} 人时/功能点；"
                   f"涉及 {mm_bad:,.2f} 人月 / 约 {mm_bad * rate / 1e4:,.2f} 万元。"
                   f"这些人月是照柳州包倒算的，换包后条目未重算",
        "action": f"按本包生产率重算 effort_basis.man_months（人月 ∝ 1/生产率，"
                  f"{v0} → {want} 应上调约 {delta:.1%}，"
                  f"约 +{mm_bad * delta * rate / 1e4:,.2f} 万元），"
                  f"并把 basis 里的「取自柳州标准」改成本标准出处 —— "
                  f"那句话原样印在乙本 05 的举证列上"}]


def _hardware_issues(hw: dict, deal_dir: Path) -> list[dict[str, str]]:
    """从硬件数据本身判出的自检项 —— 不是人工誊抄的清单。

    誊抄的清单会过期：设备补齐了、参考机型补上了，清单还写着「待补 80 项」。
    这里每一条的数字都是本次生成时数出来的，数没了条目自然就没了。
    """
    out: list[dict[str, str]] = []
    rows = hw["priced"]
    lack = [r for r in rows if (r.get("ref_count") or 0) < 3]
    if lack:
        amt = sum(r["total"] for r in lack)
        out.append({
            "category": "硬件举证", "title": f"{len(lack)} 项设备参考机型不足 3 个",
            "severity": "阻断",
            "finding": f"涉及 {amt / 1e4:,.2f} 万元，占设备采购成本 "
                       f"{amt / max(sum(r['total'] for r in rows), 1e-9):.0%}",
            "action": "按型号补询价单或公开成交价，每项不少于 3 个参考品牌型号"})
    est = [r for r in rows if r.get("price_basis")]
    if est:
        amt = sum(r["total"] for r in est)
        codes = sorted({r["code"] for r in est})
        out.append({
            "category": "硬件计价", "title": f"{len(codes)} 个物料用的是估算价不是询价价",
            "severity": "阻断",
            "finding": f"涉及 {amt / 1e4:,.2f} 万元；料号 {codes[0]}–{codes[-1]}，"
                       f"单价取自对客材料的估算区间，非询价/成交价",
            "action": "正式编报前逐项询价，回填 bom/**/pricing.yaml 并清空 price_basis"})
    if hw["pending"]:
        out.append({
            "category": "硬件计价", "title": f"{len(hw['pending'])} 项设备待核价（列而不计）",
            "severity": "重要",
            "finding": "数量非零、单价为 0，已列于甲附「附 4.2-2」但未计入采购成本",
            "action": "定价后并入附 4.2-1，并同步调整 4.2 的分摊基数"})
    self_made = [r for r in rows if r.get("自研") == "自研"]
    if self_made:
        out.append({
            "category": "硬件举证", "title": f"{len(self_made)} 项自研设备",
            "severity": "重要",
            "finding": f"涉及 {sum(r['total'] for r in self_made) / 1e4:,.2f} 万元，"
                       f"自研件不适用参考机型",
            "action": "须提供成本构成与定价依据"})
    # device-config 自带的复核项 —— 口径类问题只有配置的作者知道，
    # 让它随数据走，而不是每次出表由人记得加一句。
    cfg = yaml.safe_load((deal_dir / "device-config.yaml").read_text(encoding="utf-8"))
    for it in (cfg.get("review_notes") or []):
        out.append({k: str(it.get(k, "")) for k in
                    ("category", "title", "severity", "finding", "action")})
    return out


def _deal_issues(deal: dict, deal_dir: Path) -> list[dict[str, str]]:
    """商机级待补项：暂计 0 的计列额、缺依据的判定。"""
    out: list[dict[str, str]] = []
    lf = deal_dir / "listed-fees.yaml"
    if lf.exists():
        d = yaml.safe_load(lf.read_text(encoding="utf-8")) or {}
        zero = [k for k, v in d.items() if not k.startswith("_") and v == 0.0]
        if zero:
            out.append({
                "category": "其他费用", "title": f"{len(zero)} 项计列额暂计 0",
                "severity": "重要", "finding": "：".join(["暂计 0 的项", "、".join(zero)]),
                "action": "业务侧按梅州项目实际填报 listed-fees.yaml"})
    for k in ("reuse_project_type_basis", "integration_layout_basis",
              "fee_project_type_basis"):
        v = str(deal.get(k) or "")
        if "⚠" in v or "试算" in v:
            out.append({
                "category": "商机判定", "title": k.replace("_basis", "") + " 的依据带保留",
                "severity": "提示", "finding": v[:200],
                "action": "由方案侧/商务侧坐实后更新 deal.yaml"})
    return out


def emit_fp_method(wb, fp_systems: list[dict], pack: StandardPack) -> float:
    """表2-1 方法一：功能点法测算。

    列按总则 p8 原样：预计功能点数 / 基准人月费率 / 直接非人力成本 / 金额 / 依据。
    **直接非人力成本一般不计列**，计列时须说明原因及测算依据（分册 p13）——
    这里恒为 0 并在表上写明，而不是把它悄悄省掉一列。
    """
    s = GovSheet(
        wb, "表2-1 定制软件开发(功能点法)",
        title="表 2-1：定制软件开发服务分项预算表（方法一：功能点法测算）",
        subtitle=f"编制依据：{pack.data.get('doc_no', '')} 软件开发服务分册 5.1.1；"
                 f"生产率 {pack.rate('productivity_hours_per_fp')} 人时/功能点、"
                 f"人月折算 {pack.rate('man_hours_per_month')} 人时/人月、"
                 f"基准人月费率 {pack.rate('base_man_month_rate'):,.0f} 元/人月",
        columns=[
            Col("名称及类别", "text", width=30),
            Col("预计功能点数（个）", "fp", width=16, sum=True),
            Col("基准人月费率（元每人月）", "money", width=18),
            Col("直接非人力成本（元）", "money", width=16, sum=True),
            Col("金额（元）", "money", width=16, sum=True),
            Col("依据", "text", width=52),
        ],
        clause_required=False)
    total = 0.0
    for x in sorted(fp_systems, key=lambda v: -v["cost"]):
        total += x["cost"]
        s.row({
            "名称及类别": f"{x['system']}（{x['app_type']}）",
            "预计功能点数（个）": x["fp"],
            "基准人月费率（元每人月）": x["man_month_rate"],
            "直接非人力成本（元）": 0,
            "金额（元）": x["cost"],
            "依据": (f"UFP {x['ufp']:,.0f} × 类别因子 {x['app_type_factor']} "
                     f"× 复用系数 {x['reuse']:.4f} = {x['fp']:,.2f} 功能点；"
                     f"工作量 {x['effort_man_months']:,.2f} 人月"),
        })
    s.total(expect={"金额（元）": total})
    s.note("直接非人力成本（办公费、差旅费、培训费、采购费、设备折旧费）一般不计列；"
           "本项目未计列，故各行为 0。计列时须说明原因及测算依据（软件开发服务分册 5.1.1）。")
    s.note("须在《服务方案》中明确计算过程（总则 表2-1 备注）。")
    s.finish()
    return total


def emit_effort_method(wb, effort_systems: list[dict], pack: StandardPack) -> float:
    """表2-1 方法二：工作量法测算。

    ⚠ **四阶段，且不分阶段计价**。表里的需求设计/编码/测试/部署四列是工程量
    分解，人月单价统一取基准人月费率。柳州那套「各阶段各有单价」的算法
    在广东没有依据 —— 若把五阶段单价搬过来，金额会错且无条款可引。

    本商机的 BOM 只给到子系统级人月总量，没有四阶段分解，所以四列留空并
    在表上标明。**留空不是漏填**：编造一个四阶段分摊比例，等于给评审一个
    没有依据的数，比空着更难解释。
    """
    s = GovSheet(
        wb, "表2-1 定制软件开发(工作量法)",
        title="表 2-1：定制软件开发服务分项预算表（方法二：工作量法测算）",
        subtitle=f"编制依据：{pack.data.get('doc_no', '')} 软件开发服务分册 5.1.2；"
                 f"人月单价 {pack.rate('base_man_month_rate'):,.0f} 元/人月（不分阶段）",
        columns=[
            Col("子系统名称", "text", width=22),
            Col("模块名称", "text", width=34),
            *[Col(st, "fp", width=10) for st in STAGES_GD],
            Col("小计（人月）", "fp", width=12, sum=True),
            Col("复用度调整系数", "rate", width=13),
            Col("人月单价（元）", "money", width=14),
            Col("预算价（元）", "money", width=16, sum=True),
        ],
        clause_required=False)
    rate = pack.rate("base_man_month_rate")
    total = 0.0
    for sysrec in effort_systems:
        for it in sysrec["items"]:
            mm = float(it.get("man_months") or 0)
            if not mm:
                continue
            # 复用系数已在人月里体现还是要在这里乘？—— 这一层由 pack 的
            # reuse 因子给，逐条乘一次，不能两处都乘。
            ru = sysrec["reuse_factor"]
            yuan = xlround(xlround(mm * ru, 1) * rate, 2)
            total += yuan
            s.row({
                "子系统名称": sysrec["system"], "模块名称": it["name"],
                "小计（人月）": mm, "复用度调整系数": ru,
                "人月单价（元）": rate, "预算价（元）": yuan,
            })
    s.total(expect={"预算价（元）": total})
    s.note("需求设计/编码/测试/部署四列为工程量分解口径（工程量精确到 1 位小数点）。"
           "本商机 BOM 的工作量测算依据给到末级交付物的人月总量，未做四阶段分解，"
           "故四列留空 —— 留空表示未分解，不表示为零。")
    s.note("⚠ 本标准人月单价统一为基准人月费率，**不按阶段区分**（软件开发服务分册 5.1.2）。"
           "柳州标准表1 那套分阶段单价在本标准下无依据，不得套用。")
    s.note("⚠ 复用度调整系数一律取 1.0：本表人月取自 BOM 的工作量测算依据，"
           "是按实际要建的内容估的**净工作量**，非全新开发口径。标准该列的前提是"
           "工程量按全新开发估，净工作量再乘复用系数等于同一笔折减做两遍。"
           "若评审要求按标准列示复用度，须先确认人月口径后重算 —— 这是待裁定项。")
    s.finish()
    return total


# ---------------------------------------------------------------- 运维 / 基础设施

def _filled(v) -> bool:
    """「待定」「null」都算没填。**不把没填当成 0** —— 那是本仓反复吃亏的形状。"""
    return v not in (None, "", "待定", "TBD")


def _ops_config(deal_dir: Path) -> dict | None:
    p = deal_dir / "ops-config.yaml"
    if not p.exists():
        return None
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def emit_ops_software(wb, pack: StandardPack, prof, cfg: dict, afp: float) -> float:
    """表3-2 软件系统运行维护服务分项预算表（方法二：按规模单价）。"""
    r = prof.ops_software_cost(
        afp, pack=pack, level=cfg["运维水平要求因素"], feature=cfg["运维系统特征因素"],
        direct_non_labor=float(cfg.get("直接非人力成本") or 0))
    s = GovSheet(
        wb, "表3-2 软件系统运维",
        title="表 3-2：软件系统运行维护服务分项预算表（方法二：按应用软件运维规模单价）",
        subtitle=f"编制依据：{pack.data.get('doc_no', '')} 运维服务分册 5.2.2；"
                 f"运维功能点单价 {r['unit_price']} 元/功能点"
                 f"（软件运维基准人月费率 20,000 / 人月折算 174 × 运维生产率 1.04）；"
                 f"本标准运维费按一年测算",
        columns=[
            Col("项", "text", width=26),
            Col("取值", "text", width=18),
            Col("系数", "rate", width=10),
            Col("依据", "text", width=56),
        ],
        seq=False, clause_required=False)
    s.row({"项": "调整后功能点数", "取值": f"{afp:,.2f}", "系数": None,
           "依据": "标准要求按验收投运后的实际功能点计；预算阶段取建设期测算值，"
                   "须在《服务方案》说明"})
    s.row({"项": "运维功能点单价（元）", "取值": f"{r['unit_price']}", "系数": None,
           "依据": "运维分册 5.2.2 = 20000/174×1.04"})
    for grp, detail, tag in (("运维水平要求因素", r["level_detail"], "表5/表6"),
                             ("运维系统特征因素", r["feature_detail"], "表7–表11")):
        for k, (choice, val) in detail.items():
            s.row({"项": f"　{k}", "取值": str(choice), "系数": val,
                   "依据": f"{grp} · 运维分册 {tag}"})
    s.row({"项": "运维水平要求因素 小计", "取值": "", "系数": r["level_factor"],
           "依据": "系统更新频率 × 支持方式"})
    s.row({"项": "运维系统特征因素 小计", "取值": "", "系数": r["feature_factor"],
           "依据": "部署方式 × 业务新颖性 × 用户规模 × 系统关联性 × 业务单位数"})
    s.row({"项": "直接非人力成本（元）", "取值": f"{r['direct_non_labor']:,.2f}",
           "系数": None, "依据": "一般不计列；计列须说明原因与测算依据（5.2.1 d）"})
    s.row({"项": "2.2 软件系统运维服务费（元）", "取值": f"{r['cost']:,.2f}",
           "系数": None, "依据": "功能点数 × 单价 × 水平因素 × 特征因素 + 直接非人力成本"})
    s.finish()
    return r["cost"]


def emit_infra_calc(wb, pack: StandardPack, prof, cfg: dict,
                    purchase: float, annual_ops: float) -> float:
    """4.2 特殊基础设施服务的测算过程。

    **这是一张非标准表。** 广东总则的编报格式到表5 为止，4.2 没有分项表 ——
    金额直接列在表1，依据写进《服务方案》。这里把逐年资金成本摊开，是为了
    让《服务方案》有可引用的过程，也让评审能自己加一遍。表头写明它非标准表。
    """
    r = prof.infra_special_service_fee(
        purchase, pack=pack, years=int(cfg["分摊年限"]),
        capital_rate=float(cfg["资金成本年利率"]), annual_ops=annual_ops)
    s = GovSheet(
        wb, "附 4.2 测算过程",
        title="附：4.2 特殊基础设施服务费 测算过程（非标准表，供《服务方案》引用）",
        subtitle=f"编制依据：{pack.data.get('doc_no', '')} 基础设施服务分册 5.3；"
                 f"年服务费 =［设备采购成本 + Σ资金成本］/ 分摊年限 + 年设备运行维护费用；"
                 f"分摊年限 {r['years']} 年、资金成本年利率 {r['capital_rate']:.2%}",
        columns=[
            Col("年度", "text", width=12),
            Col("上年末未收回采购成本（元）", "money", width=24),
            Col("当年资金成本（元）", "money", width=20, sum=True),
        ],
        seq=False, clause_required=False)
    for x in r["schedule"]:
        s.row({"年度": f"第 {x['year']} 年",
               "上年末未收回采购成本（元）": x["outstanding"],
               "当年资金成本（元）": x["interest"]})
    s.total(expect={"当年资金成本（元）": r["capital_total"]})
    s.note(f"设备采购成本 {r['purchase']:,.2f} 元（取自 device-config.yaml，市场询价口径）")
    s.note(f"年设备运行维护费用 {r['annual_ops']:,.2f} 元")
    s.note(f"年服务费 =（{r['purchase']:,.2f} + {r['capital_total']:,.2f}）/ "
           f"{r['years']} + {r['annual_ops']:,.2f} = {r['annual_fee']:,.2f} 元")
    s.note("资金成本逐年计算，基数是**上年末尚未收回**的设备采购成本，"
           "按直线分摊 —— 不是采购成本乘年数（分册 5.3）。")
    s.finish()
    return r["annual_fee"]


def emit_hardware_scene(wb, hw: dict, pack: StandardPack, deal_dir: Path,
                        deal: dict) -> tuple[float, float]:
    """附 4.2-0/1：硬件设备清单（按场景）＋ 网点与店型汇总。

    **这是两张非标准表。** 广东的编报格式到表5 为止，硬件不走一次性购置，
    没有柳州表7 那样的「硬件设备购置预算支出表」—— 4.2 只在表1 列一个年服务费。
    但那个年服务费的分母是设备采购成本，采购成本没有明细表就是一个没法核的数。
    所以这里把它摊开：**按场景组织逐条设备**（4.2 的计费基数从哪来），
    再按店型/网点汇总（单站包 × 站数怎么铺出来），供《服务方案》引用。

    与柳州甲附的差别只在科目归属与表头措辞，行的组织口径一致 ——
    场景是产品事实，不因换省而变。

    返回 (计入金额的设备采购成本, 待核价设备条数)。
    """
    cfg = yaml.safe_load((deal_dir / "device-config.yaml").read_text(encoding="utf-8"))
    per_site: dict[tuple[str, str], Any] = {}
    meta: dict[str, dict] = {}
    for t in cfg["targets"]:
        meta[t["id"]] = t
        for it in t["items"]:
            per_site[(t["id"], it["material"])] = it.get("qty_per_site")

    # ---- 附 4.2-0 网点与店型汇总 ----
    s0 = GovSheet(
        wb, "附 4.2-0 网点与店型汇总",
        title="附：微康养网点与店型设备投资汇总（非标准表，供《服务方案》引用）",
        subtitle=("设备采购成本 = Σ（单站设备包金额 × 该店型站数）。"
                  "本表是 4.2 特殊基础设施服务费的计费基数来源；"
                  "年服务费按［设备采购成本 + Σ资金成本］/ 分摊年限 + 年设备运维费折算，"
                  "见「附 4.2 测算过程」。"),
        columns=[
            Col("店型 / 批次", "text", width=30),
            Col("站数", "int", width=8, sum=True),
            Col("单站设备投资（元）", "money", width=20),
            Col("设备投资小计（元）", "money", width=20, sum=True),
            Col("说明", "text", width=60),
        ],
        seq=True, clause_required=False)
    grand = 0.0
    for t in cfg["targets"]:
        rows = [r for r in hw["priced"] if r["园所"] == t["id"]]
        sub = xlround(sum(r["total"] for r in rows), 2)
        grand += sub
        s0.row({"店型 / 批次": t["id"], "站数": t.get("count"),
                "单站设备投资（元）": (xlround(sub / t["count"], 2)
                                      if t.get("count") else None),
                "设备投资小计（元）": sub,
                "说明": t.get("site_note", "")})
    s0.total(expect={"设备投资小计（元）": xlround(grand, 2)})
    for line in [x for x in (cfg.get("note") or "").split("\n") if x.strip()]:
        s0.note("　" + line.strip())
    s0.finish()

    # ---- 附 4.2-1 设备清单（按场景）----
    s = GovSheet(
        wb, "附 4.2-1 设备清单（按场景）",
        title="附：站点端硬件设备清单（按康养服务场景组织，非标准表）",
        subtitle=("行按「一级场景 · 子场景」归组，场景取自设备目录的场景树"
                  "（产品事实，与地区标准无关）；数量为全批总量（单站量 × 站数），"
                  "单价取产品级参考价。单价为 0 的设备不计入本表金额，"
                  "单列于「附 4.2-2 待核价设备」。"),
        columns=[
            Col("一级场景", "text", width=14, cell_role="locked"),
            Col("子场景", "text", width=18, cell_role="locked"),
            Col("店型 / 批次", "text", width=26),
            Col("设备名称", "text", width=30),
            Col("参考品牌型号", "text", width=24),
            Col("性能参数", "text", width=40),
            Col("物料编码", "text", width=15),
            Col("单位", "text", width=7, cell_role="locked"),
            Col("单站数量", "int", width=10),
            Col("站数", "int", width=7),
            Col("数量", "int", width=9, sum=True),
            Col("单价(元)", "money", width=12, cell_role="locked"),
            Col("金额(元)", "money", width=14, sum=True),
            Col("备注（用途）", "text", width=34),
        ],
        clause_required=False)
    order = {t["id"]: i for i, t in enumerate(cfg["targets"])}
    cur = None
    tot = 0.0
    for r in sorted(hw["priced"],
                    key=lambda x: (x["场景"], x["子场景"],
                                   order.get(x["园所"], 99), -x["total"])):
        if r["子场景"] != cur:
            cur = r["子场景"]
            s.group(f"{r['场景']}　·　{cur}")
        _n = r.get("note_public") if r.get("note_public") is not None else r["note"]
        note = [_n] if _n else []
        if (r.get("ref_count") or 0) < 3:
            note.append("**待补参考机型**（不少于 3 个及价格依据）")
        s.row({"一级场景": r["场景"], "子场景": r["子场景"],
               "店型 / 批次": r["园所"],
               "设备名称": r["name"],
               "参考品牌型号": gs.raw(str(r["model"] or "**待补**")),
               "性能参数": (r["spec"] or "**待补**")[:220],
               "物料编码": gs.raw(str(r["code"] or "")),
               "单位": r["unit"],
               "单站数量": per_site.get((r["园所"], r["code"])),
               "站数": (meta.get(r["园所"]) or {}).get("count"),
               "数量": r["qty"], "单价(元)": r["unit_price"],
               "金额(元)": r["total"], "备注（用途）": "；".join(note)})
        tot += r["total"]
    s.total(expect={"金额(元)": xlround(tot, 2),
                    "数量": sum(r["qty"] for r in hw["priced"])})
    s.note("　本表合计即 4.2 特殊基础设施服务费的**设备采购成本**基数，"
           "不是本项目在建设期一次性列支的金额 —— 广东口径下硬件按年分摊为服务费。")
    s.note("　居家端设备（约 8,000 户、四类失能分层）未列入本表：无逐项配置，"
           "且按租赁＋政府补贴/长护支付投放，不属一次性采购，计入会让分摊基数虚高。")
    s.finish()

    # ---- 附 4.2-2 待核价设备 ----
    if hw["pending"]:
        p = GovSheet(
            wb, "附 4.2-2 待核价设备",
            title="待核价设备清单 —— 已列入建设内容，未计入设备采购成本",
            subtitle=("本表条目数量非零但单价为 0，故**列出但不计金额**。"
                      "列出是为了证明它们不是被漏掉的；不计金额是因为无价可计。"
                      "定价后须并入「附 4.2-1」，并同步调整 4.2 的分摊基数。"),
            columns=[
                Col("店型 / 批次", "text", width=26),
                Col("一级场景", "text", width=13),
                Col("子场景", "text", width=18),
                Col("分项名称", "text", width=28),
                Col("物料编码", "text", width=15),
                Col("单位", "text", width=7),
                Col("数量", "int", width=9, sum=True),
                Col("处置要求", "text", width=44),
            ],
            clause_required=False)
        for r in sorted(hw["pending"],
                        key=lambda x: (x["园所"], x["子场景"], x["name"])):
            p.row({"店型 / 批次": r["园所"], "一级场景": r["场景"],
                   "子场景": r["子场景"], "分项名称": r["name"],
                   "物料编码": gs.raw(str(r["code"] or "")),
                   "单位": r["unit"], "数量": r["qty"],
                   "处置要求": "须提供参考品牌型号不少于 3 个及价格依据"})
        p.total(expect={"数量": sum(r["qty"] for r in hw["pending"])})
        p.finish()
    return xlround(tot, 2), len(hw["pending"])

def emit_effort_detail(wb, effort_systems: list[dict], pack: StandardPack) -> None:
    """【乙】05_工作量法功能明细 —— 逐末级交付物，回答「这条的人月怎么来的」。

    表2-1 方法二给到子系统/模块级人月，那一级答不了「105.90 人月是怎么估的」。
    本表把 BOM 的 `effort_basis` 摊开到可复算：量纲、公式、逐项构成、分档规则。

    ## 为什么不设「数量 × 单价」两列

    走工作量法的几段各有各的公式（Σ要素条数 × 各类单价 / 基准 + 复杂度 /
    基准 + 能力点数…）。硬塞进统一的两列，有几段是空的或错的 —— 保留那两列
    等于制造一个统一口径的假象，而假象一除就露：评审拿 人月÷数量 一算，
    得到的正是我们说不出来源的那个数。

    ## 为什么不出金额

    广东人月单价统一 24,000 元不分阶段，逐条乘一遍与表2-1 并不冲突；
    但金额的口径归属在表2-1（含复用度调整系数列），本表是**工程量**的举证。
    两处都出金额，改了一处没改另一处时对不上，而两张表各自都自洽。
    """
    d = GovSheet(
        wb, "05_工作量法功能明细",
        title="工作量测算明细（逐末级交付物）",
        subtitle=f"编制依据：{pack.data.get('doc_no', '')} 软件开发服务分册 5.1.2。"
                 f"**本表回答「这条的工作量是怎么来的」**；金额与复用度调整见"
                 f"【甲本】表2-1 定制软件开发(工作量法)。"
                 f"⚠ 人月为**净工作量**口径（按实际要建的内容估），非全新开发口径。",
        columns=[
            Col("子系统", "text", width=20),
            Col("末级交付物", "text", width=34),
            Col("可核查量纲", "text", width=18, cell_role="locked"),
            Col("数量", "fp", width=10, cell_role="locked"),
            Col("单位工作量(人月/单位)", "rate", width=16, cell_role="locked"),
            Col("工作量（人月）", "fp", width=13, sum=True),
            Col("测算公式", "text", width=34, cell_role="locked"),
            Col("测算构成（逐项展开）", "text", width=52, cell_role="locked"),
            Col("分档规则", "text", width=44, cell_role="locked"),
            Col("复杂度量化说明", "text", width=60, cell_role="locked"),
            Col("状态", "text", width=18, cell_role="locked"),
        ],
        clause_required=True)
    total = 0.0
    n_nobasis = 0
    for rec in effort_systems:
        items = [i for i in rec["items"] if (i.get("man_months") or 0)]
        if not items:
            continue
        sub = sum(float(i["man_months"]) for i in items)
        total += sub
        d.group(f"■ {rec['system']}　{len(items)} 项　{sub:,.2f} 人月",
                clause="软件开发服务分册 5.1.2")
        for it in sorted(items, key=lambda x: -float(x["man_months"] or 0)):
            fml, cmp_, tier = _split_basis(it.get("basis") or "")
            # counted_from 若与 basis 同源，留空 —— 原样出表会让这一列整列
            # 重复前面几列。空白说明「这批没有逐条量化说明」，重复则说明
            # 「有，但等于没有」，后者更误导。
            cf = (it.get("counted_from") or "").strip()
            if cf and cf == (it.get("basis") or "").strip():
                cf = ""
            if not (it.get("basis") or "").strip():
                n_nobasis += 1
            d.row({"子系统": rec["system"], "末级交付物": it["name"],
                   "可核查量纲": it.get("unit") or "**待补**",
                   "数量": it.get("qty"), "单位工作量(人月/单位)": it.get("per_unit_mm"),
                   "工作量（人月）": xlround(float(it["man_months"]), 4),
                   "测算公式": fml, "测算构成（逐项展开）": cmp_,
                   "分档规则": tier, "复杂度量化说明": cf,
                   "状态": it.get("status") or ""},
                  clause="软件开发服务分册 5.1.2 表2-1 方法二")
    if n_nobasis:
        d.note(f"　⚠ 其中 {n_nobasis} 条**没有测算依据**（effort_basis.basis 为空）——"
               f"人月有数但答不出怎么来的。评审逐条问的正是这一列，须由业务侧补齐。")
    d.note("　【口径】人月为全阶段净工作量，非编码阶段一段；广东人月单价统一"
           "24,000 元不分阶段（分册 5.1.2），故本表不做四阶段分摊。")
    d.total(expect={"工作量（人月）": xlround(total, 4)})
    d.finish()


def emit_method_params(wb, pack: StandardPack, fpset: dict, deal: dict,
                       prof) -> None:
    """【乙】03_计价参数 —— 每个系数取了多少、出自哪一条。

    举证册回答的是「这个数怎么来的」。金额链路上的每一个乘数都要在这里
    找得到取值与条款，否则评审只能拿总额和别的项目比 —— 那不是核算。

    **标准值与本商机取值分两列**。广东的应用类型因子是单值、没有取值空间
    （见 `resolve_fp_settings`），所以这两列理应处处相等；相等本身就是结论，
    合成一列反而看不出「有没有人动过」。
    """
    f = pack.data.get("factors") or {}
    s = GovSheet(
        wb, "06_计价方法与口径",
        title="计价方法、系数取值与两地口径差异",
        subtitle=(f"编制依据：{pack.data.get('doc_no', '')} 软件开发服务分册 5.1。"
                  f"本表列出金额链路上的全部乘数 —— 标准值、本商机取值、条款出处。"),
        columns=[
            Col("参数", "text", width=30),
            Col("标准值", "text", width=16),
            Col("本商机取值", "text", width=16),
            Col("依据 / 条款", "text", width=76),
        ],
        clause_required=False)

    s.group("一、功能点法（分册 5.1.1）")
    prod = pack.rate("productivity_hours_per_fp")
    ratio = fpset["productivity_ratio"]
    s.row({"参数": "软件开发生产率基准", "标准值": f"{prod} 人时/功能点",
           "本商机取值": f"{prod * ratio:.4g} 人时/功能点",
           "依据 / 条款": f"分册 5.1.1；浮动系数 {ratio} —— {fpset['productivity_basis']}"})
    s.row({"参数": "人月折算系数", "标准值": f"{pack.rate('man_hours_per_month')} 人时/人月",
           "本商机取值": f"{pack.rate('man_hours_per_month')} 人时/人月",
           "依据 / 条款": "分册 5.1.1，标准固定值"})
    s.row({"参数": "基准人月费率", "标准值": f"{pack.rate('base_man_month_rate'):,.0f} 元/人月",
           "本商机取值": f"{pack.rate('base_man_month_rate'):,.0f} 元/人月",
           "依据 / 条款": "分册 5.1.1，标准固定值；广东不分阶段计价"})
    sc = f.get("size_change") or {}
    s.row({"参数": "规模变更因子", "标准值": "本标准无此环节", "本商机取值": "不适用",
           "依据 / 条款": sc.get("note", "广东标准公式中无规模变更因子")})

    s.group("二、应用类型因子（表2，本标准为单值）")
    for cat, (val, basis) in sorted(fpset["app_type"].items()):
        std = ((f.get("app_type") or {}).get("values") or {}).get(cat)
        s.row({"参数": cat, "标准值": f"{std}", "本商机取值": f"{val}",
               "依据 / 条款": basis})

    s.group("三、复用度调整系数（表2 注 / 分册 5.1.1）")
    ru = f.get("reuse") or {}
    pt = deal.get("reuse_project_type") or deal.get("project_type", "")
    amap = (ru.get("aliases_by_project_type") or {}).get(
        deal.get("project_type", ""), {})
    for lvl, val in (ru.get("values") or {}).items():
        hit = [k for k, v in amap.items() if v == lvl]
        s.row({"参数": f"复用度 · {lvl}", "标准值": f"{val}",
               "本商机取值": (f"{val}（本商机命中：产品成熟度 {'/'.join(hit)}）"
                              if hit else "—"),
               "依据 / 条款": ru.get("citation", {}).get("quote", "")[:180]})
    s.row({"参数": "项目类型（决定档位映射）", "标准值": "新建 / 升级改造",
           "本商机取值": pt,
           "依据 / 条款": deal.get("reuse_project_type_basis", "**待补依据**")})

    s.group("四、工作量法（分册 5.1.2）")
    s.row({"参数": "阶段划分", "标准值": " / ".join(STAGES_GD),
           "本商机取值": "子系统级人月总量，未做四阶段分解",
           "依据 / 条款": "分册 5.1.2 表2-1 方法二。BOM 的 effort_basis 只给到子系统级；"
                          "编造四阶段分摊比例等于给一个没有依据的数，故留空并标明。"})
    s.row({"参数": "人月单价", "标准值": f"{pack.rate('base_man_month_rate'):,.0f} 元/人月",
           "本商机取值": f"{pack.rate('base_man_month_rate'):,.0f} 元/人月",
           "依据 / 条款": "分册 5.1.2，统一单价不分阶段（柳州按五阶段 1.9/1.9/1.7/1.6/1.4 万，两地不可比）"})
    eff = (deal.get("effort_method_settings") or {})
    rf = (eff.get("risk_factor") or {})
    s.row({"参数": "风险系数", "标准值": "表1 注3", "本商机取值": f"{rf.get('value', '**待定**')}",
           "依据 / 条款": rf.get("basis", "**待补依据**")})
    s.row({"参数": "复用度调整系数（工作量法）", "标准值": "表2-1 方法二设有此列",
           "本商机取值": "1.0",
           "依据 / 条款": "该列的前提是工程量按全新开发估；BOM 的 effort_basis 人月是"
                          "按实际要做的工作估的净值，再乘一次等于同一笔折减做两遍。"})

    s.group("五、计价方法分配（本商机决策）")
    s.row({"参数": "计价方法", "标准值": "两法择一或混合",
           "本商机取值": deal.get("costing_method", ""),
           "依据 / 条款": f"{deal.get('costing_method_clause', '')}　"
                          f"「{deal.get('costing_method_quote', '')}」"})
    s.row({"参数": "计数方法", "标准值": "预估功能点法 / 估算功能点法",
           "本商机取值": deal.get("counting_method", ""),
           "依据 / 条款": "分册 5.1.1"})
    s.row({"参数": "走工作量法的子系统", "标准值": "—",
           "本商机取值": "、".join(deal.get("effort_only") or []) or "无",
           "依据 / 条款": "见 costing-method-split.yaml；其余子系统走功能点法"})
    s.finish()


def emit_worksheet(wb, deal: dict, pack: StandardPack, deal_dir: Path,
                   ops_cfg: dict | None) -> None:
    """【丙】04_商机决策登记 —— 本商机做过哪些决策、哪些还悬着。

    柳州丙本只有 01–03 三张（参数面板 / 二层试算 / 复用度清单）。这一张是
    广东链路多出来的：广东把运维与硬件分摊也列入预算，商机级待定项比柳州多
    一倍（ops-config 三段、分摊三参数），散在各表的备注里翻不齐。

    **内部件。** 它列的是「我方可以改什么、改了金额往哪走」，包括还没定的
    档位与口径 —— 送到评审手里等于把我方的不确定性清单先交出去。
    """
    s = GovSheet(
        wb, "04_商机决策登记",
        title="本商机决策登记（内部件·勿送审）",
        subtitle="商机级决策集中在此。改这里的输入重跑即可，出表器不藏硬编码的数。",
        columns=[
            Col("项", "text", width=26),
            Col("取值 / 状态", "text", width=30),
            Col("在哪一层", "text", width=16),
            Col("依据 / 说明", "text", width=76),
        ],
        clause_required=False)

    s.group("一、标准与范围")
    s.row({"项": "标准包", "取值 / 状态": pack.pack_id, "在哪一层": "商机级",
           "依据 / 说明": (deal.get("baseline_note") or "")[:400]})
    s.row({"项": "项目类型", "取值 / 状态": deal.get("project_type", ""),
           "在哪一层": "商机级", "依据 / 说明": deal.get("reuse_project_type_basis", "")})
    comp = deal.get("compose") or {}
    for layer, group in comp.items():
        for name, spec in (group or {}).items():
            s.row({"项": f"范围 · {layer}/{name}",
                   "取值 / 状态": f"v{spec.get('version', '')}　"
                                  f"{len(spec.get('include') or [])} 个子系统",
                   "在哪一层": "商机级（裁剪只在 deal 层）",
                   "依据 / 说明": "、".join(spec.get("include") or [])[:300]})

    s.group("二、待定项 —— 这些没定，相应科目就不计列")
    if ops_cfg is None:
        s.row({"项": "ops-config.yaml", "取值 / 状态": "**未建**", "在哪一层": "商机级",
               "依据 / 说明": "缺它则科目 2 运维服务费整体不计列。"
                              "广东把运维列入预算（按一年测算），柳州不列入建设期预算。"})
    else:
        for blk in ("软件运维", "基础设施运维", "特殊基础设施"):
            d = ops_cfg.get(blk) or {}
            todo = [k for k, v in _flat(d).items() if not _filled(v)]
            s.row({"项": f"ops-config · {blk}",
                   "取值 / 状态": ("已填齐" if not todo else f"**{len(todo)} 项待定**"),
                   "在哪一层": "商机级",
                   "依据 / 说明": ("、".join(todo[:8]) + ("…" if len(todo) > 8 else ""))
                                  or "填的是档位名不是系数值 —— 系数由引擎查表得出，依据自动跟着走"})
    lf = deal_dir / "listed-fees.yaml"
    if lf.exists():
        d = yaml.safe_load(lf.read_text(encoding="utf-8")) or {}
        zero = [k for k, v in d.items() if not k.startswith("_") and v == 0.0]
        s.row({"项": "listed-fees.yaml", "取值 / 状态": (f"**{len(zero)} 项暂计 0**"
                                                        if zero else "已填"),
               "在哪一层": "商机级",
               "依据 / 说明": ("暂计 0 的：" + "、".join(zero) + "。"
                               if zero else "") + str(d.get("_note", ""))[:260]})

    s.group("三、硬件配置（第二层）")
    dc = deal_dir / "device-config.yaml"
    if dc.exists():
        cfg = yaml.safe_load(dc.read_text(encoding="utf-8"))
        for t in cfg["targets"]:
            s.row({"项": f"店型 · {t['id']}",
                   "取值 / 状态": f"{t.get('count', 1)} 站 × "
                                  f"{len(t['items'])} 项设备",
                   "在哪一层": "商机级（第二层配置）",
                   "依据 / 说明": t.get("site_note", "")})
        s.row({"项": "配置来源", "取值 / 状态": "见 device-config.yaml",
               "在哪一层": "商机级",
               "依据 / 说明": "；".join(cfg.get("bootstrapped_from") or [])[:400]})
    else:
        s.row({"项": "device-config.yaml", "取值 / 状态": "**未建**", "在哪一层": "商机级",
               "依据 / 说明": "缺它则 4.2 特殊基础设施服务无计费基数"})
    s.finish()


def _flat(d: Any, pre: str = "") -> dict[str, Any]:
    """把嵌套配置摊平成「a.b.c → 值」，只为列出还没填的项。"""
    out: dict[str, Any] = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        if str(k).startswith("_") or k in ("enabled", "功能点口径", "note"):
            continue
        key = f"{pre}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        else:
            out[key] = v
    return out


def emit_review(wb, issues: list[dict]) -> None:
    """【丁】01_复核意见 —— 我方自己查出来的问题。

    **不送审。** 这一页列的是超上限项、待核价、待补依据 ——
    送到评审手里等于替对方把核减清单先写好。
    """
    s = GovSheet(
        wb, "01_复核意见",
        title="出表自检 · 复核意见（内部件·勿送审）",
        subtitle="每条都是本次生成时由数据判定的，不是人工誊抄的清单。"
                 "严重度：阻断＝不解决不能报；重要＝报了会被问；提示＝知悉即可。",
        columns=[
            Col("类别", "text", width=16),
            Col("事项", "text", width=40),
            Col("严重度", "text", width=8),
            Col("我方结论", "text", width=54),
            Col("处置要求", "text", width=54),
        ],
        clause_required=False)
    rank = {"阻断": 0, "重要": 1, "提示": 2}
    for it in sorted(issues, key=lambda x: (rank.get(x["severity"], 9),
                                            x["category"])):
        s.row({"类别": it["category"], "事项": it["title"],
               "严重度": it["severity"], "我方结论": it["finding"],
               "处置要求": it["action"]})
    s.finish()

def emit_total(wb, deal: dict, pack: StandardPack, *,
               money: dict[str, float],
               not_involved: list[str],
               pending: dict[str, str] | None = None) -> float:
    """表1 项目预算总表 —— 五大类 18 子科目，序号与顺序按标准原样。

    **未涉及的科目也列出来并标明**，不省行。省掉的行在评审眼里与「漏了」
    没有区别；而广东的科目比本商机用到的多，正是最容易被读成漏项的地方。

    `pending` 按科目号给「有内容但尚未计列」的说明。**这一类必须与
    「本商机不涉及」区分开**：4.2 特殊基础设施服务下有 448 条设备配置，
    只是口径未定还没折算成年服务费 —— 在备注里写「本商机不涉及」是一句
    假陈述，评审据此就不会再问硬件，而硬件是这个项目最大的一块。
    """
    pending = pending or {}
    subs = pack.data["budget_subjects"]["subjects"]
    s = GovSheet(
        wb, "表1 项目预算总表",
        title="表 1：省级政务信息化服务项目预算总表",
        subtitle=(f"{deal['project_name']}　编制依据：{pack.data.get('doc_no', '')} "
                  f"《{pack.data.get('doc_title', '')}》三、预算编报格式"),
        columns=[
            Col("名称", "text", width=34),
            Col("金额（元）", "money", width=18, sum=True),
            Col("备注", "text", width=60),
        ],
        seq=False, clause_required=False)
    money = {k: v for k, v in money.items() if v}
    total = 0.0
    for top in subs:
        # 大类金额 = 其子科目之和；标准的表里大类行也有金额格
        sub_sum = sum(money.get(c["no"], 0.0) for c in top.get("children", []))
        total += sub_sum
        has_pending = any(c["no"] in pending for c in top.get("children", []))
        s.group(f"{top['no']}　{top['name']}",
                {"金额（元）": sub_sum or None,
                 "备注": top.get("note", "") or
                         ("" if sub_sum else "部分科目待计列，见下" if has_pending
                          else "本商机不涉及")})
        for c in top.get("children", []):
            v = money.get(c["no"], 0.0)
            if v:
                note = c.get("pack_ref", "")
            elif c["no"] in pending:
                note = pending[c["no"]]          # 有内容，待计列 —— 不是不涉及
            else:
                note = f"本商机不涉及（{c.get('pack_ref', '')}）".rstrip("（）")
            s.row({"名称": f"　{c['no']}　{c['name']}",
                   "金额（元）": v or None, "备注": note})
    s.total("总计", expect={"金额（元）": total})
    for line in not_involved:
        s.note(line)
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

    if pack.pack_id != "guangdong-2019":
        raise SystemExit(
            f"本出表器只出广东科目的表，但 deal.baseline 指向 {pack.pack_id}。\n"
            f"  科目体系与标准包绑定 —— 拿广东的表装柳州的数，"
            f"金额算得出来但科目不合规，且表上看不出来。")
    if not pack.data.get("budget_subjects"):
        raise SystemExit("标准包缺 budget_subjects —— 无法组织表1 的科目结构")

    prof = get_profile(pack.formula_profile)
    bl = _latest_baseline(root, pack)
    split_p = a.deal.parent / "costing-method-split.yaml"
    effort_only: set[str] = set()
    if split_p.exists():
        split = yaml.safe_load(split_p.read_text(encoding="utf-8"))
        effort_only = {x["name"] for x in split["effort_method"]["systems"]}
    elif deal.get("effort_only"):
        effort_only = set(deal["effort_only"])

    fpset = prof.resolve_fp_settings(pack, deal.get("fp_method_settings"))
    in_fp = [x for x in bl["software_dev"]["systems"]
             if x["system"] not in effort_only]
    ru = resolve_reuse(root, pack, deal, [x["system"] for x in in_fp],
                       detail=bl.get("detail"))
    fp_systems = []
    for x in in_fp:
        info = ru[x["system"]]
        at = _sys_app_type(root, x["system"])
        # 同上：产品事实 → 本包词表，查取值前先过 aliases
        cat_v = fpset["app_type"][pack.factor_label("app_type", at)][0]
        grps = [{**g, "contrib": g["ufp"] * cat_v * g["reuse_factor"]}
                for g in info["groups"]]
        c = prof.fp_cost(sum(g["ufp"] * g["reuse_factor"] for g in grps),
                         pack=pack, app_type=at, settings=fpset, reuse_level="低")
        fp_systems.append({
            # 表上写广东自己的词（同柳州出表器的理由）
            **x, **info, "groups": grps,
            "app_type": pack.factor_label("app_type", at),
            "app_type_factor": cat_v, "man_month_rate": c["man_month_rate"],
            "fp": c["fp"], "effort_man_months": c["effort_man_months"],
            "cost": c["cost"], "reuse": info["reuse_factor"]})

    # ---- 工作量法各段 ----
    # **复用系数取 1.0，且这是一个需要裁定的口径，不是默认值。**
    #
    # 广东表2-1 方法二有「复用度调整系数」列，那一列的前提是工程量按
    # **全新开发**估；BOM 的 effort_basis 人月是业务侧按实际要做的工作估的
    # 净值（本体工程 105.90 人月 = Σ 各类要素条数 × 该类单价，数的是真要
    # 建的要素）。净工作量再乘一次复用系数，等于把同一笔折减做两遍。
    #
    # 不用 resolve_reuse：那是功能点法的路径，按 BOM 的 FP 模块 UFP 加权，
    # 而工作量法条目是 SOFTWARE_EFFORT，没有 FP 模块 —— 硬套会拿不到模块而报错。
    items_by_sys = _effort_items(root)
    effort_systems = [{"system": s, "items": items_by_sys.get(s, []),
                       "reuse_factor": 1.0,
                       "reuse_note": "BOM 人月为净工作量口径，未再乘复用系数"}
                      for s in sorted(effort_only)]

    out = a.out or (a.deal.parent / "out")
    out.mkdir(parents=True, exist_ok=True)
    d = deal.get("doc") or {}
    prefix, ver = d.get("prefix", "套表"), str(d.get("version", "0.1.0"))
    date, status = str(d.get("date", "")), d.get("status", "试算版")
    _archive_previous(out, date, ver)

    # ============================================================
    # 一、先把「这一版会不会出这张表」判完 —— 再登记分册
    # ============================================================
    # BookRouter 的全部价值在「没登记的表名直接报错」这一条。若允许一边造表
    # 一边补登记，那道闸就等于没有：以后有人加一张内部用的表，它会静静进到
    # 送审册里跟着发出去，而没有任何地方会响。所以条件表的判定必须前置。
    ni: list[str] = []
    pending: dict[str, str] = {}
    issues: list[dict[str, str]] = []

    dc_p = a.deal.parent / "device-config.yaml"
    hw: dict[str, Any] | None = None
    hw_purchase, n_pend = 0.0, 0
    if dc_p.exists():
        try:
            hw = read_device_config(root, a.deal.parent)
            hw_purchase = xlround(sum(r["total"] for r in hw["priced"]), 2)
            n_pend = len(hw["pending"])
        except Exception as e:            # 读不出来也要说，不能当没有
            ni.append(f"⚠ device-config.yaml 存在但读取失败：{e}")
            issues.append({"category": "硬件配置", "title": "device-config 读取失败",
                           "severity": "阻断", "finding": str(e)[:200],
                           "action": "修好配置再出表；硬件段现为空"})
    else:
        ni.append("⚠ 4.2 特殊基础设施服务（硬件）未计列 —— 本商机尚未建 device-config.yaml。")

    cfg = _ops_config(a.deal.parent)
    sw = (cfg or {}).get("软件运维") or {}
    need = ([f"软件运维.运维水平要求因素.{k}"
             for k in ("系统更新频率", "支持方式")
             if not _filled((sw.get("运维水平要求因素") or {}).get(k))]
            + [f"软件运维.运维系统特征因素.{k}"
               for k in ("部署方式", "业务新颖性", "用户规模",
                         "系统关联性", "业务单位数")
               if not _filled((sw.get("运维系统特征因素") or {}).get(k))])
    has_ops = bool(cfg) and sw.get("enabled") is True and not need
    sp = ((cfg or {}).get("特殊基础设施") or {})
    ops_blocked = ((sp.get("年设备运维费") or {}).get("status") == "阻塞")
    has_infra = bool(hw_purchase) and sp.get("enabled") is True and not ops_blocked \
        and _filled(sp.get("分摊年限")) and _filled(sp.get("资金成本年利率"))

    wb = BookRouter(build_books(has_fp=bool(fp_systems),
                                has_effort=bool(effort_systems),
                                has_ops=has_ops, has_infra=has_infra))

    # ============================================================
    # 二、【甲】编报格式表
    # ============================================================
    fp_yuan = emit_fp_method(wb, fp_systems, pack) if fp_systems else 0.0
    eff_yuan = emit_effort_method(wb, effort_systems, pack) if effort_systems else 0.0
    money: dict[str, float] = {"1.1": fp_yuan + eff_yuan}

    # ============================================================
    # 三、【甲附】硬件设备清单 —— 另建工作簿，不进路由
    # ============================================================
    # 表名随店型批次动态生成，且是非标准附表（广东无表7）。同柳州甲附的处理。
    hwb = None
    if hw is not None:
        hwb = gs.new_workbook()
        hw_purchase, n_pend = emit_hardware_scene(
            hwb, hw, pack, a.deal.parent, deal)
        if n_pend:
            ni.append(f"⚠ 站点端硬件有 {n_pend} 项数量非零但单价为 0，"
                      f"**未计入设备采购成本**，见甲附「附 4.2-2 待核价设备」。")
        issues.extend(_hardware_issues(hw, a.deal.parent))

    # ---- 硬件：口径不同，不能直接搬 ----
    # device-config 里的金额是**一次性购置**口径（柳州那套）。广东的
    # 4.2 特殊基础设施服务是按年分摊：
    #     年服务费 = [设备采购成本 + Σ(资金成本)] / 分摊年限 + 年设备运维费
    # **所以列出采购成本并写明差哪些参数，而不是静默不计列** ——
    # 「漏了却看不出漏了」比报错难查得多。
    if hw_purchase and not has_infra:
        pending["4.2"] = (
            f"⚠ **待计列，非不涉及** —— 已有设备配置，设备采购成本合计 "
            f"{hw_purchase / 1e4:,.2f} 万元（一次性购置口径）。广东按年分摊，"
            f"尚缺分摊年限、资金成本、年运维费率三项参数")
        ni.append(
            f"⚠ 4.2 特殊基础设施服务（硬件）**未计列**，但本商机已有设备配置：\n"
            f"    设备采购成本合计 {hw_purchase / 1e4:,.2f} 万元"
            f"（一次性购置口径，取自 device-config.yaml）。\n"
            f"    广东按年分摊计列：[设备采购成本 + Σ资金成本] / 分摊年限 + 年设备运维费。\n"
            f"    尚缺三个商机级参数，齐备后方可折算年服务费：\n"
            f"      · 分摊年限（原则上不低于折旧年限，计算机设备类 ≥6 年）\n"
            f"      · 资金成本（按人行五年期贷款基准利率，中小微企业上浮 ≤50%）\n"
            f"      · 年设备运维费率（参照运维分册硬件设备原值比例系数）\n"
            f"    ⚠ 上面那个数**不能直接填进表**：两地口径不同，直接搬会高估。")
        issues.append({
            "category": "科目 4.2", "title": "特殊基础设施服务（硬件）待计列",
            "severity": "阻断",
            "finding": f"设备采购成本 {hw_purchase / 1e4:,.2f} 万元已算出，"
                       f"但分摊年限 / 资金成本年利率 / 年设备运维费率三项未定，"
                       f"无法折算年服务费",
            "action": "在 ops-config.yaml 的「特殊基础设施」段填三项参数后重跑"})

    # ============================================================
    # 四、【甲】运维（科目 2）与【乙】4.2 测算过程
    # ============================================================
    if cfg is None:
        pending["2.1"] = pending["2.2"] = (
            "⚠ 待计列 —— 本商机尚未建 ops-config.yaml")
        ni.append("⚠ 2 运行维护服务费未计列 —— 缺 ops-config.yaml。"
                  "广东将运维列入预算（按一年测算），柳州明确不列入建设期预算。")
        issues.append({"category": "科目 2", "title": "运行维护服务费整体未计列",
                       "severity": "阻断", "finding": "缺 ops-config.yaml",
                       "action": "建 ops-config.yaml 并填档位；广东把运维列入预算"})
    else:
        if has_ops:
            money["2.2"] = emit_ops_software(
                wb, pack, prof, sw, sum(x["fp"] for x in fp_systems))
        elif sw.get("enabled") is False:
            pass                                   # 显式不涉及，走「本商机不涉及」
        else:
            pending["2.2"] = ("⚠ 待计列 —— ops-config.yaml 里这些项还是「待定」："
                              + "、".join(need[:3]) + ("…" if len(need) > 3 else "")
                              if need else "⚠ 待计列 —— 软件运维 enabled 未定")
            ni.append("⚠ 2.2 软件系统运维未计列：公式与全部系数标准已给"
                      "（运维功能点单价 119.54 元/功能点），只差 7 个档位选择，"
                      "见 ops-config.yaml。")
            issues.append({
                "category": "科目 2.2", "title": "软件系统运维待计列",
                "severity": "重要",
                "finding": f"公式与系数标准已给，差 {len(need) or 1} 个档位选择："
                           + "、".join(need[:5]),
                "action": "业务侧在 ops-config.yaml 填档位名（不是系数值）后重跑"})

        if (cfg.get("基础设施运维") or {}).get("enabled") is not False:
            pending["2.1"] = ("⚠ 待界定 —— 广东 2.1 指必须保留的**自建机房**基础环境"
                              "与硬件设备运维，不含政务云。本商机是否有自建机房未定")
            issues.append({
                "category": "科目 2.1", "title": "基础设施运维待界定",
                "severity": "重要", "finding": "本商机是否有必须保留的自建机房未定",
                "action": "方案侧确认部署形态；政务云不走 2.1"})

    if has_infra:
        money["4.2"] = emit_infra_calc(
            wb, pack, prof, sp, hw_purchase,
            float(sp.get("年设备运维费_金额") or 0))

    ni.append("⚠ 本标准未明文规定含税口径与金额取整规则（四册全文已核）——"
              "正式报批前须向省财政厅或采购代理机构确认。")
    issues.append({
        "category": "口径", "title": "含税口径与金额取整规则无明文",
        "severity": "重要", "finding": "本标准四册全文已核，未规定含税口径与取整规则",
        "action": "正式报批前向省财政厅或采购代理机构确认"})
    issues.extend(_deal_issues(deal, a.deal.parent))
    issues.extend(_effort_issues(effort_systems, pack))

    # ============================================================
    # 五、【乙】计价参数　【丙】试算参数　【丁】复核意见
    # ============================================================
    if fp_systems:
        # 复用度到**功能模块**，所以按 (子系统, 模块) 传，不是按子系统 ——
        # 写成子系统级常量的话，某个模块一旦按复用度高计列，明细的调整后
        # 功能点合计就与表2-1 分家，而两张表各自内部都是自洽的。
        _lz_fp_detail(wb, root, pack, effort_only, fpset,
                      sum(x["ufp"] for x in fp_systems),
                      reuse_of={x["system"]: {m: g["reuse_factor"]
                                              for g in x["groups"]
                                              for m in g["modules"]}
                                for x in fp_systems})
        # 计价参数出两份：乙本作举证，丙本作试算面板。**不是冗余，是被跨簿
        # 引用逼出来的** —— 活公式跨工作簿要写 `='[文件名.xlsx]表'!C4`，
        # 文件名带版本号，下一版必断。同一次生成的两份来自同一个 fpset，
        # 版本内不可能漂移，且 _assert_same_params 当场逐格核过。
        pe, _ = _lz_pricing_params(
            wb, pack, fpset, fp_systems, name="03_计价参数",
            deal_counting=deal.get("counting_method"),
            single_value_factors=True,
            subtitle_tail="本表为**举证件**：数由引擎算定，不参与计算。"
                          "要改系数看影响，用【丙本】的试算表。")
        pt, prows_t = _lz_pricing_params(
            wb, pack, fpset, fp_systems, name="01_试算参数",
            deal_counting=deal.get("counting_method"),
            subtitle_tail="本表是【丙本】02_二层试算 的参数面板，"
                          "改这里试算表全表重算。**内容与【乙本】03_计价参数 逐格相同**，"
                          "那一份是举证件。",
            single_value_factors=True)
        _assert_same_params(pe, pt)
        # ⚠ 广东的软件类别因子是**单值**（表2：业务处理 1.0 / 应用集成 1.2 /
        # 大数据多媒体 1.3 / 人工智能 1.5），没有柳州那样的取值区间。所以本表
        # 能试的不是「同一类别取区间内哪个值」，而是「这个子系统归哪一类」——
        # 那是 BOM 里的条目属性（第一层）。改类别看金额是合法的试算，
        # 改因子本身不是：偏离单值即不按标准取值，无条款可引。
        _lz_fp_whatif(wb, pack, fpset, fp_systems, prows_t, fp_yuan,
                      param_sheet="01_试算参数", root=root,
                      single_value_factors=True)
        _lz_reuse_modules(wb, root, pack, fpset, fp_systems,
                          bl.get("detail") or [])
    if effort_systems:
        emit_effort_detail(wb, effort_systems, pack)
    emit_method_params(wb, pack, fpset, deal, prof)
    emit_worksheet(wb, deal, pack, a.deal.parent, cfg)
    emit_review(wb, issues)

    total = emit_total(wb, deal, pack, money=money,
                       not_involved=ni, pending=pending)

    # ============================================================
    # 六、分册落盘
    # ============================================================
    baseline = {
        "生成时间": date, "版本": ver, "deal_id": deal["deal_id"],
        "标准包": pack.pack_id,
        "fp_method_settings.sha": _sha_fp_settings(a.deal),
        "device-config.yaml.sha": _sha(dc_p),
        "说明": "本册由造价引擎按上列输入生成。指纹供同步时做冲突检测 —— "
                "输入已被别人改过时指纹对不上，拒绝覆盖。",
    }
    books_out: list[tuple[str, Path]] = []
    for b, bwb in wb.finish().items():
        spec = wb.books[b]
        f = out / gs.doc_name(prefix, b, spec["topic"], ver, date,
                              spec["status"] or status)
        stamp_baseline(bwb, baseline)
        gs.save(bwb, f, sheets_expected=spec["sheets"])
        books_out.append((b, f))

    if hwb is not None:
        spec = BOOK_META["甲附"]
        f = out / gs.doc_name(prefix, "甲附", spec["topic"], ver, date,
                              spec["status"] or status)
        stamp_baseline(hwb, baseline)
        gs.save(hwb, f)
        books_out.append(("甲附", f))

        # 丙附 —— 硬件配置录入工具。带联动活公式，不走 GovSheet 路由。
        import device_quote_tool as dqt
        spec = BOOK_META["丙附"]
        f = out / gs.doc_name(prefix, "丙附", spec["topic"], ver, date,
                              spec["status"] or status)
        dqt.emit(root, a.deal.parent, f, baseline=baseline)
        books_out.append(("丙附", f))

    # 每一册都落了盘吗 —— 登记表是唯一出处，漏一册不会有别的地方提醒
    want = set(wb.books) | ({"甲附", "丙附"} if hwb is not None else set())
    got = {b for b, _ in books_out}
    if want - got:
        raise gs.GovSheetError(
            f"以下分册登记了但没出：{sorted(want - got)}　（已出：{sorted(got)}）")

    (out / "generate-report.json").write_text(json.dumps({
        "deal": deal["deal_id"], "pack": pack.pack_id,
        "emitter": "guangdong",
        "costing_method": deal.get("costing_method"),
        "subjects_yuan": {k: xlround(v, 2) for k, v in sorted(money.items())},
        "software_fp_method_yuan": xlround(fp_yuan, 2),
        "software_effort_method_yuan": xlround(eff_yuan, 2),
        "hardware_purchase_yuan": hw_purchase,
        "hardware_pending": n_pend,
        "grand_total_yuan": xlround(total, 2),
        "subjects_pending": sorted(pending),
        "books": {b: f.name for b, f in books_out},
        "issues": issues,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # **逐科目打印，不把总额挂在 1.1 名下** —— 接上运维与基础设施之后，
    # 总额不再等于 1.1，控制台那行若还写「1.1」就是一句假陈述。
    NAMES = {"1.1": "定制软件开发服务", "2.1": "基础设施运行维护服务",
             "2.2": "软件系统运行维护服务", "4.2": "特殊基础设施服务"}
    print(f"广东编报套表 {len(books_out)} 册 → {out}")
    for b, f in books_out:
        print(f"  {b:<3}{f.name}")
    for no in sorted(money):
        print(f"  {no} {NAMES.get(no, ''):<16}{money[no] / 1e4:>12,.2f} 万元")
        if no == "1.1":
            print(f"      功能点法        {fp_yuan / 1e4:>12,.2f} 万元　"
                  f"{len(fp_systems)} 个子系统")
            print(f"      工作量法        {eff_yuan / 1e4:>12,.2f} 万元　"
                  f"{len(effort_systems)} 个子系统")
    print(f"  合计                {total / 1e4:>12,.2f} 万元")
    if hw_purchase:
        print(f"  设备采购成本（4.2 计费基数，未计入总额）"
              f"{hw_purchase / 1e4:>10,.2f} 万元　待核价 {n_pend} 条")
    print(f"  复核意见 {len(issues)} 条　"
          f"阻断 {sum(1 for i in issues if i['severity'] == '阻断')}、"
          f"重要 {sum(1 for i in issues if i['severity'] == '重要')}")
    for line in ni:
        print(f"  {line.splitlines()[0]}")


if __name__ == "__main__":
    main()
