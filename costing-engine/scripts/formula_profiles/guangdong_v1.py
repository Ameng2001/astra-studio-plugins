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


def resolve_fp_settings(pack, settings: dict[str, Any] | None) -> dict[str, Any]:
    """把 deal 的功能点法可调项解析成取值 + 依据，并逐项校验。

    ## 与柳州的校验规则不同，因为标准给的东西不同

    柳州表3 给的是**区间**（业务处理 [0.8,1.0]、人工智能 [1.0,1.5]），
    取值有空间，所以有「凡取值超过1的需列明依据」（注1）这条要求。

    广东表2 给的是**单值**：业务处理 1.0、人工智能 1.5、大数据多媒体 1.3、
    应用集成和科学计算 1.2。没有区间就没有取值空间 —— 偏离缺省不是「取上界」，
    是**不按标准取值**。所以这里的校验是「必须等于标准值」，偏离即报错，
    而不是柳州那种「超过 1 要给依据」。

    把柳州那套「给了 basis 就放行」搬过来，会让一个没有标准依据的因子
    带着一段像模像样的说明进入报价 —— 说明写得越充分越难被发现。

    生产率则确有浮动权利：分册 5.1.1「根据实际情况可上下浮动 20%」，
    浮动时须给依据，这一点与柳州一致。
    """
    s = settings or {}
    out: dict[str, Any] = {"app_type": {}}

    node = (pack.data.get("rates") or {}).get("productivity_hours_per_fp") or {}
    rng = node.get("adjustable_range")
    ratio = s.get("productivity_ratio", 1.0)
    if rng and not (rng[0] <= ratio <= rng[1]):
        raise ValueError(
            f"{PROFILE_ID}: 生产率浮动系数 {ratio} 超出标准区间 {rng}"
            f"（软件开发服务分册 p.{node.get('citation', {}).get('page')}）")
    if abs(ratio - 1.0) > 1e-9 and not s.get("productivity_basis"):
        raise ValueError(
            f"{PROFILE_ID}: 生产率取 {ratio}（非中值）须给 productivity_basis —— "
            f"标准允许上下浮动 20%，但「实际情况」要写得出来")
    out["productivity_ratio"] = ratio
    out["productivity_basis"] = s.get("productivity_basis") or "取 CSBMK 中位值 P50，未浮动"

    node = (pack.data.get("factors") or {}).get("app_type") or {}
    defaults = node.get("values") or {}
    aliases = node.get("aliases") or {}
    for cat, spec in (s.get("app_type_factors") or {}).items():
        key = aliases.get(cat, cat)
        if key not in defaults:
            raise ValueError(
                f"{PROFILE_ID}: 软件类别 {cat!r} 不在本标准表2 的四类里；"
                f"可选：{sorted(defaults)}（别名：{sorted(aliases)}）")
        val = spec["value"] if isinstance(spec, dict) else spec
        std = defaults[key]
        if abs(float(val) - float(std)) > 1e-9:
            raise ValueError(
                f"{PROFILE_ID}: 「{cat}」取 {val}，但本标准表2 该类为单值 {std}。\n"
                f"  广东不给取值区间 —— 偏离缺省不是「取上界」，是不按标准取值，"
                f"无条款可引。若确需偏离，须先取得省财政厅认可并记进标准包，"
                f"不能在 deal 里改。")
        basis = (spec.get("basis") if isinstance(spec, dict) else None)
        out["app_type"][key] = (std, basis or f"表2 该类取值 {std}（本标准为单值，无取值空间）")
    for cat, val in defaults.items():
        out["app_type"].setdefault(cat, (val, f"表2 该类取值 {val}（本标准为单值）"))
    return out


def fp_cost(ufp: float, *, pack, app_type: str, settings: dict[str, Any],
            reuse_level: str = "新建", digits: int = 2) -> dict[str, Any]:
    """按 deal 的可调项算一个子系统的功能点法金额，返回逐步展开。

    链路与 `adjusted_fp` + `software_dev_cost` 一致，这里多返回中间量供出表列示。
    **不乘规模变更因子** —— 本标准公式中无此环节。
    """
    key = ((pack.data.get("factors") or {}).get("app_type") or {}) \
        .get("aliases", {}).get(app_type, app_type)
    if key not in settings["app_type"]:
        raise ValueError(
            f"{PROFILE_ID}: 子系统的软件类别 {app_type!r} 未解析到取值；"
            f"已解析：{sorted(settings['app_type'])}")
    cat_val, cat_basis = settings["app_type"][key]
    reuse = pack.factor("reuse", reuse_level)
    fp_count = xlround(ufp * cat_val * reuse, 2)
    prod = pack.rate("productivity_hours_per_fp") * settings["productivity_ratio"]
    hours_pm = pack.rate("man_hours_per_month")
    effort = xlround(fp_count * prod / hours_pm, 2)
    rate = pack.rate("base_man_month_rate")
    return {"ufp": ufp, "app_type": key, "app_type_factor": cat_val,
            "app_type_basis": cat_basis, "reuse": reuse, "fp": fp_count,
            "productivity": round(prod, 4), "effort_man_months": effort,
            "man_month_rate": rate, "cost": xlround(effort * rate, digits)}
