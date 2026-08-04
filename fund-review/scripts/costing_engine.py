"""costing_engine — BOM × 区域标准包 × 交付方案 → 造价。

三层合成的主引擎。每一层只负责自己的事：
  L0 BOM        有什么、多大（功能点类型、app_type 分类名、dev_category 分类名）
  L1 标准包     这个省怎么算（权重、系数取值、费率、费用科目）
  L2 交付方案   这次怎么卖（计数方法、复用度取定、范围、交付形态）

引擎不硬编码任何系数 —— 全部经 StandardPack 取，且每个取值都能追到 citation。

**时光回溯**：`as_of` 重建任意历史版本时点的 BOM 状态。
这是「条目永不物理删除」的兑现 —— 任何一份历史报价都能凭 deal.lock.json 精确重算。
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from bom_schema import FP_COUNTED_CLASSES, Bom, BomItem
from nesma_weights import xlround
from standard_pack import StandardPack, get_profile

PLACEHOLDER_TAG = "placeholder"


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")) or (0,)


def snapshot(bom: Bom, as_of: str | None = None) -> list[BomItem]:
    """重建某版本时点的有效条目集合。

    条目在 `since` 时引入、在 `deprecated_in` 时废弃。给定版本 v，
    有效条目 = since ≤ v 且 (未废弃 或 deprecated_in > v)。
    """
    if as_of is None:
        return bom.active()
    target = _ver(as_of)
    out = []
    for i in bom.items:
        if _ver(i.since) > target:
            continue
        if i.deprecated_in and _ver(i.deprecated_in) <= target:
            continue
        out.append(i)
    return out


@dataclass
class DealConfig:
    """L2 —— 本次商机的决策。P5 会在此扩展交付形态。"""

    deal_id: str
    counting_method: str = "估算功能点法"
    project_type: str = "新建"                  # 新建 | 升级改造
    reuse_level: str = "新建"                   # 解析到 pack.factors.reuse 的 key
    include_placeholders: bool = False          # 占位条目默认不计入报价
    scope_tags: list[str] = field(default_factory=list)   # 空 = 全量
    as_of_bom_version: str | None = None


@dataclass
class SystemCost:
    system: str
    dev_category: str
    items: int
    ufp: int
    afp: float
    effort_man_months: float
    man_month_rate: float
    cost: float
    by_type: dict[str, int]


class CostingEngine:
    def __init__(self, bom: Bom, pack: StandardPack, deal: DealConfig) -> None:
        self.bom = bom
        self.pack = pack
        self.deal = deal
        self.profile = get_profile(pack.formula_profile)

    # ---- 范围 ----

    def in_scope(self) -> tuple[list[BomItem], dict[str, Any]]:
        items = snapshot(self.bom, self.deal.as_of_bom_version)
        excluded: dict[str, int] = defaultdict(int)
        kept = []
        for i in items:
            if not self.deal.include_placeholders and PLACEHOLDER_TAG in i.tags:
                excluded["占位未确认"] += 1
                continue
            if self.deal.scope_tags and not (set(i.tags) & set(self.deal.scope_tags)):
                excluded["不在范围标签内"] += 1
                continue
            kept.append(i)
        return kept, dict(excluded)

    # ---- 软件开发费用 ----

    def software_dev(self, items: list[BomItem]) -> list[SystemCost]:
        """按 (系统, 开发类别) 分组计算。

        同一系统内条目的 dev_category 可能不同 —— 分组而非取众数，
        避免把「数据分析及加工」(0.8) 的条目按「大型行业软件开发」(1.1) 计价。
        """
        method = self.deal.counting_method
        groups: dict[tuple[str, str], list[BomItem]] = defaultdict(list)
        for i in items:
            if i.cls not in FP_COUNTED_CLASSES or not i.nesma:
                continue
            groups[(i.path.system, i.dev_category or "基于统一平台的软件开发")].append(i)

        out: list[SystemCost] = []
        for (system, dev_cat), members in sorted(groups.items()):
            ufp = 0
            afp = 0.0
            by_type: dict[str, int] = defaultdict(int)
            for i in members:
                w = self.pack.fp_weight(method, i.nesma.type)
                ufp += w
                by_type[i.nesma.type] += 1
                afp += self.profile.adjusted_fp(
                    w, pack=self.pack, counting_method=method,
                    reuse_level=self.deal.reuse_level,
                    app_type=i.app_type or "业务处理")
            afp = xlround(afp, 2)
            effort = self.profile.effort_man_months(afp, pack=self.pack)
            rate = self.profile.man_month_rate(pack=self.pack, dev_category=dev_cat)
            out.append(SystemCost(
                system=system, dev_category=dev_cat, items=len(members),
                ufp=ufp, afp=afp, effort_man_months=effort,
                man_month_rate=rate, cost=xlround(effort * rate, 2),
                by_type=dict(by_type)))
        return out

    # ---- 硬件 ----

    def hardware(self, items: list[BomItem]) -> dict[str, Any]:
        rows, total = [], 0.0
        for i in items:
            if i.cls != "HARDWARE":
                continue
            price = i.spec.get("reference_unit_price_yuan")
            qty = i.spec.get("qty") or 0
            amount = (price or 0) * qty
            total += amount
            rows.append({"id": i.id, "name": i.name, "qty": qty,
                         "unit": i.spec.get("unit", ""),
                         "reference_unit_price": price,
                         "amount": xlround(amount, 2),
                         "pricing_basis": i.spec.get("pricing_basis", "")})
        ev = self.pack.data.get("procurement_evidence", {}).get("hardware", {})
        need_quote = [r for r in rows
                      if (r["reference_unit_price"] or 0) >= ev.get("unit_price_threshold", 1e18)]
        return {"rows": rows, "total": xlround(total, 2),
                "quotes_required_count": len(need_quote),
                "quotes_required_rule": (
                    f"单价 ≥{ev.get('unit_price_threshold')} 元或单一类型总价 "
                    f"≥{ev.get('category_total_threshold')} 元需 "
                    f"{ev.get('quotes_required')} 家盖章询价单")}

    # ---- 其他建设费用 ----

    def other_fees(self, software_dev_total: float, hardware_total: float,
                   software_purchase_total: float = 0.0,
                   dev_categories: set[str] | None = None) -> list[dict[str, Any]]:
        bases = {"software_dev": software_dev_total,
                 "hardware_purchase": hardware_total,
                 "software_purchase": software_purchase_total}
        out = []
        for spec in self.pack.data.get("other_fees", []):
            base_yuan = sum(bases.get(b, 0.0) for b in spec["base"])
            base_wan = base_yuan / 10000
            eligible = spec.get("eligible_dev_category")
            blocked = None
            if eligible and dev_categories is not None:
                if not (set(eligible) & dev_categories):
                    blocked = (f"本项目开发类别为 {sorted(dev_categories)}，"
                               f"不属于 {eligible} —— 按标准不单独申报")
            if blocked or base_yuan <= 0:
                amount_wan = 0.0
            elif spec["method"] == "progressive":
                amount_wan = self.profile.progressive(base_wan, spec["table"])
            else:
                amount_wan = xlround(base_wan * spec["max_rate"], 2)
            out.append({
                "id": spec["id"], "name": spec["name"],
                "base": spec["base"], "base_wan": xlround(base_wan, 4),
                "method": spec["method"],
                "amount_wan": amount_wan,
                "amount_yuan": xlround(amount_wan * 10000, 2),
                "blocked_reason": blocked,
                "citation": spec.get("citation"),
            })
        return out

    # ---- 合成 ----

    def run(self) -> dict[str, Any]:
        items, excluded = self.in_scope()
        sys_costs = self.software_dev(items)
        hw = self.hardware(items)

        sw_total = xlround(sum(s.cost for s in sys_costs), 2)
        dev_cats = {s.dev_category for s in sys_costs}
        fees = self.other_fees(sw_total, hw["total"], dev_categories=dev_cats)
        fee_total = xlround(sum(f["amount_yuan"] for f in fees), 2)

        return {
            "deal": self.deal.deal_id,
            "bom_version": self.deal.as_of_bom_version or self.bom.version,
            "pack_id": self.pack.pack_id,
            "formula_profile": self.pack.formula_profile,
            "counting_method": self.deal.counting_method,
            "project_type": self.deal.project_type,
            "reuse_level": self.deal.reuse_level,
            "scope": {"items": len(items), "excluded": excluded},
            "software_dev": {
                "systems": [vars(s) for s in sys_costs],
                "ufp_total": sum(s.ufp for s in sys_costs),
                "afp_total": xlround(sum(s.afp for s in sys_costs), 2),
                "effort_total": xlround(sum(s.effort_man_months for s in sys_costs), 2),
                "total": sw_total,
            },
            "hardware": hw,
            "other_fees": fees,
            "totals": {
                "software_dev": sw_total,
                "hardware_purchase": hw["total"],
                "other_fees": fee_total,
                "construction_total": xlround(sw_total + hw["total"] + fee_total, 2),
            },
        }


def lock(result: dict[str, Any], bom: Bom, pack: StandardPack,
         deal: DealConfig) -> dict[str, Any]:
    """版本锁 —— 凭此可精确重算本次报价。"""
    return {
        "deal_id": deal.deal_id,
        "bom_version": result["bom_version"],
        "bom_items_in_scope": result["scope"]["items"],
        "standard_pack": {"pack_id": pack.pack_id,
                          "formula_profile": pack.formula_profile,
                          "effective_from": pack.data.get("effective_from"),
                          "csbmk_baseline": pack.data.get("csbmk_baseline")},
        "deal_config": {
            "counting_method": deal.counting_method,
            "project_type": deal.project_type,
            "reuse_level": deal.reuse_level,
            "include_placeholders": deal.include_placeholders,
            "scope_tags": deal.scope_tags,
        },
        "totals": result["totals"],
        "reproduce": (
            "python3 quote_generate.py --bom <dir> --pack <dir> --deal <deal.yaml> "
            f"--as-of {result['bom_version']}"),
    }
