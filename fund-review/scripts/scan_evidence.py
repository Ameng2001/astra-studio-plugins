"""scan_evidence — 取值依据扫描器.

针对 PDF 多处明文要求"凡取值需列明依据"的硬要求，扫描已有建议，
为下列三类取值生成"答辩话术"：

  1. 软件类别因子取值（业务处理/应用集成/大数据多媒体/人工智能）
     PDF 表3 注1: 凡取值超过 1 的需列明具体取值依据
  2. 复用度调整系数取值（高/中/低）
     PDF 表3 注2: 新建项目默认低；已有系统改造默认中；按实际调整
  3. 取上限/中位/下限取值
     PDF p.13 ⑤: 直接非人力成本一般为 0，特殊情况需明确原因

emit `evidence-supplement` 类建议（estimated_delta=0），不动金额，只附文字。
所有依据按 (类别 × 复用度) 组合去重，每个组合一条建议（避免按行炸开）。

辅助产物：取值依据说明.md，作为财评附件。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


# 类别取值依据模板（按 PDF 表3）
CATEGORY_BASIS = {
    "业务处理": {
        "examples": "业务申报、审批、记录、流程管理类功能",
        "pdf_ref": "PDF 表3 序号1 — 各类业务应用系统、政务服务系统、协同办公系统等",
        "factor_band": "0.8-1.0",
        "stance": "默认取上限 1.0（系统含部分自动化与规则引擎）",
    },
    "应用集成": {
        "examples": "API 接口对接、消息总线、跨系统集成功能",
        "pdf_ref": "PDF 表3 序号2 — 应用集成、公共支撑平台、企业服务总线",
        "factor_band": "1.0-1.2",
        "stance": "取上限 1.2（多系统协议适配 + 数据同步复杂度）",
    },
    "大数据多媒体": {
        "examples": "数据可视化大屏、地图展示、多源数据分析",
        "pdf_ref": "PDF 表3 序号3 — 图形、影像、声音等多媒体应用领域；大数据分析系统",
        "factor_band": "1.0-1.3",
        "stance": "取上限 1.3（项目含数智民生大屏 + 多维数据可视化）",
    },
    "人工智能": {
        "examples": "大模型推理、智能体、知识工程、AI 训练",
        "pdf_ref": "PDF 表3 序号4 — 自然语言处理、深度学习等",
        "factor_band": "1.0-1.5",
        "stance": "取上限 1.5（行业大模型 + 多专业模型 + 智能体编排）",
    },
}

REUSE_BASIS = {
    "低": {
        "factor": "1.0（基线）",
        "stance": "本项目为**新建系统**，按表3 注2 「新建项目默认取 1（复用度低）」",
    },
    "中": {
        "factor": "2/3",
        "stance": "本部分基于已有 L1 底座扩展，按「已有软件系统基础上优化完善」取 2/3",
    },
    "高": {
        "factor": "1/3",
        "stance": "高度复用既有功能模块，需列明具体复用对象及程度",
    },
}


def scan(quote: dict[str, Any], other_suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    sid = 0

    # 统计实际使用的 (类别, 复用度) 组合 — 仅基于 labor-pricing 建议
    combos: dict[tuple[str, str], dict] = defaultdict(lambda: {"rows": 0, "total_delta": 0, "samples": []})
    for sg in other_suggestions:
        if sg["category"] != "labor-pricing":
            continue
        pc = sg["proposed_change"]
        cat, reuse = pc.get("category"), pc.get("reuse")
        if not cat or not reuse:
            continue
        c = combos[(cat, reuse)]
        c["rows"] += 1
        c["total_delta"] += sg.get("estimated_delta_amount", 0)
        if len(c["samples"]) < 3:
            c["samples"].append({
                "sheet": sg["target"]["sheet"],
                "row": sg["target"]["row"],
                "new_value": pc.get("new_value"),
                "fp": pc.get("fp_estimate"),
            })

    # 1. 类别因子取值依据 — 每个类别一条
    cats_used = sorted({c for (c, _) in combos})
    for cat in cats_used:
        basis = CATEGORY_BASIS.get(cat)
        if not basis:
            continue
        rows_count = sum(combos[(cat, r)]["rows"] for r in {"低", "中", "高"} if (cat, r) in combos)
        sid += 1
        findings.append({
            "id": f"E{sid:03d}",
            "category": "evidence-supplement",
            "severity": "info",
            "target": {"workbook": "@meta", "sheet": "@evidence", "row": 0},
            "proposed_change": {
                "operation": "evidence-text",
                "kind": "category-factor",
                "category": cat,
                "factor_band": basis["factor_band"],
                "applies_to_rows": rows_count,
                "evidence_text": basis["stance"],
                "examples": basis["examples"],
            },
            "rationale": f"PDF 表3 注1：凡取值超过 1 的需列明依据。本项目共 {rows_count} 行采用 {cat} 类别，取值 {basis['factor_band']} 上限。",
            "standard_refs": [
                {"section": "三.(一).2.1.表3", "page": 14, "snippet": "软件类别调整因子取值"},
                {"section": "三.(一).2.1.表3", "page": 14, "snippet": "凡取值超过 1 的需列明具体取值依据"},
            ],
            "estimated_delta_amount": 0,
            "auto_applicable": True,
        })

    # 2. 复用度取值依据 — 每个复用度档一条
    reuses_used = sorted({r for (_, r) in combos})
    for reuse in reuses_used:
        basis = REUSE_BASIS.get(reuse)
        if not basis:
            continue
        rows_count = sum(combos[(c, reuse)]["rows"] for c in cats_used if (c, reuse) in combos)
        sid += 1
        findings.append({
            "id": f"E{sid:03d}",
            "category": "evidence-supplement",
            "severity": "info",
            "target": {"workbook": "@meta", "sheet": "@evidence", "row": 0},
            "proposed_change": {
                "operation": "evidence-text",
                "kind": "reuse-factor",
                "reuse": reuse,
                "factor": basis["factor"],
                "applies_to_rows": rows_count,
                "evidence_text": basis["stance"],
            },
            "rationale": f"PDF 表3 注2：复用度调整。本项目共 {rows_count} 行取 {reuse} 复用度。",
            "standard_refs": [
                {"section": "三.(一).2.1.表3", "page": 14, "snippet": "复用度调整系数 — 新建项目默认低（1.0）"},
            ],
            "estimated_delta_amount": 0,
            "auto_applicable": True,
        })

    # 3. 项目性质总论
    sid += 1
    findings.append({
        "id": f"E{sid:03d}",
        "category": "evidence-supplement",
        "severity": "info",
        "target": {"workbook": "@meta", "sheet": "@evidence", "row": 0},
        "proposed_change": {
            "operation": "evidence-text",
            "kind": "project-nature",
            "evidence_text": (
                "本项目为新建数智民生 + 行业大模型一体化建设项目，按 PDF 表3 注2 整体取「新建复用度低」。"
                "项目含数智底座 + 行业大模型 + 智能体 + 数据可视化 + 业务管理多类技术，"
                "按表3 注1「主体功能类型取值」原则，平台软件主体功能为大数据多媒体 + 部分业务处理，"
                "行业大模型部分主体功能为人工智能。"
            ),
        },
        "rationale": "项目性质总论 — 财评开篇判定的关键依据",
        "standard_refs": [
            {"section": "三.(一).2.1.表3", "page": 14, "snippet": "对于定制开发软件类型多种情况，按主体功能取值"},
        ],
        "estimated_delta_amount": 0,
        "auto_applicable": True,
    })

    # 4. FP 估算方法说明
    sid += 1
    findings.append({
        "id": f"E{sid:03d}",
        "category": "evidence-supplement",
        "severity": "info",
        "target": {"workbook": "@meta", "sheet": "@evidence", "row": 0},
        "proposed_change": {
            "operation": "evidence-text",
            "kind": "fp-method",
            "evidence_text": (
                "FP 估算采用 NESMA 估算功能点计数法（按 PDF 第三章 2.1.① 说明），"
                "由原报价的「人/天」工作量反推：FP = 人天 ÷ (6.51 人时/FP ÷ 8 工时/人天) ≈ 人天 ÷ 0.814。"
                "生产率基准取 CSBMK-202010 电子政务领域 P50 中位 6.51 人时/FP（PDF 第三章 2.1.②）。"
                "人月费率取广西标准 1.7 万元/人月（含直接 + 间接人力成本 + 合理利润）。"
                "直接非人力成本一般情况不进行计列（PDF 第三章 2.1.⑤），本项目按 0 计。"
            ),
        },
        "rationale": "FP 估算方法说明 — 配套 feasibility-fp-table.xlsx 附件",
        "standard_refs": [
            {"section": "三.(一).2.1", "page": 13, "snippet": "功能点估算法计算方式"},
        ],
        "estimated_delta_amount": 0,
        "auto_applicable": True,
    })

    return findings
