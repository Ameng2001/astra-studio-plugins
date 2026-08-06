"""excel_styler — 送审 Excel 统一格式与美化（政府/财评公文风）.

规范：
  - 字体：等线 / 微软雅黑 11pt 正文，标题 14pt 加粗
  - 表头行：深蓝底 (#1F4E79) + 白字 + 加粗 + 居中
  - 数据区：细边框，金额列右对齐 + 千分位
  - 合计/总计行：浅灰底 + 加粗
  - 标题行：跨列合并 + 居中
  - 冻结表头；列宽自适应（限幅）
"""
from __future__ import annotations

import openpyxl
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

BODY_FONT = "微软雅黑"
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(name=BODY_FONT, size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name=BODY_FONT, size=14, bold=True, color="1F4E79")
BODY_FONT_OBJ = Font(name=BODY_FONT, size=11)
TOTAL_FILL = PatternFill("solid", fgColor="DCE6F1")
NO_FILL = PatternFill(fill_type=None)   # 清除原始底稿临时标记色（荧光绿等）
ZEBRA_FILL = PatternFill("solid", fgColor="F4F8FB")  # 可选隔行浅色
TOTAL_FONT = Font(name=BODY_FONT, size=11, bold=True)
# 待补占位 → 蓝色斜体，送审前必须替换
TODO_FONT = Font(name=BODY_FONT, size=11, italic=True, color="1F66E5")
TODO_MARKERS = ("待核价", "待核", "待补", "待查证", "待确认", "待技术",
                "待商务", "待填", "≥3·待", "型号/单价待核", "（待")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")

MONEY_HINTS = ("金额", "总价", "单价", "合计", "成本", "报价", "费", "FP", "数量", "占比")
MONEY_FMT = "#,##0"


def _detect_header_row(ws) -> int:
    best, best_n = 1, 0
    for r in range(1, min(ws.max_row + 1, 12)):
        n = sum(1 for c in range(1, ws.max_column + 1)
                if isinstance(ws.cell(row=r, column=c).value, str))
        if n > best_n:
            best, best_n = r, n
    return best


def _last_content_col(ws, header_row: int) -> int:
    last = 0
    for c in range(1, ws.max_column + 1):
        if ws.cell(row=header_row, column=c).value not in (None, ""):
            last = c
    return last or ws.max_column


def style_sheet(ws) -> None:
    if ws.max_row is None or ws.max_row < 1:
        return
    hr = _detect_header_row(ws)
    maxc = _last_content_col(ws, hr)   # 忽略原始文件空尾列，不给空列描边/调宽

    # 全表先清除原始底稿临时填充（含空尾列 P~AN 的散落荧光色），
    # 再按规范上色。范围覆盖 ws.max_column，确保无残留。
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell, MergedCell):
                continue
            if cell.fill and cell.fill.patternType:
                cell.fill = NO_FILL

    # 标题行（hr 之前的非空单行）当作大标题
    for r in range(1, hr):
        # 标题行：统一字体 + 清除原始底稿填充色（含整行所有列）
        for c in range(1, maxc + 1):
            tc = ws.cell(row=r, column=c)
            if isinstance(tc, MergedCell):
                continue
            tc.fill = NO_FILL
            if c == 1 and isinstance(ws.cell(row=r, column=1).value, str) \
                    and ws.cell(row=r, column=1).value.strip():
                tc.font = TITLE_FONT
                tc.alignment = LEFT

    # 表头
    headers = []
    for c in range(1, maxc + 1):
        cell = ws.cell(row=hr, column=c)
        headers.append(str(cell.value or ""))
        if isinstance(cell, MergedCell):
            continue
        if cell.value is not None:
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = CENTER
            cell.border = BORDER

    money_cols = {i + 1 for i, h in enumerate(headers)
                  if any(k in h for k in MONEY_HINTS)}

    # 数据区
    for r in range(hr + 1, ws.max_row + 1):
        rowtext = " ".join(str(ws.cell(row=r, column=c).value or "")
                           for c in range(1, maxc + 1))
        is_total = any(k in rowtext for k in ("合计", "总计", "小计")) and \
            sum(1 for c in range(1, maxc + 1)
                if isinstance(ws.cell(row=r, column=c).value, str)) <= 3
        is_note = rowtext.strip().startswith(("【", "说明", "[说明", "注："))
        for c in range(1, maxc + 1):
            cell = ws.cell(row=r, column=c)
            if isinstance(cell, MergedCell):
                continue
            _is_todo = isinstance(cell.value, str) and any(m in cell.value for m in TODO_MARKERS)
            if _is_todo and not is_total:
                cell.font = TODO_FONT          # 蓝色斜体：送审前待替换
            else:
                cell.font = TOTAL_FONT if is_total else BODY_FONT_OBJ
            # 视觉规范：合计行=浅蓝灰；其余一律清除原始底稿临时色（荧光绿等）
            cell.fill = TOTAL_FILL if is_total else NO_FILL
            if not is_note:
                cell.border = BORDER
            if cell.value is None:
                continue
            if c in money_cols and isinstance(cell.value, (int, float)):
                cell.number_format = MONEY_FMT
                cell.alignment = RIGHT
            elif isinstance(cell.value, (int, float)):
                cell.alignment = CENTER
            else:
                cell.alignment = LEFT

    # 列宽（按内容估，限 8–60）
    for c in range(1, maxc + 1):
        L = get_column_letter(c)
        maxlen = 0
        for r in range(hr, min(ws.max_row + 1, 400)):
            v = ws.cell(row=r, column=c).value
            if v is not None:
                # 中文按 2 宽
                w = sum(2 if ord(ch) > 127 else 1 for ch in str(v)[:80])
                maxlen = max(maxlen, w)
        ws.column_dimensions[L].width = max(8, min(60, maxlen + 3))

    # 冻结表头
    ws.freeze_panes = f"A{hr + 1}"
    # 行高
    ws.row_dimensions[hr].height = 28


def style_workbook(path) -> None:
    wb = openpyxl.load_workbook(path)
    for sn in wb.sheetnames:
        style_sheet(wb[sn])
    wb.save(path)


if __name__ == "__main__":  # pragma: no cover
    import sys
    style_workbook(sys.argv[1])
    print("styled", sys.argv[1])
