"""锁定舍入口径 —— 这类差异会静默回退，且回退后没人发现。

财评评审拿 Excel 复核。口径不一致本身就是一条 finding，
而且它不报错、不崩溃，只是数字对不上。所以要用测试钉住。

跑法（无需 pytest）：
    PYTHONPATH=fund-review/scripts python3 fund-review/tests/test_rounding.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import formula_engine as fe
from nesma_weights import xlround

FAILURES: list[str] = []


def check(name: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{name}: 实得 {actual!r}，期望 {expected!r}")


# ---- xlround 本身 ------------------------------------------------------

# 方向：half-up 而非 half-even。Python 内置 round(9.075, 2) 给 9.07。
check("half-up 9.075", xlround(9.075, 2), 9.08)
check("half-up 2.5", xlround(2.5, 0), 3.0)
check("half-up 3.5", xlround(3.5, 0), 4.0)          # round() 也给 4，但 2.5 给 2
check("half-up 0.125", xlround(0.125, 2), 0.13)

# 精度：先规整到 15 位有效数字。
# 8.87 × 26009.5 的 IEEE754 值是 230704.26499999998，
# 直接 half-up 得 .26；规整后成 230704.265，Excel 得 .27。
check("15 位有效数字规整", xlround(8.87 * 26009.5, 2), 230704.27)

# 负数不应改变方向语义
check("负数 half-up", xlround(-2.5, 0), -3.0)


# ---- formula_engine 全模块统一走 xlround --------------------------------

# compute_total 取整。返回 int —— 改口径不应把 ¥3600 显示成 ¥3600.0
check("compute_total 半值进位", fe.compute_total(2.5, 1), 3)
check("compute_total 常规", fe.compute_total(1200, 3), 3600)
check("compute_total 返回 int", isinstance(fe.compute_total(1200, 3), int), True)

# recommend_per_day_rate 取整，同样保持 int
floor, ceiling = fe.recommend_per_day_rate("业务处理", "低")
check("per_day 返回 int", isinstance(floor, int) and isinstance(ceiling, int), True)
check("per_day floor 取值", floor, int(xlround(17000 * 0.8 * 1.0 / 21.75, 0)))
check("per_day ceiling 取值", ceiling, int(xlround(17000 * 1.0 * 1.0 / 21.75, 0)))

# compute_design_fee 保留两位
fee = fe.compute_design_fee(1500, "软件开发")
check("design_fee 两位小数", round(fee, 2), fee)

# compute_software_dev_cost 的输出已按 2 位舍入（此前返回未舍入浮点）
r = fe.compute_software_dev_cost(100, "业务处理", "低")
check("dev_cost min 已舍入", round(r.min_cost, 2), r.min_cost)
check("dev_cost max 已舍入", round(r.max_cost, 2), r.max_cost)


# ---- 源码里不应再有对内置 round 的调用 ----------------------------------
# 用 AST 而非文本扫描 —— 文档字符串里提到 round() 是正当的，不该误报

import ast

src_path = Path(__file__).resolve().parent.parent / "scripts" / "formula_engine.py"
tree = ast.parse(src_path.read_text(encoding="utf-8"))
bare = [n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "round"]
check("formula_engine 无内置 round 调用", bare, [])


if FAILURES:
    print("舍入口径回归 —— 失败 %d 项：" % len(FAILURES))
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("舍入口径回归：全部通过")
