"""deploy_breakdown — 部署&交付 sheet 拆解为 PDF 合规科目的统一计算源.

被 scan_deploy_reclassify / scan_completeness / generate_project_summary 共用，
保证三处口径一致，杜绝重复计列。

拆解规则（PDF 第三章）：
  现场实施（业务分析/初始化/配置/数据录入/调试/操作培训）
      → 80% 系统集成费(实施)  + 20% 培训费 (2.6.1 + 2.8)
  平台部署（系统/平台/服务部署调试）
      → 100% 系统集成费 (2.6.1)
  项目管理（进度/质量/风险/资源调度）
      → 100% 系统集成费(项目管理) (2.6.1 含项目组织管理)
  产品/方案/技术现场支持
      → 100% 系统集成费 (2.6.1)
  差旅成本
      → 100% 直接非人力成本-差旅费 (2.1.⑤，需附测算依据)
"""
from __future__ import annotations

from typing import Any

TRAIN_RATIO_IN_IMPL = 0.20   # 现场实施中操作培训占比（经验值，可调）


def _row_amount(cells: dict) -> float:
    for k in ("小计（元）", "小计", "金额", "合计", "成本总价", "对外总价"):
        v = cells.get(k)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    # fallback: 单价 × 数量
    price = None
    qty = None
    for pk in ("单价", "成本单价"):
        if isinstance(cells.get(pk), (int, float)):
            price = cells[pk]
            break
    for qk in ("数量",):
        if isinstance(cells.get(qk), (int, float)):
            qty = cells[qk]
            break
    if price and qty:
        return float(price) * float(qty)
    return 0.0


def compute(quote: dict[str, Any]) -> dict[str, Any]:
    """Return {系统集成: x, 培训: y, 差旅: z, total: t, source_sheets: [...], lines: [...]}"""
    result = {
        "系统集成费": 0.0,
        "培训费": 0.0,
        "直接非人力成本-差旅费": 0.0,
        "total": 0.0,
        "source_sheets": [],
        "lines": [],   # 明细行供报告使用
    }
    for wb in quote["workbooks"]:
        if wb["kind"] not in {"platform", "llm"}:
            continue
        for sh in wb["sheets"]:
            if sh["kind"] != "deploy":
                continue
            result["source_sheets"].append(sh["name"])
            for row in sh["rows"]:
                cells = row["cells"]
                # 跳过 合计/总计/小计 行（避免重复计入）
                joined = " ".join(str(v) for v in cells.values() if isinstance(v, str))
                if any(kw in joined for kw in ("合计", "总计", "小计")) and \
                   sum(1 for v in cells.values() if isinstance(v, str)) <= 2:
                    continue
                amount = _row_amount(cells)
                if amount <= 0:
                    continue
                cat_label = (cells.get("成本大类") or cells.get("现场实施")
                             or cells.get("成本内容描述") or "")
                desc = (cells.get("成本内容描述") or cells.get("建设内容") or "")
                txt = f"{cat_label} {desc}"

                if "差旅" in txt or "差旅" in str(cat_label):
                    result["直接非人力成本-差旅费"] += amount
                    target = "直接非人力成本-差旅费"
                    split = {"差旅": amount}
                elif "现场实施" in txt or "实施" in str(cat_label):
                    train = round(amount * TRAIN_RATIO_IN_IMPL)
                    integ = amount - train
                    result["系统集成费"] += integ
                    result["培训费"] += train
                    target = "系统集成费(实施) + 培训费"
                    split = {"系统集成费": integ, "培训费": train}
                else:
                    # 平台部署 / 项目管理 / 技术支持 → 系统集成费
                    result["系统集成费"] += amount
                    target = "系统集成费"
                    split = {"系统集成费": amount}

                result["total"] += amount
                result["lines"].append({
                    "sheet": sh["name"],
                    "row": row["row_index"],
                    "cost_category": str(cat_label),
                    "desc": str(desc)[:60],
                    "amount": round(amount),
                    "reclassify_to": target,
                    "split": {k: round(v) for k, v in split.items()},
                })
    for k in ("系统集成费", "培训费", "直接非人力成本-差旅费", "total"):
        result[k] = round(result[k])
    return result


if __name__ == "__main__":  # pragma: no cover
    import json, sys
    q = json.load(open(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/保守/quote.json"))
    import pprint
    pprint.pprint(compute(q))
