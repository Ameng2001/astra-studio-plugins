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
    scope_tags: list[str] = field(default_factory=list)   # 空 = 全量（遗留，见 scope_filter）
    #: 范围过滤器 —— 由交付方案的 `scope` 块提供，签名 (item) -> (是否在内, 原因)。
    #: 选择单元是**子系统**，与交付形态共用同一套 match 语法。
    #: 为什么不用 scope_tags：BOM 的 tags 全是治理标记（ilf-补齐 / placeholder /
    #: 采购科目…），没有一个是范围标记。拿治理标记当范围用是错的；而给每个商机
    #: 往 BOM 里加一轮「一期/二期」tag，是把商机决策塞回产品事实。
    scope_filter: Any = None
    #: 项目特征因子（GB/T 36964 口径）。**默认关闭** —— 一旦默认生效，
    #: 报价里就有一段没有地方标准依据的调整，而清单上看不出来。
    #: 形如 {"id": "gbt36964", "selected": {"开发语言": "JAVA…", ...}, "factors": {...}}
    project_factors: dict[str, Any] | None = None
    as_of_bom_version: str | None = None


@dataclass
class DeliveryContext:
    """交付方案上下文 —— 建设期算什么，取决于每个条目怎么交付。

    SaaS 下不该有定制开发费（你订阅就不用建），私有化下不该有订阅费。
    不接交付方案就三个场景算出同一个建设期总额，TCO 对比毫无意义。
    """

    assigned: dict[str, str]              # 条目 id → 形态代码
    modes: dict[str, Any]                 # 形态定义
    internal: dict[str, Any] = field(default_factory=dict)   # 实施投入等


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
    def __init__(self, bom: Bom, pack: StandardPack, deal: DealConfig,
                 delivery: "DeliveryContext | None" = None) -> None:
        self.bom = bom
        self.pack = pack
        self.deal = deal
        self.delivery = delivery
        self.profile = get_profile(pack.formula_profile)

    def _band(self, sys_costs: list[SystemCost]) -> list[dict[str, Any]] | None:
        """生产率三档 —— 只在标准包给了浮动依据时出。见 pack.productivity_band()。"""
        band = self.pack.productivity_band()
        if not band:
            return None
        hours_pm = self.pack.rate("man_hours_per_month")
        pf_val, _ = self.project_factor()
        out = []
        for label, prod in band:
            cost = 0.0
            effort_total = 0.0
            for s in sys_costs:
                e = xlround(xlround(s.afp * prod / hours_pm, 2) * pf_val, 2)
                effort_total += e
                cost += xlround(e * s.man_month_rate, 2)
            out.append({"档": label, "生产率": round(prod, 4),
                        "工作量人月": xlround(effort_total, 2),
                        "软件开发费": xlround(cost, 2)})
        return out

    def project_factor(self) -> tuple[float, list[dict[str, Any]]]:
        """项目特征因子的连乘值与逐项明细。未启用时返回 (1.0, [])。

        它是**项目级**的，与条目无关 —— 所以整体乘一次，不逐条乘。
        （层边界由「什么变了要重算」定，粒度只决定乘在哪一级。）
        """
        pf = self.deal.project_factors
        if not pf:
            return 1.0, []
        defs = pf.get("factors") or {}
        sel = pf.get("selected") or {}
        total, rows = 1.0, []
        for name, spec in defs.items():
            if name == "质量特性":
                subs = spec.get("sub") or {}
                s, picks = 0, {}
                for sub_name, table in subs.items():
                    choice = (sel.get("质量特性") or {}).get(sub_name)
                    if choice is None:
                        continue
                    if choice not in table:
                        raise ValueError(
                            f"质量特性.{sub_name} 的取值 {choice!r} 不在 "
                            f"{pf.get('id')} 词表中：{sorted(table)}")
                    s += table[choice]; picks[sub_name] = table[choice]
                v = xlround(1 + 0.025 * s, 4)
                rows.append({"因子": name, "取值": v, "构成": picks,
                             "公式": "1 + 0.025 × Σ子项"})
            else:
                choice = sel.get(name)
                if choice is None:
                    continue
                table = spec.get("values") or {}
                if choice not in table:
                    raise ValueError(
                        f"{name} 的取值 {choice!r} 不在 {pf.get('id')} 词表中："
                        f"{sorted(table)}")
                v = table[choice]
                rows.append({"因子": name, "取值": v, "选择": choice})
            total *= v
        return xlround(total, 6), rows

    def reuse_level(self, item: BomItem) -> str:
        """本条目适用的复用度档位。

        **复用度不该由人逐条填。** BOM 里已有 `maturity`（产品成熟度，产品事实），
        各省标准包用 `reuse.aliases_by_project_type` 把它映射到自己的档位词表 ——
        同一个 `partial`，山东叫「升级改造_既有功能优化完善」、广东叫「中」，
        取值还不一样。BOM 只存事实，档位由各省认领。

        新建项目下山东一律取「新建」=1.0（标准明文：与供应商的产品成熟度无关，
        产品成熟度是内部成本口径，不是甲方的既有系统），所以映射表里三个
        maturity 都指向「新建」—— 等于本项目忽略这一维，但忽略是**算出来的**，
        不是靠人在表里填 1763 个「低」。

        没有映射表就退回 deal 级取值，兼容旧标准包。
        """
        node = (self.pack.data.get("factors", {}).get("reuse") or {})
        table = (node.get("aliases_by_project_type") or {}).get(self.deal.project_type)
        if not table:
            return self.deal.reuse_level
        return table.get(item.maturity, self.deal.reuse_level)

    def _construction_elements(self, item: BomItem) -> set[str] | None:
        """该条目在其交付形态下，建设期出现哪些成本元素。

        无交付方案时返回 None，表示按「全部定制开发」计 —— 这是 P4 的行为，
        保留以兼容尚未指定交付方式的快速估算。
        """
        if self.delivery is None:
            return None
        mode = self.delivery.assigned.get(item.id)
        if mode is None:
            return set()
        return set(self.delivery.modes.get(mode, {}).get("construction", []))

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
            if self.deal.scope_filter is not None:
                ok, why = self.deal.scope_filter(i)
                if not ok:
                    excluded[f"范围外：{why}"] += 1
                    continue
            kept.append(i)
        return kept, dict(excluded)

    def _out_of_scope_systems(self) -> list[dict[str, Any]]:
        """被范围排除掉的子系统 —— 供清单「列而不计」。

        只报**整个子系统**被排除的，不逐条列 1360 条。范围的选择单元就是子系统，
        呈现也该是子系统。
        """
        if self.deal.scope_filter is None:
            return []
        by_sys: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for i in snapshot(self.bom, self.deal.as_of_bom_version):
            ok, _ = self.deal.scope_filter(i)
            by_sys[i.path.system][0 if ok else 1] += 1
        return [{"system": s, "items": n[1]}
                for s, n in sorted(by_sys.items()) if n[0] == 0 and n[1] > 0]

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
            ce = self._construction_elements(i)
            if ce is not None and "CE-DEV" not in ce:
                continue          # 订阅/买断形态不走功能点法
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
                    reuse_level=self.reuse_level(i),
                    app_type=i.app_type or "业务处理")
            afp = xlround(afp, 2)
            effort = self.profile.effort_man_months(afp, pack=self.pack)
            pf_val, _ = self.project_factor()
            if pf_val != 1.0:
                effort = xlround(effort * pf_val, 2)
            rate = self.profile.man_month_rate(pack=self.pack, dev_category=dev_cat)
            out.append(SystemCost(
                system=system, dev_category=dev_cat, items=len(members),
                ufp=ufp, afp=afp, effort_man_months=effort,
                man_month_rate=rate, cost=xlround(effort * rate, 2),
                by_type=dict(by_type)))
        return out

    # ---- 硬件 ----

    def hardware(self, items: list[BomItem]) -> dict[str, Any]:
        """硬件购置费。

        无单价的**列而不计**，与购置科目同一处理 —— `(price or 0) * qty`
        会让一条待选型的 GPU 服务器悄悄贡献 ¥0，而清单上看不出它是
        「没有这项」还是「价格没到位」。这两种状态在报表上必须能区分。
        """
        rows, total = [], 0.0
        pending: list[dict[str, Any]] = []
        for i in items:
            if i.cls != "HARDWARE":
                continue
            ce = self._construction_elements(i)
            if ce is not None and "CE-HW" not in ce:
                continue
            price = i.spec.get("reference_unit_price_yuan")
            qty = i.spec.get("qty")
            amount = (price or 0) * (qty or 0)
            if price is None or qty is None:
                pending.append({"id": i.id, "name": i.name,
                                "missing": [k for k, v in
                                            (("单价", price), ("数量", qty)) if v is None],
                                "note": i.spec.get("pricing_basis", "待询价")})
            else:
                total += amount
            rows.append({"id": i.id, "name": i.name, "qty": qty,
                         "unit": i.spec.get("unit", ""),
                         "reference_unit_price": price,
                         "amount": xlround(amount, 2) if price is not None and qty is not None else None,
                         "pricing_basis": i.spec.get("pricing_basis", "")})
        ev = self.pack.data.get("procurement_evidence", {}).get("hardware", {})
        need_quote = [r for r in rows
                      if (r["reference_unit_price"] or 0) >= ev.get("unit_price_threshold", 1e18)]
        return {"rows": rows, "total": xlround(total, 2),
                "pending": pending,
                "quotes_required_count": len(need_quote),
                "quotes_required_rule": (
                    f"单价 ≥{ev.get('unit_price_threshold')} 元或单一类型总价 "
                    f"≥{ev.get('category_total_threshold')} 元需 "
                    f"{ev.get('quotes_required')} 家盖章询价单")}

    # ---- 采购科目（软件产品 / 数据资源与数据模型）----

    #: 这两个成本元素都表示「本条目在建设期按购置计价」。
    #: 落到哪个财评科目由条目自己的 spec.subject 决定 —— 成本元素回答
    #: 「怎么算」，科目回答「记到哪一行」，两件事不该合并。
    PURCHASE_ELEMENTS = {"CE-LIC", "CE-DATA"}

    def purchases(self, items: list[BomItem]) -> dict[str, Any]:
        """按财评科目汇总购置条目。

        无单价的**不静默计 0**，单列「待询价」—— 财评看到 ¥0 会当成没有这项，
        看到「待询价 13 项」才知道是价格没到位。这两种状态必须能区分。
        """
        groups: dict[str, dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []

        for i in items:
            subject = i.spec.get("subject")
            if not subject:
                continue
            ce = self._construction_elements(i)
            if ce is not None and not (ce & self.PURCHASE_ELEMENTS):
                mode = self.delivery.assigned.get(i.id) if self.delivery else None
                skipped.append({"id": i.id, "name": i.name, "subject": subject,
                                "mode": mode,
                                "reason": ("本方案不采用此形态" if mode == "D0"
                                           else f"形态 {mode} 的建设期不含购置元素"),
                                # 带上 raw-input 参照值 —— 与旧清单对不上时，
                                # 差额要能归因到「这批标的换了交付形态」，
                                # 而不是含糊说一句「口径不同」
                                "legacy_reference_yuan":
                                    (i.legacy_quote or {}).get("quote_yuan")})
                continue

            price = i.spec.get("reference_unit_price_yuan")
            qty = i.spec.get("qty") or 0
            # 举证句算一次，明细行与「待询价」节共用。此前两处各取一次
            # spec.pricing_basis，改了一处另一处照旧 —— 同一条标的在同一份
            # 清单里给出两种举证要求。
            basis = (i.spec.get("pricing_basis", "")
                     if not str(i.spec.get("pricing_basis", "")).startswith("TODO(询价)")
                     else self.pack.evidence_sentence(
                         self.pack.evidence_for_subject(subject), subject))
            row = {"id": i.id, "name": i.name, "system": i.path.system,
                   "subject_detail": i.spec.get("subject_detail", ""),
                   "pricing_model": i.spec.get("pricing_model", ""),
                   "unit": i.spec.get("unit", ""), "qty": qty,
                   "reference_unit_price": price,
                   "amount": xlround((price or 0) * qty, 2),
                   # 举证要求由**标准包**按科目给出，不用条目里那句复制文本 ——
                   # 那句写的是软件产品/硬件的要求（三家不同品牌+加盖公章），
                   # 而数据模型要的是人月工作量举证。换省时要求也不同。
                   "pricing_basis": basis,
                   "evidence_required": i.spec.get("evidence_required", []),
                   "legacy_reference_yuan": (i.legacy_quote or {}).get("quote_yuan")}
            g = groups.setdefault(subject, {"subject": subject, "rows": [],
                                            "total": 0.0, "pending_count": 0,
                                            "legacy_reference_total": 0})
            g["rows"].append(row)
            if price is None:
                g["pending_count"] += 1
                pending.append({"id": i.id, "name": i.name, "subject": subject,
                                "note": basis or "待询价",
                                "legacy_reference_yuan": row["legacy_reference_yuan"]})
            else:
                g["total"] += row["amount"]
            g["legacy_reference_total"] += row["legacy_reference_yuan"] or 0

        for g in groups.values():
            g["total"] = xlround(g["total"], 2)

        ev = self.pack.data.get("procurement_evidence", {})
        return {
            "by_subject": [groups[k] for k in sorted(groups)],
            "total": xlround(sum(g["total"] for g in groups.values()), 2),
            "pending": pending,
            "not_applicable": skipped,
            "evidence_rules": {
                "software_product": ev.get("software_product", {}).get("citation"),
                "data_model": ev.get("data_model", {}).get("citation"),
            },
        }

    @staticmethod
    def subject_total(purchases: dict[str, Any], code: str) -> float:
        """按科目号取小计 —— 其他费用的计费基数要分科目，不能一锅端。"""
        return xlround(sum(g["total"] for g in purchases["by_subject"]
                           if g["subject"].startswith(code)), 2)

    # ---- 实施集成 ----

    def implementation(self) -> dict[str, Any]:
        """CE-IMPL —— 各交付形态的建设期实施投入。

        SaaS 形态的建设期几乎只有这一项：不用建，但要接入配置。
        走 CE-DEV 的形态其实施投入含在系统集成费里，此处不重复计。
        """
        if self.delivery is None:
            return {"rows": [], "total": 0.0}
        impl = self.delivery.internal.get("implementation", {})
        rows, total = [], 0.0
        for mode in sorted(set(self.delivery.assigned.values())):
            spec = self.delivery.modes.get(mode, {})
            if "CE-IMPL" not in spec.get("construction", []):
                continue
            if "CE-DEV" in spec.get("construction", []):
                continue          # 实施含在系统集成费，不重复计
            cfg = impl.get(mode, {})
            amount = float(cfg.get("amount", 0))
            if not amount:
                continue
            total += amount
            # 科目落位：从形态的 region_key 经标准包解析。没有落点的**要说出来**
            # —— 一笔进了 construction_total 却没有科目的钱，评审加不平账。
            key = spec.get("region_key")
            sup = self.pack.delivery_support(key) if key else {}
            rows.append({"mode": mode, "mode_name": spec.get("name", mode),
                         "amount": amount,
                         "subject": sup.get("subject") or "",
                         "subject_status": sup.get("status") or "unmapped",
                         # basis 走的是 internal-cost-model 的人天单价 ——
                         # **对外产物不得写这个数**，只对内做毛利核算用。
                         "basis": (f"{cfg.get('man_days')} 人天 × "
                                   f"¥{cfg.get('rate_per_day')}/人天"
                                   if cfg.get("man_days") else cfg.get("basis", "")),
                         "man_days": cfg.get("man_days"),
                         "note": cfg.get("note", "")})
        return {"rows": rows, "total": xlround(total, 2)}

    # ---- 待核价项 ----

    def pending_pricing(self, items: list[BomItem]) -> list[dict[str, Any]]:
        """走 CE-LIC 却**连采购条目都没有**的系统 —— 显式列为待核价。

        有 spec.subject 的条目由 purchases() 逐条列出待询价，这里不重复报。
        本方法剩下的职责是兜底：某个系统被指派了买断形态，但 BOM 里根本
        没建对应的采购标的 —— 那是漏建条目，比缺价格严重。
        """
        if self.delivery is None:
            return []
        out = []
        seen = set()
        for i in items:
            if i.spec.get("subject"):
                continue          # 已由 purchases() 逐条列明
            ce = self._construction_elements(i) or set()
            if "CE-LIC" not in ce:
                continue
            key = (i.path.system, self.delivery.assigned.get(i.id))
            if key in seen:
                continue
            seen.add(key)
            out.append({"system": i.path.system, "mode": self.delivery.assigned.get(i.id),
                        "element": "CE-LIC",
                        "note": "该系统按买断形态交付，但 BOM 中无对应的采购标的条目 —— 缺的是条目不是价格，须先补 class=PRODUCT/MODEL 的采购条目"})
        return out

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
                "rate": spec.get("max_rate"),
                # 分档明细：让清单能把「超额累进」摊开成 300×2%+200×1.6%+…
                # 而不是只写四个字。评审要能自己加出这个数。
                "breakdown": (self._progressive_steps(base_wan, spec["table"],
                                                      amount_wan, spec["name"])
                              if spec["method"] == "progressive" else None),
                "amount_wan": amount_wan,
                "amount_yuan": xlround(amount_wan * 10000, 2),
                "blocked_reason": blocked,
                "citation": spec.get("citation"),
            })
        return out

    @staticmethod
    def _progressive_steps(base_wan: float, table: list, total_wan: float,
                           name: str) -> list[dict[str, Any]]:
        """把超额累进摊成逐档。

        这里**重走了一遍 `profile.progressive` 的分档逻辑** —— 两处实现分叉的话，
        印出来的分解就会和总额对不上，而分解正是给评审自己加的。
        所以算完立刻与 profile 的结果比对，对不上直接报错。
        """
        steps, low = [], 0.0
        for cap, rate in table:
            hi = base_wan if cap is None else min(base_wan, cap)
            if hi > low:
                steps.append({"from_wan": low, "to_wan": hi, "rate": rate,
                              "amount_wan": xlround((hi - low) * rate, 4)})
                low = hi
            if cap is not None and base_wan <= cap:
                break
        got = xlround(sum(s["amount_wan"] for s in steps), 2)
        if abs(got - total_wan) > 0.01:
            raise ValueError(
                f"{name}：分档累加 {got} 万 ≠ 引擎算出的 {total_wan} 万。\n"
                f"  分档明细是印给评审自己加的，加不出总额就是在给错误的依据。"
                f"  检查 _progressive_steps 是否与 formula_profile.progressive 分叉。")
        return steps

    # ---- 合成 ----

    def run(self) -> dict[str, Any]:
        items, excluded = self.in_scope()
        sys_costs = self.software_dev(items)
        hw = self.hardware(items)
        pur = self.purchases(items)
        impl = self.implementation()
        pending = self.pending_pricing(items)

        sw_total = xlround(sum(s.cost for s in sys_costs), 2)
        dev_cats = {s.dev_category for s in sys_costs}
        # 成品软件集成费与第三方测试费的基数只含**软件产品购置费**（三(二)1）；
        # 数据资源和服务购置费（三(二)2）不在任何一项其他费用的基数里 ——
        # 山东标准 表4/表5 的基数定义就是这样，别顺手加进去。
        sw_purchase = self.subject_total(pur, "三(二)1")
        fees = self.other_fees(sw_total, hw["total"],
                               software_purchase_total=sw_purchase,
                               dev_categories=dev_cats)
        fee_total = xlround(sum(f["amount_yuan"] for f in fees), 2)

        return {
            "deal": self.deal.deal_id,
            "bom_version": self.deal.as_of_bom_version or self.bom.version,
            "pack_id": self.pack.pack_id,
            "formula_profile": self.pack.formula_profile,
            "counting_method": self.deal.counting_method,
            "project_type": self.deal.project_type,
            "reuse_level": self.deal.reuse_level,
            "scope": {"items": len(items), "excluded": excluded,
                      "out_of_scope_systems": self._out_of_scope_systems()},
            "productivity_band": self._band(sys_costs),
            "band_note": (None if self.pack.productivity_band() else
                          "本标准未规定生产率浮动区间，按 P50 单值计 —— 不替标准编造区间"),
            "project_factors": ({"id": self.deal.project_factors.get("id"),
                                 "source": self.deal.project_factors.get("source"),
                                 "combined": self.project_factor()[0],
                                 "rows": self.project_factor()[1],
                                 "warning": self.deal.project_factors.get("warning")}
                                if self.deal.project_factors else None),
            "software_dev": {
                "systems": [vars(s) for s in sys_costs],
                "ufp_total": sum(s.ufp for s in sys_costs),
                "afp_total": xlround(sum(s.afp for s in sys_costs), 2),
                "effort_total": xlround(sum(s.effort_man_months for s in sys_costs), 2),
                "total": sw_total,
            },
            "hardware": hw,
            "purchases": pur,
            "implementation": impl,
            "pending_pricing": pending,
            "other_fees": fees,
            "delivery": ({"modes_used": sorted(set(self.delivery.assigned.values()))}
                         if self.delivery else None),
            "totals": {
                "software_dev": sw_total,
                "software_purchase": sw_purchase,
                "data_purchase": self.subject_total(pur, "三(二)2"),
                "hardware_purchase": hw["total"],
                "implementation": impl["total"],
                "other_fees": fee_total,
                "construction_total": xlround(
                    sw_total + pur["total"] + hw["total"]
                    + impl["total"] + fee_total, 2),
                "pending_pricing_count": len(pur["pending"]) + len(pending),
            },
        }


def lock(result: dict[str, Any], bom: Bom, pack: StandardPack,
         deal: DealConfig, baseline_lock: dict[str, Any] | None = None,
         delivery_info: dict[str, Any] | None = None,
         deal_file: dict[str, Any] | None = None,
         doc_info: dict[str, Any] | None = None) -> dict[str, Any]:
    """版本锁 —— 凭此可精确重算本次报价。

    **此前它记不全，所以其实复现不了。** 缺的四项里最要命的是交付形态指派 ——
    那是第三层最大的杠杆：同一份 BOM 与基准，换个交付方案，金额和待询价项数
    都会变（实测 C→A：+¥80,000，57 个模型从「不采用」变成 66 项待询价）。
    lock 里没有它，「凭此可精确重算」就是一句空话。
    """
    out = {
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
        # 交付形态指派：记形态分布与方案名。逐条目 1851 条太长，
        # 分布 + 方案文件哈希已足以判定「是不是同一套指派」。
        "delivery": delivery_info,
        "project_factors": ({"id": (deal.project_factors or {}).get("id"),
                             "selected": (deal.project_factors or {}).get("selected")}
                            if deal.project_factors else None),
        "as_of": deal.as_of_bom_version,
        "doc": doc_info,
        # 商机配置文件的**内容哈希**，不是路径 —— 改了文件而路径不变，
        # 只记路径的 lock 会悄悄失效。与基准锁哈希输入文件同一条纪律。
        "deal_file": deal_file,
        "totals": result["totals"],
        # 引第二层的锁，形成 deal → baseline → BOM 的链。
        # 没有它，「这份报价基于哪个基准」只能靠 pack_id 猜 —— 而标准包改一个
        # 系数、pack_id 不变，基准就悄悄过期了。
        "baseline": ({"kind": baseline_lock.get("kind"),
                      "generated": baseline_lock.get("generated"),
                      "bom_sha256_16": baseline_lock["bom"]["items_sha256_16"],
                      "pack_sha256_16": baseline_lock["standard_pack"]["sha256_16"],
                      "totals": baseline_lock.get("totals")}
                     if baseline_lock else None),
    }
    # reproduce 放最后生成 —— 它要引用上面已经填好的字段。
    # 此前这行写着 `--deal <deal.yaml>`，而那个参数**根本不存在**，
    # 等于在指导别人跑一条跑不通的命令。
    df = (deal_file or {}).get("path")
    out["reproduce"] = (
        f"python3 quote_generate.py --deal {df}" if df else
        "python3 quote_generate.py --baseline <baselines/…> --out <deals/…> "
        f"--deal-id {deal.deal_id!r}"
        + (f" --as-of {deal.as_of_bom_version}" if deal.as_of_bom_version else ""))
    return out
