"""scan_labor — labor-pricing scanner.

For every software row with 人/天 (or 人天), derive FP, classify category,
compute the standard-compliant cost range, and emit a labor-pricing
suggestion if the current cost is outside the range.
"""
from __future__ import annotations

from typing import Any

from derive_fp import derive_fp_from_person_days, classify_fp_row, fp_to_cost_range
from mode_config import current as _current_mode


PERSON_DAYS_HEADERS = ["人/天", "人天"]
UNIT_PRICE_HEADERS = ["成本单价", "单价（元）"]
COST_TOTAL_HEADERS = ["成本总价", "成本总价（元）", "研发费计算（元）", "研发费用报价"]


def _cell(row: dict[str, Any], headers: list[str]):
    for h in headers:
        if h in row["cells"]:
            return row["cells"][h], h
    return None, None


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    sid = 0
    for wb in quote["workbooks"]:
        if wb["kind"] not in {"platform", "llm"}:
            continue
        for sh in wb["sheets"]:
            if sh["kind"] not in {"items", "deploy"}:
                continue   # skip ops/summary
            for row in sh["rows"]:
                person_days, _ = _cell(row, PERSON_DAYS_HEADERS)
                unit_price, price_header = _cell(row, UNIT_PRICE_HEADERS)
                cost_total, total_header = _cell(row, COST_TOTAL_HEADERS)
                if not isinstance(person_days, (int, float)) or person_days <= 0:
                    continue
                if not isinstance(unit_price, (int, float)):
                    continue

                detail = (
                    row["cells"].get("建设详情")
                    or row["cells"].get("详情")
                    or row["cells"].get("建设详情（定位）")
                    or row["cells"].get("建设内容")
                    or ""
                )
                category, reuse = classify_fp_row(str(detail), sh["name"], wb["kind"])
                fp = derive_fp_from_person_days(person_days)
                if fp <= 0:
                    continue
                min_cost, max_cost = fp_to_cost_range(fp, category, reuse)
                current_total = (
                    cost_total
                    if isinstance(cost_total, (int, float))
                    else unit_price * person_days
                )

                # 缓冲由 mode 配置决定（激进 1.00 / 保守 0.98）
                target_total = max_cost * _current_mode().target_buffer
                if current_total > max_cost * 1.02:
                    severity = "high"
                    proposed_total = target_total
                elif current_total > target_total * 1.005:
                    # 在带内但贴上限 → 轻微下调到 -2% 缓冲位
                    severity = "medium"
                    proposed_total = target_total
                elif current_total < min_cost * 0.98:
                    severity = "medium"
                    proposed_total = min_cost
                else:
                    continue

                sid += 1
                suggestions.append({
                    "id": f"L{sid:03d}",
                    "category": "labor-pricing",
                    "severity": severity,
                    "target": {
                        "workbook": wb["path"],
                        "sheet": sh["name"],
                        "row": row["row_index"],
                        "col_header": price_header,
                        "current_value": unit_price,
                        "current_total": current_total,
                    },
                    "proposed_change": {
                        "fp_estimate": fp,
                        "category": category,
                        "reuse": reuse,
                        "target_total_min": min_cost,
                        "target_total_max": max_cost,
                        "new_value": round(proposed_total / person_days),
                        "also_recompute": [total_header] if total_header else [],
                    },
                    "rationale": (
                        f"按完全定制化(G2)反推 FP={fp}，类别 {category}，复用度 {reuse}；"
                        f"标准成本带 ¥{min_cost:,.0f}~¥{max_cost:,.0f}，当前 ¥{current_total:,.0f}"
                    ),
                    "standard_refs": [
                        {"section": "三.(一).2.1", "page": 13, "snippet": "定制软件开发费用公式"},
                        {"section": "三.(一).2.1.表3", "page": 14, "snippet": f"软件类别调整因子 {category}"},
                    ],
                    "estimated_delta_amount": round(proposed_total - current_total),
                    "auto_applicable": True,
                })
    return suggestions
