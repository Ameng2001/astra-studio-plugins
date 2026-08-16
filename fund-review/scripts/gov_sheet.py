"""gov_sheet — 送审 Excel 的**结构**规范（取代已归档的 `excel_styler`）。

## 规范从哪来

不是我编的。从柳州幼教送审包（`配置清单/送审包/`，5 个 xlsx + 1 份文件清单
docx，已过评审）反推：

  1. **总报价汇总单独成册**，序号/费用大类/金额/占比/标准条款 + 合计 + 关联附件索引
  2. **每张明细表第一列是序号**，不是技术 ID —— 评审意见按「第 37 行」提
  3. **每行带标准条款**（`三.(一).2.1 定制软件功能点法`），逐行可追溯到依据
  4. **末行合计用活公式** `=SUM(I4:I220)`，评审点开能自己验
  5. 汇总 sheet 排在明细前，sheet 名带序号前缀
  6. 金额千分位右对齐、工时两位小数

## 为什么要「强制」而不是「美化」

我们此前生成的 `测算汇总` 有一列 `类型分布`，值是

    {'EI': 66, 'EQ': 48, 'EO': 28, 'ILF': 9}

—— Python dict 的 repr 直接落进了要报给财政评审的公文。同一张表里
`产品成熟度` 列是 `existing` / `new`，没翻译的枚举。这两个都不是样式问题，
**再漂亮的配色也盖不住**，而事后遍历改字体的做法（原 `excel_styler`）
看不见它们。

所以这里把「什么能进单元格」变成写入时的硬约束：

  - `dict/list/set` 进单元格 → 直接报错（`_scalar`）
  - 裸小写 ASCII 标识符（`existing`/`warn`）进单元格 → 报错，要求先登记中文标签
  - 明细行不给条款出处 → 报错（`clause_required`）
  - 合计行的活公式与引擎值对不上 → 报错（`total(expect=...)`）

最后一条尤其重要：写 `=SUM()` 是为了让评审能验，可一旦 Excel 的和与我们
引擎的数不一致，评审看到的就是自相矛盾的两个数。**给出可验证的公式，
就必须自己先验过。** 这与 P0 那次（合并单元格击穿 SUMIF，少算 ¥421.6 万
且不报错）是同一类防线。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


class GovSheetError(RuntimeError):
    """送审表结构违规 —— 硬失败，不静默产出不合规的公文。"""


# ---- 视觉常量（沿用原 excel_styler，取自送审包实测）--------------------

BODY = "微软雅黑"
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
TOTAL_FILL = PatternFill("solid", fgColor="DCE6F1")
GROUP_FILL = PatternFill("solid", fgColor="EAF1F8")
HEADER_FONT = Font(name=BODY, size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=BODY, size=14, bold=True, color="1F4E79")
SUB_FONT = Font(name=BODY, size=9, color="595959")
BODY_FONT = Font(name=BODY, size=11)
BOLD_FONT = Font(name=BODY, size=11, bold=True)
TODO_FONT = Font(name=BODY, size=11, italic=True, color="1F66E5")
_THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

#: 数字格式。金额一律两位小数 —— 送审包用 `#,##0`，但那是整数化的报价；
#: 我们的引擎按 `xlround(...,2)` 逐条目取两位，显示成整数会让评审自己加
#: 出来的和与合计差几分钱。宁可多两位，不可让和对不上。
FMT = {
    "money": "#,##0.00",
    "int": "#,##0",
    "fp": "#,##0.00",
    "rate": "0.000",
    "pct": "0.00%",
    "text": None,
}
_RIGHT_FMTS = {"money", "int", "fp", "rate", "pct"}

CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")

#: 单元格职责配色。**抄自样例表**（`模板使用说明&基础参数` A4:B7）——
#: 方案人员和评审都认这套：哪些格子能动、哪些不能，一眼看颜色即可，
#: 不必去读说明。这正好回答了「区域参数要不要支持人编辑」：
#: 系数字典不得改（那是标准原文），项目特征可填（那是本次选择），计算区自动。
LOCKED_FILL = PatternFill("solid", fgColor="D9D9D9")   # 深灰：标准取值，不得修改
#: 浅黄 —— **可试算的格子**。原来用白色（FFFFFF），与表底完全一样，
#: 等于没标：样例表里「白色＝需填写」成立是因为它整表有底色，我们没有。
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
CALC_FILL = PatternFill("solid", fgColor="E2EFDA")     # 绿：自动计算，不得修改
LEGEND = [("D9D9D9", "标准取值，不得修改 —— 改了就偏离编制依据"),
          ("FFF2CC", "**可试算**：改这些格子看对总价的影响（多数带下拉）"),
          ("E2EFDA", "自动计算，不得修改")]

TODO_MARKERS = ("待核价", "待核", "待补", "待查证", "待确认", "待技术",
                "待商务", "待填", "待选型", "待询价", "（待")

#: 枚举 → 中文标签。**送审公文里不能出现没翻译的枚举值。**
#: 写入时凡遇到裸小写 ASCII 标识符都会拦下来，要求先在这里登记。
ENUM_LABELS: dict[str, str] = {
    # 产品成熟度
    "existing": "已有产品", "new": "全新开发", "partial": "部分复用",
    # 违规级别。`fail` 是冒烟矩阵在广东上炸出来的 —— 山东的一致性检查
    # 只产 warn，广东因 D4/D8 not_in_scope 会产 fail。单跑一个省抓不到。
    "warn": "提示", "error": "错误", "info": "说明",
    "fail": "否决", "pass": "通过", "ok": "通过", "skip": "跳过",
    # 是否
    "true": "是", "false": "否", "yes": "是", "no": "否",
    # 计价模型
    "onetime": "一次性", "annual": "按年", "usage": "按量",
}

#: `parse_quote.classify_sheet` 判成这些 kind 的表，下游求总额时会跳过。
#: 与 `quote_shape.NON_QUOTE_SHEET_KINDS` 同源 —— 汇总表不能与被它汇总的
#: 明细一起相加。
NON_SUMMABLE_SHEET_KINDS = {"summary"}

#: 这些裸标识符是**编号不是枚举**，允许原样写入。
_ID_ALLOW = re.compile(r"^(?:[A-Z]+[.\-_][\w.\-]+|[A-Z]\d+|v?\d[\w.\-]*)$")
_BARE_ENUM = re.compile(r"^[a-z][a-z0-9_]*$")


def label(v: Any) -> Any:
    """把枚举值换成中文标签；非枚举原样返回。"""
    if isinstance(v, str) and v in ENUM_LABELS:
        return ENUM_LABELS[v]
    return v


class _Raw(str):
    """`raw()` 的载体 —— 标记「这串小写英文是我确认过要照原样印的」。"""


def raw(v: str) -> _Raw:
    """绕过枚举检查的显式逃生口。

    有些小写英文确实该原样出现在公文里（产品专名、协议名、文件名）。
    但**必须显式说出来**：写 `gov_sheet.raw("kubernetes")` 是一次有据可查的
    决定，可以 grep 出来复核；而把检查放宽成「短的就放行」会让下一个
    `existing` 悄悄溜过去 —— 那正是这个检查要防的。
    """
    return _Raw(v)


def _scalar(v: Any, where: str) -> Any:
    """单元格只收标量。

    容器落进单元格会变成 Python repr —— `{'EI': 66, 'EQ': 48}` 这种东西
    出现在报财评的表里，是格式问题里最难看的一种，而且事后美化看不见它。
    """
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (dict, list, tuple, set)):
        raise GovSheetError(
            f"{where}：单元格收到容器 {type(v).__name__} —— "
            f"写进去就是 Python repr 泄漏到公文（如 \"{v!r}\"[:40]）。\n"
            f"  修法：调用侧先摊平成文本，例如 "
            f"gov_sheet.flatten_counts(d) → 'EI 66 / EQ 48'；\n"
            f"  或拆成多列。**不要 str() 了事** —— 那只是把 repr 换个写法。")
    return v


def _check_enum(v: Any, where: str) -> Any:
    if isinstance(v, _Raw):
        return str(v)
    if not isinstance(v, str) or not _BARE_ENUM.match(v):
        return v
    if _ID_ALLOW.match(v):
        return v
    raise GovSheetError(
        f"{where}：值 {v!r} 像是没翻译的枚举 —— 送审公文里不该出现英文枚举。\n"
        f"  修法：在 gov_sheet.ENUM_LABELS 里登记中文标签，"
        f"或调用侧先过 gov_sheet.label()。\n"
        f"  若它确实是编号而非枚举，请改成大写/带前缀的形式。")


def flatten_counts(d: dict[str, Any], sep: str = " / ") -> str:
    """`{'EI': 66, 'EQ': 48}` → `'EI 66 / EQ 48'`。"""
    return sep.join(f"{k} {v}" for k, v in d.items())


class Formula(str):
    """一个活公式单元格。构造时就验过它算出来等于引擎的值。

    `str` 的子类 —— openpyxl 见到以 `=` 开头的字符串就当公式写入，
    所以它能直接进单元格，同时携带自验的证据。
    """
    computed: float
    engine: float


def formula(text: str, computed: float, engine: float, *,
            where: str = "", tol: float = 0.01, rel_tol: float = 0.0) -> Formula:
    """写一个可试算的活公式，并当场验它。

    ## 为什么活公式在这里是安全的

    本项目的铁律是「Excel 只是渲染产物，数值由引擎算定」——那条铁律来自 P0：
    1200 行明细里 `SUMIF` 被合并单元格击穿，少算 ¥421.6 万且不报错。

    但**试算链条不是那个形状**：它是 20 行以内的「参数 → 汇总」直引用，
    没有跨表查找、没有合并单元格参与计算。样例表（`raw-input/样例表.xlsx#
    应急管理系统应用系统功能规模`）就是这么做的，方案人员改一个系数即可看到
    全盘影响 —— 这是静态值给不了的东西。

    所以分界线不是「能不能用公式」，而是**哪一段**：

      - 逐条明细的 FP 累加 → 静态（P0 的地盘，1738 行）
      - 子系统级的参数 → 金额 → 活公式（试算的价值所在）

    ## 代价：必须自己先验过

    `text` 是给 Excel 算的，`computed` 是同一个算式用 Python 算一遍的结果。
    两者都必须等于 `engine`（引擎算定的值）。对不上就报错 ——
    否则用户打开文件看到的是「公式算出来的数」和「汇总页的数」不一致，
    比不给公式更糟。这与 `GovSheet.total(expect=)` 是同一条纪律。
    """
    limit = max(tol, abs(engine) * rel_tol)
    if abs(computed - engine) > limit:
        raise GovSheetError(
            f"{where or text}：活公式与引擎值不符\n"
            f"  公式 {text}\n"
            f"  按此算式 Python 得 {computed:,.4f}\n"
            f"  引擎给的       {engine:,.4f}\n"
            f"  差             {computed - engine:,.4f}（容差 {limit:,.4f}）\n"
            f"  可试算的公式必须先自验 —— 否则打开文件就是两个数打架。"
            f"**不要放宽容差**，先查算式是不是漏了 ROUND 或某个因子。")
    f = Formula(text)
    f.computed, f.engine = computed, engine
    return f


#: 本进程里所有带 `role` 的列：(sheet 名, 列名, 角色)。
#: 由 `assert_roles_parseable()` 结算 —— 见那个函数的注释。
_ROLE_REGISTRY: list[tuple[str, str, str]] = []


@dataclass
class Col:
    """一列的规格。

    `fmt` 决定对齐与数字格式，`sum` 决定是否进合计行，
    `role` 声明这一列在**下游解析**时扮演的语义角色。
    """
    name: str
    fmt: str = "text"
    width: int | None = None
    sum: bool = False
    role: str | None = None
    #: 单元格职责：locked（标准取值）/ input（可试算）/ calc（自动算）/ None（普通）
    cell_role: str | None = None
    #: 下拉候选。给了就在该列数据区加数据有效性 ——
    #: **能枚举的就别让人手打**：手打一个不在系数表里的应用类型，
    #: VLOOKUP 返回 #N/A，而那看起来像「表坏了」而不是「你填错了」。
    choices: list[str] | None = None

    def __post_init__(self) -> None:
        if self.fmt not in FMT:
            raise GovSheetError(f"列 {self.name!r} 的 fmt={self.fmt!r} 未知；"
                                f"可选：{sorted(FMT)}")
        if self.choices is not None:
            inline = '"' + ",".join(str(c) for c in self.choices) + '"'
            if len(inline) > 255:
                raise GovSheetError(
                    f"列 {self.name!r} 的下拉候选内联后 {len(inline)} 字符，"
                    f"超过 Excel 数据有效性的 255 上限；请改引用区域。")
            if any("," in str(c) for c in self.choices):
                raise GovSheetError(
                    f"列 {self.name!r} 的下拉候选含逗号 —— 内联列表以逗号分隔，"
                    f"会被拆成两项：{[c for c in self.choices if ',' in str(c)]}")
        if self.cell_role not in (None, "locked", "input", "calc"):
            raise GovSheetError(
                f"列 {self.name!r} 的 cell_role={self.cell_role!r} 未知；"
                f"可选：locked | input | calc")


def assert_roles_parseable() -> None:
    """**我们自己生成的表，我们自己的解析器必须认得。**

    生成端（这里的 `Col` 名字）和解析端（`quote_shape.COLUMN_ROLES` 的候选
    列名）此前是两份各自维护的词表，中间没有任何联系。改了一边不会有人发现 ——
    实测：把功能点明细的列名改成送审格式后，`build_fp_table` 一律从「人天」
    倒推 FP，而新表没有人天列，于是 `total_fp` 归零、报告照印，命令退出 0。
    那是本项目「静默出 0」家族的第六种写法。

    单靠一条测试挡不住：测试要有人记得跑，而且得配齐 BOM 与标准包。
    所以把结算放在**每次真实生成的末尾** —— 数据在哪，检查就在哪。
    """
    import quote_shape
    bad = []
    for sheet, name, role in _ROLE_REGISTRY:
        if role not in quote_shape.COLUMN_ROLES:
            bad.append(f"[{sheet}] 列「{name}」声明的角色 {role!r} 不存在；"
                       f"现有：{sorted(quote_shape.COLUMN_ROLES)}")
        elif name not in quote_shape.COLUMN_ROLES[role]:
            bad.append(
                f"[{sheet}] 列「{name}」声明角色 {role!r}，"
                f"但 quote_shape.COLUMN_ROLES[{role!r}] 认不出这个列名\n"
                f"      它认的是：{quote_shape.COLUMN_ROLES[role]}")
    if bad:
        raise GovSheetError(
            "生成的表下游解析不了 —— 改了列名却没同步解析器：\n  "
            + "\n  ".join(bad)
            + "\n  修法：把列名加进 quote_shape.COLUMN_ROLES 的对应角色。"
              "\n  **不要把 role 删掉了事** —— 那只是让检查闭嘴，"
              "下游照样静默出 0。")


@dataclass
class GovSheet:
    """一张送审明细表。

        s = GovSheet(wb, "01_建设期采购清单", title="…建设期采购",
                     subtitle="编制依据：…", columns=[...], clause_required=True)
        s.group("一、软件开发费用", {"金额（元）": 7550472})
        s.row({"明细": "…", "金额（元）": 123}, clause="三.(一).2.1")
        s.total(expect={"金额（元）": result["totals"]["construction_total"]})
        s.finish()
    """
    wb: Any
    name: str
    title: str
    columns: list[Col]
    subtitle: str | None = None
    clause_required: bool = False
    clause_name: str = "标准条款"
    seq: bool = True
    #: 本表是**别处金额的再汇总**（如 Z00 总报价、Z02 测算汇总）。
    #: 下游求总额时必须跳过它，否则同一笔钱算两遍。见 `_assert_rollup_detected`。
    rollup: bool = False

    ws: Any = field(init=False, default=None)
    header_row: int = field(init=False, default=0)
    _seq: int = field(init=False, default=0)
    _first_data: int = field(init=False, default=0)
    #: 最后一条**明细行**的行号。合计行与说明行不算。
    _last_data: int = field(init=False, default=0)
    #: 每一条明细行的行号。合计行的 `=SUM()` **只覆盖这些行** ——
    #: 从前是 `SUM(首行:末行)` 连续区间，一旦中间夹了带小计的分组行，
    #: Excel 的和就把小计和它的明细各加一遍，而 `expect` 的自验只累加
    #: `row()` 写入的值，两边量的不是同一个东西，于是**自验通过、表却是双倍**。
    #: 这正是 P0 那个形状：不报错，只是数错了。
    _data_rows: list = field(init=False, default_factory=list)
    #: 每列实际写成 input（可试算）的行号。**下拉只挂在这些格子上** ——
    #: 判据不是「是明细行」而是「这一格可试算」，两者不等：04 的费用行也是
    #: 明细行，但它那格写的是「自动」，挂个能改的下拉只会误导。
    _input_rows: dict = field(init=False, default_factory=dict)
    _sums: dict[str, float] = field(init=False, default_factory=dict)
    #: 待折叠的列组 [(首列名, 末列名, 是否默认折叠)]，由 group_columns() 登记，
    #: 在 finish() 里统一施加 —— 必须在列宽设完之后，见那里的注释。
    _col_groups: list = field(init=False, default_factory=list)
    _finished: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        self.ws = self.wb.create_sheet(self.name)
        cols = list(self.columns)
        if self.seq:
            cols.insert(0, Col("序号", "int", width=6))
        if self.clause_required and not any(c.name == self.clause_name for c in cols):
            cols.append(Col(self.clause_name, "text", width=30))
        self.columns = cols
        self._by_name = {c.name: i + 1 for i, c in enumerate(cols)}
        for c in cols:
            if c.role:
                _ROLE_REGISTRY.append((self.name, c.name, c.role))

        r = 1
        self.ws.cell(r, 1, self.title).font = TITLE_FONT
        self.ws.cell(r, 1).alignment = LEFT
        self.ws.merge_cells(start_row=r, start_column=1,
                            end_row=r, end_column=len(cols))
        self.ws.row_dimensions[r].height = 22
        r += 1
        if self.subtitle:
            self.ws.cell(r, 1, self.subtitle).font = SUB_FONT
            self.ws.cell(r, 1).alignment = LEFT
            self.ws.merge_cells(start_row=r, start_column=1,
                                end_row=r, end_column=len(cols))
            r += 1
        self.header_row = r
        for i, c in enumerate(cols, 1):
            cl = self.ws.cell(r, i, c.name)
            cl.font, cl.fill, cl.alignment, cl.border = (
                HEADER_FONT, HEADER_FILL, CENTER, BORDER)
        self.ws.row_dimensions[r].height = 28
        self._first_data = r + 1
        self._next = r + 1
        self._assert_rollup_detected()

    def _assert_rollup_detected(self) -> None:
        """汇总表必须能被 `parse_quote` 认出来 —— 直接问它，不靠约定。

        `classify_sheet` 是按**表名**判 summary 的（含「汇总」/「总表」）。
        我们的 `00_测算汇总` 恰好命中，所以求总额时被跳过、¥1053 万没有被
        重复计入。但那是**碰巧对**：把这张表改名叫「软件开发费构成」，
        同一笔钱就会悄悄算两遍，而且没有任何地方会报错。

        与其在这里复刻一遍它的命名约定（那就成了第二份要同步的规则），
        不如把真正的判定函数拿来当场跑一遍。写的人立刻知道改名的后果。
        """
        if not self.rollup:
            return
        try:
            from parse_quote import classify_sheet
        except ImportError:                     # 单独用 gov_sheet 时不强求
            return
        header = [c.name for c in self.columns]
        kind = classify_sheet(self.name, header)
        if kind not in NON_SUMMABLE_SHEET_KINDS:
            raise GovSheetError(
                f"[{self.name}] 声明 rollup=True，但 parse_quote.classify_sheet "
                f"把它判成 {kind!r} 而不是「汇总」。\n"
                f"  后果：下游求总额时会把这张表的金额与被它汇总的明细表**算两遍**，"
                f"且不报错。\n"
                f"  修法：表名里保留「汇总」或「总表」二字"
                f"（这正是 classify_sheet 的判据），或改 classify_sheet。")

    # ---- 写行 ----

    def _put(self, row: int, col: Col, idx: int, v: Any, *, bold: bool = False,
             fill: PatternFill | None = None) -> None:
        where = f"[{self.name}] r{row} 列「{col.name}」"
        v = _check_enum(_scalar(v, where), where)
        # openpyxl 见到以 = 开头的字符串就写成**公式单元格**，不管你的本意。
        # 于是「复算式」这种想展示算式的列，打开是算出来的数；
        # 「= ROUND(人月×单价, 4)」这种备注，打开是 #N/A。
        # 两种都不报错 —— 又是同一个形状：没有错误，只有错的表。
        # 要活公式请用 formula()（它会自验）；要展示算式就别以 = 开头。
        if isinstance(v, str) and not isinstance(v, Formula) and v.lstrip().startswith("="):
            raise GovSheetError(
                f"{where}：写了以「=」开头的普通字符串 {v[:60]!r}。\n"
                f"  openpyxl 会把它写成公式单元格，Excel 打开显示的是**计算结果**"
                f"（算式里有中文就是 #N/A），不是你想展示的算式文本。\n"
                f"  要活公式 → 用 gov_sheet.formula(text, computed, engine)，它会自验；\n"
                f"  要展示算式 → 去掉开头的「=」，或写成「金额 = ROUND(…)」。")
        cl = self.ws.cell(row, idx, v)
        is_todo = isinstance(v, str) and any(m in v for m in TODO_MARKERS)
        cl.font = BOLD_FONT if bold else (TODO_FONT if is_todo else BODY_FONT)
        if fill is not None:
            cl.fill = fill
        elif col.cell_role:
            cl.fill = {"locked": LOCKED_FILL, "input": INPUT_FILL,
                       "calc": CALC_FILL}[col.cell_role]
        cl.border = BORDER
        # 活公式：按数值格式右对齐 —— 它算出来是数，不是文本
        if isinstance(v, Formula):
            cl.number_format = FMT[col.fmt] or FMT["money"]
            cl.alignment = RIGHT
            return
        if col.fmt in _RIGHT_FMTS and isinstance(v, (int, float)) and not isinstance(v, bool):
            cl.number_format = FMT[col.fmt]
            cl.alignment = RIGHT
        elif col.name == "序号":
            cl.alignment = CENTER
        else:
            cl.alignment = LEFT

    def row(self, values: dict[str, Any], clause: str | None = None,
            cell_roles: dict[str, str] | None = None) -> int:
        """写一条明细。`clause_required` 时不给条款出处会报错。

        `cell_roles` 按列名覆盖该行的单元格职责 —— 整列 locked 的参数面板里
        要放一格「可试算」时用它（项目特征因子就是这种：它在标准取值表里，
        但它本身**不是标准取值**，是本商机的选择）。
        """
        if self._finished:
            raise GovSheetError(f"[{self.name}] 已 finish()，不能再写行")
        unknown = set(values) - set(self._by_name)
        if unknown:
            raise GovSheetError(
                f"[{self.name}] 未声明的列 {sorted(unknown)}；"
                f"已声明：{[c.name for c in self.columns]}")
        if self.clause_required:
            clause = clause or values.get(self.clause_name)
            if not clause:
                raise GovSheetError(
                    f"[{self.name}] 明细行缺标准条款出处：{values!r}\n"
                    f"  送审明细每一行都要能追到依据条款 —— 评审逐行核对时"
                    f"「没写出处」等同于「没有依据」。")
            values = {**values, self.clause_name: clause}
        for k, v_ in (cell_roles or {}).items():
            if k not in self._by_name:
                raise GovSheetError(
                    f"[{self.name}] cell_roles 指向未声明的列 {k!r}；"
                    f"已声明：{[c.name for c in self.columns]}")
            if v_ not in ("locked", "input", "calc"):
                raise GovSheetError(
                    f"[{self.name}] cell_roles[{k!r}]={v_!r} 未知；"
                    f"可选：locked | input | calc")

        r = self._next
        self._seq += 1
        for c in self.columns:
            i = self._by_name[c.name]
            v = self._seq if c.name == "序号" else values.get(c.name)
            ov = (cell_roles or {}).get(c.name)
            eff = ov or c.cell_role
            if eff == "input":
                self._input_rows.setdefault(c.name, []).append(r)
            self._put(r, c, i, v,
                      fill=({"locked": LOCKED_FILL, "input": INPUT_FILL,
                             "calc": CALC_FILL}[ov] if ov else None))
            # 活公式也要进合计 —— 它是 str 子类，但携带自验过的 computed 值。
            # 漏掉会让合计行的 =SUM() 与逐行公式算出来的数对不上。
            if c.sum:
                if isinstance(v, Formula):
                    self._sums[c.name] = self._sums.get(c.name, 0.0) + v.computed
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    self._sums[c.name] = self._sums.get(c.name, 0.0) + v
        self._next += 1
        self._last_data = r
        self._data_rows.append(r)
        return r

    def group(self, label_text: str, values: dict[str, Any] | None = None,
              clause: str | None = None) -> int:
        """科目分组行（`一、软件开发费用`）。不占序号，不进合计。"""
        r = self._next
        vals = dict(values or {})
        if clause:
            vals[self.clause_name] = clause
        first = next(c for c in self.columns if c.name != "序号")
        vals.setdefault(first.name, label_text)
        for c in self.columns:
            # 自动序号时分组行不占号，故清空；**但关掉自动序号（seq=False）时
            # 「序号」是调用方自己填的内容列**（如甲方模板要求分组行写
            # 「一」「（一）」），此时一律清空会把它静默吃掉。
            v = (None if (c.name == "序号" and self.seq)
                 else vals.get(c.name))
            self._put(r, c, self._by_name[c.name], v,
                      bold=True, fill=GROUP_FILL)
        self._next += 1
        return r

    def note(self, text: str) -> int:
        """整行说明。跨列合并、无边框 —— 与明细区在视觉上分开。"""
        r = self._next
        cl = self.ws.cell(r, 1, text)
        cl.font = SUB_FONT
        cl.alignment = LEFT
        self.ws.merge_cells(start_row=r, start_column=1,
                            end_row=r, end_column=len(self.columns))
        self._next += 1
        return r

    def blank(self) -> None:
        self._next += 1

    @property
    def seq_count(self) -> int:
        """已写明细行数（不含科目行/说明行）。"""
        return self._seq

    def group_columns(self, first: str, last: str, *,
                      collapsed: bool = True) -> None:
        """把 `first`…`last` 之间的列折叠成一个可展开的列组。

        用在「明细维度」上：主线之外、需要时才展开的那几列。
        默认折叠，读表的人先看到主线，点 [+] 再看细节。

        逐列设 outlineLevel/hidden，**不用 openpyxl 的
        `column_dimensions.group()`** —— 那个会把首列的 dimension 撑成跨列
        区间并删掉区间内其余列的 dimension，于是组内各列的宽度统一跟首列走。
        「层次(10) / 计价方法(18) / 推导要点(54)」这种组一折叠，展开后三列
        全变成 10 宽，而且不报错。Excel 表示列组本来就只需要连续几个 col
        元素带相同的 outlineLevel，逐列设既保住宽度也是一样的效果。
        """
        for n in (first, last):
            if n not in self._by_name:
                raise GovSheetError(f"[{self.name}] group_columns 指向未声明的列 {n!r}")
        i, j = self._by_name[first], self._by_name[last]
        if j < i:
            raise GovSheetError(f"[{self.name}] group_columns({first!r}, {last!r}) 顺序反了")
        if any(i <= x <= j for _, _, x in
               ((f, l, self._by_name[f]) for f, l, _ in self._col_groups)):
            raise GovSheetError(
                f"[{self.name}] 列组 {first}…{last} 与已登记的组重叠；"
                f"Excel 的列组不支持同级重叠")
        self._col_groups.append((first, last, collapsed))

    # ---- 合计 ----

    def total(self, label_text: str = "合计",
              expect: dict[str, float] | None = None,
              tolerance: float = 0.01) -> int:
        """写合计行：活 `=SUM()` 公式 + 与引擎值比对。

        公式是给评审点开验的。**所以它必须先被我们自己验过** —— 否则
        评审看到的是 Excel 的和与汇总页两个不一致的数字，比不给公式更糟。
        """
        if self._next <= self._first_data:
            raise GovSheetError(f"[{self.name}] 没有数据行，不该写合计")
        r = self._next
        label_col = next(c.name for c in self.columns if c.name != "序号")
        spans = _compress(self._data_rows)
        if len(spans) > 255:
            raise GovSheetError(
                f"[{self.name}] 明细行被分组行切成 {len(spans)} 段，"
                f"超过 Excel SUM 的 255 个参数上限。"
                f"改用小计列或减少分组层级 —— 不要退回连续区间，"
                f"那会把分组小计重复计入。")
        for c in self.columns:
            i = self._by_name[c.name]
            if c.sum:
                col_l = get_column_letter(i)
                # 只加明细行，跳过分组行与说明行 —— 分组行常带小计，
                # 连续区间会把小计和它的明细各加一遍。
                args = ",".join(f"{col_l}{a}" if a == b else f"{col_l}{a}:{col_l}{b}"
                                for a, b in spans)
                cl = self.ws.cell(r, i, f"=SUM({args})")
                cl.number_format = FMT[c.fmt]
                cl.alignment = RIGHT
            else:
                cl = self.ws.cell(r, i,
                                  label_text if c.name == label_col else None)
                cl.alignment = CENTER if c.name == "序号" else LEFT
            cl.font, cl.fill, cl.border = BOLD_FONT, TOTAL_FILL, BORDER

        for col_name, want in (expect or {}).items():
            if col_name not in self._by_name:
                raise GovSheetError(f"[{self.name}] expect 里的列 {col_name!r} 不存在")
            got = self._sums.get(col_name, 0.0)
            if abs(got - want) > tolerance:
                raise GovSheetError(
                    f"[{self.name}] 合计行与引擎值不符：列「{col_name}」\n"
                    f"  逐行累加 = {got:,.2f}\n"
                    f"  引擎给的 = {want:,.2f}\n"
                    f"  差       = {got - want:,.2f}\n"
                    f"  表里写的是活公式 =SUM()，评审点开就能看见这个差。"
                    f"**不要放宽容差** —— 先查明细是不是漏行/重复计。")
        self._next += 1
        return r

    # ---- 收尾 ----

    def finish(self) -> None:
        """列宽、冻结、打印设置。写完必须调用。"""
        if self._finished:
            return
        for c in self.columns:
            i = self._by_name[c.name]
            if c.width:
                w = c.width
            else:
                w = 0
                for r in range(self.header_row, min(self._next, self.header_row + 400)):
                    v = self.ws.cell(r, i).value
                    if v is not None and not (isinstance(v, str) and v.startswith("=")):
                        w = max(w, sum(2 if ord(ch) > 127 else 1
                                       for ch in str(v)[:80]))
                w = max(8, min(60, w + 3))
            self.ws.column_dimensions[get_column_letter(i)].width = w

        # 下拉：挂在该列的数据区。放在 finish() 是因为这时才知道数据区多长。
        from openpyxl.worksheet.datavalidation import DataValidation
        for c in self.columns:
            rows_ = self._input_rows.get(c.name) or []
            if not c.choices or not rows_:
                continue
            col_l = get_column_letter(self._by_name[c.name])
            # 连续段合并成区间，不连续就分段 —— 逐格加会让 sqref 长得离谱
            spans, s0, prev = [], rows_[0], rows_[0]
            for rr in rows_[1:]:
                if rr == prev + 1:
                    prev = rr; continue
                spans.append((s0, prev)); s0 = prev = rr
            spans.append((s0, prev))
            dv = DataValidation(
                type="list",
                formula1='"' + ",".join(str(x) for x in c.choices) + '"',
                allow_blank=True, showErrorMessage=True,
                errorTitle="取值不在候选内",
                error=f"「{c.name}」只能取：" + "、".join(str(x) for x in c.choices)
                      + "。手打一个候选外的值，下游查表会返回 #N/A。")
            self.ws.add_data_validation(dv)
            for a_, b_ in spans:
                dv.add(f"{col_l}{a_}:{col_l}{b_}")

        self.ws.freeze_panes = f"A{self.header_row + 1}"
        self.ws.auto_filter.ref = (
            f"A{self.header_row}:"
            f"{get_column_letter(len(self.columns))}{max(self._next - 1, self.header_row)}")
        ps = self.ws.page_setup
        ps.paperSize = 9                      # A4
        ps.orientation = "landscape" if len(self.columns) > 7 else "portrait"
        ps.fitToWidth, ps.fitToHeight = 1, 0
        self.ws.sheet_properties.pageSetUpPr.fitToPage = True
        self.ws.print_title_rows = f"{self.header_row}:{self.header_row}"

        # 列分组放在最后 —— group() 会删掉区间内其余列的 dimension，
        # 放在设列宽之前的话，后面再设宽会把刚建好的分组拆掉。
        if self._col_groups:
            # 汇总列在明细组的**左边**（如「数量」在各园所列之前），
            # 所以 +/- 按钮要显示在左侧；默认 summaryRight=True 会把按钮
            # 放到组的右边，点起来对不上。
            self.ws.sheet_properties.outlinePr.summaryRight = False
        for first, last, collapsed in self._col_groups:
            for idx in range(self._by_name[first], self._by_name[last] + 1):
                cd = self.ws.column_dimensions[get_column_letter(idx)]
                cd.outline_level = 1
                cd.hidden = collapsed
        self._finished = True


def _compress(rows: list[int]) -> list[tuple[int, int]]:
    """把行号列表压成连续区间，供 `=SUM(B5:B20,B22:B40)` 用。

    不压缩的话，几百条明细会生成几百个逗号参数 —— Excel 的 SUM 最多 255 个
    参数，且公式栏里没法读。压缩后通常只有「分组数 + 1」个区间。
    """
    out: list[tuple[int, int]] = []
    for r in sorted(rows):
        if out and r == out[-1][1] + 1:
            out[-1] = (out[-1][0], r)
        else:
            out.append((r, r))
    return out


def new_workbook():
    """建工作簿并去掉 openpyxl 的默认空 sheet。"""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    return wb


def save(wb, path, *, sheets_expected: Iterable[str] | None = None) -> None:
    if not wb.sheetnames:
        raise GovSheetError(f"{path}：工作簿一张表都没有")
    if sheets_expected is not None:
        missing = set(sheets_expected) - set(wb.sheetnames)
        if missing:
            raise GovSheetError(f"{path}：缺表 {sorted(missing)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def doc_name(prefix: str, no: str, topic: str, version: str,
             date: str, status: str = "正式版") -> str:
    """送审包文件名：`数智康养_Z01_建设期采购清单_正式版_v0.17.0_20260806.xlsx`。

    格式抄自柳州送审包 —— 文件清单 docx 是按这个名字索引的，评审在几十份
    附件里靠文件名定位，`01-建设期采购清单.xlsx` 这种名字既没有项目也没有
    版本，一旦和别的项目混在一个目录就分不出来。
    """
    return f"{prefix}_{no}_{topic}_{status}_v{version}_{date}.xlsx"
