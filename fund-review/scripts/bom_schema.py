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
    #: 定制开发，但**按工作量估算法计价**（人月 × 阶段单价），不数功能点。
    #: 柳州标准 p.10 三.(一)2.1 明文两法择一；本类别用于那些功能点法
    #: **按定义测不到**的开发内容 —— 模型训练、语料治理、提示工程、算法引擎，
    #: 它们不产生 ILF/EIF/EI/EO/EQ。不是「懒得数」，是这把尺子量不了。
    #: 与 SOFTWARE_FP 的区别只在造价方法，两者都是定制开发。
    "SOFTWARE_EFFORT",
}

#: 走工作量估算法的类别 —— 不要求 nesma，但**必须有工作量依据**
EFFORT_COUNTED_CLASSES = {"SOFTWARE_EFFORT"}

#: 走功能点法计数的类别 —— 带 nesma 段时按功能点计
FP_COUNTED_CLASSES = {"SOFTWARE_FP", "KB", "DATASET"}

#: 造价方法二选一的类别：同一条目要么按功能点计（nesma），要么按购置计（spec）。
#: KB/DATASET 天然两栖 —— 「我们把外部药品目录接进来做治理加工」是开发工作量，
#: 「我们替客户买药智网年度订阅」是购置费。两者是**不同标的**，可以并存为
#: 两条互相引用的条目，但**单条**只能有一个造价口径，否则就是重复计列。
DUAL_METHOD_CLASSES = {"KB", "DATASET"}

#: 只能按购置计的类别 —— 必须有 spec，不得有 nesma
PURCHASE_ONLY_CLASSES = {"PRODUCT", "MODEL", "HARDWARE", "SERVICE"}


@dataclass
class Vocabulary:
    """BOM 受控词表 —— 条目能说自己是什么。地域无关，不含任何取值。

    此前 `app_type` / `dev_category` 的合法值实际上借用的是山东包的 factor 键，
    BOM 依赖了 L1，方向反了；而且 `validate()` 对这两个字段根本不校验，
    填错要等到算价时 `PackError` 才暴露，一次一条。
    """

    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    purchase_must_omit: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> "Vocabulary":
        p = Path(root) / "vocabulary.yaml"
        if not p.exists():
            return cls()          # 未定义词表时不校验，兼容旧 BOM 目录
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(fields=d.get("fields", {}),
                   purchase_must_omit=d.get("purchase_classes_must_omit", []))

    def values(self, field_name: str) -> dict[str, str]:
        return (self.fields.get(field_name) or {}).get("values") or {}

    def check(self, item: "BomItem") -> list[str]:
        """返回该条目的词表问题。空列表 = 干净。"""
        out: list[str] = []
        fp = is_fp_counted(item)
        for name in ("app_type", "dev_category"):
            allowed = self.values(name)
            if not allowed:
                continue
            v = getattr(item, name)
            if fp:
                if not v:
                    out.append(f"走功能点法但 {name} 为空")
                elif v not in allowed:
                    out.append(f"{name}={v!r} 不在词表 {sorted(allowed)}")
            elif v and name in self.purchase_must_omit:
                # 填了会让人以为它参与了测算，而它根本不走功能点法
                out.append(f"非功能点条目不该有 {name}={v!r}")
        return out


def is_fp_counted(item: "BomItem") -> bool:
    """这一条是否按功能点计。

    光看 class 不够 —— KB/DATASET 两栖，同一个 class 下既有走功能点的
    治理加工条目，也有走购置的订阅条目。凡是筛「进功能点测算表的条目」
    都该用这个，别再写 `cls in FP_COUNTED_CLASSES`。
    """
    return item.cls in FP_COUNTED_CLASSES and item.nesma is not None


def is_purchase(item: "BomItem") -> bool:
    """这一条是否按购置计 —— 以「报到哪个财评科目」为准，不以 class 为准。"""
    return bool(item.spec.get("subject"))

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

#: `FP.PARK.0012` 特性级；`FP.PARK.0012.03` 是从该特性拆出的第 3 个基本过程。
#: 保留父段而不是重新编号，是为了**在 id 上就能看出这条来自哪个特性** ——
#: 拆分后一个特性变成两三条，评审问「这三条是不是同一件事重复计了」时，
#: id 本身就是答案。
ID_RE = re.compile(r"^[A-Z]{2,6}(\.[A-Z0-9]{2,8})+\.\d{4}(\.\d{2})?$")


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
    #: 逻辑文件在**跨子系统共享**时的角色。规则：一份逻辑数据组只有一个维护方，
    #: 维护方记 ILF（权重 10），其余引用方记 ELF（权重 7）。
    #: 不填 = 该逻辑文件不跨子系统共享，无需区分。
    logical_file_role: str | None = None    # maintainer | reference
    logical_file_note: str | None = None    # 判定依据 —— 谁维护、凭什么

    def validate(self, item_id: str) -> None:
        if self.type not in NESMA_TYPES:
            raise BomError(f"{item_id}: nesma.type={self.type!r} 不在 {sorted(NESMA_TYPES)}")
        if self.logical_file_role not in (None, "maintainer", "reference"):
            raise BomError(
                f"{item_id}: nesma.logical_file_role={self.logical_file_role!r} "
                f"须为 maintainer | reference")
        # 角色与类型必须自洽 —— 「引用方」却记 ILF 就是在重复计列，
        # 而这正是加这两个字段要防的事。
        if self.logical_file_role == "reference" and self.type != "ELF":
            raise BomError(
                f"{item_id}: 标为 reference（引用方）却记 {self.type}；"
                f"引用他方维护的逻辑数据组应记 ELF（外部逻辑文件，权重 7）")
        if self.logical_file_role == "maintainer" and self.type != "ILF":
            raise BomError(
                f"{item_id}: 标为 maintainer（维护方）却记 {self.type}；"
                f"维护方应记 ILF（内部逻辑文件，权重 10）")
        if self.logical_file_role and not (self.logical_file_note or "").strip():
            raise BomError(
                f"{item_id}: 填了 logical_file_role 却没写 logical_file_note —— "
                f"「凭什么这个子系统是维护方」必须有据可查，否则改判无从复核")


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
    #: 重挂产品线之前这条挂在哪个系统下。**不进 as_tuple，不参与计数** ——
    #: 只为在架构调整后还能回答「这条原来是基座下的哪个子系统」。
    #: 丢掉它，重挂就成了一次不可逆的改写。
    l0_source: str | None = None

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
    #: 共创状态与处理建议（由 bom_annotate 打）。**是条目的事实，不是渲染细节** ——
    #: 放在这里才能随 BOM 一起进 git、随 push 一起进飞书、随 pull 一起回来做 diff。
    #: 之前它只写在 yaml 里，`from_dict` 直接丢掉，push 出去是空的。
    #: 工作量估算法的采集项。**功能点法那套字段回答不了「人月怎么估出来的」** ——
    #: SOFTWARE_EFFORT 条目没有 NESMA 类型、UFP 永远是 0，真正要采的是
    #: 阶段（决定表1 单价）、人月、以及**可核查的量纲**（语料条数/模型数/
    #: 评测轮次/数据源数）。没有量纲，人月就只是一个数字，财评问不出来源。
    #: 形如 {"stage": "软件开发（编码）", "man_months": 2.9,
    #:       "role": "算法工程师", "basis": "…", "metric": "菜品库 2000 条 …"}
    effort: dict[str, Any] = field(default_factory=dict)
    #: 工作量的**自下而上测算依据**（2026-08-09 起为工作量法的唯一取数口径）。
    #: 形如 {"unit": "数据类目", "qty": 5, "per_unit_mm": 0.75,
    #:       "man_months": 3.75, "counted_from": …, "counting_rule": …,
    #:       "basis": …, "source": …}
    #:
    #: **与 `effort` 的关系**：`effort` 是旧采集结构（阶段/人月/角色/metric），
    #: 其人月来自源估算表，实测 132 条全部满足 `人月 = 报价 ÷ 17,000`，
    #: 即由对外报价倒算而来，不构成独立测算。`effort_basis` 是重估后的口径，
    #: 引擎只读它（见 quote_generate_liuzhou 的 effort_from_bom 分支）。
    #: 旧字段保留仅供交叉对账（丁本 02），**不得用于计价**。
    effort_basis: dict[str, Any] = field(default_factory=dict)
    #: 复用度（柳州标准表1 注3 / 表3 注2）。粒度为**建设对象**，
    #: 形如 {"level": "高|中|低", "basis": "…"}。缺省为「低」（系数 1）。
    #: 非「低」而无 basis 时引擎拒绝出表 —— 无出处的折扣在评审那里是「随意定价」。
    reuse: dict[str, Any] = field(default_factory=dict)
    coauthor_status: str = ""
    coauthor_advice: str = ""

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

        if self.cls in PURCHASE_ONLY_CLASSES:
            if self.nesma is not None:
                raise BomError(f"{self.id}: class={self.cls} 不走功能点法，不应有 nesma 段")
        elif self.cls in DUAL_METHOD_CLASSES:
            # 二选一，且必选其一 —— 两个都有是重复计列，都没有是漏计
            if self.nesma is not None and self.spec.get("subject"):
                raise BomError(
                    f"{self.id}: class={self.cls} 同时有 nesma 与 spec.subject —— "
                    f"同一条目不能既按功能点计又按购置计。拆成两条互相引用的条目")
            if self.nesma is None and not self.spec.get("subject"):
                raise BomError(
                    f"{self.id}: class={self.cls} 既无 nesma 也无 spec.subject —— "
                    f"造价口径未定")
        elif self.cls in EFFORT_COUNTED_CLASSES:
            # 工作量法不数功能点，但**人月必须有出处** ——
            # 「不数功能点」不等于「不用给依据」，财评照样问「人月怎么来的」。
            if self.nesma is not None:
                raise BomError(
                    f"{self.id}: class={self.cls} 走工作量估算法，不应有 nesma 段 —— "
                    f"同一条目两种口径并存就是重复计列的入口")
            if not (self.legacy_quote or {}).get("man_months"):
                raise BomError(
                    f"{self.id}: class={self.cls} 走工作量估算法，但没有人月取值。"
                    f"标准 p.11 表1 按人月计价，没有人月就没有造价口径")
        elif self.nesma is None:      # SOFTWARE_FP
            raise BomError(f"{self.id}: class={self.cls} 走功能点法，但缺 nesma 段")
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
        if self.effort:
            d["effort"] = self.effort
        # **这两个字段一旦漏在 to_dict 外，`Bom.save()` 就会把它们从 yaml 里删掉。**
        # 而删掉之后 yaml 看起来完全正常，只有下一次出表才会报「没有
        # effort_basis.man_months」—— 那时 149 条测算依据已经不可恢复。
        # bom_apply / bom_rationale / bom_build 都会 save，任一次运行即触发。
        if self.effort_basis:
            d["effort_basis"] = self.effort_basis
        if self.reuse:
            d["reuse"] = self.reuse
        if self.coauthor_status:
            d["coauthor_status"] = self.coauthor_status
        if self.coauthor_advice:
            d["coauthor_advice"] = self.coauthor_advice
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
            effort=d.get("effort", {}),
            effort_basis=d.get("effort_basis", {}),
            reuse=d.get("reuse", {}),
            coauthor_status=d.get("coauthor_status", ""),
            coauthor_advice=d.get("coauthor_advice", ""),
        )


class Bom:
    """一个 BOM 版本的全部条目。"""

    def __init__(self, version: str, items: Iterable[BomItem] = (),
                 taxonomy: dict[str, Any] | None = None) -> None:
        self.version = version
        self.items: list[BomItem] = list(items)
        self.taxonomy = taxonomy or {}
        #: 从哪个目录加载的 —— 门禁据此找 vocabulary.yaml。
        #: 内存里构造的 BOM（测试、临时快照）没有目录，词表门禁自动跳过。
        self.root: Path | None = None

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

    def save(self, root: Path, shard_key=None) -> list[Path]:
        root = Path(root)
        (root / "items").mkdir(parents=True, exist_ok=True)
        (root / "VERSION").write_text(self.version + "\n", encoding="utf-8")
        if self.taxonomy:
            (root / "taxonomy.yaml").write_text(
                yaml.safe_dump(self.taxonomy, allow_unicode=True, sort_keys=False),
                encoding="utf-8")

        key = shard_key or default_shard_key
        shards: dict[str, list[BomItem]] = {}
        for it in self.items:
            shards.setdefault(_slug(key(it)), []).append(it)

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
        b = cls(version, items, taxonomy)
        b.root = root          # 门禁要据此找 vocabulary.yaml
        return b


def default_shard_key(item: BomItem) -> str:
    """默认按系统分片；带采购科目的条目自成一片。

    采购条目跨系统（一个成品软件顶掉整个系统的功能点），而且审阅的人不同 ——
    功能点清单给方案人员看，采购清单给商务看。混在一起两边都不好读。
    """
    if item.spec.get("subject"):
        return "采购科目"
    return item.path.system


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
