"""nesma_weights — NESMA 计数权重与 Excel 兼容舍入。

⚠️ 权重的**权威来源是区域标准包** (`standard-packs/{region}/pack.yaml`)，
不是本模块。这里的取值仅用于两类不涉及报价的场景：
  - BOM 层面的规模统计与漂移门禁（G-10）
  - 未指定区域时的粗略体量估算
所幸山东、广东、柳州三地的估算功能点法权重完全一致（10/7/4/5/4），
预估功能点法也一致（35/15），故此处取值与三地均不冲突。
一旦引入权重不同的区域，本模块的调用点必须改为从 pack 读取。
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Context, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bom_schema import Bom

#: 估算功能点法（GB/T 42588 / SJ/T 11619）
ESTIMATED_WEIGHTS = {"ILF": 10, "ELF": 7, "EI": 4, "EO": 5, "EQ": 4}
#: 预估功能点法 —— 只数数据功能
INDICATIVE_WEIGHTS = {"ILF": 35, "ELF": 15}


def xlround(value: float, digits: int = 2) -> float:
    """复刻 Excel / LibreOffice 的 ROUND。与 Python 内置 round() 有两处不同：

    1. 方向：Excel 四舍五入（half-up），Python 银行家舍入（half-even）。
       EO(5) × 规模变更 1.21 × 应用类型 1.5 = 9.075 恰在半值点。
    2. 精度：Excel 先把二进制结果规整到 15 位有效数字再舍入。
       8.87 × 26009.5 的 IEEE754 值是 230704.26499999998，
       规整后成 230704.265 → Excel 得 .27；直接 half-up 只能得 .26。

    财评评审会拿 Excel 复核，口径不一致即被质疑 —— 造价全链路统一走本函数。
    实测依据见 clife-elderly-care/p0-baseline/README.md。
    """
    normalized = Context(prec=15).create_decimal(Decimal(value))
    return float(normalized.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def ufp_total(bom: "Bom", weights: dict[str, int] | None = None) -> int:
    """BOM 的未调整功能点合计。仅统计走功能点法的有效条目。"""
    from bom_schema import FP_COUNTED_CLASSES

    w = weights or ESTIMATED_WEIGHTS
    return sum(w.get(i.nesma.type, 0) for i in bom.active()
               if i.cls in FP_COUNTED_CLASSES and i.nesma)


def ufp_by_system(bom: "Bom", weights: dict[str, int] | None = None) -> dict[str, int]:
    from bom_schema import FP_COUNTED_CLASSES

    w = weights or ESTIMATED_WEIGHTS
    out: dict[str, int] = {}
    for system, items in bom.by_system().items():
        out[system] = sum(w.get(i.nesma.type, 0) for i in items
                          if i.cls in FP_COUNTED_CLASSES and i.nesma)
    return out
