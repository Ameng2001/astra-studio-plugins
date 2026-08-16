"""liuzhou_v1 — 柳州市本级信息化建设项目预算支出标准（柳财审〔2020〕16号）的计算链。

    功能点数 = 未调整功能点数量(UFP) × 软件类别调整因子 × 复用系数
    定制软件开发费用 = 功能点数 × 软件开发生产率基准 ÷ 人月折算系数
                      × 软件开发基准人月费率 + 直接非人力成本
                                        —— 标准 p.13-14，2.1.2

软件开发段与广东**同形**（两地同出一套国标模板：GB/T 36964 + CSBMK），
只有三个取值不同：

  生产率      柳州 6.51（CSBMK-202010）　广东 6.65（CSBMK-201809）
  基准人月费率 柳州 17000（广西工资水平）　广东 24000
  软件类别因子 柳州给的是**区间**（0.8-1.0 / 1.0-1.2 / 1.0-1.3 / 1.0-1.5）

同形不等于可以复用 —— 生产率与费率的差让同一份 BOM 在两地差 27%，
`get_profile` 按 pack 的 formula_profile 取，不做「反正一样」的合并。

## 与山东、广东都不同的一处：其他费用一律**分档直线内插**

山东的设计费/测试费是超额累进（300×2% + 200×1.6% + …），
柳州的设计费（表11）、监理费（表12）、测试费（表13）是**在两个档位之间直线内插**：

    某项目工程费用 4000 万，项目类型机房建设
    咨询费 =｛44 + [(4000-2000)÷(5000-2000)] × (100-44)｝× 1.2 = 97.6 万元
                                        —— 标准 p.21 表11 注3 算例（已登记回归）

两者数值差得很远，混用不会报错、只会出一个错数 —— 所以 `progressive`
在本 profile 里直接抛错，不给「反正都是分档」的错觉留口子。

## 表12 监理费的「平均基价」

表12 只给了硬件系统一列基价，另有工程类型调整系数（软件开发 1.2、综合类 1.1），
末列「监理费支出标准 平均基价」= [基价 + 基价×1.2 + 基价×1.1] ÷ 3 = 基价 × 1.1
（注1）。**这一列是单值，不随项目类型变** —— 所以本 profile 对监理费取
average_of_types，而不是像设计费那样按项目类型选系数。看着别扭，但表就是这么定的。

舍入一律走 nesma_weights.xlround（复刻 Excel ROUND）。
"""
from __future__ import annotations

from typing import Any

from nesma_weights import xlround

PROFILE_ID = "liuzhou_v1"


# ---- 软件开发费（2.1.2 功能点估算法）--------------------------------------

def adjusted_fp(ufp: float, *, pack, counting_method: str, reuse_level: str,
                app_type: str, digits: int = 2) -> float:
    """功能点数 = UFP × 软件类别调整因子 × 复用系数（标准 p.13 ①）。

    **不乘规模变更因子** —— 本标准公式中无此环节（山东有 1.21/1.39）。
    """
    category = pack.factor("app_type", app_type)
    reuse = pack.factor("reuse", reuse_level)
    return xlround(ufp * category * reuse, digits)


def effort_man_months(fp_sum: float, *, pack, digits: int = 2) -> float:
    """开发工作量（人月）= 功能点数 × 生产率基准 ÷ 人月折算系数。"""
    productivity = pack.rate("productivity_hours_per_fp")
    hours_pm = pack.rate("man_hours_per_month")
    return xlround(fp_sum * productivity / hours_pm, digits)


def man_month_rate(*, pack, dev_category: str | None = None,
                   digits: int = 2) -> float:
    """人月费率固定 17000，**不按开发类别调整**（山东有 0.8-1.1 的类别系数）。

    dev_category 只为与其他 profile 保持调用签名一致，本标准中不影响取值。
    """
    return xlround(pack.rate("base_man_month_rate"), digits)


def software_dev_cost(fp_sum: float, *, pack, dev_category: str | None = None,
                      direct_non_labor: float = 0.0, digits: int = 2) -> float:
    """定制软件开发费用 = 工作量 × 人月费率 + 直接非人力成本。

    直接非人力成本「一般情况不进行计列（通常为0），特殊情况需要计列时
    应明确说明原因及测算依据」（标准 p.15）。
    """
    effort = effort_man_months(fp_sum, pack=pack)
    rate = man_month_rate(pack=pack)
    return xlround(effort * rate + direct_non_labor, digits)


# ---- 软件开发费（2.1.1 工作量估算法）--------------------------------------

def effort_method_cost(man_months: float, *, pack, stage: str,
                       risk: float = 1.0, reuse: float = 1.0,
                       digits: int = 4) -> float:
    """工作量估算法（表1）：金额（万元）= 人月 × 单价，单价 = 人工成本 × 风险系数 × 复用系数。

    人工成本按**阶段**取值：需求分析/系统设计 1.9、开发编码 1.7、
    系统测试 1.6、实施部署 1.4（万元/人月，标准 p.11 表1 注3）。

    ⚠️ 本函数只做**交叉校验**，不做定价。理由见 `derive_fp.py` 的模块注释：
    工作量估算法要求「人月」本身有测算依据，而既有清单里的人天档位是从
    末级功能报价倒推的。财评会直接问「你的人月是怎么估出来的」，
    倒推链路答不上来。正向链路是功能点法（2.1.2），走 `software_dev_cost`。
    """
    node = (pack.data.get("effort_method") or {}).get("stage_rates") or {}
    if stage not in node:
        raise ValueError(
            f"{PROFILE_ID}: 表1 无阶段 {stage!r}；可选：{sorted(node)}")
    unit = node[stage]["value"] * risk * reuse
    return xlround(man_months * unit, digits)


# ---- 其他费用 --------------------------------------------------------------

def progressive(base_wan: float, table: list[list[Any]], digits: int = 2) -> float:
    """本标准**无超额累进类费用项**，一律分档直线内插。

    不返回 0、不退化成内插 —— 两种算法在 4000 万计费额上差 40% 以上，
    静默换算法就是本项目反复遇到的那类「不报错、只是数错了」。
    """
    raise ValueError(
        f"{PROFILE_ID}: 柳州标准的设计费/监理费/测试费均为分档直线内插（表11/12/13），"
        f"非超额累进。调用方应走 interpolate()")


def interpolate(base_wan: float, table: list[list[Any]], digits: int = 6) -> float:
    """分档直线内插，返回**收费基价**（万元），不含项目类型系数。

    table = [[计费额万元, 收费基价万元], ...]，按计费额升序。
    落在两档之间时按直线内插；**超出表首/表尾一律抛错**，由 other_fee()
    按标准各自的表外规则处理 —— 静默外推会给出标准里根本没有的取值。
    """
    if not table:
        raise ValueError(f"{PROFILE_ID}: 内插表为空")
    if base_wan < table[0][0] or base_wan > table[-1][0]:
        raise ValueError(
            f"{PROFILE_ID}: 计费额 {base_wan} 万元超出内插表区间 "
            f"[{table[0][0]}, {table[-1][0]}]，须按表外规则计取")
    for (a1, b1), (a2, b2) in zip(table, table[1:]):
        if a1 <= base_wan <= a2:
            return xlround(b1 + (base_wan - a1) / (a2 - a1) * (b2 - b1), digits)
    raise RuntimeError(f"{PROFILE_ID}: 内插区间断裂 base_wan={base_wan}")


def other_fee(pack, fee_id: str, base_wan: float,
              dev_category: str | None = None,
              fee_project_type: str | None = None,
              integration_layout: str | None = None,
              count: int | None = None,
              adjust: float | None = None) -> float:
    """其他费用。返回万元。

    `fee_project_type` 是表11/12/13 的**项目类型**（机房建设/软件开发/
    系统集成/综合类），与 DealConfig.project_type（新建/升级改造）是两回事，
    故不共用字段名。

    `integration_layout` 是表9 的**施工地点分布**（集中/分散），上限差 33%。
    """
    spec = pack.fee(fee_id)
    method = spec["method"]

    if method == "rate":
        by_layout = spec.get("max_rate_by_layout")
        if by_layout:
            if integration_layout not in by_layout:
                raise ValueError(
                    f"{PROFILE_ID}: {fee_id} 须显式声明施工地点分布"
                    f"（{'/'.join(by_layout)}），当前 {integration_layout!r}。"
                    f"两者上限差 {max(by_layout.values())/min(by_layout.values())-1:.0%}，"
                    f"不设默认值 —— 默认成集中会让分散项目被无声核减，"
                    f"默认成分散等于替项目做了一个需要论证的声明")
            return xlround(base_wan * by_layout[integration_layout], 2)
        return xlround(base_wan * spec["max_rate"], 2)

    if method == "fixed":
        # 等保测评这类「几万元/系统」：标准只定单价与调整区间，
        # 数量与调整系数是本项目的事实，由调用方给。
        rng = spec.get("adjust_range")
        adj = 1.0 if adjust is None else adjust
        if rng and not (rng[0] <= adj <= rng[1]):
            raise ValueError(
                f"{PROFILE_ID}: {fee_id} 调整系数 {adj} 超出标准区间 {rng}")
        return xlround(spec["fixed_base"] * (count if count is not None else 1)
                       * adj, 2)

    if method != "interpolate":
        raise ValueError(f"{PROFILE_ID}: 未知计费方式 {method!r}")

    table = spec["table"]
    first, last = table[0][0], table[-1][0]

    if base_wan < first:
        below = spec.get("below_first_tier")
        if not below:
            raise ValueError(
                f"{PROFILE_ID}: {fee_id} 计费额 {base_wan} 万元低于表首 {first}，"
                f"且本标准未规定表下规则")
        base = base_wan * below["rate"]
    elif base_wan > last:
        above = spec.get("above_last_tier")
        if not above:
            raise ValueError(
                f"{PROFILE_ID}: {fee_id} 计费额 {base_wan} 万元高于表尾 {last}，"
                f"且本标准未规定表上规则")
        base = base_wan * above["max_rate"]
    else:
        base = interpolate(base_wan, table)

    factor, _ = fee_project_factor(spec, fee_project_type, fee_id)
    return xlround(base * factor, 2)


def fee_project_factor(spec: dict[str, Any], fee_project_type: str | None,
                       fee_id: str) -> tuple[float, str]:
    """取项目类型调整系数，返回 (系数, 说明)。

    监理费（表12）的末列是**平均基价**，不随项目类型变 —— 那一列已经把
    三种类型平均掉了，再按类型选一次就是重复调整。
    """
    if spec.get("factor_mode") == "average_of_types":
        types = spec["project_factors"]
        avg = (1.0 + sum(types.values())) / (1 + len(types))
        return avg, (f"平均基价 = [基价 + " +
                     " + ".join(f"基价×{v}" for v in types.values()) +
                     f"] ÷ {1 + len(types)} = 基价×{avg:g}")

    types = spec.get("project_factors")
    if not types:
        return 1.0, "本费用项无项目类型调整系数"
    key = fee_project_type or spec.get("default_project_type")
    if key not in types:
        raise ValueError(
            f"{PROFILE_ID}: {fee_id} 无项目类型 {key!r}；可选：{sorted(types)}")
    return types[key], f"项目类型「{key}」调整系数 {types[key]}"


# ---- 第三层可调项：软件类别因子与生产率 ------------------------------------

def resolve_fp_settings(pack, settings: dict[str, Any] | None) -> dict[str, Any]:
    """把 deal 的功能点法可调项解析成取值 + 依据，并逐项校验。

    ## 为什么要有依据字段，而不只是一个数

    表3 注1：「凡取值超过1的，需列明具体取值依据」（p.14）。
    「需列明依据」是**出表时的硬要求**，不是建议 —— 所以依据缺失就不该让它
    算出一个数来。让它先算出来、指望编制说明那边补，是本项目反复吃亏的形状：
    数已经进了汇总，没人回头看它有没有依据。

    生产率同理：p.14 ② 明文「根据实际情况可上下浮动20%」，浮动是标准给的
    权利，但「实际情况」得写出来。

    返回 {"productivity_ratio", "productivity_basis", "app_type": {类别: (取值, 依据)}}
    """
    s = settings or {}
    out: dict[str, Any] = {"app_type": {}}

    node = (pack.data.get("rates") or {}).get("productivity_hours_per_fp") or {}
    rng = node.get("adjustable_range")
    ratio = s.get("productivity_ratio", 1.0)
    if rng and not (rng[0] <= ratio <= rng[1]):
        raise ValueError(
            f"{PROFILE_ID}: 生产率浮动系数 {ratio} 超出标准区间 {rng}"
            f"（p.{node.get('citation', {}).get('page')} 「根据实际情况可上下浮动20%」）")
    if abs(ratio - 1.0) > 1e-9 and not s.get("productivity_basis"):
        raise ValueError(
            f"{PROFILE_ID}: 生产率取 {ratio}（非中值）须给 productivity_basis —— "
            f"标准允许浮动，但「实际情况」要写得出来；出表时这段话要进编制说明")
    out["productivity_ratio"] = ratio
    out["productivity_basis"] = s.get("productivity_basis") or "取 CSBMK 中位值 P50，未浮动"

    node = (pack.data.get("factors") or {}).get("app_type") or {}
    ranges = node.get("value_range") or {}
    defaults = node.get("values") or {}
    for cat, spec in (s.get("app_type_factors") or {}).items():
        if cat not in defaults:
            raise ValueError(
                f"{PROFILE_ID}: 软件类别 {cat!r} 不在本标准的表3 里；"
                f"可选：{sorted(defaults)}")
        val = spec["value"] if isinstance(spec, dict) else spec
        basis = spec.get("basis") if isinstance(spec, dict) else None
        lo, hi = ranges.get(cat, (val, val))
        if not (lo <= val <= hi):
            raise ValueError(
                f"{PROFILE_ID}: 「{cat}」取 {val}，超出表3 区间 [{lo}, {hi}]")
        if val > 1.0 and not basis:
            raise ValueError(
                f"{PROFILE_ID}: 「{cat}」取 {val} > 1.0，必须给 basis —— "
                f"表3 注1「凡取值超过1的，需列明具体取值依据」（p.14）。"
                f"没有依据就不该算出这个数：数一旦进了汇总，没人回头查它有没有依据")
        out["app_type"][cat] = (val, basis or f"取表3 区间下界 {val}，无需另附依据")
    for cat, val in defaults.items():
        out["app_type"].setdefault(cat, (val, f"取 {val}，未超 1.0，无需另附依据"))
    return out


def fp_cost(ufp: float, *, pack, app_type: str, settings: dict[str, Any],
            reuse_level: str = "新建", digits: int = 2) -> dict[str, Any]:
    """按 deal 的可调项算一个子系统的功能点法金额，返回逐步展开。"""
    cat_val, cat_basis = settings["app_type"][app_type]
    reuse = pack.factor("reuse", reuse_level)
    fp_count = xlround(ufp * cat_val * reuse, 2)
    prod = pack.rate("productivity_hours_per_fp") * settings["productivity_ratio"]
    hours_pm = pack.rate("man_hours_per_month")
    effort = xlround(fp_count * prod / hours_pm, 2)
    rate = pack.rate("base_man_month_rate")
    return {"ufp": ufp, "app_type": app_type, "app_type_factor": cat_val,
            "app_type_basis": cat_basis, "reuse": reuse, "fp": fp_count,
            "productivity": round(prod, 4), "effort_man_months": effort,
            "man_month_rate": rate, "cost": xlround(effort * rate, digits)}
