"""quote_shape — 报价表结构解析的统一守卫。

## 它防的是哪一类错

同一个 bug 家族在 `run_optimize` / `run_review` 里出现了**五次**：

  1. 金额列认死 `成本总价/对外总价/研发报价` → 换一份表原总额 0，±5% 预算带对 0 取带
  2. `build_fp_table` 要求 `wb.kind ∈ {platform,llm}` → kind=unknown 时反推 FP 0
  3. `review_row` 同样的 kind 门控 → 整条逐行检查静默跳过，0 findings
  4. 单价列只认 `成本单价/单价（元）` → 「人天 + 报价(总额)」的表逐行跳过
  5. `verdict == "ok"` 不记 pass → 「全合规」与「一行没查」计分上完全一样

五处都不报错。**「认不出」不是「合格」，是「没检查」** —— 而报告会印出
「综合得分 0/100」，读的人只会以为报价烂透了。

这和 P0 是同一个形状（合并单元格击穿 SUMIF，少算 ¥421.6 万，不报错），
也和 `pack.factor()` 拒绝静默取 1 是同一个应对。逐处打补丁挡不住第六处 ——
所以把「按语义角色取列」这件事收敛到这里，并强制它对**整表零命中**报错。

## 两级守卫

  resolve()            逐行取值。取不到返回 None —— 单行没有某列是正常的
                       （表头行、小计行），所以逐行不报错，只记账。
  assert_recognized()  整表扫完后结算。**某个必需角色零命中 = 表结构没认出来**，
                       此时硬失败并列出期望与实际列名。

## kind 门控要用排除法而不是包含法

`parse_quote` 按文件名/表头猜 `kind`，猜不出就是 `"unknown"`。
用 `kind in {platform, llm}` 做包含式白名单，等于把「没猜出来的」一律排除 ——
而没猜出来恰恰是最常见的情况。`is_priceable()` 改成排除法：
只排除**明确不是报价**的，其余都查。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


class QuoteShapeError(RuntimeError):
    """报价表结构与预期不符 —— 硬失败，不静默出 0。"""


#: 按**语义角色**归类的候选列名。新格式的报价表往这里加，不要在调用点各写一份。
#: 顺序有意义：靠前的更精确（`成本总价` 比 `总价` 明确）。
COLUMN_ROLES: dict[str, list[str]] = {
    "total": ["成本总价", "对外总价", "成本总价（元）", "对外总价（元）",
              "研发报价", "报价", "总价", "总价（元）", "金额", "金额（元）",
              "增量成本"],
    "unit_price": ["成本单价", "单价（元）", "单价", "对外单价", "人天单价"],
    "person_days": ["人/天", "人天", "人日", "工时（人天）", "增量工时"],
    "detail": ["建设详情", "详情", "建设详情（定位）", "建设内容",
               "功能描述", "分项名称", "名称"],
    "qty": ["数量", "台数", "套数"],
}

#: 明确**不是报价**的 workbook / sheet kind。其余一律参与检查 —— 包括 unknown。
NON_QUOTE_WORKBOOK_KINDS = {"standard", "guideline"}
NON_QUOTE_SHEET_KINDS = {"summary"}


def is_priceable(wb_kind: str | None, sheet_kind: str | None = None) -> bool:
    """这个工作簿/表是否参与逐行检查。

    **排除法，不是包含法。** 用白名单会把 `kind=unknown` 一律排除，
    而那正是最常见的情况 —— 实测一份 1215 行的真实报价整簿判为 unknown，
    逐行检查全跳过、报告 0 findings、得分 0/100，命令正常退出。
    """
    if (wb_kind or "") in NON_QUOTE_WORKBOOK_KINDS:
        return False
    if sheet_kind is not None and (sheet_kind or "") in NON_QUOTE_SHEET_KINDS:
        return False
    return True


class Resolver:
    """按语义角色取列，并记账命中情况。

    典型用法：

        rs = Resolver()
        for row in rows:
            price = rs.get(row["cells"], "total")
            ...
        rs.assert_recognized("total")     # 整表零命中就在这里炸
    """

    def __init__(self, roles: dict[str, list[str]] | None = None) -> None:
        self.roles = roles or COLUMN_ROLES
        self.hits: dict[str, int] = defaultdict(int)
        self.rows_seen = 0
        self.columns_seen: set[str] = set()
        #: 每个角色实际命中的是哪个列名 —— 出报告时要能说清「我按哪一列算的」
        self.matched_columns: dict[str, set[str]] = defaultdict(set)

    def observe(self, cells: dict[str, Any]) -> None:
        self.rows_seen += 1
        self.columns_seen |= set(cells)

    def get(self, cells: dict[str, Any], role: str,
            numeric: bool = True) -> Any | None:
        """取该行在此角色下的值。取不到返回 None —— 单行缺列是正常的。"""
        if role not in self.roles:
            raise KeyError(f"未知语义角色 {role!r}；现有：{sorted(self.roles)}")
        for c in self.roles[role]:
            if c not in cells:
                continue
            v = cells[c]
            if numeric and not isinstance(v, (int, float)):
                continue
            if v is None:
                continue
            self.hits[role] += 1
            self.matched_columns[role].add(c)
            return v
        return None

    def derive_unit_price(self, cells: dict[str, Any]) -> float | None:
        """单价：显式列优先，否则由总额 ÷ 人天推出。

        很多报价表没有单价列，只有「人天 + 报价(总额)」，单价是隐含的
        （如某表总表注明 1,000 元/人天）。认不出就跳过整行，
        等于「认不出的行一律判合格」。
        """
        v = self.get(cells, "unit_price")
        if v is not None:
            return float(v)
        pd = self.get(cells, "person_days")
        total = self.get(cells, "total")
        if isinstance(pd, (int, float)) and pd > 0 and isinstance(total, (int, float)):
            self.hits["unit_price_derived"] += 1
            return round(total / pd, 2)
        return None

    # ---- 结算 ----

    def assert_recognized(self, *roles: str) -> None:
        """必需角色整表零命中即报错。**这是这个模块存在的理由。**"""
        missing = [r for r in roles
                   if self.hits.get(r, 0) == 0
                   and self.hits.get(f"{r}_derived", 0) == 0]
        if not missing:
            return
        raise QuoteShapeError(
            "报价表结构没认出来 —— 以下语义角色在全表零命中：\n  "
            + "\n  ".join(
                f"{r}：期望其一 {self.roles.get(r, [])}" for r in missing)
            + f"\n  实际列名（共 {len(self.columns_seen)} 个）："
              f"{sorted(self.columns_seen)[:30]}"
            + f"\n  已扫描 {self.rows_seen} 行。"
            + "\n  修法：把本表的列名加进 quote_shape.COLUMN_ROLES 的对应角色。"
              "\n  **不要改成静默跳过** —— 「认不出」不是「合格」，是「没检查」。")

    def summary(self) -> dict[str, Any]:
        return {
            "rows_seen": self.rows_seen,
            "hits": dict(self.hits),
            "matched_columns": {k: sorted(v) for k, v in self.matched_columns.items()},
            "columns_seen": sorted(self.columns_seen),
        }


def iter_priceable_rows(quote: dict) -> Iterable[tuple[dict, dict, dict]]:
    """遍历所有参与检查的行，产出 (workbook, sheet, row)。

    把 kind 门控收在一处 —— 此前 `run_optimize` 与 `run_review` 各写一份，
    两份还不一样（一个要 items/deploy、一个不限），于是同一份表在两个
    脚本里被算成不同的范围。
    """
    for wb in quote.get("workbooks", []):
        if not is_priceable(wb.get("kind")):
            continue
        for sh in wb.get("sheets", []):
            if not is_priceable(wb.get("kind"), sh.get("kind")):
                continue
            for row in sh.get("rows", []):
                yield wb, sh, row
