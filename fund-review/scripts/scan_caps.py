"""scan_caps — 比例守恒检查 (PDF 硬上限).

依据：
  - 其他费用比例（设计/监理/测试/等保/风险/密码）合计 ≤ 10%（扣除系统集成费后） — p.20
  - 预备费 ≤ 2% — p.25
  - 移动办公 ≤ 总价 30% — p.16（OA 表4）

本 scanner 在 run_optimize 末尾运行，吃 completeness 后的总账，验证比例。
"""
from __future__ import annotations

from typing import Any


OTHER_FEE_ITEMS = {
    "设计/咨询服务费", "工程监理费", "第三方测试费",
    "信息安全等级保护测评费", "信息安全风险评估费", "密码应用安全评估费",
}


def scan(quote: dict[str, Any], all_suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    sid = 0

    # 算总价（原始 + 所有建议 delta）
    candidates = ["成本总价", "对外总价", "成本总价（元）", "对外总价（元）", "研发报价"]
    orig_total = 0.0
    for wb in quote["workbooks"]:
        for sh in wb["sheets"]:
            if sh["kind"] == "summary":
                continue
            for row in sh["rows"]:
                for c in candidates:
                    v = row["cells"].get(c)
                    if isinstance(v, (int, float)):
                        orig_total += v
                        break
    delta_sum = sum(s.get("estimated_delta_amount", 0) for s in all_suggestions)
    proj_total = orig_total + delta_sum

    # 其他费用合计（add-cost-item 类，名字在 OTHER_FEE_ITEMS 内）
    other_fee_sum = 0.0
    integration_fee = 0.0
    reserve_fee = 0.0
    for s in all_suggestions:
        if s["category"] != "add-cost-item":
            continue
        name = s["proposed_change"]["fee_name"]
        amt = s["proposed_change"]["amount"]
        if name in OTHER_FEE_ITEMS:
            other_fee_sum += amt
        elif name == "系统集成费":
            integration_fee += amt
        elif name == "预备费":
            reserve_fee += amt

    # Cap 1: 其他费用 ≤ 10% （以扣除集成费后的总价为分母）
    base_for_other = proj_total - integration_fee
    if base_for_other > 0 and other_fee_sum / base_for_other > 0.10:
        sid += 1
        findings.append({
            "id": f"P{sid:03d}",
            "category": "compliance-cap",
            "severity": "high",
            "target": {"workbook": "@meta", "sheet": "@global", "row": 0},
            "proposed_change": {
                "operation": "scale_down",
                "what": "其他费用合计",
                "current_ratio": round(other_fee_sum / base_for_other, 4),
                "cap": 0.10,
                "current_value": other_fee_sum,
                "max_allowed": round(base_for_other * 0.10, 2),
            },
            "rationale": f"其他费用合计 ¥{other_fee_sum:,.0f} / 基数 ¥{base_for_other:,.0f} "
                         f"= {other_fee_sum/base_for_other*100:.2f}% > 10%",
            "standard_refs": [{"section": "三.(一).2.6", "page": 20,
                               "snippet": "扣除系统集成费后的其他几项费用取费比例之和不得超过预算总额的 10%"}],
            "estimated_delta_amount": 0,
            "auto_applicable": False,
        })

    # Cap 2: 预备费 ≤ 2%
    if proj_total > 0 and reserve_fee / proj_total > 0.020:
        sid += 1
        findings.append({
            "id": f"P{sid:03d}",
            "category": "compliance-cap",
            "severity": "high",
            "target": {"workbook": "@meta", "sheet": "@global", "row": 0},
            "proposed_change": {
                "operation": "scale_down",
                "what": "预备费",
                "current_ratio": round(reserve_fee / proj_total, 4),
                "cap": 0.02,
                "current_value": reserve_fee,
                "max_allowed": round(proj_total * 0.02, 2),
            },
            "rationale": f"预备费 ¥{reserve_fee:,.0f} / 总价 ¥{proj_total:,.0f} "
                         f"= {reserve_fee/proj_total*100:.2f}% > 2%",
            "standard_refs": [{"section": "三.(一).2.9", "page": 25,
                               "snippet": "预备费按建设工程项目工程费用的 2% 作为上限"}],
            "estimated_delta_amount": 0,
            "auto_applicable": False,
        })

    # 信息性输出：当前比例摘要（severity=low，便于报告呈现）
    sid += 1
    findings.append({
        "id": f"P{sid:03d}",
        "category": "compliance-cap-info",
        "severity": "low",
        "target": {"workbook": "@meta", "sheet": "@global", "row": 0},
        "proposed_change": {
            "operation": "info",
            "summary": {
                "proj_total": round(proj_total, 2),
                "integration_fee": round(integration_fee, 2),
                "integration_pct": round(integration_fee / proj_total * 100, 2) if proj_total else 0,
                "other_fee_sum": round(other_fee_sum, 2),
                "other_fee_pct_of_excl_integration": round(other_fee_sum / base_for_other * 100, 2) if base_for_other else 0,
                "reserve_fee": round(reserve_fee, 2),
                "reserve_pct": round(reserve_fee / proj_total * 100, 2) if proj_total else 0,
            },
        },
        "rationale": "比例守恒检查信息汇总",
        "standard_refs": [{"section": "三.(一).2.6", "page": 20, "snippet": "其他费用 ≤ 10%"},
                          {"section": "三.(一).2.9", "page": 25, "snippet": "预备费 ≤ 2%"}],
        "estimated_delta_amount": 0,
        "auto_applicable": False,
    })

    return findings
