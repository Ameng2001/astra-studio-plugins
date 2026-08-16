"""generate_project_summary — 项目总报价汇总表.

财评打开第一眼看的总盘子文件。生成 `项目总报价汇总.xlsx`：

  | # | 费用大类                  | 金额 | 占比 | 引用明细 |
  |---|---------------------------|------|------|----------|
  | 1 | 软件开发费                | ¥xxM |  xx% | quote-final-平台 + 大模型 L1/L2 建设 |
  | 2 | 软件产品购置费            | ¥xx  |  xx% | quote-additional-fees / 待补品牌型号 |
  | 3 | 硬件设备购置费 (六园所)   | ¥13M |  xx% | quote-final-设备 / 六园所设备汇总 |
  | 4 | 其他费用与预备费          | ¥4M  |  xx% | quote-additional-fees |
  | 5 | 大模型运营运维 (建设期外) | ¥xxM |  xx% | quote-final-大模型 附6-9 |
  |   | 合计                      | ¥53.6M | 100% | |
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl


CANDIDATE_MONEY_KEYS = ("成本总价", "对外总价", "成本总价（元）", "对外总价（元）",
                        "研发报价", "对外运营报价（元）", "运维报价", "对外总价")


def _row_money(row_cells: dict) -> float:
    for k in CANDIDATE_MONEY_KEYS:
        v = row_cells.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return 0.0


def compute_breakdown(quote: dict, sugg: dict) -> dict:
    """Compute each cost category from quote.json + suggestions."""
    breakdown = {
        "软件开发费 (定制) — 平台软件":   0.0,
        "软件开发费 (定制) — 大模型 L1 建设":   0.0,
        "软件开发费 (定制) — 大模型 L2 建设":   0.0,
        "软件产品购置费 — 商业组件剥离": 0.0,
        "硬件设备购置费 — 六园所合计":  0.0,
        "大模型运营运维 (L1)":          0.0,
        "大模型运营运维 (L2)":          0.0,
        "其他费用与预备费":              0.0,
    }

    for wb in quote["workbooks"]:
        for sh in wb["sheets"]:
            if sh["kind"] == "summary":
                continue
            # 部署&交付 sheet 已重分类到「其他费用与预备费」，不计入软件开发费（避免重复）
            if sh["kind"] == "deploy":
                continue
            sheet_total = sum(_row_money(r["cells"]) for r in sh["rows"])
            if sheet_total <= 0:
                continue
            sn = sh["name"]
            if wb["kind"] == "platform":
                breakdown["软件开发费 (定制) — 平台软件"] += sheet_total
            elif wb["kind"] == "llm":
                if sh["kind"] == "ops":
                    if "L1" in sn:
                        breakdown["大模型运营运维 (L1)"] += sheet_total
                    else:
                        breakdown["大模型运营运维 (L2)"] += sheet_total
                else:
                    if "L1" in sn or "底座" in sn or "知识工程" in sn or "数据建设" in sn:
                        breakdown["软件开发费 (定制) — 大模型 L1 建设"] += sheet_total
                    else:
                        breakdown["软件开发费 (定制) — 大模型 L2 建设"] += sheet_total
            elif wb["kind"] == "device":
                breakdown["硬件设备购置费 — 六园所合计"] += sheet_total

    # 其他费用与预备费 — 来自 add-cost-item suggestions
    for s in sugg.get("suggestions", []):
        if s["category"] == "add-cost-item":
            breakdown["其他费用与预备费"] += s["proposed_change"]["amount"]

    # 软件产品购置费 — 来自 commercial-component 检测的剥离
    for s in sugg.get("suggestions", []):
        if s["category"] == "commercial-component":
            breakdown["软件产品购置费 — 商业组件剥离"] += \
                s["proposed_change"]["detach_amount_to_purchase_fee"]

    return breakdown


def main(session_dir: str) -> None:
    s = Path(session_dir)
    quote = json.loads((s / "quote.json").read_text())
    sugg = json.loads((s / "optimize-suggestions.json").read_text())
    mode_name = sugg.get("mode", {}).get("name", "?")

    # 修正：当存在 commercial-component 剥离时，要从「软件开发费」相应扣减
    bd = compute_breakdown(quote, sugg)
    purchase_fee = bd["软件产品购置费 — 商业组件剥离"]
    # 简化：均摊扣减到三个开发费小项
    if purchase_fee > 0:
        dev_keys = ["软件开发费 (定制) — 平台软件",
                    "软件开发费 (定制) — 大模型 L1 建设",
                    "软件开发费 (定制) — 大模型 L2 建设"]
        dev_total = sum(bd[k] for k in dev_keys)
        if dev_total > 0:
            for k in dev_keys:
                bd[k] -= purchase_fee * (bd[k] / dev_total)

    grand = sum(bd.values())

    # 生成 xlsx
    wb_out = openpyxl.Workbook()
    ws = wb_out.active
    ws.title = "项目总报价汇总"

    # 标题
    ws.append(["广西数智幼教项目报价总览"])
    ws.cell(row=1, column=1).font = openpyxl.styles.Font(bold=True, size=14)
    ws.merge_cells(start_row=1, end_row=1, start_column=1, end_column=5)
    ws.append([])

    ws.append([f"报价版本: {mode_name}", "", "", "", ""])
    ws.append([])

    ws.append(["序号", "费用大类", "金额（元）", "占比", "标准条款"])
    for cell in ws[5]:
        cell.font = openpyxl.styles.Font(bold=True)

    sec_map = {
        "软件开发费 (定制) — 平台软件":           "三.(一).2.1",
        "软件开发费 (定制) — 大模型 L1 建设":     "三.(一).2.1",
        "软件开发费 (定制) — 大模型 L2 建设":     "三.(一).2.1",
        "软件产品购置费 — 商业组件剥离":          "三.(一).2.4 表6",
        "硬件设备购置费 — 六园所合计":           "三.(一).2.5 表7",
        "大模型运营运维 (L1)":                  "三.(三).2",
        "大模型运营运维 (L2)":                  "三.(三).2",
        "其他费用与预备费":                      "三.(一).2.6-2.9",
    }

    for i, (cat, amt) in enumerate(bd.items(), 1):
        pct = amt / grand * 100 if grand else 0
        ws.append([i, cat, round(amt, 0), f"{pct:.2f}%", sec_map.get(cat, "")])
    ws.append([])
    ws.append([None, "合计", round(grand, 0), "100.00%", ""])
    last = ws.max_row
    ws.cell(row=last, column=2).font = openpyxl.styles.Font(bold=True)
    ws.cell(row=last, column=3).font = openpyxl.styles.Font(bold=True)

    # 关联附件
    ws.append([])
    ws.append(["关联附件"])
    ws.cell(row=ws.max_row, column=1).font = openpyxl.styles.Font(bold=True)
    # 引用标准化送审件名（与送审包一致、不含输入日期/内部术语）
    attachments = [
        ("1", "Z01 平台软件功能报价", "平台软件功能明细"),
        ("2", "Z02 行业大模型功能报价", "行业大模型功能明细 (L1/L2 建设 + 运营运维)"),
        ("3", "Z03 场景设备报价", "六园所设备明细 + 设备汇总 sheet"),
        ("4", "Z04 其他费用与预备费", "其他费用与预备费 (9 大科目)"),
        ("5", "F02 可研功能点估算表", "可研功能点估算表 (含 NESMA 五类分类)"),
        ("6", "F01 取值依据说明", "类别/复用度/FP 方法依据"),
        ("7", "F03 评审应答说明", "评审应答说明 (含模板填空)"),
    ]
    ws.append(["序号", "文件名", "用途"])
    for cell in ws[ws.max_row]:
        cell.font = openpyxl.styles.Font(bold=True)
    for sn, fname, purpose in attachments:
        ws.append([sn, fname, purpose])

    # 列宽
    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 10
    ws.column_dimensions["E"].width = 18

    out = s / "项目总报价汇总.xlsx"
    wb_out.save(out)
    print(f"{out} written — 合计 ¥{grand:,.0f}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/smoke")
