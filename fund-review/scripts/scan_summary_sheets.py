"""scan_summary_sheets — 汇总 sheet 缺失检测.

财评典型挑战："设备表 6 个园所并列、无统一合计，请提供汇总"。本扫描器检测
device 工作簿是否有 summary sheet，缺失则 emit `add-summary-sheet` 建议。

实际执行（apply_change）：读所有园所 sheet 的合计行，按设备类别（网络/主机/
存储/安全/智能化）汇总到新 sheet "六园所设备总表"。
"""
from __future__ import annotations

from typing import Any


def _sheet_total(sh: dict) -> float:
    """Sum money values from a parsed sheet (quote.json was parsed with data_only=True so formulas already evaluated)."""
    money_keywords = ("金额", "合计", "总价", "对外总价", "成本总价", "直销总价", "目录总价")
    total = 0.0
    for row in sh["rows"]:
        # take the first numeric money-named cell
        for k, v in row["cells"].items():
            if isinstance(v, (int, float)) and v > 1 and any(mk in k for mk in money_keywords):
                total += v
                break
    return round(total)


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    sid = 0
    for wb in quote["workbooks"]:
        if wb["kind"] != "device":
            continue
        has_summary = any(sh["kind"] == "summary" for sh in wb["sheets"])
        if has_summary:
            continue
        sub_sheets = [sh for sh in wb["sheets"] if sh["kind"] in {"items", "unknown"}]
        per_sheet_totals = [(sh["name"], _sheet_total(sh)) for sh in sub_sheets]
        sid += 1
        findings.append({
            "id": f"U{sid:03d}",
            "category": "add-summary-sheet",
            "severity": "high",
            "stance_origin": "合规守护",
            "target": {
                "workbook": wb["path"],
                "sheet": "@new:六园所设备汇总",
                "row": 0,
            },
            "proposed_change": {
                "operation": "add_summary_sheet",
                "sheet_name": "六园所设备汇总",
                "source_sheets": [s["name"] for s in sub_sheets],
                "per_sheet_totals": per_sheet_totals,
                "grand_total": round(sum(t for _, t in per_sheet_totals)),
                "advice": (
                    f"设备工作簿含 {len(sub_sheets)} 个园所 sheet 但无统一汇总。"
                    f"按 PDF 表7 编制要求应有合计行，且利于财评核对总金额。"
                    f"自动生成「六园所设备汇总」sheet，列出各园所合计 + 总计。"
                ),
            },
            "rationale": (
                f"设备工作簿 {len(sub_sheets)} 园所并列、缺统一汇总，财评核对总金额无入口。"
                f"按 PDF p.18-19 表7 编制要求补汇总 sheet。"
            ),
            "standard_refs": [
                {"section": "三.(一).2.5", "page": 18, "snippet": "表7 硬件设备购置预算支出表"},
            ],
            "estimated_delta_amount": 0,
            "auto_applicable": True,
        })
    return findings
