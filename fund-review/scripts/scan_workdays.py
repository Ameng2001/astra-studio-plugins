"""scan_workdays — 人天极值检测.

财评常见挑战："单个建设项的工作量 > 200 人天，请拆分为子项并分别估算 FP"。
本扫描器找出 outlier 行，提示投标方拆分以便财评核查。

阈值（可后续按项目调）：
  - 人天 > 200 → high（必拆）
  - 100 < 人天 ≤ 200 → medium（建议拆）
  - < 100 → 不报

不动金额，只 emit 建议（属于「财评红队」立场——预演挑战点）。
"""
from __future__ import annotations

from typing import Any


WORKDAYS_HIGH = 200
WORKDAYS_MEDIUM = 100


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    sid = 0
    for wb in quote["workbooks"]:
        if wb["kind"] not in {"platform", "llm"}:
            continue
        for sh in wb["sheets"]:
            if sh["kind"] not in {"items", "deploy"}:
                continue
            for row in sh["rows"]:
                pd = row["cells"].get("人/天") or row["cells"].get("人天")
                if not isinstance(pd, (int, float)) or pd <= WORKDAYS_MEDIUM:
                    continue

                severity = "high" if pd > WORKDAYS_HIGH else "medium"
                detail = (
                    row["cells"].get("建设详情")
                    or row["cells"].get("详情")
                    or row["cells"].get("建设详情（定位）")
                    or row["cells"].get("建设内容")
                    or ""
                )
                detail_short = str(detail)[:60]
                fp_est = round(pd / 0.81375)

                # 自动拆分提议：按 80 人天/子项 拆为 N 份；从 detail 抽取"1、" "2、"
                # 等枚举段作为子项命名
                target_subitem_workdays = 60   # 推荐每子项 ≤60 人天
                n_splits = max(2, round(pd / target_subitem_workdays))
                per_subitem_wd = round(pd / n_splits, 1)
                per_subitem_fp = round(per_subitem_wd / 0.81375)

                # 抽 detail 中的枚举项 "1、xxx 2、yyy" 当 sub-item 提议名
                import re
                enum_items = re.findall(r"[（(]?[一二三四五六七八九十1234567890]+[）)、.][^\n。]+",
                                         str(detail))
                enum_items = [s.strip("（）()、. ")[:30] for s in enum_items[:n_splits]]
                if len(enum_items) < n_splits:
                    # 无足够"1、2、"数字枚举 → 退而用首句的 顿号/斜杠 分段作子项名
                    head = re.split(r"[。\n]", str(detail))[0]
                    head = re.sub(r"^\s*(涵盖|包括|包含|含|覆盖)\s*", "", head)
                    segs = [s.strip("（）()、.,/ ")[:30] for s in re.split(r"[、/，;；]", head)]
                    for s in segs:
                        if len(enum_items) >= n_splits:
                            break
                        if len(s) >= 2 and s not in enum_items:
                            enum_items.append(s)
                if len(enum_items) < n_splits:
                    enum_items += [f"子项{i+1}" for i in range(len(enum_items), n_splits)]

                sid += 1
                findings.append({
                    "id": f"W{sid:03d}",
                    "category": "workdays-outlier",
                    "severity": severity,
                    "stance_origin": "财评红队",
                    "target": {
                        "workbook": wb["path"],
                        "sheet": sh["name"],
                        "row": row["row_index"],
                    },
                    "proposed_change": {
                        "operation": "split-into-subitems",
                        "current_workdays": pd,
                        "implied_fp": fp_est,
                        "n_splits": n_splits,
                        "per_subitem_workdays": per_subitem_wd,
                        "per_subitem_fp": per_subitem_fp,
                        "suggested_subitems": [
                            {"name": name, "workdays": per_subitem_wd, "fp": per_subitem_fp}
                            for name in enum_items
                        ],
                        "advice": (
                            f"单行 {pd} 人天（≈ {fp_est} FP）属高值。"
                            f"建议拆为 {n_splits} 个子项，每子项 ≈{per_subitem_wd} 人天（≈{per_subitem_fp} FP）。"
                            f"提议子项命名见 suggested_subitems（基于建设详情自动抽取枚举段）。"
                        ),
                        "detail_excerpt": detail_short,
                    },
                    "rationale": (
                        f"工作量 {pd} 人天超 {WORKDAYS_HIGH if severity=='high' else WORKDAYS_MEDIUM} 阈值。"
                        f"PDF 第三章 2.1 要求按功能点分项核算；单行过大不利财评核查。"
                    ),
                    "standard_refs": [
                        {"section": "三.(一).2.1", "page": 13,
                         "snippet": "功能点估算法计算方式 — 按 ILF/EIF/EI/EO/EQ 分项计数"},
                    ],
                    "estimated_delta_amount": 0,
                    "auto_applicable": False,
                })
    return findings
