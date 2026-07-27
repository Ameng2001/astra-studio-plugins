"""scan_brand_models — 设备品牌型号 ≥3 检查 (PDF 表7 注3 硬要求).

PDF 表7 注3 / 表6 注3 明文要求："参考品牌型号一般不少于 3 个；如有相关价格依据的一并提供"。
本扫描器逐 device sheet 检查每行是否包含 ≥3 个品牌或型号说明。

简化逻辑（v0.1）：
  - 检查行单元格中是否含 ≥3 个「品牌名」（用启发式列表）或 ≥2 个换行 + "/"分隔
  - 命中条件松一些：只要含有 "/" 或 "、" 分隔且含 ≥3 段就视为有对比
  - 否则视为缺失，发出 warn

输出层级：每 device sheet 一条建议（避免噪声），列出缺失行数和典型样本。
"""
from __future__ import annotations

import re
from typing import Any


SEPARATORS = re.compile(r"[/、，;；\n]")
KNOWN_BRAND_HINTS = {
    "海康", "大华", "宇视", "华为", "中兴", "锐捷", "H3C", "新华三", "联想", "戴尔", "Dell",
    "曙光", "浪潮", "深信服", "天融信", "启明星辰", "绿盟", "天彩", "亿联",
    "Cisco", "Aruba", "TP-Link", "讯飞", "希沃", "天波", "数联", "深圳数联",
}


def _row_has_brand_alternatives(row: dict) -> bool:
    """Check if a device row contains alternative brand/model references."""
    text = " ".join(str(v) for v in row["cells"].values() if isinstance(v, str))
    if not text:
        return False
    # Heuristic 1: ≥3 segments separated by / 、 ，
    segs = [s.strip() for s in SEPARATORS.split(text) if s.strip()]
    long_segs = [s for s in segs if 2 <= len(s) <= 40]
    if len(long_segs) >= 3:
        return True
    # Heuristic 2: known brand names ≥2
    hits = sum(1 for b in KNOWN_BRAND_HINTS if b in text)
    return hits >= 2


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    sid = 0
    for wb in quote["workbooks"]:
        if wb["kind"] != "device":
            continue
        for sh in wb["sheets"]:
            total_rows = 0
            missing_rows = []
            for row in sh["rows"]:
                # Must have a monetary value
                has_money = any(
                    isinstance(row["cells"].get(c), (int, float)) and row["cells"][c] > 0
                    for c in ("金额", "合计", "总价", "对外总价", "成本总价")
                )
                if not has_money:
                    continue
                total_rows += 1
                if not _row_has_brand_alternatives(row):
                    missing_rows.append(row["row_index"])
            if total_rows == 0:
                continue

            miss_rate = len(missing_rows) / total_rows
            sid += 1
            findings.append({
                "id": f"B{sid:03d}",
                "category": "evidence-missing",
                "severity": "high" if miss_rate >= 0.5 else "medium",
                "target": {"workbook": wb["path"], "sheet": sh["name"], "row": 0},
                "proposed_change": {
                    "operation": "supply-brand-comparison",
                    "missing_rows": missing_rows[:30],   # 截取首 30 个示例
                    "missing_count": len(missing_rows),
                    "total_rows": total_rows,
                    "miss_rate": round(miss_rate, 3),
                    "instructions": (
                        "为下列设备行补充 ≥3 个参考品牌型号对比 + 市场询价依据。"
                        "建议形式：在新增列「品牌/型号对比」填写 '海康XXX / 大华YYY / 宇视ZZZ'，并附市场询价单或电商截图。"
                    ),
                },
                "rationale": f"PDF 表7 注3 要求参考品牌型号 ≥3 个。本 sheet 共 {total_rows} 行，{len(missing_rows)} 行缺失（{miss_rate*100:.0f}%）。",
                "standard_refs": [
                    {"section": "三.(一).2.5", "page": 19, "snippet": "表7 注3：参考品牌型号一般不少于 3 个；如有相关价格依据的一并提供"},
                ],
                "estimated_delta_amount": 0,
                "auto_applicable": False,
            })
    return findings
