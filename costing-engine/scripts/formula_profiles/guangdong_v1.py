"""guangdong_v1 — 广东省省级政务信息化服务预算编制标准 软件开发服务分册 的计算链。

    功能点数 = 未调整功能点数量(UFP) × 软件类别调整因子 × 复用系数
    定制软件开发服务费用 = 功能点数 × 软件开发生产率基准 ÷ 人月折算系数
                          × 软件开发基准人月费率 + 直接非人力成本
                                            —— 标准 p.10-13

**与山东的三处链路差异**，这是不能合并成一个「万能公式」的原因：

  1. 广东**无规模变更因子**。山东 ×1.21，广东没有这一环 ——
     用「系数置 1」糊弄虽然数值上等价，但会让 pack 看起来像有这个概念，
     后续维护者可能误填。profile 直接不调用它，pack 里标 absent_in_standard。
  2. 复用系数作用在 **UFP 层**（与类别因子同级相乘得到「功能点数」），
     山东作用在调整后功能点层。本例中乘法可交换故数值同，
     但取整点不同，且概念层次不同。
  3. 人月费率**固定 24000，无开发类别系数**。山东是 基准 × 类别系数(0.8-1.1)。

另有山东没有的一项：**直接非人力成本**是公式内的显式加项
（办公费、差旅费、培训费、采购费、设备折旧费），一般不计列，
计列时须说明原因及测算依据。
"""
from __future__ import annotations

from typing import Any

from nesma_weights import xlround

PROFILE_ID = "guangdong_v1"


def adjusted_fp(ufp: float, *, pack, counting_method: str, reuse_level: str,
                app_type: str, digits: int = 2) -> float:
    """功能点数 = UFP × 软件类别调整因子 × 复用系数。

    注意：**不乘规模变更因子** —— 本标准公式中无此环节。
    """
    category = pack.factor("app_type", app_type)
    reuse = pack.factor("reuse", reuse_level)
    return xlround(ufp * category * reuse, digits)


def effort_man_months(fp_sum: float, *, pack, digits: int = 2) -> float:
    productivity = pack.rate("productivity_hours_per_fp")
    hours_pm = pack.rate("man_hours_per_month")
    return xlround(fp_sum * productivity / hours_pm, digits)


def man_month_rate(*, pack, dev_category: str | None = None,
                   digits: int = 2) -> float:
    """人月费率固定，**不按开发类别调整**。

    dev_category 参数保留只为与其他 profile 保持调用签名一致；
    本标准中它不影响取值。
    """
    return xlround(pack.rate("base_man_month_rate"), digits)


def software_dev_cost(fp_sum: float, *, pack, dev_category: str | None = None,
                      direct_non_labor: float = 0.0, digits: int = 2) -> float:
    """定制软件开发服务费用 = 工作量 × 人月费率 + 直接非人力成本。

    直接非人力成本一般不计列；计列时须说明原因及测算依据（标准 p.13）。
    """
    effort = effort_man_months(fp_sum, pack=pack)
    rate = man_month_rate(pack=pack)
    return xlround(effort * rate + direct_non_labor, digits)


def upgrade_cost(actual_dev_cost: float, change_rate: float | None = None,
                 *, pack, digits: int = 2) -> float:
    """定制软件升级服务费 = 定制软件实际开发服务费 × 升级功能变化率。

    变化率上限 30%，预算阶段无法确定时用推荐值 15%（标准 p.14）。
    """
    spec = pack.data.get("guangdong_specific", {}).get("定制软件升级服务费", {})
    rate = change_rate if change_rate is not None else spec.get("change_rate_default", 0.15)
    cap = spec.get("change_rate_max", 0.30)
    if rate > cap:
        raise ValueError(f"{PROFILE_ID}: 升级功能变化率 {rate} 超过上限 {cap}")
    return xlround(actual_dev_cost * rate, digits)


def rental_annual(one_time_dev_fee: float, annual_ops_fee: float = 0.0,
                  *, pack, years: int | None = None, w: float = 0.0,
                  digits: int = 2) -> float:
    """定制软件租赁服务费（按年）= 定制软件租赁综合费 ÷ 分摊年数 × (1+W)。

    综合费 = 软件一次性开发投入费 + Σ(软件系统运行维护服务费)。
    预算编报中 W 取 0；结算时按服务指标完成情况调整（标准 p.15-16）。

    **山东完全没有这个科目** —— 同一份 BOM 在广东可以按租赁报，在山东不行。
    """
    spec = pack.data.get("guangdong_specific", {}).get("定制软件租赁服务费", {})
    n = years or spec.get("amortization_years", 3)
    comprehensive = one_time_dev_fee + annual_ops_fee * n
    return xlround(comprehensive / n * (1 + w), digits)


def progressive(base_wan: float, table: list[list[Any]], digits: int = 2) -> float:
    """本分册无超额累进类费用项；保留接口以兼容 run_regression。"""
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
    raise ValueError(
        f"{PROFILE_ID}: 本分册只覆盖软件开发服务，无「{fee_id}」科目。"
        f"设计费/系统集成费/第三方测试费/硬件购置见其他分册")
