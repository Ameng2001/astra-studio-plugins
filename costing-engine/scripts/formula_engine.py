"""formula_engine — 柳州预算标准的确定性计算引擎.

All numeric findings used by quote-optimize and quote-review come from here.
LLM is responsible for prose only; this module is responsible for numbers.

**舍入口径：全模块走 `nesma_weights.xlround`，即 Excel 的 ROUND。**
与 Python 内置 `round()` 有两处不同，都是实打实的差别：

  1. 方向：Excel 四舍五入（half-up），Python 银行家舍入（half-even）
  2. 精度：Excel 先把二进制结果规整到 15 位有效数字再舍入
     （`8.87 × 26009.5` 的 IEEE754 值是 `230704.26499999998`，
      规整后成 `230704.265` → Excel 得 `.27`，直接 half-up 只能得 `.26`）

财评评审会拿 Excel 复核，口径不一致即被质疑。实测依据见
`clife-elderly-care/p0-baseline/README.md`：某 7133-FP 工作簿上差 ¥1,418.75。

⚠️ 本模块的常量仍硬编码柳州取值。跨区域使用请走 `standard_pack` +
`formula_profiles/`，那条路径的参数一律从标准包取且带 citation。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple

from nesma_weights import xlround


# ---- Defaults from 柳财审〔2020〕16号 ------------------------------------------
MAN_MONTH_RATE = 17000              # 元/人月 (p.14, 广西取值)
WORKDAYS_PER_MONTH = 21.75
MAN_HOURS_PER_MONTH = 174           # 174 = 21.75 × 8
PRODUCTIVITY_HOURS_PER_FP = 6.51    # 电子政务领域 P50 (CSBMK-202010)
PRODUCTIVITY_FLOOR_RATIO = 0.8      # ±20% 浮动
PRODUCTIVITY_CEILING_RATIO = 1.2

CATEGORY_FACTOR = {
    "业务处理": (0.8, 1.0),
    "应用集成": (1.0, 1.2),
    "大数据多媒体": (1.0, 1.3),
    "人工智能": (1.0, 1.5),
}
REUSE_FACTOR = {"高": 1 / 3, "中": 2 / 3, "低": 1.0}

# 系统集成临时人员 (表10, p.21)
INTEG_EXPERT_PER_DAY = 2000
INTEG_ENGINEER_PER_DAY = 1000

# 软件运维 (2.5, p.31)
SOFTWARE_OPS_YUAN_PER_MAN_YEAR = (83_900, 125_900)

# 等保 (2.6.5, p.24)
DENGBAO_L2_PRICE = 80_000
DENGBAO_L3_BASE = 100_000
DENGBAO_L3_FACTOR = (1.0, 1.3)

# 硬件运维 (1.3, p.26)
HW_OPS_RATE_NORMAL = 0.05
HW_OPS_RATE_DISPERSED = 0.08

Tri = Literal["ok", "warn", "fail"]


# ---- 软件开发 公式 -----------------------------------------------------------
@dataclass(frozen=True)
class SoftwareDevCostRange:
    min_cost: float
    max_cost: float
    formula_used: str


def compute_software_dev_cost(
    fp: float,
    category: str,
    reuse: str,
    direct_non_labor: float = 0.0,
    productivity_ratio: float = 1.0,
) -> SoftwareDevCostRange:
    """定制软件开发费 = FP × 生产率/人月折算 × 人月费率 × 类别因子 × 复用因子 + 直接非人力.

    Returns a (min, max) range because category factor itself is a range.
    """
    if category not in CATEGORY_FACTOR:
        raise ValueError(f"unknown category: {category} (expected one of {list(CATEGORY_FACTOR)})")
    if reuse not in REUSE_FACTOR:
        raise ValueError(f"unknown reuse level: {reuse} (expected one of {list(REUSE_FACTOR)})")
    if not PRODUCTIVITY_FLOOR_RATIO <= productivity_ratio <= PRODUCTIVITY_CEILING_RATIO:
        raise ValueError(
            f"productivity_ratio {productivity_ratio} outside ±20% band"
        )

    cf_min, cf_max = CATEGORY_FACTOR[category]
    rf = REUSE_FACTOR[reuse]
    productivity = PRODUCTIVITY_HOURS_PER_FP * productivity_ratio

    base = fp * productivity / MAN_HOURS_PER_MONTH * MAN_MONTH_RATE * rf
    return SoftwareDevCostRange(
        min_cost=xlround(base * cf_min + direct_non_labor, 2),
        max_cost=xlround(base * cf_max + direct_non_labor, 2),
        formula_used=(
            f"{fp} FP × {productivity:.2f}h/FP / {MAN_HOURS_PER_MONTH}h/月 × ¥{MAN_MONTH_RATE}/人月"
            f" × 类别[{cf_min}-{cf_max}] × 复用{rf:.3f} + ¥{direct_non_labor}"
        ),
    )


# ---- 人天单价 推荐 ----------------------------------------------------------
def recommend_per_day_rate(category: str, reuse: str) -> Tuple[float, float]:
    """For person-day priced rows: derive defensible floor/ceiling per day."""
    cf_min, cf_max = CATEGORY_FACTOR[category]
    rf = REUSE_FACTOR[reuse]
    floor = MAN_MONTH_RATE * cf_min * rf / WORKDAYS_PER_MONTH
    ceiling = MAN_MONTH_RATE * cf_max * rf / WORKDAYS_PER_MONTH
    return int(xlround(floor, 0)), int(xlround(ceiling, 0))


def check_per_day_rate(actual: float, category: str, reuse: str, tolerance: float = 0.10) -> Tri:
    floor, ceiling = recommend_per_day_rate(category, reuse)
    if floor * (1 - tolerance) <= actual <= ceiling * (1 + tolerance):
        return "ok" if floor <= actual <= ceiling else "warn"
    return "fail"


# ---- 其他费率检查 -----------------------------------------------------------
def check_integration_fee_rate(rate: float, dispersed: bool) -> Tri:
    band = (0.04, 0.08) if dispersed else (0.03, 0.06)
    if band[0] <= rate <= band[1]:
        return "ok"
    if band[0] * 0.9 <= rate <= band[1] * 1.1:
        return "warn"
    return "fail"


def check_reserve_fee_rate(rate: float) -> Tri:
    if rate <= 0.02:
        return "ok"
    if rate <= 0.025:
        return "warn"
    return "fail"


def check_hw_ops_rate(rate: float, dispersed: bool) -> Tri:
    expected = HW_OPS_RATE_DISPERSED if dispersed else HW_OPS_RATE_NORMAL
    if abs(rate - expected) <= 0.005:
        return "ok"
    if rate <= expected * 1.2:
        return "warn"
    return "fail"


# ---- 设计费 表11 内插 -------------------------------------------------------
DESIGN_FEE_TABLE = [
    # (计费额 万元, 收费基价 万元, 占计费额%)
    (1000, 24.0),
    (2000, 44.0),
    (5000, 100.0),
    (10000, 160.0),
]
#: 表11 的四列依次是 机房建设 1.2 / 软件开发 1.0 / 系统集成 0.8 / 综合类 0.9。
#: ⚠️ 此前这里写成 {机房:1.2, 综合:1.0, 软件开发:0.8, 系统集成:0.9} —— 后三个
#: 系数整体错位一列。以软件开发为主的项目会按 0.8 计，设计费直接少算 20%，
#: 且不报错。已按 p.21 表11 渲染页面逐格核对更正。
DESIGN_PROJECT_FACTOR = {"机房建设": 1.2, "软件开发": 1.0,
                         "系统集成": 0.8, "综合类": 0.9,
                         # 旧调用点用的简称，保留别名避免静默 KeyError→改行为
                         "机房": 1.2, "综合": 0.9}


def compute_design_fee(amount_wan: float, project_type: str) -> float:
    """表11 — 内插法 + 项目类型系数. amount_wan in 万元."""
    if project_type not in DESIGN_PROJECT_FACTOR:
        raise ValueError(f"unknown project_type: {project_type}")
    factor = DESIGN_PROJECT_FACTOR[project_type]
    if amount_wan <= 1000:
        base = amount_wan * 0.024
    elif amount_wan >= 10000:
        return xlround(amount_wan * 0.015 * factor, 2)   # ≤1.5%
    else:
        # 直线内插
        for (a1, b1), (a2, b2) in zip(DESIGN_FEE_TABLE, DESIGN_FEE_TABLE[1:]):
            if a1 <= amount_wan <= a2:
                base = b1 + (amount_wan - a1) / (a2 - a1) * (b2 - b1)
                break
        else:  # pragma: no cover
            raise RuntimeError("interpolation gap")
    return xlround(base * factor, 2)


# ---- 软件运维 ---------------------------------------------------------------
def check_software_ops_per_man_year(yuan_per_man_year: float) -> Tri:
    lo, hi = SOFTWARE_OPS_YUAN_PER_MAN_YEAR
    if lo <= yuan_per_man_year <= hi:
        return "ok"
    if lo * 0.9 <= yuan_per_man_year <= hi * 1.1:
        return "warn"
    return "fail"


# ---- Totals ----------------------------------------------------------------
def compute_total(unit_price: float, qty: float) -> int:
    """金额合计取整。返回 int —— 元为最小单位，且避免展示成 ¥3600.0。"""
    return int(xlround(unit_price * qty, 0))
