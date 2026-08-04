"""shandong_v1 — 山东省省级政务信息化建设项目支出预算限额标准 的计算链。

    调整后功能点 = 功能点合计 × 规模变更因子 × 复用度调整因子 × 应用类型调整因子
    开发工作量   = 调整后功能点 × 软件开发生产率 ÷ 人月折算系数
    人月费率     = 基准人月费率 × 开发类别调整系数
    软件开发费用 = 开发工作量 × 人月费率
                                            —— 标准 p.5-8

与广东的差别（跨区域时不可混用）：
  · 山东有规模变更因子（1.39/1.21），广东**无此因子**
  · 山东人月费率带开发类别系数（0.8-1.1），广东固定 24000 无系数
  · 广东公式含「直接非人力成本」加项，山东未单列

舍入一律走 nesma_weights.xlround（复刻 Excel ROUND：half-up + 15 位有效数字
规整）。财评会拿 Excel 复核，口径不一致即被质疑。
"""
from __future__ import annotations

from typing import Any

from nesma_weights import xlround

PROFILE_ID = "shandong_v1"


def adjusted_fp(ufp: float, *, pack, counting_method: str, reuse_level: str,
                app_type: str, digits: int = 2) -> float:
    """调整后功能点。逐条目计算并按 Excel 口径舍入，再由调用方求和。"""
    size = pack.factor("size_change", counting_method)
    reuse = pack.factor("reuse", reuse_level)
    app = pack.factor("app_type", app_type)
    return xlround(ufp * size * reuse * app, digits)


def effort_man_months(afp_sum: float, *, pack, digits: int = 2) -> float:
    """开发工作量（人月）。"""
    productivity = pack.rate("productivity_hours_per_fp")
    hours_pm = pack.rate("man_hours_per_month")
    return xlround(afp_sum * productivity / hours_pm, digits)


def man_month_rate(*, pack, dev_category: str, digits: int = 2) -> float:
    """人月费率 = 基准人月费率 × 开发类别调整系数。"""
    base = pack.rate("base_man_month_rate")
    coef = pack.factor("dev_category", dev_category)
    return xlround(base * coef, digits)


def software_dev_cost(afp_sum: float, *, pack, dev_category: str,
                      digits: int = 2) -> float:
    """软件开发费用 = 开发工作量 × 人月费率。"""
    effort = effort_man_months(afp_sum, pack=pack)
    rate = man_month_rate(pack=pack, dev_category=dev_category)
    return xlround(effort * rate, digits)


def progressive(base_wan: float, table: list[list[Any]], digits: int = 2) -> float:
    """超额累进计费。

    table = [[上限万元, 费率], ...]，末项上限为 null 表示不封顶。
    标准 p.11 算例：800 万 → 300×2% + (500-300)×1.6% + (800-500)×1.28% = 13.04 万
    """
    fee, low = 0.0, 0.0
    for cap, rate in table:
        hi = base_wan if cap is None else min(base_wan, cap)
        if hi > low:
            fee += (hi - low) * rate
            low = hi
        if cap is not None and base_wan <= cap:
            break
    return xlround(fee, digits)


def other_fee(pack, fee_id: str, base_wan: float,
              dev_category: str | None = None) -> float:
    """其他建设费用。返回单位与 pack 中该费用项的 unit 一致（山东为万元）。"""
    spec = pack.fee(fee_id)

    eligible = spec.get("eligible_dev_category")
    if eligible and dev_category is not None and dev_category not in eligible:
        # 山东 三.(四)2：除大型行业/统一平台类项目，其他类型原则上不单独申报系统集成费
        return 0.0

    method = spec["method"]
    if method == "progressive":
        return progressive(base_wan, spec["table"])
    if method == "rate":
        return xlround(base_wan * spec["max_rate"], 2)
    raise ValueError(f"{PROFILE_ID}: 未知计费方式 {method!r}")
