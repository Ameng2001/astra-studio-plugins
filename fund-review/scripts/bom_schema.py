"""bom_schema — 造价 BOM（唯一事实源 / TOS）的模型、序列化与版本比对。

BOM 回答「我们这套解决方案里有什么、每一项有多大」，**不回答**「值多少钱」。
- 区域相关参数（系数、费率、科目）→ standard-packs/{region}/pack.yaml
- 商机相关决策（交付形态、复用度取定）→ deals/{deal}/delivery-plan.yaml

分层判据：换个省会变的进标准包；换个客户会变的进交付方案；两者都不变的才进 BOM。

目录布局：
    bom/
      VERSION            # 语义化版本，如 0.9.0
      taxonomy.yaml      # 产品树 + 代码字典
      items/*.yaml       # 条目分片，按产品线拆分
      CHANGELOG.md
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

SCHEMA_VERSION = 1

# ---- 受控词表 ----------------------------------------------------------

#: 条目类别 —— 决定该条目**合法的造价方法**，不是业务分类。
ITEM_CLASSES = {
    "SOFTWARE_FP",  # 定制开发的功能 → 功能点法
    "PRODUCT",      # 整体转售的成品软件 → 购置询价 / 期限授权 / 订阅
    "MODEL",        # 作为整体交付的模型资产 → 数据模型购置 / 订阅
    "KB",           # 知识库 → 功能点法(数据功能) / 数据资源购置
    "DATASET",      # 数据集 → 数据资源购置 / 功能点法(治理加工)
    "HARDWARE",     # 硬件设备 → 硬件购置询价
    "SERVICE",      # 实施/集成/培训/设计/测试 → 费率法 / 人天法
}

#: 走功能点法计数的类别 —— 必须有 nesma 段
FP_COUNTED_CLASSES = {"SOFTWARE_FP", "KB", "DATASET"}

NESMA_TYPES = {"ILF", "ELF", "EI", "EO", "EQ"}

#: 产品成熟度 —— 产品事实，不是定价决策。
#: 复用度调整因子由「区域标准包 × 交付方案」共同解析，不在 BOM 里取值。
MATURITY = {
    "new",       # 没有，规划中 / 复用 0%
    "partial",   # 已有 demo / 需要迭代 / 复用 10%-80%
    "existing",  # 已有成熟版本 / 是 / 复用 100%
}

STATUSES = ["draft", "reviewed", "released", "deprecated"]
#: 条目必须达到 reviewed 才允许带 rationale 门禁，达到 released 才允许进报价
STATUS_ORDER = {s: i for i, s in enumerate(STATUSES)}

ID_RE = re.compile(r"^[A-Z]{2,6}(\.[A-Z0-9]{2,8})+\.\d{4}$")


class BomError(ValueError):
    """BOM 结构性错误 —— 与质量门禁（可警告可通过）区分开，这类必须修。"""


# ---- 模型 --------------------------------------------------------------


@dataclass
class Nesma:
    """NESMA 计数事实。只有 FP_COUNTED_CLASSES 的条目需要。"""

    type: str                        # ILF | ELF | EI | EO | EQ
    rationale: str | None = None     # 为何判定为该类型 —— reviewed 起必填
    counted_by: str | None = None
    reviewed_by: str | None = None   # 双人复核 —— released 起必填
    det_hint: int | None = None      # 数据元素个数，供后续升级到详细功能点法
    ret_hint: int | None = None

    def validate(self, item_id: str) -> None:
        if self.type not in NESMA_TYPES:
            raise BomError(f"{item_id}: nesma.type={self.type!r} 不在 {sorted(NESMA_TYPES)}")


@dataclass
class Path_:
    """产品树路径。层级数因产品线而异，允许 l2..l4 为空。

    l4 是为对齐《功能点计算书》的「四级模块」而加 —— 方案人员在飞书上
    可能用到第四级。BOM 自身导入只到三级，l4 默认为空。
    """

    product_line: str
    system: str
    l1: str | None = None
    l2: str | None = None
    l3: str | None = None
    l4: str | None = None

    def as_tuple(self) -> tuple[str, ...]:
        return tuple(x for x in (self.product_line, self.system,
                                 self.l1, self.l2, self.l3, self.l4) if x)


@dataclass
class Runtime:
    """运营期成本触发器 —— 交付方案据此展开 recurring 行。

    `evidence` 记录触发标记的原文关键词。自动识别只产出**候选**，
    人工复核后才应被当作事实 —— 这两个标记直接决定运营期是否出现
    推理算力费与外部大模型 API 费，误标会改变整张报价结构。
    """

    needs_inference_gpu: bool = False
    calls_external_llm: bool = False
    data_refresh: str | None = None   # none | monthly | quarterly | yearly
    evidence: list[str] = field(default_factory=list)
    reviewed: bool = False            # 人工复核过标记后置 true


@dataclass
class BomItem:
    id: str
    cls: str                          # 序列化为 "class"，避开 Python 关键字
    name: str
    path: Path_
    description: str = ""
    nesma: Nesma | None = None

    # 产品事实 —— 区域无关、交付无关
    app_type: str | None = None       # 对应各地「应用类型/软件类别」分类名，不含取值
    dev_category: str | None = None   # 对应山东表1 六类，不含系数
    maturity: str = "new"
    maturity_evidence: str | None = None
    runtime: Runtime = field(default_factory=Runtime)

    # 非 FP 条目的规格（硬件/成品软件）
    spec: dict[str, Any] = field(default_factory=dict)

    # 治理
    since: str = "0.9.0"
    deprecated_in: str | None = None
    status: str = "draft"
    source: str | None = None
    tags: list[str] = field(default_factory=list)

    # 仅供交叉校验，**不得**用于定价（见方案 §0.3 方法论倒挂）
    legacy_quote: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not ID_RE.match(self.id):
            raise BomError(f"{self.id!r}: id 不符合 命名空间.分段.4位序号 格式")
        if self.cls not in ITEM_CLASSES:
            raise BomError(f"{self.id}: class={self.cls!r} 不在 {sorted(ITEM_CLASSES)}")
        if self.maturity not in MATURITY:
            raise BomError(f"{self.id}: maturity={self.maturity!r} 不在 {sorted(MATURITY)}")
        if self.status not in STATUS_ORDER:
            raise BomError(f"{self.id}: status={self.status!r} 不在 {STATUSES}")
        if not self.name.strip():
            raise BomError(f"{self.id}: name 为空")

        needs_fp = self.cls in FP_COUNTED_CLASSES
        if needs_fp and self.nesma is None:
            raise BomError(f"{self.id}: class={self.cls} 走功能点法，但缺 nesma 段")
        if not needs_fp and self.nesma is not None:
            raise BomError(f"{self.id}: class={self.cls} 不走功能点法，不应有 nesma 段")
        if self.nesma:
            self.nesma.validate(self.id)

    # ---- 序列化 ----

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "class": self.cls, "name": self.name}
        d["path"] = {k: v for k, v in asdict(self.path).items() if v is not None}
        if self.description:
            d["description"] = self.description
        if self.nesma:
            d["nesma"] = {k: v for k, v in asdict(self.nesma).items() if v is not None}
        for key in ("app_type", "dev_category", "maturity_evidence", "source", "deprecated_in"):
            if getattr(self, key) is not None:
                d[key] = getattr(self, key)
        d["maturity"] = self.maturity
        rt = {k: v for k, v in asdict(self.runtime).items()
              if v not in (False, None, [])}
        if rt:
            d["runtime"] = rt
        if self.spec:
            d["spec"] = self.spec
        d["since"] = self.since
        d["status"] = self.status
        if self.tags:
            d["tags"] = self.tags
        if self.legacy_quote:
            d["legacy_quote"] = self.legacy_quote
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BomItem:
        nesma = Nesma(**d["nesma"]) if d.get("nesma") else None
        return cls(
            id=d["id"], cls=d["class"], name=d["name"],
            path=Path_(**d["path"]), description=d.get("description", ""),
            nesma=nesma, app_type=d.get("app_type"), dev_category=d.get("dev_category"),
            maturity=d.get("maturity", "new"), maturity_evidence=d.get("maturity_evidence"),
            runtime=Runtime(**d.get("runtime", {})), spec=d.get("spec", {}),
            since=d.get("since", "0.9.0"), deprecated_in=d.get("deprecated_in"),
            status=d.get("status", "draft"), source=d.get("source"),
            tags=d.get("tags", []), legacy_quote=d.get("legacy_quote", {}),
        )


class Bom:
    """一个 BOM 版本的全部条目。"""

    def __init__(self, version: str, items: Iterable[BomItem] = (),
                 taxonomy: dict[str, Any] | None = None) -> None:
        self.version = version
        self.items: list[BomItem] = list(items)
        self.taxonomy = taxonomy or {}

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[BomItem]:
        return iter(self.items)

    def active(self) -> list[BomItem]:
        """未废弃的条目 —— 报价只看这些。"""
        return [i for i in self.items if i.status != "deprecated"]

    def by_system(self) -> dict[str, list[BomItem]]:
        out: dict[str, list[BomItem]] = {}
        for it in self.active():
            out.setdefault(it.path.system, []).append(it)
        return out

    def validate(self) -> None:
        seen: dict[str, str] = {}
        for it in self.items:
            it.validate()
            if it.id in seen:
                raise BomError(f"id 重复: {it.id}")
            seen[it.id] = it.name

    # ---- 磁盘 IO ----

    def save(self, root: Path, shard_key=lambda i: i.path.system) -> list[Path]:
        root = Path(root)
        (root / "items").mkdir(parents=True, exist_ok=True)
        (root / "VERSION").write_text(self.version + "\n", encoding="utf-8")
        if self.taxonomy:
            (root / "taxonomy.yaml").write_text(
                yaml.safe_dump(self.taxonomy, allow_unicode=True, sort_keys=False),
                encoding="utf-8")

        shards: dict[str, list[BomItem]] = {}
        for it in self.items:
            shards.setdefault(_slug(shard_key(it)), []).append(it)

        written = []
        for name, items in sorted(shards.items()):
            p = root / "items" / f"{name}.yaml"
            payload = {
                "schema_version": SCHEMA_VERSION,
                "bom_version": self.version,
                "items": [i.to_dict() for i in items],
            }
            p.write_text(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120),
                encoding="utf-8")
            written.append(p)
        return written

    @classmethod
    def load(cls, root: Path) -> Bom:
        root = Path(root)
        version = (root / "VERSION").read_text(encoding="utf-8").strip()
        taxonomy = {}
        tp = root / "taxonomy.yaml"
        if tp.exists():
            taxonomy = yaml.safe_load(tp.read_text(encoding="utf-8")) or {}
        items: list[BomItem] = []
        for p in sorted((root / "items").glob("*.yaml")):
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if data.get("schema_version") != SCHEMA_VERSION:
                raise BomError(f"{p.name}: schema_version={data.get('schema_version')} "
                               f"≠ {SCHEMA_VERSION}，需迁移")
            items.extend(BomItem.from_dict(d) for d in data.get("items", []))
        return cls(version, items, taxonomy)


def _slug(text: str) -> str:
    """产品线名 → 安全文件名，保留中文。"""
    return re.sub(r"[^\w一-鿿.-]+", "-", str(text)).strip("-")


# ---- 版本比对 ----------------------------------------------------------


def diff(old: Bom, new: Bom) -> dict[str, Any]:
    """两个 BOM 版本的变更集 —— 供 CHANGELOG 与漂移门禁（G-10）使用。"""
    o = {i.id: i for i in old.items}
    n = {i.id: i for i in new.items}
    added = [n[k] for k in n.keys() - o.keys()]
    removed = [o[k] for k in o.keys() - n.keys()]
    changed = []
    for k in o.keys() & n.keys():
        if o[k].to_dict() != n[k].to_dict():
            fields = sorted(
                f for f in set(o[k].to_dict()) | set(n[k].to_dict())
                if o[k].to_dict().get(f) != n[k].to_dict().get(f)
            )
            changed.append({"id": k, "fields": fields})
    return {
        "added": [{"id": i.id, "name": i.name} for i in added],
        "removed": [{"id": i.id, "name": i.name} for i in removed],
        "changed": changed,
        "summary": {"added": len(added), "removed": len(removed), "changed": len(changed)},
    }
