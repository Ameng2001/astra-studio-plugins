"""derive_fp —— 从既有人天反推功能点。

    FP = 人天 ÷ (6.51 / 8) = 人天 ÷ 0.81375

⚠️ **本模块做的是反推，与本插件的第一性原理冲突，不得用于定价。**

设计方案 §0.3「定价方法倒挂」点名批评的正是这个链路：

  raw-input 主清单：先有人天档位 → 得报价（`报价参照说明` sheet 明写
      「人天档位均来自源清单末级功能报价」）
  功能点测算表：事后补的 FP，与主清单报价互不校验
      （行业专业模型 FP 法 ¥207K vs 报价 ¥1,470,000，差 7.1 倍）
  本模块：FP = 人天 ÷ 0.81375，同样是反推

**财评专家会直接问「你的功能点是怎么数出来的」。反推链路无法回答。**

正向链路是：BOM 逐条按 NESMA 识别功能点类型（`bom_build` + `nesma_rules`，
每条带 rationale 与双人复核）→ `baseline_build` 按区域标准算基准 →
`quote_generate` 施加交付方案。那条链路上任何一个数都能追到条目与条款。

## 那这个模块留着干什么

**只用于与旧报价交叉校验** —— 拿旧清单的人天反推出一个 FP 量级，
与正向数出来的 FP 比对，量级差得离谱时提示「这一块可能拆漏了或拆重了」。
它是**体检工具**，不是计价工具。

对应地，BOM 条目上的 `legacy_quote` 字段也标着「仅供交叉校验，不作定价依据」，
`quote_selfreview` 的 C2-01 检查就是拿它来回答「你们是先有价再凑功能点吗」。

调用方（`run_optimize` / `run_review` / `scan_labor`）目前都在
`quote-optimize` / `quote-review` 那条老链路上，且**技能并不调它们**
（技能用散文描述扫描逻辑）。那条链路是接回去还是归档，待产品决策 ——
但无论怎么决定，本模块的产物都不能进报价。
"""
from __future__ import annotations

from formula_engine import (
    PRODUCTIVITY_HOURS_PER_FP,
    CATEGORY_FACTOR,
    REUSE_FACTOR,
    MAN_MONTH_RATE,
    WORKDAYS_PER_MONTH,
)

PERSON_DAYS_PER_FP = PRODUCTIVITY_HOURS_PER_FP / 8.0   # ≈0.81375


def derive_fp_from_person_days(person_days: float) -> int:
    if person_days is None or person_days <= 0:
        return 0
    return max(1, round(person_days / PERSON_DAYS_PER_FP))


def classify_fp_row(detail_text: str, sheet_name: str, workbook_kind: str) -> tuple[str, str]:
    """Return (category, reuse) using G2's default (人工智能 / 低) but with refinements."""
    text = (detail_text or "") + " " + (sheet_name or "")

    # G2 default: 人工智能 / 低 (新建项目)
    category = "人工智能"
    reuse = "低"

    # Stance: 本项目是数智民生体系，平台 sheet 默认归「大数据多媒体」(PDF p.14 表3)
    # 依据：项目本身含大屏可视化 + 数据分析 + AI 推理，按主体功能类型取值原则。
    # 凡明显含 AI/智能体描述的，再上抬到「人工智能」类。
    if workbook_kind == "platform":
        if any(kw in text for kw in ["AI", "智能识别", "智能推荐", "智能分析", "大模型", "智能体", "知识图谱"]):
            category = "人工智能"
        elif any(kw in text for kw in ["接口", "集成", "对接", "总线", "API同步"]):
            category = "应用集成"
        else:
            # G2 立场：数智民生平台默认归大数据多媒体类（合规带上限 ¥1016 vs 业务处理 ¥782）
            category = "大数据多媒体"

    # llm side: keep 人工智能
    # device side: 不走 FP 法

    # G2 立场：新建项目复用度统一取「低」(=1.0)。PDF p.14 明确规定：
    # "新建项目的复用度调整系数默认取值为 1（复用度低），根据实际情况进行调整"
    # L2 智能体/专业模型虽基于 L1 底座，但场景层面是新建，整体仍属新建项目范畴。
    # 取「中」会主动砍掉一半合规带，自伤行为，违背取上限立场。
    return category, reuse


def fp_to_cost_range(fp: int, category: str, reuse: str) -> tuple[float, float]:
    """Returns (min_cost, max_cost) for the FP at given category/reuse (低 reuse default)."""
    cf_min, cf_max = CATEGORY_FACTOR[category]
    rf = REUSE_FACTOR[reuse]
    base = fp * (PRODUCTIVITY_HOURS_PER_FP / 174) * MAN_MONTH_RATE * rf
    return round(base * cf_min, 2), round(base * cf_max, 2)
