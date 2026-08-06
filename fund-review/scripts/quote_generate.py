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
from collections import defaultdict
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

    # 二、购置类科目 —— 无价的**列而不计**，不能因为没价格就整节消失。
    # 财评看到没有这一节会认为方案漏项；看到「待询价 9 项」才知道是价格没到位。
    pur = result.get("purchases") or {"by_subject": [], "pending": [],
                                      "not_applicable": []}
    for g in pur["by_subject"]:
        pend = (f"；其中 {g['pending_count']} 项待询价，未计入金额"
                if g["pending_count"] else "")
        ws.append([f"二、{g['subject']}", "", g["total"],
                   f"{len(g['rows'])} 项{pend}",
                   "三.(二) p.8-9"])
        for r in g["rows"]:
            amount = "待询价" if r["reference_unit_price"] is None else r["amount"]
            ws.append(["", f"{r['name']}（{r['qty']}{r['unit']}"
                           f"{'，' + r['pricing_model'] if r['pricing_model'] else ''}）",
                       amount, r["pricing_basis"], ""])

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
        # 列而不计 ≠ 不列。范围外的子系统整段消失，财评会认为方案漏项 ——
        # 采购科目那一轮的教训，这里同样适用。
        ws.append(["【范围说明】未计入本清单的条目", "", "", "列而不计：列出以证明不是漏项", ""])
        for k, v in result["scope"]["excluded"].items():
            ws.append(["", k, "不计入", f"{v} 条", ""])
        oos = result["scope"].get("out_of_scope_systems") or []
        if oos:
            ws.append(["", "本次范围外的子系统", "不计入",
                       "；".join(f"{s['system']}（{s['items']} 条）" for s in oos), ""])

    if pur["not_applicable"]:
        ws.append([])
        ws.append(["【本方案不采用的采购形态】", "", "", "同一标的的两种表达只能取一种，"
                   "另一种在此列出以证明不是漏项", ""])
        by_reason: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in pur["not_applicable"]:
            by_reason[f"{r['reason']}（{r['mode']}）"].append(r)
        for reason, rows in sorted(by_reason.items()):
            ref = sum(r.get("legacy_reference_yuan") or 0 for r in rows)
            note = reason + (f"；raw-input 旧清单对应 ¥{ref:,.0f}（仅供差额归因，"
                             f"非定价依据）" if ref else "")
            ws.append(["", f"{rows[0]['subject']}　{len(rows)} 项", "不计入", note, ""])

    if pur["pending"]:
        ws.append([])
        ws.append([f"【待询价】{len(pur['pending'])} 项已列入清单但未计金额，"
                   f"出正式报价前须补齐盖章询价报价单"])
        for r in pur["pending"]:
            ws.append(["", r["name"], "待询价", r["note"], ""])

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

    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "参数"
    ws.append(["参数", "取值", "出处"])
    for label, dotted in [
        ("规模变更因子", f"factors.size_change.values.{method}"),
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
                "产品成熟度", "复用度档位", "复用度因子", "应用类型因子",
                "调整后功能点", "复算公式"])
    for i in items:
        if i.cls not in FP_COUNTED_CLASSES or not i.nesma:
            continue
        w = pack.fp_weight(method, i.nesma.type)
        app_f = pack.factor("app_type", i.app_type or "业务处理")
        # 复用度按条目的 maturity 经标准包词表解析 —— 不是全表一个值
        lvl = engine.reuse_level(i)
        reuse_f = pack.factor("reuse", lvl)
        afp = engine.profile.adjusted_fp(
            w, pack=pack, counting_method=method,
            reuse_level=lvl, app_type=i.app_type or "业务处理")
        det.append([i.id, i.path.system, i.name, i.nesma.type, w,
                    size_f, i.maturity, lvl, reuse_f, app_f, afp,
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
    ap.add_argument("--publish-lark", metavar="FOLDER_TOKEN",
                    help="把生成的 xlsx 导入飞书文件夹供在线查看（快照，非维护对象）")
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

    result = CostingEngine(bom, pack, deal, delivery).run()
    out = args.out / "out"
    out.mkdir(parents=True, exist_ok=True)

    emit_procurement_list(result, pack, out / "01-建设期采购清单.xlsx")
    emit_fp_worksheet(bom, result, pack, deal, out / "02-功能点测算表.xlsx")
    emit_notes(bom, result, pack, deal, out / "05-编制说明.md")
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
    if args.publish_lark:
        for line in publish_lark(sorted(out.glob("*.xlsx")), args.publish_lark):
            print(f"  飞书 {line}")


if __name__ == "__main__":
    main()
