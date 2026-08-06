"""delivery_matrix — 交付形态展开与一致性检查。

**不枚举场景。** 部署形态是 BOM 逐条目的属性，客户对 20 个模块做 20 个不同
选择都能算。最初提出的 5 个 SaaS/私有化场景其实是 2×2 矩阵（平台/模型 ×
私有化/SaaS）的格子，场景 5「应用私有化 + 模型 SaaS」是组合而非第五种模式。

交付方案用规则匹配而非逐条指定 —— 1500+ 条目手工指派不现实：
    defaults: 按 (class / system / tag) 匹配，先匹配到的先生效
    overrides: 按条目 id 精确覆盖
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bom_schema import BomItem
from standard_pack import StandardPack


@dataclass
class Violation:
    rule: str
    severity: str        # fail | warn
    message: str
    targets: list[str] = field(default_factory=list)


class DeliveryPlan:
    """把 BOM 条目映射到交付形态，并跑一致性规则。"""

    def __init__(self, modes: dict[str, Any], plan: dict[str, Any]) -> None:
        self.modes = modes["modes"]
        self.cost_elements = modes["cost_elements"]
        self.rules = {r["id"]: r for r in modes["rules"]}
        self.plan = plan
        #: 方案名 —— 进 deal.lock.json，让人一眼看出这份报价按哪套形态算的
        self.name: str | None = plan.get("name")

    @classmethod
    def load(cls, modes_path: Path, plan_path: Path) -> "DeliveryPlan":
        return cls(yaml.safe_load(Path(modes_path).read_text(encoding="utf-8")),
                   yaml.safe_load(Path(plan_path).read_text(encoding="utf-8")))

    # ---- 形态指派 ----

    def assign(self, items: list[BomItem]) -> dict[str, str]:
        """条目 id → 形态代码。overrides 优先，defaults 按顺序先匹配先生效。"""
        overrides = {o["id"]: o["mode"] for o in self.plan.get("overrides", [])}
        out: dict[str, str] = {}
        for i in items:
            if i.id in overrides:
                out[i.id] = overrides[i.id]
                continue
            for d in self.plan.get("defaults", []):
                if self._match(i, d.get("match", {})):
                    out[i.id] = d["mode"]
                    break
        return out

    def in_scope(self, item: BomItem) -> tuple[bool, str]:
        """本商机范围内？返回 (是否在内, 原因)。

        范围与交付形态**共用同一套 match 语法**，方案人员只学一次。
        选择单元是**子系统** —— 不引入「报价模块」：l1 在各子系统被切在不同
        语义高度上（82 个 l1 里 17% 只有 ≤5 条，数据建设整个只有 1 个），
        挂上范围后报价颗粒度会不可控。

        没写 scope 就是全量 —— 保持与切层前一致。
        """
        sc = self.plan.get("scope") or {}
        inc, exc = sc.get("include") or [], sc.get("exclude") or []
        for c in exc:
            if self._match(item, c):
                return False, f"被 exclude 规则排除：{c}"
        if not inc:
            return True, ""
        for c in inc:
            if self._match(item, c):
                return True, ""
        return False, "不在 include 规则内"

    @staticmethod
    def _match(item: BomItem, cond: dict[str, Any]) -> bool:
        if "cls" in cond and item.cls != cond["cls"]:
            return False
        if "system" in cond and item.path.system != cond["system"]:
            return False
        if "product_line" in cond and item.path.product_line != cond["product_line"]:
            return False
        if "tag" in cond and cond["tag"] not in item.tags:
            return False
        return True

    # ---- 一致性检查 ----

    def check(self, items: list[BomItem], assigned: dict[str, str],
              pack: StandardPack) -> list[Violation]:
        by_id = {i.id: i for i in items}
        out: list[Violation] = []

        unassigned = [i.id for i in items if i.id not in assigned]
        if unassigned:
            out.append(Violation("R-1", "fail",
                                 f"{len(unassigned)} 条条目未指派交付形态 —— "
                                 f"交付方案的 defaults 未覆盖全部条目",
                                 unassigned[:10]))

        # R-1 同一系统内 SaaS 与私有化混用需显式确认（跨系统混用是合法的场景5）
        by_system: dict[str, set[str]] = defaultdict(set)
        for iid, mode in assigned.items():
            if iid in by_id:
                by_system[by_id[iid].path.system].add(mode)
        SAAS, ONPREM = {"D3", "D5"}, {"D1", "D2", "D4"}
        for system, modes in by_system.items():
            if (modes & SAAS) and (modes & ONPREM):
                out.append(Violation(
                    "R-1", "fail",
                    f"系统「{system}」内同时存在 SaaS({sorted(modes & SAAS)}) 与"
                    f"私有化({sorted(modes & ONPREM)})形态 —— 同一系统只能择一，"
                    f"否则构成重复计列", [system]))

        # R-2 模型私有化必须配 GPU；模型 SaaS 不得出现 GPU
        has_gpu = any(i.cls == "HARDWARE" and
                      ("GPU" in i.name.upper() or "GPU" in str(i.spec).upper())
                      for i in items)
        d4 = [iid for iid, m in assigned.items() if m == "D4"]
        d5 = [iid for iid, m in assigned.items() if m == "D5"]
        if d4 and not has_gpu:
            out.append(Violation("R-2", "fail",
                                 f"{len(d4)} 条走模型私有化(D4)，但 BOM 中无 GPU 硬件条目 —— "
                                 f"私有化部署必须配套算力", d4[:5]))
        if d5 and has_gpu:
            out.append(Violation("R-2", "warn",
                                 f"{len(d5)} 条走模型 SaaS(D5)，但清单中含 GPU 硬件 —— "
                                 f"SaaS 的算力在服务方，请确认 GPU 是否另有用途"))

        # R-3 订阅期内不得再单列同系统运维费
        sub_systems = {by_id[iid].path.system for iid, m in assigned.items()
                       if m in SAAS and iid in by_id}
        for system in sub_systems:
            modes = by_system[system]
            if modes & {"D1", "D2"}:
                out.append(Violation("R-3", "fail",
                                     f"系统「{system}」已按订阅计费，不得再单列运维",
                                     [system]))

        # R-6 运营期条目必须有 payee 与 in_scope
        for mode_id in set(assigned.values()):
            spec = self.modes.get(mode_id)
            if not spec:
                out.append(Violation("R-1", "fail", f"未知交付形态 {mode_id}"))
                continue
            for op in spec.get("operation", []):
                if "payee" not in op or "in_scope" not in op:
                    out.append(Violation("R-6", "fail",
                                         f"形态 {mode_id} 的运营期元素 "
                                         f"{op.get('element')} 缺 payee 或 in_scope"))

        # R-7 maturity=existing 不宜走 D1
        bad = [iid for iid, m in assigned.items()
               if m == "D1" and iid in by_id and by_id[iid].maturity == "existing"]
        if bad:
            r7 = self.rules["R-7"]
            out.append(Violation("R-7", r7["severity"],
                                 f"{len(bad)} 条产品成熟度=已有产品 走 D1（按新开发报价）—— "
                                 f"{r7.get('note', '').strip().splitlines()[0]}", bad[:5]))

        # R-8 区域支持
        for mode_id in sorted(set(assigned.values())):
            spec = self.modes.get(mode_id) or {}
            key = spec.get("region_key")
            if not key:
                continue
            sup = pack.delivery_support(key)
            if sup.get("status") in ("forbidden", "not_in_scope"):
                n = sum(1 for m in assigned.values() if m == mode_id)
                out.append(Violation(
                    "R-8", "fail",
                    f"形态 {mode_id}（{spec['name']}，{n} 条）对应科目「{key}」"
                    f"在 {pack.pack_id} 中为 {sup['status']} —— "
                    f"{sup.get('note') or sup.get('reason')}", [mode_id]))
            elif sup.get("status") == "allowed_with_evidence":
                out.append(Violation(
                    "R-8", "warn",
                    f"形态 {mode_id}（{spec['name']}）需举证落位：{sup.get('note')}",
                    [mode_id]))
        return out

    def summary(self, items: list[BomItem], assigned: dict[str, str]) -> dict[str, Any]:
        by_id = {i.id: i for i in items}
        counts = Counter(assigned.values())
        detail: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for iid, m in assigned.items():
            if iid in by_id:
                detail[by_id[iid].path.system][m] += 1
        return {
            "by_mode": {m: {"name": self.modes[m]["name"], "items": n}
                        for m, n in sorted(counts.items()) if m in self.modes},
            "by_system": {k: dict(v) for k, v in sorted(detail.items())},
        }
