"""scan_completeness — 检测 9 大费用科目缺失情况.

按 PDF p.20《其他费用》及配套表 9-13，一份合规的信息化项目报价应包含：
  1. 软件开发费       三.(一).2.1   p.13
  2. 软件产品购置费    三.(一).2.4   p.17  表6
  3. 硬件设备购置费    三.(一).2.5   p.18  表7
  4. 系统集成费       三.(一).2.6.1 p.20  表9   (软硬件总额 3-8%)
  5. 测试费(测评/检测) 三.(一).2.6.4 p.23  表13  (软件开发费 0.5-2.5%)
  6. 设计/咨询服务费   三.(一).2.6.2 p.21  表11
  7. 工程监理费       三.(一).2.6.3 p.22  表12
  8. 等保测评/风险评估/密码评估 三.(一).2.6.5-7 p.24
  9. 预备费           三.(一).2.9   p.25  (≤总价 2%)

当前 3 张表只覆盖 1+3 → 缺 4/5/6/7/8/9（约 6 个独立科目）。本 scanner 检测缺失，
按 PDF 公式估算金额，emit `add-cost-item` 建议。投标方可选择采纳/拒绝/调整。
"""
from __future__ import annotations

from typing import Any

from formula_engine import (
    compute_design_fee,
    DENGBAO_L3_BASE,
)
from deploy_breakdown import compute as compute_deploy_breakdown


# 缺失检测的关键词识别：扫整个 quote.json，若已有则不重复建议
CATEGORY_HINTS = {
    "系统集成费":   ["系统集成", "集成服务"],
    "测试费":       ["第三方测试", "测试费", "检测费", "测评"],
    "设计咨询费":   ["设计费", "咨询费", "可行性研究", "方案设计"],
    "工程监理费":   ["监理费", "工程监理"],
    "等保测评费":   ["等保", "等级保护", "安全测评"],
    "风险评估费":   ["风险评估"],
    "密码评估费":   ["密码评估", "密码应用安全"],
    "预备费":       ["预备费", "不可预见"],
    "软件产品购置费": ["软件购置", "成品软件"],
}


def _quote_has_keyword(quote: dict, keywords: list[str]) -> bool:
    """Only match against short title columns (not 详情/建设内容 multi-line text)
    AND require the row has a money value, to avoid false positives from feature
    descriptions like "测评"出现在功能详情里."""
    short_title_cols = ("分项", "费用名称", "名称", "类别", "类型")
    money_cols = ("成本总价", "对外总价", "成本总价（元）", "金额", "总报价金额（元）")
    for wb in quote["workbooks"]:
        for sh in wb["sheets"]:
            for row in sh["rows"]:
                title_text = ""
                for c in short_title_cols:
                    v = row["cells"].get(c)
                    if isinstance(v, str):
                        title_text += " " + v
                if not title_text:
                    continue
                has_money = any(isinstance(row["cells"].get(c), (int, float)) and row["cells"].get(c) > 0
                                for c in money_cols)
                if has_money and any(k in title_text for k in keywords):
                    return True
    return False


def _compute_base_amounts(quote: dict) -> dict[str, float]:
    """估算各类基数（按当前 quote.json 的cached values）."""
    software_total = 0.0
    hardware_total = 0.0
    candidates = ["成本总价", "对外总价", "成本总价（元）", "对外总价（元）", "研发报价"]
    for wb in quote["workbooks"]:
        if wb["kind"] in {"platform", "llm"}:
            for sh in wb["sheets"]:
                # deploy sheet 属实施类，不计入软件开发费基数（避免 测试费/设计费 虚高）
                if sh["kind"] == "items":
                    for row in sh["rows"]:
                        for c in candidates:
                            v = row["cells"].get(c)
                            if isinstance(v, (int, float)):
                                software_total += v
                                break
        elif wb["kind"] == "device":
            for sh in wb["sheets"]:
                for row in sh["rows"]:
                    for c in candidates + ["金额", "合计"]:
                        v = row["cells"].get(c)
                        if isinstance(v, (int, float)):
                            hardware_total += v
                            break
    base_total = software_total + hardware_total
    return {
        "software_dev_total": software_total,
        "hardware_total": hardware_total,
        "soft_hw_subtotal": base_total,
        "project_total": base_total,        # 简化：暂不含尚未补全的费用
    }


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    base = _compute_base_amounts(quote)
    sw = base["software_dev_total"]
    hw = base["hardware_total"]
    subtotal = base["soft_hw_subtotal"]
    total = base["project_total"]

    sid = 0

    def emit(name: str, section_id: str, page: int, formula: str, amount: float,
             severity: str = "high", note: str = "") -> None:
        nonlocal sid
        sid += 1
        suggestions.append({
            "id": f"C{sid:03d}",
            "category": "add-cost-item",
            "severity": severity,
            "target": {
                "workbook": "@new:quote-additional-fees.xlsx",
                "sheet": "其他费用与预备费",
                "row": sid + 1,  # row in the new sheet (header at row 1)
            },
            "proposed_change": {
                "operation": "add_row",
                "fee_name": name,
                "amount": round(amount),
                "formula_explain": formula,
                "note": note,
            },
            "rationale": f"PDF 必有科目「{name}」未在当前报价中独立列示。"
                         f"按 {section_id} 公式估算 ¥{amount:,.0f}。",
            "standard_refs": [{"section": section_id, "page": page, "snippet": formula}],
            "estimated_delta_amount": round(amount),  # 增加总价
            "auto_applicable": True,
        })

    # 1) 系统集成费 + 培训费 + 差旅 — 优先采用「部署&交付」sheet 实绩，消除重复计列
    deploy = compute_deploy_breakdown(quote)
    if deploy["total"] > 0:
        # 部署&交付 sheet 存在 → 用实绩拆分，不再叠加 软硬件×3% 估算
        emit("系统集成费", "三.(一).2.6.1", 20,
             f"采用「部署&交付」sheet 实绩（实施+部署+管理+技术支持），来源 {deploy['source_sheets']}",
             deploy["系统集成费"],
             note="不叠加软硬件×3%估算，避免与部署&交付重复计列（PDF 2.7）")
        if deploy["培训费"] > 0:
            emit("培训费", "三.(一).2.8", 24,
                 "来自「部署&交付」现场实施中操作培训部分（按 20% 拆分）",
                 deploy["培训费"],
                 severity="medium",
                 note="按柳州市相关培训费文件标准执行；拆分比例可据实调整")
        if deploy["直接非人力成本-差旅费"] > 0:
            emit("直接非人力成本-差旅费", "三.(一).2.1.⑤", 14,
                 "来自「部署&交付」差旅成本",
                 deploy["直接非人力成本-差旅费"],
                 severity="medium",
                 note="PDF 2.1.⑤ 直接非人力一般为0，特殊情况需附测算依据（自有项目/交付人员差旅）")
    elif not _quote_has_keyword(quote, CATEGORY_HINTS["系统集成费"]):
        # 无部署&交付 sheet → 回退 软硬件×3% 估算
        amount = subtotal * 0.03
        emit("系统集成费", "三.(一).2.6.1", 20,
             "软硬件总额 × 3%（表9 集中部署下限）",
             amount,
             note="若供应商承担总集成，可在工程费用中合并计列；否则需独立列示")

    # 2) 测试费：软件开发费 0.5-2.5%
    if not _quote_has_keyword(quote, CATEGORY_HINTS["测试费"]):
        amount = sw * 0.01    # 取 1% (表13 1000-2000万档 1-2% 下沿)
        emit("第三方测试费", "三.(一).2.6.4", 23,
             "软件开发费 × 1.0%（表13 1000-2000万档下沿）",
             amount,
             note="如不委托第三方测试可拒绝；按主管要求决定")

    # 3) 设计咨询费：按表11 内插法 + 项目类型系数
    if not _quote_has_keyword(quote, CATEGORY_HINTS["设计咨询费"]):
        amount_wan = subtotal / 10000
        try:
            fee_wan = compute_design_fee(amount_wan, "综合")
            amount = fee_wan * 10000
        except Exception:
            amount = subtotal * 0.020
        emit("设计/咨询服务费", "三.(一).2.6.2", 21,
             f"计费额 ¥{amount_wan:,.0f}万 按表11 综合类内插",
             amount)

    # 4) 工程监理费：按表12 分档 + 综合类系数 1.1
    if not _quote_has_keyword(quote, CATEGORY_HINTS["工程监理费"]):
        amount_wan = subtotal / 10000
        # 表12 简化插值：4000万对应基价 40W、5000万 42W
        if amount_wan <= 200:
            base = max(amount_wan * 0.03 * 10000, 5 * 10000)
        elif amount_wan <= 5000:
            # 内插 (200, 5) → (5000, 42)
            base = (5 + (amount_wan - 200) / (5000 - 200) * (42 - 5)) * 10000
        else:
            base = amount_wan * 0.0084 * 10000   # ≤0.84%
        amount = base * 1.1   # 综合类系数 1.1
        emit("工程监理费", "三.(一).2.6.3", 22,
             f"计费额 ¥{amount_wan:,.0f}万 按表12 综合类系数 1.1",
             amount)

    # 5) 等保测评费：三级 ¥10W × 1.0（基本）
    if not _quote_has_keyword(quote, CATEGORY_HINTS["等保测评费"]):
        amount = DENGBAO_L3_BASE * 1.0
        emit("信息安全等级保护测评费", "三.(一).2.6.5", 24,
             "等保三级基价 ¥10 万 × 1.0 调整系数",
             amount,
             note="若系统定级为二级则改为 ¥8 万；定级未明确时按三级估")

    # 6) 风险评估费 — 按内容核定，估 ¥10W
    if not _quote_has_keyword(quote, CATEGORY_HINTS["风险评估费"]):
        emit("信息安全风险评估费", "三.(一).2.6.6", 24,
             "按评估内容核定，含漏洞扫描/人工审计/渗透测试，参考 ¥10 万",
             100_000,
             severity="medium",
             note="若已含等保测评的渗透测试内容，按 PDF 2.7 重复内容只计一次原则可减")

    # 7) 密码应用安全评估费 — 新规要求，估 ¥8W
    if not _quote_has_keyword(quote, CATEGORY_HINTS["密码评估费"]):
        emit("密码应用安全评估费", "三.(一).2.6", 20,
             "按 GM/T 0054 标准核定，参考 ¥8 万",
             80_000,
             severity="medium",
             note="若项目无独立密码系统可拒绝；涉及电子签章/CA 时必有")

    # 8) 预备费：≤总价 2%，取 2% 上限（立场：投标方多预留缓冲）
    if not _quote_has_keyword(quote, CATEGORY_HINTS["预备费"]):
        amount = total * 0.02
        emit("预备费", "三.(一).2.9", 25,
             "总价 × 2%（PDF 规定上限）",
             amount,
             note="不可预见费用预留，必有")

    # 9) 软件产品购置费 — 数据库/中间件/操作系统等。当前没有，可能合并在大模型里。
    #    标记为 informational，severity=low
    if not _quote_has_keyword(quote, CATEGORY_HINTS["软件产品购置费"]):
        suggestions.append({
            "id": f"C{sid+1:03d}",
            "category": "add-cost-item",
            "severity": "low",
            "target": {
                "workbook": "@new:quote-additional-fees.xlsx",
                "sheet": "其他费用与预备费",
                "row": sid + 2,
            },
            "proposed_change": {
                "operation": "add_row",
                "fee_name": "软件产品购置费",
                "amount": 0,
                "formula_explain": "若使用商业操作系统/数据库/中间件需独立列示并提供 ≥3 个品牌型号对比（表6 注3）",
                "note": "本项目大模型已含算力底座，若无独立商业软件采购可拒绝",
            },
            "rationale": "PDF 必有科目「软件产品购置费」未列示。本项目可能不需要该项，标记 informational",
            "standard_refs": [{"section": "三.(一).2.4", "page": 17, "snippet": "表6 软件产品购置预算支出表"}],
            "estimated_delta_amount": 0,
            "auto_applicable": False,
        })

    return suggestions
