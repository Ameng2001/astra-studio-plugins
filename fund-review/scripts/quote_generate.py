"""quote_generate — 三层合成 → 报价输出物。

产出（送审包，命名与结构对齐政府送审惯例，见 `gov_sheet`）：
  …_Z00_项目总报价汇总_…xlsx   一页纸看清总额、构成、占比、依据 + 附件索引
  …_Z01_建设期采购清单_…xlsx   按标准科目树组织，每行带条款出处
  …_Z02_功能点测算表_…xlsx     00 汇总 / 01 明细 / 02 计价参数
  …_Z03_运营期费用清单_…xlsx   逐项运营期成本 + 一致性检查
  …_Z04_交付方式TCO对比_…xlsx  各模式 3 年 / 5 年 TCO
  Z05-编制说明.md              计数方法、参数取值与出处、假设与边界
  deal.lock.json               版本锁：BOM 版本 + 标准包 + 交付配置

**数值由引擎算定，Excel 不承担计算。** 从根上消除 P0 那类 SUMIF/VLOOKUP
因单元格布局失效的缺陷。

唯二的活公式是**合计行的 `=SUM()`** 与**明细的「复算式」`=ROUND(...)`** ——
它们的存在是为了让评审能点开自己验。正因如此，写之前必须自己先验过：
`GovSheet.total(expect=)` 逐列比对引擎值，对不上就拒绝出表。

用法：
    python3 quote_generate.py --bom <dir> --pack <dir> --out <deal_dir> \
        [--deal-id <id>] [--as-of <bom_version>] [--include-placeholders]
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl

import gov_sheet
from bom_schema import Bom, is_fp_counted
from costing_engine import (PLACEHOLDER_TAG, CostingEngine, DealConfig,
                            DeliveryContext, lock, snapshot, xlround)
from delivery_matrix import DeliveryPlan
from ops_model import OpsModel, tco
from standard_pack import StandardPack


def load_baseline(bdir: Path) -> tuple[dict, dict, Bom, StandardPack]:
    """从第二层产物解析出这次报价的 BOM 与标准包，并校验哈希。

    第三层**不重新实现聚合逻辑** —— 仍由引擎算。基准的作用是两个：
      1. 声明依赖：这份报价建立在哪个 (BOM 版本 × 标准包) 上，有据可查
      2. 完整性断言：引擎在无交付方案下算出的 UFP/AFP 必须与基准一致，
         对不上说明输入被人动过而基准没重建 —— 那时报价就不可信了

    只记 pack_id 而不校验哈希是不够的：标准包改了一个系数、pack_id 不变，
    基准过期而报价照出，没人会发现。
    """
    import hashlib
    lock = json.loads((bdir / "baseline.lock.json").read_text(encoding="utf-8"))
    bl = json.loads((bdir / "baseline.json").read_text(encoding="utf-8"))
    src = lock.get("source_paths") or {}
    bom_dir, pack_dir = Path(src.get("bom", "bom")), Path(src.get("pack", ""))

    def digest(paths: list[Path]) -> str:
        h = hashlib.sha256()
        for q in sorted(paths):
            h.update(q.read_bytes())
        return h.hexdigest()[:16]

    now_bom = digest(sorted((bom_dir / "items").glob("*.yaml")))
    now_pack = digest([pack_dir / "pack.yaml" if pack_dir.is_dir() else pack_dir])
    drift = []
    if now_bom != lock["bom"]["items_sha256_16"]:
        drift.append(f"BOM 条目已变（基准 {lock['bom']['items_sha256_16']} → 现 {now_bom}）")
    if now_pack != lock["standard_pack"]["sha256_16"]:
        drift.append(f"标准包已变（基准 {lock['standard_pack']['sha256_16']} → 现 {now_pack}）")
    if drift:
        raise SystemExit(
            "基准已过期，拒绝出报价：\n  " + "\n  ".join(drift) +
            f"\n  请先重建基准：baseline_build --bom {bom_dir} --pack {pack_dir}")

    bom = Bom.load(bom_dir)
    pack = StandardPack.load(pack_dir, allow_deprecated=True)  # 基准已锁，退役与否由基准负责
    return bl, lock, bom, pack


def assert_matches_baseline(bl: dict, bom: Bom, pack: StandardPack,
                            deal: DealConfig) -> None:
    """引擎在无交付方案、含占位下的结果必须与基准逐位一致。

    这条断言是分层契约的兑现 —— 没有它，`--baseline` 就只是个装饰性的参数。
    """
    base_deal = DealConfig(deal_id="__check__", counting_method=deal.counting_method,
                           project_type=deal.project_type,
                           include_placeholders=True,
                           as_of_bom_version=deal.as_of_bom_version)
    r = CostingEngine(bom, pack, base_deal).run()["software_dev"]
    b = bl["software_dev"]
    for k in ("ufp_total", "afp_total", "total"):
        if r[k] != b[k]:
            raise SystemExit(
                f"与基准不一致：software_dev.{k} 引擎 {r[k]} vs 基准 {b[k]}。"
                f"\n  基准是 {bl['pack_id']} × BOM {bl['bom_version']}，"
                f"计数方法 {bl['counting_method']}；本次 {deal.counting_method}。"
                f"\n  若确实换了计数方法/项目类型，须先重建基准。")


def publish_lark(files: list[Path], folder: str) -> list[str]:
    """把生成的表格导入飞书文件夹，成为可在线查看的表格。

    **计算书是命令的产物，不是维护对象。** 它每一格都能从 BOM × 标准包重算出来，
    所以放飞书上只为了看和分享 —— 像 PDF 一样是快照。重跑覆盖同名文件即可。

    早先做过一版「计算书 Base」，把 1763 行明细复制一份到飞书维护。那是错的：
    飞书的关联字段只能在同一个 Base 内用（`link_table` 吃的是 base 内作用域的
    table_id），跨 Base 引用 BOM 做不到，于是只能复制 —— 一份数据两处维护，
    改了名字两边就静默分叉。维护面必须收敛在 BOM。
    """
    import subprocess
    out = []
    for f in files:
        r = subprocess.run(
            ["lark-cli", "drive", "+import", "--file", str(f), "--type", "sheet",
             "--folder-token", folder, "--name", f.stem, "--as", "user",
             "--format", "json"],
            capture_output=True, text=True)
        ok = '"ok": true' in r.stdout or '"ok":true' in r.stdout
        # 导入类命令会在 JSON 前打进度行，解析崩不代表失败 —— 只看 ok 标记
        out.append(f"{'✓' if ok else '✗'} {f.name}"
                   + ("" if ok else f"  {(r.stdout or r.stderr)[:160]}"))
    return out


#: `totals` 的内部键 → 公文用词。其他费用的计费基数会把这些键拼进「计算依据」，
#: 不译就是英文字段名印在报财评的表上。
BASE_LABELS = {
    "software_dev": "软件开发费",
    "software_purchase": "软件产品购置费",
    "data_purchase": "数据资源和服务购置费",
    "hardware_purchase": "硬件设备购置费",
    "implementation": "实施费用",
    "other_fees": "其他建设费用",
}


def _short_name(deal: str, limit: int = 12) -> str:
    """从商机全称取一个够短的项目简称，用于送审文件名。

    截断而非硬编码映射 —— 这是个显示问题，不该要求每个商机都先登记简称；
    真要精确控制，传 `--doc-prefix`。
    """
    s = deal.strip().replace(" ", "")
    for sep in ("：", ":", "—", "-"):
        if sep in s:
            s = s.split(sep)[-1] or s
    return s[:limit] if len(s) > limit else s


def _subtitle(result: dict[str, Any], pack: StandardPack) -> str:
    return (f"编制依据：{pack.data['standard_doc']}　|　"
            f"BOM {result['bom_version']}　|　标准包 {pack.pack_id}　|　"
            f"项目类型 {result['project_type']}　|　"
            f"计数方法 {result['counting_method']}")


def emit_procurement_list(result: dict[str, Any], pack: StandardPack,
                          path: Path) -> None:
    """Z01 建设期采购清单 —— 按区域标准的科目树组织。

    科目行走 `group()`（不占序号、不进合计），明细行走 `row()`（进合计）。
    这样末行 `=SUM()` 累加的是明细，与 `construction_total` 对得上；
    若科目行也计入就会翻倍 —— `total(expect=)` 会当场把这种错拦下来。
    """
    wb = gov_sheet.new_workbook()
    s = gov_sheet.GovSheet(
        wb, "01_建设期采购清单",
        title=f"{result['deal']}　建设期采购清单",
        subtitle=_subtitle(result, pack),
        columns=[
            gov_sheet.Col("科目", width=26),
            gov_sheet.Col("明细", width=46, role="detail"),
            gov_sheet.Col("金额（元）", "money", width=16, sum=True, role="total"),
            gov_sheet.Col("计算依据", width=52),
        ],
        clause_required=True)

    sw = result["software_dev"]
    s.group("一、软件开发费用",
            {"计算依据": f"功能点法：{sw['ufp_total']} UFP → {sw['afp_total']} "
                         f"调整后功能点 → {sw['effort_total']} 人月"},
            clause="三.(一) p.5-8")
    for x in sw["systems"]:
        s.row({"明细": f"{x['system']}（{x['dev_category']}）",
               "金额（元）": x["cost"],
               "计算依据": f"{x['ufp']} UFP × 系数 → {x['effort_man_months']} 人月 × "
                           f"¥{x['man_month_rate']:,.2f}/人月"},
              clause="三.(一) p.5-8")

    # 二、购置类科目 —— 无价的**列而不计**，不能因为没价格就整节消失。
    # 财评看到没有这一节会认为方案漏项；看到「待询价 9 项」才知道是价格没到位。
    pur = result.get("purchases") or {"by_subject": [], "pending": [],
                                      "not_applicable": []}
    for g in pur["by_subject"]:
        pend = (f"；其中 {g['pending_count']} 项待询价，未计入金额"
                if g["pending_count"] else "")
        s.group(f"二、{g['subject']}",
                {"计算依据": f"{len(g['rows'])} 项{pend}"}, clause="三.(二) p.8-9")
        for r in g["rows"]:
            amount = "待询价" if r["reference_unit_price"] is None else r["amount"]
            s.row({"明细": f"{r['name']}（{r['qty']}{r['unit']}"
                           f"{'，' + r['pricing_model'] if r['pricing_model'] else ''}）",
                   "金额（元）": amount, "计算依据": r["pricing_basis"]},
                  clause="三.(二) p.8-9")

    hw = result["hardware"]
    if hw["rows"]:
        s.group("二、硬件设备购置费",
                {"计算依据":
                 f"{len(hw['rows'])} 项，按询价基线；"
                 f"{hw['quotes_required_count']} 项需三家询价"
                 + (f"；其中 {len(hw['pending'])} 项待询价/待选型，未计入金额"
                    if hw.get("pending") else "")},
                clause="三.(三) p.10-11")
        for r in hw["rows"]:
            amount = "待询价" if r["amount"] is None else r["amount"]
            qty = r["qty"] if r["qty"] is not None else "待定"
            s.row({"明细": f"{r['name']}（{qty}{r['unit']}）",
                   "金额（元）": amount, "计算依据": r["pricing_basis"]},
                  clause="三.(三) p.10-11")

    # 三、实施费用 —— 此前它只进 construction_total 不进清单，评审逐行加会差
    # 这一笔（本项目 ¥40,000）。`total(expect=)` 把这个洞照了出来。
    impl = result.get("implementation") or {"rows": []}
    if impl["rows"]:
        # 说明按**实际形态**生成，不写死「SaaS/订阅」—— 换成全私有化后
        # 实施费来自 D4 模型私有化部署调试，文案还写着 SaaS 就是在说假话。
        s.group("三、实施费用",
                {"计算依据": f"{len(impl['rows'])} 项："
                             + "、".join(f"{x['mode_name']}（{x['mode']}）"
                                        for x in impl["rows"])},
                clause="三.(二) p.8-9")
        for r in impl["rows"]:
            # 只写工作量与用途，**不写 internal-cost-model 里的人天单价**
            basis = (f"{r['man_days']} 人天　{r['note']}" if r.get("man_days")
                     else (r.get("basis") or r.get("note") or ""))
            clause = r["subject"] or "待落位：本形态在本标准中无对应科目（须确认）"
            s.row({"明细": f"{r['mode_name']}（{r['mode']}）接入实施",
                   "金额（元）": r["amount"], "计算依据": basis},
                  clause=clause)

    s.group("四、其他建设费用", clause="三.(四) p.11-13")
    for f in result["other_fees"]:
        # base 是 totals 的内部键（software_dev/hardware_purchase…），
        # 直接拼进「计算依据」等于把英文字段名印到公文上。
        bases = "＋".join(BASE_LABELS.get(b, b) for b in f["base"])
        note = f["blocked_reason"] or (
            f"以{bases} {f['base_wan']:,.2f} 万元为基数，"
            f"{'超额累进' if f['method'] == 'progressive' else '费率'}")
        cite = f.get("citation") or {}
        clause = (f"p.{cite.get('page', '')} {cite.get('section', '')}".strip()
                  or "三.(四) p.11-13")
        # 费率上限写进依据 —— 「以 68.05 万元为基数，费率」评审验不了；
        # 「×5%（标准：不高于 5%）」才验得了。
        if f.get("rate") is not None and not f["blocked_reason"]:
            note += f"　×{f['rate']:.0%}（标准：不高于 {f['rate']:.0%}）"
        s.row({"明细": f["name"], "金额（元）": f["amount_yuan"], "计算依据": note},
              clause=clause)
        # 超额累进摊成逐档 —— 照标准正文自带算例的写法
        # （p.11：300×2%+（500-300）×1.6%+（800-500）×1.28%=13.04 万元）。
        # 只写「超额累进」四个字，评审加不出这个数。
        for st in (f.get("breakdown") or []):
            s.note(f"　　　{st['from_wan']:,.2f} ~ {st['to_wan']:,.2f} 万元段　"
                   f"（{st['to_wan'] - st['from_wan']:,.2f} 万 × {st['rate']:.2%}）"
                   f"　= {st['amount_wan']:,.4f} 万元")
        if f.get("breakdown"):
            s.note(f"　　　逐档合计 = {f['amount_wan']:,.2f} 万元 = "
                   f"¥{f['amount_yuan']:,.2f}")

    s.total("合计", expect={"金额（元）": result["totals"]["construction_total"]})

    # ---- 以下是「列而不计」的说明区，不参与合计 ----
    if result["scope"]["excluded"]:
        s.blank()
        # 列而不计 ≠ 不列。范围外的子系统整段消失，财评会认为方案漏项 ——
        # 采购科目那一轮的教训，这里同样适用。
        s.note("【范围说明】未计入本清单的条目 —— 列而不计：列出以证明不是漏项")
        for k, v in result["scope"]["excluded"].items():
            s.note(f"　　{k}：不计入，{v} 条")
        oos = result["scope"].get("out_of_scope_systems") or []
        if oos:
            s.note("　　本次范围外的子系统：不计入，"
                   + "；".join(f"{x['system']}（{x['items']} 条）" for x in oos))

    if pur["not_applicable"]:
        s.blank()
        s.note("【本方案不采用的采购形态】同一标的的两种表达只能取一种，"
               "另一种在此列出以证明不是漏项")
        by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in pur["not_applicable"]:
            by_reason[f"{r['reason']}（{r['mode']}）"].append(r)
        for reason, rows in sorted(by_reason.items()):
            ref = sum(r.get("legacy_reference_yuan") or 0 for r in rows)
            tail = (f"；raw-input 旧清单对应 ¥{ref:,.0f}（仅供差额归因，非定价依据）"
                    if ref else "")
            s.note(f"　　{rows[0]['subject']}　{len(rows)} 项：不计入。{reason}{tail}")

    if pur["pending"]:
        s.blank()
        s.note(f"【待询价】{len(pur['pending'])} 项已列入清单但未计金额，"
               f"出正式报价前须补齐盖章询价报价单")
        for r in pur["pending"]:
            s.note(f"　　{r['name']}：待询价。{r['note']}")

    s.finish()
    gov_sheet.save(wb, path)


def emit_fp_worksheet(bom: Bom, result: dict[str, Any], pack: StandardPack,
                      deal: DealConfig, path: Path,
                      delivery: DeliveryContext | None = None) -> None:
    """02 功能点测算表 —— 逐条目可复算。

    不依赖单元格布局：数值全部由引擎算定后写入，另附「复算公式」文本列。
    """
    from costing_engine import CostingEngine

    # **必须带上 delivery** —— 此前这里另建了一个无交付方案的引擎，
    # 于是明细表的形态过滤全部失效：汇总按 CE-DEV 过滤得 11,105.10 FP，
    # 明细却把订阅形态的条目也算进去得 12,022.18，两张表在同一个文件里
    # 各说各话，且没有任何地方报错。
    engine = CostingEngine(bom, pack, deal, delivery)
    items, _ = engine.in_scope()
    method = deal.counting_method
    size_f = pack.factor("size_change", method)

    wb = gov_sheet.new_workbook()
    sw = result["software_dev"]
    sub = _subtitle(result, pack)
    _, w_cite = pack.value(f"fp_counting.{method}.weights")
    fp_clause = str(w_cite)

    # 汇总排在明细之前 —— 评审打开文件先看钱，不该先撞上 1738 行明细。
    smy = gov_sheet.GovSheet(
        wb, "00_测算汇总", title=f"{result['deal']}　软件开发费测算汇总",
        subtitle=sub, rollup=True,   # 它汇总 01_功能点明细，不能与之相加
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
        ],
        clause_required=True)
    for x in sw["systems"]:
        smy.row({"子系统": x["system"], "开发类别": x["dev_category"],
                 "条目数": x["items"], "未调整功能点": x["ufp"],
                 "调整后功能点": x["afp"],
                 "开发工作量（人月）": x["effort_man_months"],
                 "人月费率（元）": x["man_month_rate"],
                 "软件开发费用（元）": x["cost"],
                 # dict 直接进单元格会变成 Python repr —— gov_sheet 会拦，
                 # 这里显式摊平成人能读的文本。
                 "功能点类型分布": gov_sheet.flatten_counts(x["by_type"])},
                clause=fp_clause)
    smy.total("合计", expect={"未调整功能点": sw["ufp_total"],
                              "调整后功能点": sw["afp_total"],
                              "软件开发费用（元）": sw["total"]})
    smy.finish()

    det = gov_sheet.GovSheet(
        wb, "01_功能点明细", title=f"{result['deal']}　功能点测算明细",
        subtitle=sub + "　|　「复算式」为活公式，可现场点开核验",
        columns=[
            gov_sheet.Col("条目编号", width=18),
            gov_sheet.Col("子系统", width=34),
            gov_sheet.Col("功能点名称", width=34, role="detail"),
            gov_sheet.Col("功能点类型", width=11),
            gov_sheet.Col("未调整功能点", "fp", width=13, sum=True),
            gov_sheet.Col("规模变更因子", "rate", width=13),
            gov_sheet.Col("产品成熟度", width=12),
            gov_sheet.Col("复用度档位", width=14),
            gov_sheet.Col("复用度因子", "rate", width=12),
            gov_sheet.Col("应用类型因子", "rate", width=13),
            gov_sheet.Col("调整后功能点", "fp", width=13, sum=True, role="fp"),
            gov_sheet.Col("复算式", "fp", width=16),
            gov_sheet.Col("交付形态", width=18),
            gov_sheet.Col("计入软件开发费", width=13),
            gov_sheet.Col("计入开发费功能点", "fp", width=15, sum=True, role="fp"),
        ],
        clause_required=True)
    n_excluded = 0
    for i in items:
        if not is_fp_counted(i):
            continue
        w = pack.fp_weight(method, i.nesma.type)
        app_f = pack.factor("app_type", i.app_type or "业务处理")
        # 复用度按条目的 maturity 经标准包词表解析 —— 不是全表一个值
        lvl = engine.reuse_level(i)
        reuse_f = pack.factor("reuse", lvl)
        afp = engine.profile.adjusted_fp(
            w, pack=pack, counting_method=method,
            reuse_level=lvl, app_type=i.app_type or "业务处理")
        # 「复算式」写的是活公式，Excel 会自己算。它算出来的必须等于引擎的
        # 调整后功能点，否则评审点开就看见同一行有两个不一样的数。
        #
        # 公式里必须带 ROUND(...,2) —— 引擎逐条目做 xlround(...,2)，裸乘积
        # 会差半分（5×1.21×1.5 = 9.075 vs 引擎 9.08）。Excel 的 ROUND 是
        # 四舍五入（半数远离零），与 xlround 同语义，两边这才真的等价。
        # 飞书那版 US 公式漏 ROUND 也是同一处毛病。
        if abs(xlround(w * size_f * reuse_f * app_f, 2) - afp) > 0.0001:
            raise gov_sheet.GovSheetError(
                f"{i.id}：复算式与引擎值不符 —— "
                f"ROUND({w}×{size_f}×{reuse_f}×{app_f}, 2) = "
                f"{xlround(w * size_f * reuse_f * app_f, 2)}，引擎给 {afp}。\n"
                f"  表里那一列是活公式，写进去就等于把这个矛盾摆给评审看。")
        # 明细列的是全部在范围条目，汇总只算走 CE-DEV 的那批（订阅/买断形态
        # 不按功能点法计价）。两者差 900 多 FP —— 不写出来，评审自己加会
        # 发现明细与汇总对不上，而表里没有任何地方解释这个差。
        ce = engine._construction_elements(i)
        counted = ce is None or "CE-DEV" in ce
        mode = (engine.delivery.assigned.get(i.id) if engine.delivery else None)
        mode_name = (engine.delivery.modes.get(mode, {}).get("name", mode)
                     if mode else "按功能点法开发")
        if not counted:
            n_excluded += 1
        det.row({"条目编号": i.id, "子系统": i.path.system, "功能点名称": i.name,
                 "功能点类型": i.nesma.type, "未调整功能点": w,
                 "规模变更因子": size_f,
                 # existing/new 是内部枚举，公文里要用中文
                 "产品成熟度": gov_sheet.label(i.maturity),
                 "复用度档位": lvl, "复用度因子": reuse_f, "应用类型因子": app_f,
                 "调整后功能点": afp,
                 "复算式": f"=ROUND({w}*{size_f}*{reuse_f}*{app_f},2)",
                 "交付形态": f"{mode_name}（{mode}）" if mode else mode_name,
                 "计入软件开发费": "是" if counted else "否",
                 "计入开发费功能点": afp if counted else None},
                clause=fp_clause)
    det.total("合计", expect={"计入开发费功能点": sw["afp_total"]})
    det.note(
        f"　口径说明：本表列出全部在范围功能点条目共 {det.seq_count} 条；其中 "
        f"{n_excluded} 条按订阅/买断形态交付，**不走功能点法计价**，"
        f"故「计入开发费功能点」列为空。")
    det.note(
        f"　「调整后功能点」列合计 = 全部条目规模；"
        f"「计入开发费功能点」列合计 = {sw['afp_total']:,.2f} = "
        f"00_测算汇总的调整后功能点合计。两列之差即订阅/买断形态的规模，"
        f"列而不计 —— 列出以证明不是漏项。")
    det.finish()

    par, rows = emit_pricing_params(wb, result, pack, method, items)
    emit_whatif(wb, result, pack, engine, items, method, par, rows)

    gov_sheet.save(wb, path,
                   sheets_expected=["00_测算汇总", "01_功能点明细",
                                    "02_计价参数", "03_试算"])



def emit_pricing_params(wb, result: dict[str, Any], pack: StandardPack,
                        method: str, items: list | None = None
                        ) -> tuple[Any, dict[str, int]]:
    """02 计价参数 —— 同时是 03 试算的**参数面板**。

    结构与配色抄自样例表的 `项目特征` + `模板使用说明&基础参数`：
    深灰 = 标准取值不得改，白 = 可改做试算。这套颜色方案人员和评审都认，
    不必去读说明就知道哪些格子能动。

    返回 (sheet, {参数名: 行号})，供 03 用绝对地址引用。
    """
    s = gov_sheet.GovSheet(
        wb, "02_计价参数", title=f"{result['deal']}　计价参数与依据",
        subtitle="每个取值均标注标准原文页码与条款。"
                 "**深灰格为标准取值，改动即偏离编制依据**；"
                 "03_试算 直接引用本表 C 列，改这里全表重算。",
        columns=[
            gov_sheet.Col("参数", width=26),
            gov_sheet.Col("取值", width=18, cell_role="locked"),
            gov_sheet.Col("单位/说明", width=26),
            gov_sheet.Col("标准出处", width=36),
        ],
        clause_required=True, clause_name="标准出处")

    rows: dict[str, int] = {}
    for key, lb, dotted, unit in [
        ("size", "规模变更因子", f"factors.size_change.values.{method}",
         f"{method}的估算时机取值"),
        ("prod", "软件开发生产率", "rates.productivity_hours_per_fp", "人时/功能点"),
        ("hpm", "人月折算系数", "rates.man_hours_per_month", "人时/人月"),
        ("rate", "基准人月费率", "rates.base_man_month_rate", "元/人月"),
    ]:
        v, c = pack.value(dotted)
        rows[key] = s.row({"参数": lb, "取值": v, "单位/说明": unit}, clause=str(c))

    # 复用度：本项目按项目类型定档，不是逐条目取值 —— 说清楚它为什么是 1.0
    lvl = result["project_type"]
    reuse_v = pack.factor("reuse", "新建")
    rows["reuse"] = s.row(
        {"参数": "复用度调整因子", "取值": reuse_v,
         "单位/说明": f"{lvl}项目一律取此值；产品成熟度是内部成本口径，不参与造价"},
        clause=str(pack.value("factors.reuse")[1]))

    # 应用类型因子表 —— 明细里逐行印着 1.0/1.5，但**哪种类型对应哪个系数**
    # 此前整个包里没有。评审看到 1.5 无从判断对不对。
    # 本项目用到哪些 —— 从条目实际取值统计，**不从 result 里猜键名**。
    # 先前这里读了个不存在的 key，静默得到空集：全表一个「本项目用到」都没标，
    # 而表面上功能是「有」的。
    used_apps = {(i.app_type or "业务处理") for i in (items or [])
                 if is_fp_counted(i)}
    if items is not None and not used_apps:
        raise gov_sheet.GovSheetError(
            "算不出本项目用到的应用类型 —— 传了 items 却一个 FP 条目都没有")
    s.blank()
    s.note("　【应用类型调整因子表】明细表逐行标注的「应用类型因子」取自此表")
    app_tbl = (pack.data.get("factors", {}).get("app_type", {}).get("values") or {})
    rows["app_first"] = s._next
    for k, v in sorted(app_tbl.items(), key=lambda kv: kv[1]):
        rows[f"app:{k}"] = s.row(
            {"参数": f"　应用类型 · {k}", "取值": v,
             "单位/说明": "本项目用到" if k in used_apps else ""},
            clause=str(pack.value("factors.app_type")[1]))
    rows["app_last"] = s._next - 1

    # 开发类别系数 —— 人月费率 = 基准人月费率 × 本系数。清单里印着
    # ¥26,009.50 与 ¥23,645，中间这个 1.1 此前哪儿都没有，评审只能反推。
    # 广东 dev_category.values 为空且 absent_in_standard —— 人月费率固定，
    # 不按开发类别调。此前这里 `if dev_tbl:` 直接跳过，rows 里就没有区间，
    # 下游 emit_whatif 取 prows['dev_first'] 当场 KeyError。
    # **本标准没有这个维度，也要说出来**，否则读的人会以为是漏印。
    dev_node = pack.data.get("factors", {}).get("dev_category", {}) or {}
    dev_tbl = dev_node.get("values") or {}
    if dev_tbl:
        s.blank()
        s.note("　【开发类别调整系数】人月费率 = 基准人月费率 × 本系数")
        rows["dev_first"] = s._next
        for k, v in sorted(dev_tbl.items(), key=lambda kv: -kv[1]):
            rows[f"dev:{k}"] = s.row(
                {"参数": f"　开发类别 · {k}", "取值": v,
                 "单位/说明": "本项目用到" if k in _dev_cats_used(result) else ""},
                clause=str(pack.value("factors.dev_category")[1]))
        rows["dev_last"] = s._next - 1
    else:
        s.blank()
        s.note(f"　【开发类别调整系数】**本标准无此维度** —— "
               f"{dev_node.get('note') or '人月费率不按开发类别调整'}。"
               f"03_试算 的人月费率直接取基准人月费率。")

    weights, w_cite = pack.value(f"fp_counting.{method}.weights")
    s.blank()
    s.note("　【功能点权重】")
    for k, v in weights.items():
        rows[f"w:{k}"] = s.row({"参数": f"　{k}", "取值": v,
                                "单位/说明": "未调整功能点/个"}, clause=str(w_cite))

    s.blank()
    s.note("　【图例】" + "；".join(f"{d}" for _c, d in gov_sheet.LEGEND))
    s.finish()
    return s, rows


def _dev_cats_used(result: dict[str, Any]) -> set[str]:
    return {s["dev_category"] for s in result["software_dev"]["systems"]}


def emit_whatif(wb, result: dict[str, Any], pack: StandardPack, engine,
                items, method: str, par, prows: dict[str, int]) -> None:
    """03 试算 —— 改参数看全盘影响，形制照 `样例表#应急管理系统应用系统功能规模`。

    ## 活公式在这里为什么安全

    本项目铁律「数值由引擎算定、Excel 只渲染」来自 P0（1200 行明细里 SUMIF
    被合并单元格击穿，少算 ¥421.6 万且不报错）。但**试算不是那个形状**：
    15 行、直接引用参数面板的绝对地址、无跨表查找、无合并单元格参与计算。

    所以分界线是**哪一段**用公式，不是能不能用：

      - 1738 行明细的 FP 累加 → 静态（P0 的地盘）
      - 子系统级 UFP → 金额 → 活公式（试算的价值所在）

    ## 与 00_测算汇总 的口径差

    引擎逐条目 `xlround(...,2)` 后累加；本表按子系统整体乘。两者在
    应用类型系数 ≠ 1 的子系统差零点几个功能点（全项目 −0.32 FP，
    0.003%）。**差多少直接印在表里**，不藏 —— 送审值以 00 为准。
    """
    P = "'02_计价参数'"
    a_size, a_prod = f"{P}!$C${prows['size']}", f"{P}!$C${prows['prod']}"
    a_hpm, a_rate = f"{P}!$C${prows['hpm']}", f"{P}!$C${prows['rate']}"
    a_reuse = f"{P}!$C${prows['reuse']}"
    size = pack.factor("size_change", method)
    reuse = pack.factor("reuse", "新建")
    hpm = pack.rate("man_hours_per_month")
    prod = pack.rate("productivity_hours_per_fp")
    base_rate = pack.rate("base_man_month_rate")

    # 维度按**标准包实际有什么**来定，不预设山东的形状 ——
    # 广东人月费率固定 24000，没有开发类别这个维度，硬留一列就是假的。
    has_dev = "dev_first" in prows
    s = gov_sheet.GovSheet(
        wb, "03_试算", title=f"{result['deal']}　造价试算",
        subtitle="**绿色格全部为活公式**，参数取自 02_计价参数 的 C 列 —— "
                 "改那里的深灰格，本表即时重算。"
                 "本表为试算工具，**送审值以 00_测算汇总 为准**。",
        columns=[
            gov_sheet.Col("子系统", width=38),
            gov_sheet.Col("应用类型", width=14, cell_role="input"),
            *([gov_sheet.Col("开发类别", width=20, cell_role="input")]
              if has_dev else []),
            gov_sheet.Col("未调整功能点", "fp", width=13, sum=True),
            gov_sheet.Col("应用类型因子", "rate", width=12, cell_role="calc"),
            *([gov_sheet.Col("开发类别系数", "rate", width=12, cell_role="calc")]
              if has_dev else []),
            gov_sheet.Col("调整后功能点", "fp", width=13, sum=True,
                          cell_role="calc"),
            gov_sheet.Col("工作量（人月）", "fp", width=13, sum=True,
                          cell_role="calc"),
            gov_sheet.Col("人月费率（元）", "money", width=14, cell_role="calc"),
            gov_sheet.Col("软件开发费（元）", "money", width=16, sum=True,
                          cell_role="calc"),
            gov_sheet.Col("与 00 汇总差（元）", "money", width=14, sum=True),
        ])

    sum_cost = 0.0
    for sysrow in result["software_dev"]["systems"]:
        its = [i for i in items if is_fp_counted(i)
               and i.path.system == sysrow["system"]]
        app_name = (its[0].app_type or "业务处理") if its else "业务处理"
        app_f = pack.factor("app_type", app_name)
        app_local = pack.factor_label("app_type", app_name)
        dev_f = round(sysrow["man_month_rate"] / base_rate, 4)
        row_no = s._next                   # 本行将写在哪一行
        ufp = sysrow["ufp"]

        afp = xlround(ufp * size * reuse * app_f, 2)
        effort = xlround(afp * prod / hpm, 2)
        rate = xlround(base_rate * dev_f, 2)
        cost = xlround(effort * rate, 2)
        sum_cost += cost

        C = {n: get_col_letter(s, n) for n in
             (["应用类型", "未调整功能点", "应用类型因子", "调整后功能点",
               "工作量（人月）", "人月费率（元）"]
              + (["开发类别", "开发类别系数"] if has_dev else []))}
        # 系数不写死，从 02 的系数表查 —— 样例表是 IF(C2=…)，我们用 VLOOKUP。
        # 写死的话「把平台能力从智能信息改成业务处理」这种试算就做不了：
        # 改了名字系数不动，等于什么都没试。
        app_rng = (f"{P}!$B${prows['app_first']}:$C${prows['app_last']}")
        f_app = (f'=VLOOKUP("　应用类型 · "&{C["应用类型"]}{row_no},{app_rng},2,FALSE)')
        if has_dev:
            dev_rng = f"{P}!$B${prows['dev_first']}:$C${prows['dev_last']}"
            f_dev = (f'=VLOOKUP("　开发类别 · "&{C["开发类别"]}{row_no},'
                     f'{dev_rng},2,FALSE)')
        vals = {
            "子系统": sysrow["system"],
            # 写**本标准自己的称谓**：BOM 存「智能信息」，广东标准叫「人工智能」，
            # 写 BOM 的词 VLOOKUP 会在广东的表里查不到。
            "应用类型": app_local, "未调整功能点": ufp,
            "应用类型因子": gov_sheet.formula(
                f_app, app_f, app_f,
                where=f"[03_试算] {sysrow['system'][:20]} 应用类型因子"),
            "调整后功能点": gov_sheet.formula(
                f"=ROUND({C['未调整功能点']}{row_no}*{a_size}*{a_reuse}"
                f"*{C['应用类型因子']}{row_no},2)",
                afp, sysrow["afp"], rel_tol=0.001,
                where=f"[03_试算] {sysrow['system'][:20]} 调整后功能点"),
            "工作量（人月）": gov_sheet.formula(
                f"=ROUND({C['调整后功能点']}{row_no}*{a_prod}/{a_hpm},2)",
                effort, sysrow["effort_man_months"], rel_tol=0.001,
                where=f"[03_试算] {sysrow['system'][:20]} 工作量"),
            "人月费率（元）": gov_sheet.formula(
                (f"=ROUND({a_rate}*{C['开发类别系数']}{row_no},2)" if has_dev
                 else f"={a_rate}"),
                rate, sysrow["man_month_rate"],
                where=f"[03_试算] {sysrow['system'][:20]} 人月费率"),
            "软件开发费（元）": gov_sheet.formula(
                f"=ROUND({C['工作量（人月）']}{row_no}*{C['人月费率（元）']}{row_no},2)",
                cost, sysrow["cost"], rel_tol=0.001,
                where=f"[03_试算] {sysrow['system'][:20]} 软件开发费"),
            "与 00 汇总差（元）": round(cost - sysrow["cost"], 2),
        }
        if has_dev:
            vals["开发类别"] = sysrow["dev_category"]
            vals["开发类别系数"] = gov_sheet.formula(
                f_dev, dev_f, dev_f,
                where=f"[03_试算] {sysrow['system'][:20]} 开发类别系数")
        s.row(vals)

    s.total("试算合计")
    eng = result["software_dev"]["total"]
    s.note(f"　00_测算汇总（送审值）软件开发费：¥{eng:,.2f}　|　"
           f"本表试算合计：¥{sum_cost:,.2f}　|　差 ¥{sum_cost - eng:,.2f}"
           f"（{abs(sum_cost - eng) / eng:.4%}）")
    s.note("　差的来源：引擎按**逐条目** xlround(...,2) 后累加，本表按**子系统整体**"
           "相乘。应用类型因子 = 1 的子系统两者完全一致；≠ 1 的差零点几个功能点。"
           "**送审以 00_测算汇总 为准**，本表用于看参数变动的影响量级。")
    s.note("　试算用法：改 02_计价参数 的取值 → 本表绿色格自动重算 → "
           "看「软件开发费」合计的变化。注意那些格子是**标准取值**，"
           "试算完请勿把改动当成送审依据。")
    s.finish()


def get_col_letter(sheet, name: str) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(sheet._by_name[name])


def _procurement_notes(pur: dict[str, Any]) -> list[str]:
    """采购科目的三段说明：待询价、本方案不采用、与功能点不重复计列。

    这三件事财评一定会问，而且问的是同一个方向：「你这份清单是不是漏了/重了」。
    先自己写清楚，比等着被问强。
    """
    if not (pur["by_subject"] or pur["not_applicable"]):
        return []
    L = ["### 购置类科目说明", ""]

    if pur["pending"]:
        by_sub: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in pur["pending"]:
            by_sub[r["subject"]].append(r)
        L += [f"**待询价 {len(pur['pending'])} 项**，已列入清单但未计入金额 —— "
              f"计 0 与「没有这一项」在报表上无法区分，故列而不计：", ""]
        for sub, rows in sorted(by_sub.items()):
            L.append(f"- {sub}：{'、'.join(r['name'] for r in rows)}")
        L += ["", "出正式报价前须按标准补齐盖章询价报价单。", ""]

    if pur["not_applicable"]:
        by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in pur["not_applicable"]:
            by_reason[r["reason"]].append(r)
        L += ["**本方案不采用的采购形态**　同一标的常有两种互斥的表达 —— "
              "定制开发（功能点法）与成品/模型购置（询价法）。本次交付方案选了其中一种，"
              "另一种在此列出，以证明不是漏项：", ""]
        for reason, rows in sorted(by_reason.items()):
            ref = sum(r.get("legacy_reference_yuan") or 0 for r in rows)
            tail = (f"；对应旧清单 ¥{ref:,.0f}，该金额在本方案中"
                    f"由另一种口径承担或转入运营期" if ref else "")
            L.append(f"- {rows[0]['subject']}　{len(rows)} 项：{reason}{tail}")
        L.append("")

    if pur["by_subject"]:
        L += ["**为何不构成重复计列**　外部数据源在本清单里出现两次，但是两个标的：",
              "", "- 功能点侧计的是「把这个外部逻辑数据组接进来」的**开发工作量**"
              "（按 NESMA 记为 ELF）；",
              "- 购置侧计的是「买这份数据的**访问权**」（订阅费）。", "",
              "二者缺一都不成立：没有开发就用不上数据，没有订阅就没有数据可用。"
              "每条采购条目的 `spec.related_fp` 标明了它对应哪些功能点条目，可逐条对照。", ""]
    return L


def emit_notes(bom: Bom, result: dict[str, Any], pack: StandardPack,
               deal: DealConfig, path: Path,
               rule_violations: list[Any] | None = None) -> None:
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
          f"| 软件开发费用 | {result['totals']['software_dev']:,.2f} |"]
    pur = result.get("purchases") or {"by_subject": [], "pending": [],
                                      "not_applicable": []}
    for g in pur["by_subject"]:
        mark = f"（{g['pending_count']} 项待询价未计）" if g["pending_count"] else ""
        L.append(f"| {g['subject']}{mark} | {g['total']:,.2f} |")
    L += [f"| 硬件设备购置费 | {result['totals']['hardware_purchase']:,.2f} |",
          f"| 其他建设费用 | {result['totals']['other_fees']:,.2f} |",
          f"| **合计** | **{result['totals']['construction_total']:,.2f}** |", "",
          f"功能点合计 {result['software_dev']['ufp_total']}，"
          f"调整后 {result['software_dev']['afp_total']}，"
          f"开发工作量 {result['software_dev']['effort_total']} 人月。", ""]

    L += _procurement_notes(pur)

    pf = result.get("project_factors")
    if pf:
        # 启用了地方标准之外的因子，必须在编制说明里自己先说清楚 ——
        # 等财评问出来就晚了
        L += ["### ⚠️ 项目特征因子（不属本省标准）", "",
              f"本次启用了 `{pf['id']}` 项目特征因子，来源：**{pf['source']}**。", "",
              f"> {str(pf.get('warning','')).strip()}", "",
              "| 因子 | 取值 | 依据 |", "|---|---:|---|"]
        for r in pf["rows"]:
            basis = r.get("选择") or (f"{r.get('公式','')}，构成 {r.get('构成')}")
            L.append(f"| {r['因子']} | {r['取值']} | {basis} |")
        L += ["", f"**连乘 {pf['combined']}**，作用于开发工作量（项目级，非逐条目）。",
              "", "本省标准中已有的维度（如应用类型）**以本省标准为准，不叠加**。", ""]

    band = result.get("productivity_band")
    if band:
        L += ["### 生产率区间", "",
              "本标准规定了生产率浮动区间，故给出三档而非单点：", "",
              "| 档 | 生产率（人时/FP） | 工作量（人月） | 软件开发费（元） |",
              "|---|---:|---:|---:|"]
        for b in band:
            L.append(f"| {b['档']} | {b['生产率']} | {b['工作量人月']} | {b['软件开发费']:,.2f} |")
        L += ["", "单点值等于假装精确 —— 功能点法本就是估算，区间比单点诚实。", ""]
    elif result.get("band_note"):
        L += ["### 生产率", "", result["band_note"], ""]

    blocked = [f for f in result["other_fees"] if f["blocked_reason"]]
    if blocked:
        L += ["### 未计列的费用项", ""]
        for f in blocked:
            L.append(f"- **{f['name']}**：{f['blocked_reason']}")
        L.append("")

    fails = [v for v in (rule_violations or []) if v.severity == "fail"]
    if fails:
        # 财评看的是编制说明，不是 xlsx 里的某个 sheet。结构性违规必须在这里出现，
        # 而且要写在「假设与边界」之前 —— 它不是假设，是本方案在本标准下不成立。
        L += ["## ⚠️ 一致性违规（本方案在本标准下不成立）", "",
              f"检出 {len(fails)} 条 `fail` 级违规。**送审前必须消解**，"
              f"否则相应科目在本标准中无处落账：", "",
              "| 规则 | 问题 |", "|---|---|"]
        for v in fails:
            # 违规文案里带换行会把 Markdown 表格撑破 —— 压平
            msg = " ".join(str(v.message).split()).replace("|", "｜")
            L.append(f"| {v.rule} | {msg} |")
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
    wb = gov_sheet.new_workbook()
    s = gov_sheet.GovSheet(
        wb, "01_运营期费用清单", title="运营期费用清单",
        subtitle="「计入本次采购」=是 的行计入本次采购预算；=否 的行须另行立项"
                 "或由客户直付，此处列出以保证方案完整",
        columns=[
            gov_sheet.Col("成本元素", width=12),
            gov_sheet.Col("名称", width=26),
            gov_sheet.Col("范围", width=30),
            gov_sheet.Col("交付形态", width=12),
            gov_sheet.Col("年度金额（元）", "money", width=16, sum=True, role="total"),
            gov_sheet.Col("付款对象", width=16),
            gov_sheet.Col("计入本次采购", width=12),
            gov_sheet.Col("测算依据", width=40),
            gov_sheet.Col("说明", width=34),
        ],
        clause_required=False)
    for l in ops_lines:
        s.row({"成本元素": l.element, "名称": l.element_name, "范围": l.scope,
               "交付形态": l.mode, "年度金额（元）": l.annual_yuan,
               "付款对象": l.payee,
               "计入本次采购": "是" if l.in_scope else "否",
               "测算依据": l.basis, "说明": l.note})
    s.total("合计（全部）",
            expect={"年度金额（元）": sum(l.annual_yuan for l in ops_lines)})
    s.note(f"　其中计入本次采购："
           f"¥{sum(l.annual_yuan for l in ops_lines if l.in_scope):,.2f}；"
           f"其余由客户直付或须另行立项。")
    s.finish()

    if violations:
        v = gov_sheet.GovSheet(
            wb, "02_一致性检查", title="交付方案一致性检查",
            subtitle="规则编号见 delivery-modes/modes.yaml 的 consistency_rules",
            columns=[gov_sheet.Col("规则", width=10),
                     gov_sheet.Col("级别", width=10),
                     gov_sheet.Col("说明", width=90)])
        for x in violations:
            # severity 是 warn/fail 这类内部枚举，公文里要中文
            v.row({"规则": x.rule, "级别": gov_sheet.label(x.severity),
                   "说明": x.message})
        v.finish()
    gov_sheet.save(wb, path)


def emit_tco(comparisons: list[dict], path: Path) -> None:
    """04 TCO 对比 —— 对客户最有说服力的一张表。

    这张表**不写合计** —— 各行是互斥的备选方案，纵向相加没有意义。
    `GovSheet` 不强制合计行正是为了这种表。
    """
    wb = gov_sheet.new_workbook()
    payees = sorted({p for c in comparisons for p in c["tco5"]["annual_by_payee"]})
    s = gov_sheet.GovSheet(
        wb, "01_TCO对比", title="交付方式 TCO 对比",
        subtitle="建设期为一次性投入；运营期按年计。各行为互斥备选方案，"
                 "不设合计。",
        columns=[
            gov_sheet.Col("方案", width=22),
            gov_sheet.Col("说明", width=40),
            gov_sheet.Col("建设期（元）", "money", width=16),
            gov_sheet.Col("年度经常性（元）", "money", width=16),
            *[gov_sheet.Col(f"其中付{p}（元）", "money", width=16) for p in payees],
            gov_sheet.Col("3年TCO（元）", "money", width=16),
            gov_sheet.Col("5年TCO（元）", "money", width=16),
        ])
    for c in comparisons:
        t3, t5 = c["tco3"], c["tco5"]
        s.row({"方案": c["name"], "说明": c["description"],
               "建设期（元）": t5["construction"],
               "年度经常性（元）": t5["annual_total"],
               **{f"其中付{p}（元）": t5["annual_by_payee"].get(p, 0)
                  for p in payees},
               "3年TCO（元）": t3["tco"], "5年TCO（元）": t5["tco"]})
    best = min(comparisons, key=lambda c: c["tco5"]["tco"])
    s.blank()
    s.note(f"　5 年 TCO 最低：{best['name']}（¥{best['tco5']['tco']:,.2f}）")
    s.finish()

    gaps = [g for c in comparisons for g in c.get("gaps", [])]
    if gaps:
        g = gov_sheet.GovSheet(
            wb, "02_待核价与缺口", title="待核价与缺口",
            subtitle="以下项目未计入上表金额，出正式报价前须补齐",
            columns=[gov_sheet.Col("方案", width=22),
                     gov_sheet.Col("类型", width=12),
                     gov_sheet.Col("说明", width=80)])
        for c in comparisons:
            for x in c.get("gaps", []):
                g.row({"方案": c["name"], "类型": gov_sheet.label(x["kind"]),
                       "说明": x["note"]})
        g.finish()
    gov_sheet.save(wb, path)


def emit_summary(result: dict[str, Any], pack: StandardPack,
                 files: list[tuple[str, str]], path: Path) -> None:
    """Z00 项目总报价汇总 —— 参考包里最先被打开的一册，我们此前没有。

    评审拿到一叠附件，先要一页纸看清「总共多少钱、由哪几大类构成、每类
    依据哪条标准」。此前我们只给 4 份明细，没有这一页，读的人得自己把
    4 个文件的合计抄下来相加。
    """
    t = result["totals"]
    rows = [
        ("软件开发费（定制）", t["software_dev"], "三.(一).2.1 定制软件功能点法"),
        ("软件产品购置费", t["software_purchase"], "三.(二).1 软件产品购置费"),
        ("数据资源和服务购置费", t["data_purchase"], "三.(二).2 数据资源和服务购置费"),
        ("硬件设备购置费", t["hardware_purchase"], "三.(三) 硬件设备购置费"),
        ("实施费用", t.get("implementation", 0.0), "三.(二) 接入实施"),
        ("其他建设费用", t["other_fees"], "三.(四) 其他建设费用"),
    ]
    wb = gov_sheet.new_workbook()
    total = t["construction_total"]
    s = gov_sheet.GovSheet(
        wb, "00_项目总报价汇总", title=f"{result['deal']}　项目总报价汇总",
        subtitle=_subtitle(result, pack),
        rollup=True,                 # 它汇总 Z01~Z04，不能与之相加
        columns=[
            gov_sheet.Col("费用大类", width=34, role="detail"),
            gov_sheet.Col("金额（元）", "money", width=18, sum=True,
                          role="total"),
            gov_sheet.Col("占比", "pct", width=10),
        ],
        clause_required=True)
    for name, amount, clause in rows:
        s.row({"费用大类": name, "金额（元）": amount,
               "占比": (amount / total) if total else 0}, clause=clause)
    s.total("合计", expect={"金额（元）": total})
    if t.get("pending_pricing_count"):
        s.blank()
        s.note(f"　注：另有 {t['pending_pricing_count']} 项已列入清单但**未计金额**"
               f"（待询价/待选型），见 Z01 建设期采购清单末节。"
               f"上表合计不含这些项目。")
    s.finish()

    idx = gov_sheet.GovSheet(
        wb, "01_关联附件", title="关联附件清单",
        subtitle="本册为汇总，明细见以下附件",
        columns=[gov_sheet.Col("文件名", width=56),
                 gov_sheet.Col("用途", width=56)])
    for fname, purpose in files:
        idx.row({"文件名": fname, "用途": purpose})
    idx.finish()
    gov_sheet.save(wb, path)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="第三层：区域基准 × 交付方案 × 范围 → 本商机报价")
    ap.add_argument("--baseline", type=Path,
                    help="第二层产物目录（baseline_build 的 --out）")
    ap.add_argument("--bom", type=Path, help="不用 --baseline 时的直连模式")
    ap.add_argument("--pack", type=Path, help="不用 --baseline 时的直连模式")
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
    ap.add_argument("--mode", help="从模式库按名字选一个交付模式（delivery-modes/scenarios/）")
    ap.add_argument("--mode-lib", type=Path, default=Path("delivery-modes/scenarios"),
                    help="模式库目录")
    ap.add_argument("--project-factors", type=Path,
                    help="项目特征因子文件（如 standard-packs/_common/gbt36964-factors.yaml）"
                         "—— **默认关闭**，不属地方标准，启用后编制说明会显式标注来源")
    ap.add_argument("--project-factor-choices", type=Path,
                    help="本项目在上述因子上的选择（yaml）")
    ap.add_argument("--scenarios", type=Path, nargs="*", default=[],
                    help="用于 TCO 对比的场景预设")
    ap.add_argument("--doc-prefix", help="送审文件名的项目简称，默认取 --deal-id")
    ap.add_argument("--doc-date", help="送审文件名的日期（YYYYMMDD），默认今天")
    ap.add_argument("--doc-status", default="正式版",
                    help="送审文件名的版本状态，如 征求意见稿/送审稿/正式版")
    ap.add_argument("--publish-lark", metavar="FOLDER_TOKEN",
                    help="把生成的 xlsx 导入飞书文件夹供在线查看（快照，非维护对象）")
    ap.add_argument("--allow-rule-violations", action="store_true",
                    help="有 fail 级一致性违规时仍以 0 退出 —— 仅用于探索性试算")
    ap.add_argument("--allow-deprecated-pack", action="store_true",
                    help="允许加载已退役的标准包 —— 仅用于历史报价复算")
    args = ap.parse_args()

    baseline = baseline_lock = None
    if args.baseline:
        baseline, baseline_lock, bom, pack = load_baseline(args.baseline)
        counting_method = baseline["counting_method"]
    elif args.bom and args.pack:
        # 直连模式：不经第二层。保留是为了快速试算与历史脚本，
        # 但**产出不带基准溯源**，正式报价应走 --baseline。
        bom = Bom.load(args.bom)
        pack = StandardPack.load(args.pack, allow_deprecated=args.allow_deprecated_pack)
        counting_method = "估算功能点法"
        print("  ⚠ 直连模式（未经第二层基准），产出不带基准溯源")
    else:
        ap.error("需要 --baseline，或同时给出 --bom 与 --pack")

    deal = DealConfig(deal_id=args.deal_id, counting_method=counting_method,
                      project_type=args.project_type,
                      reuse_level=args.reuse_level,
                      include_placeholders=args.include_placeholders,
                      as_of_bom_version=args.as_of)
    if args.project_factors:
        import yaml as _yaml
        pf = _yaml.safe_load(args.project_factors.read_text(encoding="utf-8"))
        sel = (_yaml.safe_load(args.project_factor_choices.read_text(encoding="utf-8"))
               if args.project_factor_choices else {})
        pf["selected"] = sel.get("selected", sel)
        deal.project_factors = pf
        print(f"  ⚠ 启用项目特征因子 {pf.get('id')} —— {pf.get('source')}"
              f"\n    本组因子**不属任何地方标准**，编制说明将显式标注")

    if baseline:
        # 基准断言用不带项目因子的口径 —— 基准本来就不含商机决策
        assert_matches_baseline(baseline, bom, pack,
                                DealConfig(deal_id="__chk__",
                                           counting_method=deal.counting_method,
                                           project_type=deal.project_type,
                                           as_of_bom_version=deal.as_of_bom_version))

    # 模式库：按名字挑，而不是每个商机手写 delivery-plan
    plan_path = args.delivery_plan
    if args.mode:
        cands = sorted(args.mode_lib.glob("*.yaml"))
        import yaml as _yaml
        named = {(_yaml.safe_load(c.read_text(encoding="utf-8")) or {}).get("name", c.stem): c
                 for c in cands}
        if args.mode not in named:
            ap.error(f"模式库中无 {args.mode!r}；现有：{sorted(named)}")
        plan_path = named[args.mode]

    delivery = None
    dplan = None
    violations: list[Any] = []
    scope_excluded: dict[str, Any] = {}
    if plan_path and args.modes:
        import yaml as _yaml
        modes_doc = _yaml.safe_load(args.modes.read_text(encoding="utf-8"))
        internal = (_yaml.safe_load(args.internal_cost.read_text(encoding="utf-8"))
                    if args.internal_cost else {})
        dplan = DeliveryPlan.load(args.modes, plan_path)
        # 范围先于形态：范围外的条目连形态都不该指派
        deal.scope_filter = dplan.in_scope
        base_items, scope_excluded = CostingEngine(bom, pack, deal).in_scope()
        delivery = DeliveryContext(assigned=dplan.assign(base_items),
                                   modes=modes_doc["modes"], internal=internal)
        # 一致性检查提前到这里 —— 编制说明要写违规，而它在 result 之后就生成了。
        # 放在后面算等于「算了但编制说明看不到」，正是这轮要修的毛病。
        violations = dplan.check(base_items, delivery.assigned, pack)

    result = CostingEngine(bom, pack, deal, delivery).run()
    out = args.out / "out"
    out.mkdir(parents=True, exist_ok=True)

    # 送审包命名：`{项目简称}_{册号}_{内容}_{状态}_v{BOM版本}_{日期}.xlsx`
    # 抄自柳州送审包 —— 评审在几十份附件里靠文件名定位，`01-建设期采购清单.xlsx`
    # 既没有项目也没有版本，两个项目的附件混进一个目录就分不出来。
    # 默认前缀不能直接用 deal-id：它是「山东数联数智康养业务服务体系」这类
    # 全称，拼进文件名就是 60+ 字符。取末段的业务名做简称，够短且仍可辨认。
    pfx = args.doc_prefix or _short_name(result["deal"])
    dt = args.doc_date or date.today().strftime("%Y%m%d")
    ver = result["bom_version"]

    def name(no: str, topic: str) -> Path:
        return out / gov_sheet.doc_name(pfx, no, topic, ver, dt, args.doc_status)

    f_sum = name("Z00", "项目总报价汇总")
    f_pro = name("Z01", "建设期采购清单")
    f_fp = name("Z02", "功能点测算表")
    f_ops = name("Z03", "运营期费用清单")
    f_tco = name("Z04", "交付方式TCO对比")

    emit_procurement_list(result, pack, f_pro)
    emit_fp_worksheet(bom, result, pack, deal, f_fp, delivery)
    f_notes = out / gov_sheet.doc_name(pfx, "Z05", "编制说明", ver, dt,
                                       args.doc_status).replace(".xlsx", ".md")
    emit_notes(bom, result, pack, deal, f_notes, rule_violations=violations)
    (args.out / "deal.lock.json").write_text(
        json.dumps(lock(result, bom, pack, deal, baseline_lock),
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    (out / "costing-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if delivery is not None:
        base_items, _ = CostingEngine(bom, pack, deal).in_scope()
        om = OpsModel.load(args.modes, args.internal_cost)
        ops = om.expand(base_items, delivery.assigned)
        emit_ops_list(ops, violations, f_ops)


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
            # 待询价按科目汇总，不逐条列 —— 57 条模型逐条列会淹没真正的一致性问题。
            # 但必须列：这些是该场景建设期**尚未计入**的金额，不说等于报低了。
            pend2: dict[str, int] = defaultdict(int)
            for g in r2["purchases"]["pending"]:
                pend2[g["subject"]] += 1
            gaps += [{"kind": "待询价",
                      "note": f"{sub}：{n} 项无单价，本场景建设期未计入该金额"}
                     for sub, n in sorted(pend2.items())]
            gaps += [{"kind": f"一致性-{v.rule}", "note": v.message}
                     for v in p2.check(base_items, a2, pack) if v.severity == "fail"]
            comparisons.append({
                "name": doc.get("name", Path(sp).stem),
                "description": doc.get("description", ""),
                "tco3": tco(cons2, ops2, 3), "tco5": tco(cons2, ops2, 5),
                "gaps": gaps})
        if comparisons:
            emit_tco(comparisons, f_tco)
            (out / "tco-comparison.json").write_text(
                json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8")

        margins = [m for m in om.margin_check() if m["below_threshold"]]
        for m in margins:
            print(f"  ⚠ 订阅毛利预警：{m['kind']} 毛利 {m['margin']:.1%} "
                  f"低于阈值 {m['threshold']:.0%}（内部信息，未写入对外文档）")

    # Z00 汇总册最后生成 —— 它的附件索引要引用其余各册的**实际文件名**
    attachments = [(f_pro.name, "按标准科目树组织的建设期逐项清单，"
                                "含「列而不计」与「待询价」两节"),
                   (f_fp.name, "功能点逐条测算明细 + 分子系统汇总 + 计价参数与出处")]
    if f_ops.exists():
        attachments.append((f_ops.name, "运营期逐项费用 + 交付方案一致性检查"))
    if f_tco.exists():
        attachments.append((f_tco.name, "各交付方式的 3 年 / 5 年 TCO 对比"))
    attachments.append((f_notes.name, "计数方法、参数取值与出处、假设与边界"))
    emit_summary(result, pack, attachments, f_sum)

    # 生成端与解析端的列名词表在这里对账 —— 见 gov_sheet.assert_roles_parseable。
    # 放在全部产出之后：此时 role 注册表已收齐所有 sheet 的列。
    gov_sheet.assert_roles_parseable()

    t = result["totals"]
    print(f"BOM {result['bom_version']} × {pack.pack_id} × {deal.project_type}项目")
    print(f"  范围 {result['scope']['items']} 条"
          + (f"（排除 {result['scope']['excluded']}）" if result["scope"]["excluded"] else ""))
    print(f"  软件开发 ¥{t['software_dev']:,.2f}　硬件 ¥{t['hardware_purchase']:,.2f}　"
          f"其他 ¥{t['other_fees']:,.2f}")
    print(f"  建设期合计 ¥{t['construction_total']:,.2f}")
    print(f"  输出 → {out}")
    if args.publish_lark:
        for line in publish_lark(sorted(out.glob("*.xlsx")), args.publish_lark):
            print(f"  飞书 {line}")

    # fail 级违规**打在最后**。此前它们只写进 03 的一个 sheet，命令静默退出 0 ——
    # 广东 × 全私有化 会出一份 ¥921 万的报价，而 D4/D8 对应的科目在广东根本
    # not_in_scope。「检查了但不说」比不检查更糟：它给人一种已经查过的错觉。
    # 打在最前会被后面的汇总淹没，所以放末尾。
    fails = [v for v in violations if v.severity == "fail"]
    if fails:
        print(f"\n  ✗ {len(fails)} 条一致性违规（fail）—— 本方案在本标准下不成立：")
        for v in fails:
            print(f"      [{v.rule}] {v.message}")
        if args.allow_rule_violations:
            print("    （--allow-rule-violations 已指定，按探索性试算放行）")
        else:
            print("    产物已生成供查看，但退出码非零。"
                  "确为探索性试算请加 --allow-rule-violations。")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
