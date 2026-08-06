"""fix_references — sheet 改名后修复跨 sheet 公式 / 文本锚点 / 文件名引用.

openpyxl 改 sheet 名不会自动更新引用它的公式，必须手动重写：
  - 公式  ='附1.数智民生体系数智底座'!K139  → ='01_数智底座(L1建设)'!K139
  - 文本锚点  '附1...'!A1                  → '01_数智底座(L1建设)'!A1
  - 文件名引用 quote-final-...xlsx          → 数智幼教_Z01_..._正式版_...xlsx
含特殊字符（括号等）的新 sheet 名在公式里必须加单引号。
"""
from __future__ import annotations

import re

import openpyxl


def _quote_sheet(name: str) -> str:
    """Excel 公式中，含特殊字符的 sheet 名需用单引号包裹。"""
    if re.search(r"[ ()（）\-+*/!&%,]", name):
        return f"'{name.replace(chr(39), chr(39)*2)}'"
    return name


def fix_workbook(path, sheet_rename_map: dict[str, str],
                 file_rename_map: dict[str, str] | None = None) -> int:
    """重写 path 内所有引用。返回修复的单元格数。"""
    file_rename_map = file_rename_map or {}
    wb = openpyxl.load_workbook(path)
    fixed = 0

    # 构造 sheet 名替换：旧名（带/不带引号 + '!'）→ 新名（按需加引号 + '!'）
    # 长名优先，避免短名先匹配
    pairs = sorted(sheet_rename_map.items(), key=lambda kv: -len(kv[0]))

    def fix_ref_text(text: str) -> str:
        out = text
        for old, new in pairs:
            new_q = _quote_sheet(new)
            # 'old'!  （规范双引号）
            out = out.replace(f"'{old}'!", f"{new_q}!")
            # old'!  （源数据缺头引号，常见于本项目汇总 sheet）
            out = out.replace(f"{old}'!", f"{new_q}!")
            # 'old!  （缺尾引号，少见）
            out = out.replace(f"'{old}!", f"{new_q}!")
            # old!   （无引号）
            out = re.sub(rf"(?<![A-Za-z0-9_'])({re.escape(old)})!", f"{new_q}!", out)
        for old_f, new_f in file_rename_map.items():
            if old_f in out:
                out = out.replace(old_f, new_f)
        return out

    for sn in wb.sheetnames:
        ws = wb[sn]
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str):
                    continue
                nv = fix_ref_text(v)
                if nv != v:
                    cell.value = nv
                    fixed += 1
                # 真·超链接对象
                if cell.hyperlink and cell.hyperlink.target:
                    nt = fix_ref_text(str(cell.hyperlink.target))
                    if nt != cell.hyperlink.target:
                        cell.hyperlink.target = nt
                        fixed += 1
    wb.save(path)
    return fixed


if __name__ == "__main__":  # pragma: no cover
    import sys, json
    p = sys.argv[1]
    m = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print("fixed", fix_workbook(p, m))
