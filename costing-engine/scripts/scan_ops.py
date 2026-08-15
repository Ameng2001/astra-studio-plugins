"""scan_ops — 大模型运维/运营行合规扫描.

针对 LLM ops 表（附6-9：知识工程运维 / 数据集运维 / 专业模型迭代训练运维 /
智能体迭代运维），按 PDF 第三章 (三) 软件运维标准核查：

  - 运维基线：8.39 - 12.59 万元/人年 (PDF p.31)
  - token / 推理费按"直接非人力成本"列示（PDF 第三章 2.1.⑤）
  - 不能与人力运维重复计列

立场归因：合规守护 / 财评红队混合（取决于触发条件）。
"""
from __future__ import annotations

from typing import Any

from formula_engine import SOFTWARE_OPS_YUAN_PER_MAN_YEAR


# 运营成本字段（不同 sheet 命名）
COST_HEADERS = [
    "年预估成本费（元）",   # 附8/附9 token 年成本
    "运维成本",
    "(4)运维成本",
    "运维报价",
    "运营费总价",
    "运营费用报价（元）",
    "对外总价（元）",
    "对外运营报价（元）",
]


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    sid = 0
    for wb in quote["workbooks"]:
        if wb["kind"] != "llm":
            continue
        for sh in wb["sheets"]:
            if sh["kind"] != "ops":
                continue
            for row in sh["rows"]:
                cells = row["cells"]
                # token-based ops
                token_cost = cells.get("年预估成本费（元）")
                token_price = cells.get("token价格（元/万token）")
                annual_token = cells.get("年调用token量（w）")
                maint_cost = cells.get("(4)运维成本") or cells.get("运维成本") or cells.get("运维报价")
                external_total = cells.get("对外总价（元）") or cells.get("对外运营报价（元）")

                detail = (cells.get("智能体") or cells.get("模型") or cells.get("场景名称")
                          or cells.get("建设内容") or cells.get("建设详情") or "")
                row_idx = row["row_index"]

                # rule 1: token 年成本是否独立于人力运维列示
                if isinstance(token_cost, (int, float)) and token_cost > 0:
                    sid += 1
                    findings.append({
                        "id": f"O{sid:03d}",
                        "category": "ops-attribution",
                        "severity": "low",   # 信息性
                        "stance_origin": "合规守护",
                        "target": {"workbook": wb["path"], "sheet": sh["name"], "row": row_idx},
                        "proposed_change": {
                            "operation": "attribute-ops-cost",
                            "token_cost_yuan": token_cost,
                            "token_price_per_10k": token_price,
                            "annual_token_w": annual_token,
                            "human_maint_cost": maint_cost,
                            "advice": (
                                f"年 token 成本 ¥{token_cost:,.0f} 应归入「直接非人力成本」(PDF 2.1.⑤)，"
                                f"与人力运维（PDF 2.5 软件运维 8.39-12.59 万/人年）分项列示，"
                                f"不可重复计列。已在「其他费用与预备费」补全表中独立处理。"
                            ),
                        },
                        "rationale": (
                            f"大模型运营成本含 token 算力 ¥{token_cost:,.0f}。"
                            f"按 PDF 软件运维标准（p.31）应分为 人力运维 + 直接非人力成本（算力）两部分。"
                        ),
                        "standard_refs": [
                            {"section": "三.(三).2.5", "page": 31, "snippet": "软件运维 8.39-12.59 万元/人年"},
                            {"section": "三.(一).2.1.⑤", "page": 14, "snippet": "直接非人力成本（含算力）"},
                            {"section": "三.(一).2.7", "page": 24, "snippet": "重复内容只计一次"},
                        ],
                        "estimated_delta_amount": 0,
                        "auto_applicable": False,
                    })

                # rule 2: 对外运营报价 vs token + 人力运维 合理性
                if isinstance(external_total, (int, float)) and external_total > 1_000_000:
                    sid += 1
                    findings.append({
                        "id": f"O{sid:03d}",
                        "category": "ops-attribution",
                        "severity": "medium",
                        "stance_origin": "财评红队",
                        "target": {"workbook": wb["path"], "sheet": sh["name"], "row": row_idx},
                        "proposed_change": {
                            "operation": "scrutinize-large-ops",
                            "external_total": external_total,
                            "annual_token_w": annual_token,
                            "advice": (
                                f"对外运营报价 ¥{external_total:,.0f} 偏大。"
                                f"需在投标书附「年调用量预估依据」（用户数 × 活跃系数 × 单日调用次数 × token/次）"
                                f"+ 「token 单价依据」（参考厂商公开价或自建算力折算）。"
                            ),
                        },
                        "rationale": (
                            f"单行运营报价 ¥{external_total:,.0f} > ¥100 万。"
                            f"财评常对此质疑「token 量预估依据」与「单价合理性」。"
                        ),
                        "standard_refs": [
                            {"section": "三.(三).2.4.2", "page": 30,
                             "snippet": "软件运维预算支出公式 = (软件规模 × 运维功能点单价) × 调整因子 + 直接非人力"},
                        ],
                        "estimated_delta_amount": 0,
                        "auto_applicable": False,
                    })

    return findings
