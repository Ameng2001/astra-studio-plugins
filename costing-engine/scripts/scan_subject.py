"""scan_subject — subject-mapping scanner.

For each row, decide the standard section it maps to (using simple keyword
rules consistent with the YAML mapping pack), and emit a suggestion to add
the 标准科目 column with that section id.
"""
from __future__ import annotations

from typing import Any


def _map_section(detail: str, sheet_name: str, workbook_kind: str) -> tuple[str, int]:
    text = (detail or "") + " " + (sheet_name or "")

    if workbook_kind == "device":
        if any(k in text for k in ["LED", "显示屏", "拼接屏", "大屏"]):
            return ("三.(一).2.5.(4)", 20)
        if any(k in text for k in ["交换机", "路由", "AP", "VPN", "无线"]):
            return ("三.(一).2.5", 18)
        if any(k in text for k in ["服务器", "工作站"]):
            return ("三.(一).2.5", 19)
        if any(k in text for k in ["存储", "磁盘阵列", "备份"]):
            return ("三.(一).2.5", 19)
        if any(k in text for k in ["防火墙", "入侵检测", "加密", "安全审计"]):
            return ("三.(一).2.5", 19)
        return ("三.(一).2.5", 18)   # 智能化设备兜底

    # platform / llm → 定制软件开发
    if "运维" in sheet_name or "迭代" in sheet_name or any(k in text for k in ["token", "API", "调用"]):
        return ("三.(三).2", 27)
    if "实施" in sheet_name or "部署" in sheet_name or "培训" in sheet_name:
        return ("三.(一).2.6.1", 20)
    return ("三.(一).2.1", 13)


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    sid = 0
    for wb in quote["workbooks"]:
        for sh in wb["sheets"]:
            if sh["kind"] == "summary":
                continue
            # Skip if 标准科目 column already exists
            existing_headers = {c["header"] for c in sh["columns"] if c["header"]}
            if "标准科目" in existing_headers:
                continue
            for row in sh["rows"]:
                detail = (
                    row["cells"].get("建设详情")
                    or row["cells"].get("详情")
                    or row["cells"].get("建设内容")
                    or row["cells"].get("分项名称")
                    or row["cells"].get("名称")
                    # device sheet columns
                    or row["cells"].get("设备名称")
                    or row["cells"].get("产品名称")
                    or row["cells"].get("场景名称")
                    or ""
                )
                if not detail:
                    continue
                section_id, page = _map_section(str(detail), sh["name"], wb["kind"])
                sid += 1
                suggestions.append({
                    "id": f"M{sid:03d}",
                    "category": "subject-mapping",
                    "severity": "medium",
                    "target": {
                        "workbook": wb["path"],
                        "sheet": sh["name"],
                        "row": row["row_index"],
                    },
                    "proposed_change": {
                        "new_value": section_id,
                        "operation": "ensure_column_and_set",
                        "column_header": "标准科目",
                    },
                    "rationale": f"根据 G2(完全定制化) + mapping pack 默认规则，本行归属标准 {section_id}",
                    "standard_refs": [{"section": section_id, "page": page}],
                    "estimated_delta_amount": 0,
                    "auto_applicable": True,
                })
    return suggestions
