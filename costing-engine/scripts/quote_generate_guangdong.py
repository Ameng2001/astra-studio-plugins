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

用法：
    python3 quote_generate_guangdong.py --deal deals/<id>/deal.yaml --out out/
"""
from __future__ import annotations

import argparse
import glob
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
    _latest_baseline, _sys_app_type, read_device_config, resolve_reuse,
)

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


def emit_total(wb, deal: dict, pack: StandardPack, *,
               fp_yuan: float, effort_yuan: float,
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
    money = {"1.1": fp_yuan + effort_yuan}
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

    out = a.out or (a.deal.parent / "out-guangdong")
    out.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    fp_yuan = emit_fp_method(wb, fp_systems, pack) if fp_systems else 0.0
    eff_yuan = emit_effort_method(wb, effort_systems, pack) if effort_systems else 0.0

    # ---- 硬件：口径不同，不能直接搬 ----
    # device-config 里的金额是**一次性购置**口径（柳州那套）。广东的
    # 4.2 特殊基础设施服务是按年分摊：
    #     年服务费 = [设备采购成本 + Σ(资金成本)] / 分摊年限 + 年设备运维费
    # 三个参数（分摊年限 ≥6 年、资金成本按五年期贷款基准利率、年运维费率）
    # 都是本商机要定的，deal 里还没有。
    #
    # **所以这里列出采购成本并写明差哪些参数，而不是静默不计列。**
    # 前一版只在 device-config 不存在时才提示，文件存在时既不入表也不提醒 ——
    # 那是「漏了却看不出漏了」，比报错难查得多。
    ni: list[str] = []
    pending: dict[str, str] = {}
    dc_p = a.deal.parent / "device-config.yaml"
    hw_purchase = 0.0
    if dc_p.exists():
        try:
            hw = read_device_config(root, a.deal.parent)
            hw_purchase = sum(r["total"] for r in hw["priced"])
        except Exception as e:            # 读不出来也要说，不能当没有
            ni.append(f"⚠ device-config.yaml 存在但读取失败：{e}")
        if hw_purchase:
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
    else:
        ni.append("⚠ 4.2 特殊基础设施服务（硬件）未计列 —— 本商机尚未建 device-config.yaml。")
    pending["2.1"] = "⚠ 待界定 —— 广东将运维列入预算，本商机运维范围与年限尚未确定"
    pending["2.2"] = "⚠ 待界定 —— 同 2.1"
    ni.append("⚠ 2 运行维护服务费未计列 —— 广东将运维列入预算（按一年测算），"
              "柳州明确不列入建设期预算。本商机的运维范围与年限尚未界定。")
    ni.append("⚠ 本标准未明文规定含税口径与金额取整规则（四册全文已核）——"
              "正式报批前须向省财政厅或采购代理机构确认。")

    total = emit_total(wb, deal, pack, fp_yuan=fp_yuan, effort_yuan=eff_yuan,
                       not_involved=ni, pending=pending)

    d = deal.get("doc") or {}
    fn = (f"{d.get('prefix', '套表')}_广东编报_{d.get('version', '0.1.0')}_"
          f"{d.get('date', '')}_{d.get('status', '试算版')}.xlsx")
    wb.save(out / fn)
    print(f"广东编报套表 → {out / fn}")
    print(f"  1.1 定制软件开发服务  {total / 1e4:>12,.2f} 万元")
    print(f"      功能点法          {fp_yuan / 1e4:>12,.2f} 万元　{len(fp_systems)} 个子系统")
    print(f"      工作量法          {eff_yuan / 1e4:>12,.2f} 万元　{len(effort_systems)} 个子系统")
    print(f"  合计                  {total / 1e4:>12,.2f} 万元")
    for line in ni:
        print(f"  {line.splitlines()[0]}")


if __name__ == "__main__":
    main()
