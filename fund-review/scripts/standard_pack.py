"""standard_pack — 区域标准包的加载、校验与公式档案分派。

标准包只放「换个省会变」的东西：系数、费率、科目、举证要求、负面清单。
功能点清单在 BOM，交付决策在 deal —— 三者正交。

**硬约束：每一个数值都必须带 citation。** 加载时校验，缺 citation 直接报错。
财评答辩要逐条可查，一个查不到出处的系数会拖垮整份报价的可信度。

公式档案（formula_profile）不做「万能公式 + 系数置 1」：
山东有规模变更因子与开发类别系数，广东两者都没有，且复用系数作用在不同环节。
强行统一会让广东报价虚高 21%。每个 profile 是一段显式、可审计的计算步骤。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: 这些段落下的叶子节点必须能溯源到 citation
CITATION_REQUIRED = ("fp_counting", "factors", "rates", "other_fees",
                     "procurement_evidence", "fp_exclusions")


class PackError(ValueError):
    """标准包结构性错误 —— 必须修，不接受降级运行。"""


@dataclass
class Citation:
    page: int
    section: str | None = None
    quote: str | None = None

    def __str__(self) -> str:
        s = f"p.{self.page}"
        if self.section:
            s += f" {self.section}"
        return s


class StandardPack:
    def __init__(self, data: dict[str, Any], root: Path | None = None) -> None:
        self.data = data
        self.root = root
        self.pack_id: str = data["pack_id"]
        self.formula_profile: str = data["formula_profile"]

    # ---- 加载与校验 ----

    @classmethod
    def load(cls, path: Path) -> "StandardPack":
        path = Path(path)
        p = path / "pack.yaml" if path.is_dir() else path
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        pack = cls(data, p.parent)
        pack.validate()
        return pack

    def validate(self) -> None:
        for key in ("pack_id", "formula_profile", "standard_doc"):
            if not self.data.get(key):
                raise PackError(f"缺必填字段 {key}")
        missing = self._find_uncited()
        if missing:
            raise PackError(
                f"{self.pack_id}: {len(missing)} 处数值缺 citation —— "
                f"财评要逐条可查，不接受无出处的取值：\n  " + "\n  ".join(missing[:15]))

    def _find_uncited(self) -> list[str]:
        """递归找出 CITATION_REQUIRED 段落里够不到 citation 的数值节点。"""
        out: list[str] = []

        def walk(node: Any, path: str, cited: bool) -> None:
            if isinstance(node, dict):
                cited = cited or "citation" in node
                for k, v in node.items():
                    if k in ("citation", "note", "quote", "ranges"):
                        continue
                    walk(v, f"{path}.{k}" if path else k, cited)
            elif isinstance(node, list):
                for n, v in enumerate(node):
                    walk(v, f"{path}[{n}]", cited)
            elif isinstance(node, (int, float)) and not isinstance(node, bool):
                if not cited:
                    out.append(path)

        for seg in CITATION_REQUIRED:
            if seg in self.data:
                walk(self.data[seg], seg, False)
        return out

    # ---- 取值（一律连 citation 一起返回，杜绝裸数字流入报价） ----

    def value(self, dotted: str) -> tuple[Any, Citation]:
        """按点路径取值，同时返回最近的 citation。"""
        node: Any = self.data
        cite: dict[str, Any] | None = None
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                raise PackError(f"{self.pack_id}: 路径不存在 {dotted}")
            if "citation" in node:
                cite = node["citation"]
            node = node[part]
        if isinstance(node, dict):
            if "citation" in node:
                cite = node["citation"]
            if "value" in node:
                node = node["value"]
        if cite is None:
            raise PackError(f"{self.pack_id}: {dotted} 无 citation")
        return node, Citation(**cite)

    def rate(self, name: str) -> float:
        return self.value(f"rates.{name}")[0]

    def factor(self, group: str, key: str) -> float:
        values, _ = self.value(f"factors.{group}.values")
        if key not in values:
            raise PackError(
                f"{self.pack_id}: factors.{group} 无取值 {key!r}；"
                f"可选：{sorted(values)}")
        return values[key]

    def fp_weight(self, method: str, ftype: str) -> int:
        weights, _ = self.value(f"fp_counting.{method}.weights")
        if ftype not in weights:
            raise PackError(f"{self.pack_id}: {method} 无权重 {ftype!r}")
        return weights[ftype]

    def fee(self, fee_id: str) -> dict[str, Any]:
        for f in self.data.get("other_fees", []):
            if f["id"] == fee_id:
                return f
        raise PackError(f"{self.pack_id}: 无费用项 {fee_id}")

    # ---- 合规检查 ----

    def delivery_support(self, mode: str) -> dict[str, Any]:
        """某交付形态在本区域的支持状态与科目落点。"""
        return self.data.get("delivery_mode_support", {}).get(
            mode, {"status": "unknown", "note": "本标准包未声明该交付形态"})

    def check_negative_list(self, item_text: str) -> list[dict[str, Any]]:
        """命中负面清单的条目 —— 命中即不得列入建设项目预算。"""
        return [n for n in self.data.get("negative_list", [])
                if any(kw in item_text for kw in _keywords(n["item"]))]

    def citations_index(self) -> list[dict[str, Any]]:
        """全部 citation 的索引，供报价书附录「条款引用索引」使用。"""
        out: list[dict[str, Any]] = []

        def walk(node: Any, path: str) -> None:
            if isinstance(node, dict):
                if "citation" in node:
                    out.append({"path": path, **node["citation"]})
                for k, v in node.items():
                    if k != "citation":
                        walk(v, f"{path}.{k}" if path else k)
            elif isinstance(node, list):
                for n, v in enumerate(node):
                    walk(v, f"{path}[{n}]")

        walk(self.data, "")
        return sorted(out, key=lambda c: (c.get("page", 0), c["path"]))


def _keywords(text: str) -> list[str]:
    """负面清单条目切成可匹配的关键词 —— 整句直接 in 匹配命中率太低。"""
    parts = [p.strip() for p in text.replace("、", "，").split("，")]
    return [p for p in parts if len(p) >= 3] or [text]


# ---- 公式档案分派 ------------------------------------------------------


def get_profile(name: str):
    """按名字取公式档案模块。新增区域 = 加一个 pack + 复用或新增一个 profile。"""
    import importlib

    try:
        return importlib.import_module(f"formula_profiles.{name}")
    except ModuleNotFoundError as e:
        raise PackError(f"未知公式档案 {name!r} —— "
                        f"需在 scripts/formula_profiles/ 下实现") from e


def run_regression(pack: StandardPack) -> list[dict[str, Any]]:
    """跑标准包自带的回归用例。改任何参数后必须重跑。"""
    profile = get_profile(pack.formula_profile)
    results = []
    for case in pack.data.get("regression", []):
        given = case["given"]
        if "fee_id" in given:
            actual = profile.other_fee(pack, given["fee_id"], given["base_wan"])
        else:
            raise PackError(f"回归用例 {case['id']} 的 given 形态未支持")
        ok = abs(actual - case["expect"]) < 1e-9
        results.append({"id": case["id"], "expect": case["expect"],
                        "actual": actual, "ok": ok,
                        "citation": case.get("citation")})
    return results
