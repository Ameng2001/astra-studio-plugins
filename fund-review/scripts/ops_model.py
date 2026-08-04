"""ops_model — 运营期成本展开与 TCO。

运营期不是单独一层，而是**每个 BOM 条目在其交付形态下自动展开出的 recurring 行**。
每行强制带 payee（谁收钱）与 in_scope（是否计入本次采购预算）。

in_scope=false 的行**必须列出但不计入**：
  漏列 → 财评认为方案不完整（"运维怎么办？"答不上来）
  计入 → 超出本标准科目范围（山东无运维科目），会被划掉
列而不计，并说明另行立项，才是正确做法。

**内部成本与对外报价严格分离。** 对外只出 list_price；成本构成仅用于
毛利校验与脱敏后的定价依据说明。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bom_schema import BomItem
from nesma_weights import xlround


@dataclass
class OpsLine:
    element: str
    element_name: str
    scope: str                  # 系统或产品
    mode: str
    payee: str
    in_scope: bool
    annual_yuan: float
    basis: str
    note: str = ""


class OpsModel:
    def __init__(self, modes: dict[str, Any], internal: dict[str, Any]) -> None:
        self.modes = modes["modes"]
        self.elements = modes["cost_elements"]
        self.internal = internal

    @classmethod
    def load(cls, modes_path: Path, internal_path: Path) -> "OpsModel":
        return cls(yaml.safe_load(Path(modes_path).read_text(encoding="utf-8")),
                   yaml.safe_load(Path(internal_path).read_text(encoding="utf-8")))

    # ---- 运营期展开 ----

    def expand(self, items: list[BomItem], assigned: dict[str, str]) -> list[OpsLine]:
        """按成本元素的 aggregation 粒度合并。

        基础设施运维、推理算力、外部 API 都是**部署级**的 ——
        不随系统数增加。按系统累加会把 ¥25.8 万的运维放大到 ¥670 万。
        订阅是**产品级** —— 卖的是订阅产品，不是功能点也不是系统。
        """
        by_id = {i.id: i for i in items}
        seen: set[tuple] = set()
        lines: list[OpsLine] = []

        for iid, mode in sorted(assigned.items()):
            item = by_id.get(iid)
            spec = self.modes.get(mode)
            if item is None or spec is None:
                continue

            for op in spec.get("operation", []):
                el = op["element"]
                agg = self.elements.get(el, {}).get("aggregation", "deployment")

                if el == "CE-SUB":
                    kind = op.get("subscription_kind", "platform")
                    if ("sub", kind) in seen:
                        continue
                    seen.add(("sub", kind))
                    sub = self.internal.get("subscriptions", {}).get(kind, {})
                    covered = sub.get("scope_systems", [])
                    lines.append(OpsLine(
                        element=el, element_name=self.elements[el]["name"],
                        scope="、".join(covered) or item.path.system,
                        mode=mode, payee=op["payee"], in_scope=op["in_scope"],
                        annual_yuan=float(sub.get("list_price", 0)),
                        basis=f"{sub.get('unit', '元/年')}，C-life 定价，覆盖 "
                              f"{len(covered) or 1} 个系统",
                        note=(spec.get("note", "").strip().splitlines() or [""])[0]))
                    continue

                key = ("dep", el) if agg == "deployment" else ("sys", item.path.system, el)
                if key in seen:
                    continue
                seen.add(key)
                est, basis = self._estimate(el)
                lines.append(OpsLine(
                    element=el, element_name=self.elements[el]["name"],
                    scope="整体部署" if agg == "deployment" else item.path.system,
                    mode=mode, payee=op["payee"], in_scope=op["in_scope"],
                    annual_yuan=est, basis=basis, note=op.get("note", "")))
        return lines

    def _estimate(self, element: str) -> tuple[float, str]:
        """非订阅类运营成本的年度估算 —— 取自内部成本模型的 customer_borne。"""
        cb = self.internal.get("customer_borne", {})
        mapping = {"CE-EXTAPI": "external_llm_api", "CE-INFER": "self_inference",
                   "CE-OPS-INFRA": "ops_infra", "CE-OPS-APP": "ops_infra"}
        entry = cb.get(mapping.get(element, ""), {})
        return float(entry.get("estimate", 0)), entry.get("basis", "待测算")

    # ---- 毛利校验 ----

    def margin_check(self) -> list[dict[str, Any]]:
        """订阅定价的毛利校验。低于阈值报警 —— 用内部成本，不外发。"""
        threshold = self.internal.get("margin_alert_threshold", 0.30)
        out = []
        for kind, sub in self.internal.get("subscriptions", {}).items():
            price = sub.get("list_price", 0)
            cost = sub.get("total_internal_cost", 0)
            margin = (price - cost) / price if price else 0
            out.append({"kind": kind, "list_price": price, "margin": round(margin, 4),
                        "below_threshold": margin < threshold,
                        "threshold": threshold})
        return out

    # ---- 对外可披露的定价依据（脱敏） ----

    def pricing_basis_public(self, kind: str) -> str:
        """被问「订阅价依据」时给出的说明 —— 只出构成项，不出成本数。"""
        sub = self.internal.get("subscriptions", {}).get(kind, {})
        parts = [self.elements.get(k, {}).get("name", k)
                 for k in sub.get("internal_cost", {})]
        return (f"订阅价 {sub.get('list_price', 0):,.0f} {sub.get('unit', '元/年')}，"
                f"已包含：{'、'.join(parts)}。按年结算，服务期内不再另计上述费用。")


# ---- TCO ---------------------------------------------------------------


def tco(construction_total: float, ops_lines: list[OpsLine],
        years: int, in_scope_only: bool = False) -> dict[str, Any]:
    """N 年总拥有成本。按 payee 分组 —— 客户关心的是「一共要付谁多少钱」。"""
    annual_by_payee: dict[str, float] = defaultdict(float)
    for ln in ops_lines:
        if in_scope_only and not ln.in_scope:
            continue
        annual_by_payee[ln.payee] += ln.annual_yuan
    annual_total = sum(annual_by_payee.values())
    return {
        "years": years,
        "construction": xlround(construction_total, 2),
        "annual_total": xlround(annual_total, 2),
        "annual_by_payee": {k: xlround(v, 2) for k, v in sorted(annual_by_payee.items())},
        "operation_total": xlround(annual_total * years, 2),
        "tco": xlround(construction_total + annual_total * years, 2),
    }
