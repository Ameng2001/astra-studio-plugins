"""scan_semantic — platform-vs-llm semantic overlap scanner.

Finds rows in platform workbooks whose 详情 text overlaps significantly with
llm rows, and vice versa. Emits semantic-split suggestions: rewrite the
overlapping text with the *opposite* vocabulary (platform → 功能特性 / llm →
模型能力).
"""
from __future__ import annotations

import re
from typing import Any


FORBIDDEN_OVERLAP_KEYWORDS = {
    "使用人工智能", "AI识别", "AI能力", "智能推荐", "智能识别",
    "智能分析", "知识图谱", "大模型", "智能体",
}

PLATFORM_VOCAB = {"功能", "特性", "模块", "配置", "页面", "流程", "表单", "报表", "看板"}
LLM_VOCAB = {"能力", "推理", "生成", "知识", "智能体", "检索", "微调", "token", "RAG"}


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    return text.strip()


def _overlap_vocab(text: str, vocab: set[str]) -> int:
    return sum(1 for w in vocab if w in text)


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    sid = 0
    for wb in quote["workbooks"]:
        if wb["kind"] not in {"platform", "llm"}:
            continue
        target_vocab = LLM_VOCAB if wb["kind"] == "llm" else PLATFORM_VOCAB
        wrong_vocab = PLATFORM_VOCAB if wb["kind"] == "llm" else LLM_VOCAB
        for sh in wb["sheets"]:
            if sh["kind"] not in {"items"}:
                continue
            for row in sh["rows"]:
                detail = (
                    row["cells"].get("建设详情")
                    or row["cells"].get("详情")
                    or row["cells"].get("建设详情（定位）")
                    or ""
                )
                detail = _normalize(str(detail))
                if len(detail) < 20:
                    continue

                # Triggers:
                # (a) forbidden-overlap keyword in a platform row
                # (b) using wrong vocab heavily
                forbidden_hits = [k for k in FORBIDDEN_OVERLAP_KEYWORDS if k in detail]
                wrong_hits = _overlap_vocab(detail, wrong_vocab)
                target_hits = _overlap_vocab(detail, target_vocab)

                trigger = False
                reason_parts = []
                if wb["kind"] == "platform" and forbidden_hits:
                    trigger = True
                    reason_parts.append(f"含跨域关键词 {forbidden_hits}")
                if wrong_hits >= 3 and target_hits == 0:
                    trigger = True
                    reason_parts.append(
                        f"使用了 {wrong_hits} 个对侧词表词；本{wb['kind']}应使用 {sorted(target_vocab)} 词表"
                    )

                if not trigger:
                    continue

                sid += 1
                # Compose advisory text — keep length similar
                advisory = detail
                if wb["kind"] == "platform":
                    for k in forbidden_hits:
                        advisory = advisory.replace(k, "(功能配置项)")
                else:
                    # llm side: replace 功能模块 with 模型能力 wording
                    advisory = advisory.replace("功能", "能力").replace("模块", "推理子能力")

                suggestions.append({
                    "id": f"S{sid:03d}",
                    "category": "semantic-split",
                    "severity": "low",
                    "target": {
                        "workbook": wb["path"],
                        "sheet": sh["name"],
                        "row": row["row_index"],
                        "col_header": "建设详情" if "建设详情" in row["cells"] else "详情",
                    },
                    "proposed_change": {
                        "new_value": advisory[:500],
                        "operation": "replace_text",
                    },
                    "rationale": f"语义切分 ({wb['kind']} 应用 {wb['kind']} 专属词表)：" + "；".join(reason_parts),
                    "standard_refs": [{"section": "正文一", "page": 1, "snippet": "厉行节约 / 避免重复计列"}],
                    "estimated_delta_amount": 0,   # 不动数字
                    "auto_applicable": True,
                })
    return suggestions
