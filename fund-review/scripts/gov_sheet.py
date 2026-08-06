"""gov_sheet — 送审 Excel 的**结构**规范（`excel_styler` 只管长相，这里管骨架）。

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
**再漂亮的配色也盖不住**，而 `excel_styler` 那种事后遍历改字体的做法
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


# ---- 视觉常量（与 excel_styler 对齐，取自送审包实测）----------------------

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

TODO_MARKERS = ("待核价", "待核", "待补", "待查证", "待确认", "待技术",
                "待商务", "待填", "待选型", "待询价", "（待")

#: 枚举 → 中文标签。**送审公文里不能出现没翻译的枚举值。**
#: 写入时凡遇到裸小写 ASCII 标识符都会拦下来，要求先在这里登记。
ENUM_LABELS: dict[str, str] = {
    # 产品成熟度
    "existing": "已有产品", "new": "全新开发", "partial": "部分复用",
    # 违规级别
    "warn": "提示", "error": "错误", "info": "说明",
    # 是否
    "true": "是", "false": "否", "yes": "是", "no": "否",
    # 计价模型
    "onetime": "一次性", "annual": "按年", "usage": "按量",
}

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


@dataclass
class Col:
    """一列的规格。`fmt` 决定对齐与数字格式，`sum` 决定是否进合计行。"""
    name: str
    fmt: str = "text"
    width: int | None = None
    sum: bool = False

    def __post_init__(self) -> None:
        if self.fmt not in FMT:
            raise GovSheetError(f"列 {self.name!r} 的 fmt={self.fmt!r} 未知；"
                                f"可选：{sorted(FMT)}")


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

    ws: Any = field(init=False, default=None)
    header_row: int = field(init=False, default=0)
    _seq: int = field(init=False, default=0)
    _first_data: int = field(init=False, default=0)
    _sums: dict[str, float] = field(init=False, default_factory=dict)
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

    # ---- 写行 ----

    def _put(self, row: int, col: Col, idx: int, v: Any, *, bold: bool = False,
             fill: PatternFill | None = None) -> None:
        where = f"[{self.name}] r{row} 列「{col.name}」"
        v = _check_enum(_scalar(v, where), where)
        cl = self.ws.cell(row, idx, v)
        is_todo = isinstance(v, str) and any(m in v for m in TODO_MARKERS)
        cl.font = BOLD_FONT if bold else (TODO_FONT if is_todo else BODY_FONT)
        if fill is not None:
            cl.fill = fill
        cl.border = BORDER
        if col.fmt in _RIGHT_FMTS and isinstance(v, (int, float)) and not isinstance(v, bool):
            cl.number_format = FMT[col.fmt]
            cl.alignment = RIGHT
        elif col.name == "序号":
            cl.alignment = CENTER
        else:
            cl.alignment = LEFT

    def row(self, values: dict[str, Any], clause: str | None = None) -> int:
        """写一条明细。`clause_required` 时不给条款出处会报错。"""
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
        r = self._next
        self._seq += 1
        for c in self.columns:
            i = self._by_name[c.name]
            v = self._seq if c.name == "序号" else values.get(c.name)
            self._put(r, c, i, v)
            if c.sum and isinstance(v, (int, float)) and not isinstance(v, bool):
                self._sums[c.name] = self._sums.get(c.name, 0.0) + v
        self._next += 1
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
            self._put(r, c, self._by_name[c.name],
                      None if c.name == "序号" else vals.get(c.name),
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
        last = self._next - 1
        label_col = next(c.name for c in self.columns if c.name != "序号")
        for c in self.columns:
            i = self._by_name[c.name]
            if c.sum:
                col_l = get_column_letter(i)
                cl = self.ws.cell(
                    r, i, f"=SUM({col_l}{self._first_data}:{col_l}{last})")
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
        self._finished = True


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
