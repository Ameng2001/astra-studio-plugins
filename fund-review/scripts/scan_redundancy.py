"""scan_redundancy — 跨工作簿/sheet 重复内容检测.

PDF 第三章 (一) 2.7：「软件测评(测试)、风险评估、信息安全等级保护测评、密码测评
等工作中存在重复工作内容的，在预算支出中重复部分只计算一次费用。」

引申：财评对功能重复也按此原则挑战 — 同一功能在 平台软件 + 行业大模型 两张报价表
里都出现，会被认定为重复计列。

本扫描器用字符 n-gram Jaccard 相似度找跨工作簿的重复对，按相似度排序，emit
建议如下：
  - sim >= 0.25 → severity=high，建议「分摊计列」(60/40 or 50/50)
  - 0.15 <= sim < 0.25 → severity=medium，建议「人工复核」
  - 否则不报

注：中文 2-gram Jaccard 阈值偏低 (>=0.15) 仍能命中真实重复；4-gram 太严

注意：semantic-split 已经处理了「描述里混用对侧词表」的情形，本扫描器找的是
「功能本身重复」（同一对象 + 同一动作）。
"""
from __future__ import annotations

from typing import Any


# ---- 文本归一化 + n-gram ----
import re

STOP = set("的了是与和及或为以也在到从对一为时各等")


def _normalize(text: str) -> str:
    if not text:
        return ""
    # 去标点、空白、停用词
    text = re.sub(r"[\s\d\W_]+", "", text)
    return "".join(c for c in text if c not in STOP)


def _ngrams(text: str, n: int = 2) -> set[str]:
    text = _normalize(text)
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _detail(row_cells: dict) -> str:
    return " ".join(str(row_cells.get(k, "")) for k in [
        "建设详情", "详情", "建设详情（定位）", "建设内容", "模块", "子模块", "智能体", "模型"
    ] if row_cells.get(k))


def _money(row_cells: dict) -> float:
    for k in ["成本总价", "对外总价", "成本总价（元）", "对外总价（元）", "研发报价"]:
        v = row_cells.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return 0.0


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    sid = 0

    # Build index: list of (wb_path, sheet_name, row_idx, ngrams, detail_excerpt, money)
    platform_rows = []
    llm_rows = []
    for wb in quote["workbooks"]:
        for sh in wb["sheets"]:
            if sh["kind"] not in {"items"}:
                continue
            for row in sh["rows"]:
                detail = _detail(row["cells"])
                if len(detail) < 30:
                    continue
                grams = _ngrams(detail)
                if len(grams) < 5:
                    continue
                money = _money(row["cells"])
                entry = (wb["path"], sh["name"], row["row_index"], grams, detail[:80], money)
                if wb["kind"] == "platform":
                    platform_rows.append(entry)
                elif wb["kind"] == "llm":
                    llm_rows.append(entry)

    # Cross compare
    pairs = []
    for p in platform_rows:
        for l in llm_rows:
            sim = _jaccard(p[3], l[3])
            if sim >= 0.15:
                pairs.append((sim, p, l))

    pairs.sort(key=lambda x: -x[0])

    # Cap at top 30 pairs (avoid noise)
    for sim, p, l in pairs[:30]:
        if sim >= 0.25:
            severity = "high"
            advice = (
                f"建议分摊计列：平台保留 60%（¥{p[5]*0.6:,.0f}），大模型保留 40%（¥{l[5]*0.4:,.0f}）；"
                f"或择一保留并在另一表标注「重复部分已计入对方」。"
            )
        else:
            severity = "medium"
            advice = (
                "建议人工复核：相似度中等，可能是相关但不同功能。"
                "若确属重复，按 PDF 2.7 只计一次；否则在「建设详情」明确区分。"
            )

        sid += 1
        findings.append({
            "id": f"R{sid:03d}",
            "category": "redundancy-warning",
            "severity": severity,
            "stance_origin": "财评红队",
            "target": {
                "workbook": "@meta",
                "sheet": "@cross-workbook",
                "row": 0,
                "primary_loc": {"workbook": p[0], "sheet": p[1], "row": p[2]},
                "secondary_loc": {"workbook": l[0], "sheet": l[1], "row": l[2]},
            },
            "proposed_change": {
                "operation": "split-or-merge",
                "similarity": round(sim, 3),
                "primary_excerpt": p[4],
                "secondary_excerpt": l[4],
                "primary_money": p[5],
                "secondary_money": l[5],
                "advice": advice,
            },
            "rationale": (
                f"跨工作簿内容相似度 {sim:.2f}。"
                f"平台 sheet「{p[1]}」行 {p[2]} 与大模型 sheet「{l[1]}」行 {l[2]} 描述高度重叠。"
                f"按 PDF 2.7 重复内容只计一次原则，存在被财评挑战风险。"
            ),
            "standard_refs": [
                {"section": "三.(一).2.7", "page": 24,
                 "snippet": "存在重复工作内容的，在预算支出中重复部分只计算一次费用"},
            ],
            "estimated_delta_amount": 0,
            "auto_applicable": False,
        })

    return findings
