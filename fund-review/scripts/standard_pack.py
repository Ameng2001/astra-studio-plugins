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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from nesma_weights import xlround

#: 这些段落下的叶子节点必须能溯源到 citation
CITATION_REQUIRED = ("fp_counting", "factors", "rates", "other_fees",
                     "procurement_evidence", "fp_exclusions")


class PackError(ValueError):
    """标准包结构性错误 —— 必须修，不接受降级运行。"""


@dataclass
class Citation:
    page: int | str
    section: str | None = None
    quote: str | None = None
    #: 出自哪一分册。广东是「一个总则 + 四个各自迭代的分册」，
    #: 页码只有配上分册名才唯一 —— 「p.10」在运维分册和基础设施分册是两处。
    #: 此前这个字段没有，`pack.value()` 打到任何带 volume 的路径直接 TypeError；
    #: 没人碰到只是因为没人对分册里的取值调过 value()。
    volume: str | None = None

    def __str__(self) -> str:
        s = f"p.{self.page}"
        if self.section:
            s += f" {self.section}"
        if self.volume:
            s = f"《{self.volume}》{s}"
        return s


class StandardPack:
    def __init__(self, data: dict[str, Any], root: Path | None = None) -> None:
        self.data = data
        self.root = root
        self.pack_id: str = data["pack_id"]
        self.formula_profile: str = data["formula_profile"]

    # ---- 加载与校验 ----

    @classmethod
    def load(cls, path: Path, allow_deprecated: bool = False) -> "StandardPack":
        """加载标准包。已退役的包默认拒绝加载。

        退役的包**不删文件** —— 历史报价的 deal.lock.json 锁着它的 pack_id，
        删了历史就复算不出来了。但也不能让它被随手用于新报价：
        广东那个旧包只有软件开发一册，四个科目被判成 not_in_scope，
        拿它出新报价等于把「我们没读到」说成「广东不允许」。

        所以是「保留 + 拒用」：默认报错并指向继任者，历史复算显式传
        `allow_deprecated=True` 放行。光在 yaml 里写一句 deprecated 没有用 ——
        没人会去读那一句。
        """
        path = Path(path)
        p = path / "pack.yaml" if path.is_dir() else path
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        dep = data.get("deprecated")
        if dep and not allow_deprecated:
            raise PackError(
                f"{data.get('pack_id')} 已于 {dep.get('since')} 退役，"
                f"请改用 {dep.get('superseded_by')}。\n"
                f"  退役原因：{str(dep.get('reason', '')).strip().splitlines()[0]}\n"
                f"  仍适用于：{'；'.join(dep.get('still_valid_for') or [])}\n"
                f"  历史复算请显式传 allow_deprecated=True / --allow-deprecated-pack")
        pack = cls(data, p.parent)
        pack.validate()
        pack.deprecated = dep
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
        """取调整因子，经 aliases 解析 BOM 词表 → 本区域词表。

        各地的分类词表并不一致 —— 山东「应用类型」7 类，广东「软件类别」4 类，
        同一概念山东叫「智能信息」、广东叫「人工智能」。BOM 存的是规范词表，
        由各标准包用 aliases 映射到自己的词表。**这是「换省 BOM 零改动」
        得以成立的机制**；没有它，那个说法就是假的。

        无对应取值时**直接报错，不静默兜底** —— 广东没有「基础软件、支撑软件」
        这一类，静默取 1.0 会让报价少算而没人发现。
        """
        node = self.data.get("factors", {}).get(group, {})
        values = node.get("values", {})
        resolved = node.get("aliases", {}).get(key, key)
        if resolved not in values:
            unmapped = node.get("unmapped", [])
            hint = ""
            if key in unmapped:
                hint = (f"　「{key}」在本标准中无对应类别（{node.get('unmapped_note', '')}"
                        .strip().splitlines()[0] + "）。需业务侧显式裁定归入哪一类")
            raise PackError(
                f"{self.pack_id}: factors.{group} 无取值 {key!r}"
                f"{f'（别名解析为 {resolved!r}）' if resolved != key else ''}；"
                f"可选：{sorted(values)}{hint}")
        return values[resolved]

    def factor_label(self, group: str, key: str) -> str:
        """本区域标准中该分类的实际名称 —— 编制说明要用标准自己的词。"""
        node = self.data.get("factors", {}).get(group, {})
        return node.get("aliases", {}).get(key, key)

    #: 科目号前缀 → procurement_evidence 的键。举证要求是**标准的属性**，
    #: 不是标的的属性 —— 同一个模型在山东要人月工作量举证、在广东按成品软件
    #: 租赁询价，要求完全不同。写死在 BOM 条目里就等于把区域规则塞进了
    #: 地域无关的那一层。
    _EVIDENCE_BY_SUBJECT = [
        ("三.(二)2", "data_model"), ("三(二)2", "data_model"),
        ("三.(二)1", "software_product"), ("三(二)1", "software_product"),
        ("三.(三)", "hardware"), ("三(三)", "hardware"),
    ]

    def evidence_for_subject(self, subject: str | None) -> dict[str, Any] | None:
        """该科目在本标准下的询价举证要求。认不出科目返回 None。

        返回 None 与返回空 dict 是两回事：前者是「本标准没规定」，
        后者会被误读成「不需要举证」。调用侧必须区分。
        """
        if not subject:
            return None
        ev = self.data.get("procurement_evidence") or {}
        for prefix, key in self._EVIDENCE_BY_SUBJECT:
            if subject.startswith(prefix) and key in ev:
                return {**ev[key], "_key": key}
        return None

    @staticmethod
    def evidence_sentence(ev: dict[str, Any] | None, subject: str | None) -> str:
        """把举证要求渲染成一句给商务看的话。

        此前 70 条采购标的的 `pricing_basis` 是同一句复制文本
        「三家同级别不同品牌厂商加盖公章的询价报价单」—— 那是
        **软件产品/硬件**的要求。数据模型在山东既不要求加盖公章、也不按品牌，
        它要的是「预计搭建模型所需投入工作量（采用人月计量）」。
        让商务去为自研模型找三家不同品牌报价，是让人做一件标准没要求、
        且做不到的事。
        """
        if ev is None:
            return (f"TODO(询价)：本标准未规定「{subject}」的询价举证要求，"
                    f"须向主管部门确认" if subject else "TODO(询价)：科目未定")
        bits = [f"须提供 {ev.get('quotes_required', 3)} 份询价报价单"]
        if ev.get("sealed"):
            bits.append("**加盖公章**")
        fields = ev.get("required_fields") or []
        if fields:
            bits.append("应载明：" + "、".join(fields))
        return "TODO(询价)：" + "；".join(bits)

    def fp_weight(self, method: str, ftype: str) -> int:
        weights, _ = self.value(f"fp_counting.{method}.weights")
        if ftype not in weights:
            raise PackError(f"{self.pack_id}: {method} 无权重 {ftype!r}")
        return weights[ftype]

    def productivity_band(self) -> list[tuple[str, float]] | None:
        """生产率的三档取值，无区间依据时返回 None。

        单点值等于假装精确 —— 样例表的上限是下限的 3.1 倍，这个跨度本身就是信息。
        但**不能给每个省都硬造区间**：

          广东  `adjustable_range: [0.8, 1.2]`  标准明文 ±20%，有依据
          山东  只给 CSBMK P50 单值 6.71        全文无「浮动/区间/P25/P75」

        给山东编一个区间，等于替标准做规定。所以有 range 的出三档，
        没有的返回 None，由调用方出单点并在编制说明写明「本标准未规定浮动区间」。
        """
        node = (self.data.get("rates") or {}).get("productivity_hours_per_fp") or {}
        rng = node.get("adjustable_range")
        if not rng:
            return None
        base = node["value"]
        lo, hi = float(rng[0]), float(rng[1])
        # 生产率是「人时/功能点」—— **越大越费工**，所以低生产率对应低报价。
        # 顺序按金额升序排，免得读的人把「下限」当成生产率下限。
        return [("下限", base * lo), ("中值", base), ("上限", base * hi)]

    def fee(self, fee_id: str) -> dict[str, Any]:
        for f in self.data.get("other_fees", []):
            if f["id"] == fee_id:
                return f
        raise PackError(f"{self.pack_id}: 无费用项 {fee_id}")

    def vocabulary_coverage(self, vocab) -> dict[str, dict[str, Any]]:
        """本包对 BOM 规范词表的覆盖情况。

        `factor()` 在取不到值时报错报得很清楚，但那是**算到那一条才报**，
        一次一条。做梅州项目时不该在生成第 800 行报价时才发现「通信控制」
        这个省没有 —— 应该在选定标准包的那一刻就知道缺口有多大。

        三种状态：
          ok        本包有取值，或有别名指向本包的取值
          unmapped  本包**显式声明**无对应类别（带 unmapped_note）—— 这是
                    已知缺口，不是遗漏；用到时须业务侧裁定归入哪一类
          missing   既无取值也无声明 —— 真正的遗漏，接新省份时最该先补这个
          n/a       本包没有这个维度（如广东无 dev_category）
        """
        out: dict[str, dict[str, Any]] = {}
        for group in ("app_type", "dev_category"):
            allowed = vocab.values(group)
            if not allowed:
                continue
            node = self.data.get("factors", {}).get(group) or {}
            values = node.get("values") or {}
            if not values:
                out[group] = {"status": "n/a", "note": node.get("note", "本包无此维度"),
                              "ok": [], "unmapped": [], "missing": []}
                continue
            aliases = node.get("aliases") or {}
            declared = set(node.get("unmapped") or [])
            ok, unmapped, missing = [], [], []
            for key in allowed:
                if aliases.get(key, key) in values:
                    ok.append(key)
                elif key in declared:
                    unmapped.append(key)
                else:
                    missing.append(key)
            out[group] = {"status": "incomplete" if missing else "ok",
                          "ok": ok, "unmapped": unmapped, "missing": missing,
                          "unmapped_note": node.get("unmapped_note", "")}
        return out

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
        return sorted(out, key=lambda c: (_page_key(c.get("page")), c["path"]))


def _page_key(page: Any) -> tuple[int, str]:
    """页码排序键。

    页码可能是 int（`page: 6`）也可能是区间字符串（`page: '24-38'`，
    跨页的表）。直接比较会在混用时抛 TypeError —— 广东合并包里
    运维分册用了 `5-6`，一加进来 citations_index 就崩。
    取区间起始页排序，取不到就排到最后。
    """
    if isinstance(page, int):
        return (page, "")
    s = str(page or "")
    m = re.match(r"\s*(\d+)", s)
    return (int(m.group(1)) if m else 10 ** 6, s)


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
        elif "expr" in given:
            # 标准正文自带的推导式，如广东运维服务分册 p.10：
            #   运维功能点单价 = 20000 / 174 × 1.04 = 119.54 元/功能点
            # 这类算例不走费用科目，而是**直接验一条算式**。此前框架只认
            # fee_id，于是这种算例根本没法登记 —— 广东 regression 一直是空的，
            # 「验证通过」只验了 citation 完整性，数值一条没验。
            #
            # 取值从 `inputs` 的 dotted path 到包里取，**不在用例里重写一遍数字**：
            # 用例里抄一份就成了第二处真相，改了包不改用例，回归照样绿。
            env = {k: pack.value(dot)[0] if isinstance(dot, str) else dot
                   for k, dot in (given.get("inputs") or {}).items()}
            bad = [k for k, v in env.items() if not isinstance(v, (int, float))]
            if bad:
                raise PackError(
                    f"回归用例 {case['id']} 的输入 {bad} 不是数值 —— "
                    f"inputs 里写的是包内 dotted path，取出来必须能算")
            try:
                actual = eval(given["expr"], {"__builtins__": {}}, dict(env))
            except Exception as e:
                raise PackError(
                    f"回归用例 {case['id']} 的 expr 算不出来：{e}\n"
                    f"  expr={given['expr']!r}　可用变量={sorted(env)}")
            actual = xlround(float(actual), given.get("digits", 2))
        else:
            raise PackError(
                f"回归用例 {case['id']} 的 given 形态未支持；"
                f"现支持 {{fee_id, base_wan}} 或 {{expr, inputs[, digits]}}")
        ok = abs(actual - case["expect"]) < 1e-9
        results.append({"id": case["id"], "expect": case["expect"],
                        "actual": actual, "ok": ok,
                        "citation": case.get("citation")})
    return results
