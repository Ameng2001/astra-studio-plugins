"""finalize_delivery — 交付打包：标准化 sheet 名 + 文件名 + 出送审清单.

输入：.fund-review/{mode}/ 下的原始产物
输出：.fund-review/{mode}/送审包/ 下标准化命名的全套文件 + 送审文件清单.md

内部处理全程用原始名（scanner 靠原名匹配），本脚本只在最终交付阶段重命名。
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import openpyxl

from naming_scheme import (
    FILE_MAP, OPTIMIZE_DOC, WORKFILES, SHEET_MAP,
    std_filename, PROJECT_SHORT, PLUGIN_VERSION,
)


def _rename_sheets(xlsx_path: Path) -> list[tuple[str, str]]:
    """重命名工作簿内 sheet，返回 [(原名, 新名)]。"""
    wb = openpyxl.load_workbook(xlsx_path)
    renamed = []
    for sn in list(wb.sheetnames):
        new = SHEET_MAP.get(sn)
        if new and new != sn and new not in wb.sheetnames:
            wb[sn].title = new
            renamed.append((sn, new))
    # 重新排序：按 sheet 名前两位数字
    try:
        wb._sheets.sort(key=lambda ws: ws.title[:2] if ws.title[:2].isdigit() else "99")
    except Exception:
        pass
    wb.save(xlsx_path)
    return renamed


def _classify_file(fname: str) -> tuple[str, str, bool] | None:
    if fname in WORKFILES:
        return None
    if fname.startswith("quote-final-") and ("平台软件" in fname):
        return FILE_MAP["__platform__"]
    if fname.startswith("quote-final-") and ("大模型" in fname or "行业模型" in fname):
        return FILE_MAP["__llm__"]
    if fname.startswith("quote-final-") and ("设备" in fname):
        return FILE_MAP["__device__"]
    if fname.startswith("optimize-") and fname.endswith(".md"):
        return OPTIMIZE_DOC
    return FILE_MAP.get(fname)


def main(session_dir: str) -> None:
    from sanitize import sanitize_md_file, sanitize_xlsx_file

    s = Path(session_dir)
    mode = s.name
    date = datetime.now().strftime("%Y%m%d")
    pkg_root = s / "交付物"
    out_dir = pkg_root / "送审包"      # 对外：仅 Z+F，净化，去模式标签
    internal_dir = pkg_root / "内部包"  # 对内：N 类 + 净化对照 + 工作区，保留模式标签
    if pkg_root.exists():
        shutil.rmtree(pkg_root)
    for d in (out_dir, internal_dir):
        d.mkdir(parents=True)

    from fix_references import fix_workbook
    from excel_styler import style_workbook
    from naming_scheme import SHEET_MAP

    manifest_rows = []
    sheet_rename_log: dict[str, list] = {}
    sanitize_log: dict[str, list] = {}

    # ---- Pass 1：分类全部文件，构造 文件名替换映射（旧产物名 → 标准送审名）----
    files = []
    file_rename_map: dict[str, str] = {}
    for f in sorted(s.iterdir()):
        if f.is_dir() or f.name.startswith("."):
            continue
        cls = _classify_file(f.name)
        if cls is None:
            continue
        seq, doc_name, is_submit = cls
        ext = f.suffix.lstrip(".")
        is_internal = seq.startswith("N")
        new_name = (std_filename(seq, doc_name, mode, date, ext) if is_internal
                    else std_filename(seq, doc_name, "正式版", date, ext))
        files.append((f, seq, doc_name, is_submit, ext, is_internal, new_name))
        if not is_internal:
            file_rename_map[f.name] = new_name
            # 去掉 quote-final- 前缀的原始名也映射（Z00 引用的是原报价名）
            if f.name.startswith("quote-final-"):
                file_rename_map[f.name[len("quote-final-"):]] = new_name

    # ---- Pass 2：拷贝 → 改 sheet 名 → 修引用 → 净化 → 美化 ----
    for f, seq, doc_name, is_submit, ext, is_internal, new_name in files:
        if is_internal:
            dst = internal_dir / new_name
            shutil.copy2(f, dst)
            # 内部包 md 也转 docx（格式统一），但不净化（保留内部术语）
            if ext == "md":
                try:
                    from md_to_docx import convert as _md2docx
                    _md2docx(dst, internal_dir)
                    dst.unlink()
                    new_name = dst.stem + ".docx"
                except Exception as e:
                    print(f"  [warn] 内部 {dst.name} 转 docx 失败: {e}")
        else:
            dst = out_dir / new_name
            shutil.copy2(f, dst)
            if ext == "xlsx":
                renamed = _rename_sheets(dst)              # 改 sheet 名
                if renamed:
                    sheet_rename_log[new_name] = renamed
                fix_workbook(dst, dict(renamed), file_rename_map)  # 修跨表引用+文件名
                hits = sanitize_xlsx_file(dst)             # 净化
                style_workbook(dst)                        # 美化
            else:
                hits = sanitize_md_file(dst)
                # 送审包：md → 公文风 docx；md 原件不留送审包（归入中间产物）
                if ext == "md":
                    try:
                        from md_to_docx import convert as _md2docx
                        _md2docx(dst, out_dir)
                        dst.unlink()                       # 删送审包内 .md
                        new_name = dst.stem + ".docx"
                    except Exception as e:
                        print(f"  [warn] {dst.name} 转 docx 失败，保留 md: {e}")
            if hits:
                sanitize_log[new_name] = sorted(set(hits))

        manifest_rows.append({
            "seq": seq, "new_name": new_name, "orig": f.name,
            "submit": is_submit, "doc_name": doc_name,
        })

    manifest_rows.sort(key=lambda r: r["seq"])

    # ---- 送审包/00_送审文件清单.md（对外，无 N 类、无模式标签）----
    lines = []
    _d = datetime.now()
    lines.append(f"# {PROJECT_SHORT}项目报价文件清单")
    lines.append("")
    lines.append(f"编制日期：{_d.year}年{_d.month}月{_d.day}日")
    lines.append("")
    lines.append("文件分类：**Z**=主报价件 / **F**=评审附件")
    lines.append("")
    lines.append("## 主报价件（Z）— 按序号阅读")
    lines.append("")
    lines.append("| 序号 | 文件名 | 说明 |")
    lines.append("|---|---|---|")
    for r in manifest_rows:
        if r["seq"].startswith("Z"):
            lines.append(f"| {r['seq']} | `{r['new_name']}` | {r['doc_name']} |")
    lines.append("")
    lines.append("## 评审附件（F）")
    lines.append("")
    lines.append("| 序号 | 文件名 | 说明 |")
    lines.append("|---|---|---|")
    for r in manifest_rows:
        if r["seq"].startswith("F"):
            lines.append(f"| {r['seq']} | `{r['new_name']}` | {r['doc_name']} |")
    lines.append("")
    if sheet_rename_log:
        lines.append("## 工作簿 Sheet 一览")
        lines.append("")
        for fname, pairs in sheet_rename_log.items():
            lines.append(f"- **{fname}**: {', '.join(n for _, n in pairs)}")
        lines.append("")
    lines.append("## 阅读建议")
    lines.append("")
    lines.append("1. 先看 `Z00 项目总报价汇总` — 各费用科目分布")
    lines.append("2. 按 Z01-Z04 逐册核对明细")
    lines.append("3. 取值依据见 `F01 取值依据说明` + `F02 可研功能点估算表`")
    lines.append("4. 评审应答见 `F03 评审应答说明`")
    lines.append("")
    manifest_md = out_dir / "00_送审文件清单.md"
    manifest_md.write_text("\n".join(lines))
    sanitize_md_file(manifest_md)   # 兜底净化（防工具词残留）
    try:
        from md_to_docx import convert as _md2docx
        _md2docx(manifest_md, out_dir)
        manifest_md.unlink()   # 送审包只留 docx
    except Exception as e:
        print(f"  [warn] 送审清单转 docx 失败，保留 md: {e}")

    # ---- 内部包/00_内部说明.md + 净化对照表 ----
    il = []
    il.append(f"# 内部留存说明 — {PROJECT_SHORT}（{mode}版 v{PLUGIN_VERSION}）")
    il.append("")
    il.append("> ⚠️ 本目录仅供投标方内部使用，**严禁随标书提交甲方/财评**。")
    il.append("")
    il.append("## N 类内部材料")
    il.append("")
    il.append("| 序号 | 文件名 | 说明 |")
    il.append("|---|---|---|")
    for r in manifest_rows:
        if r["seq"].startswith("N"):
            il.append(f"| {r['seq']} | `{r['new_name']}` | {r['doc_name']} |")
    il.append("")
    il.append("## 送审包净化对照（内部自查用）")
    il.append("")
    il.append("> 下列内部术语在送审包中已被替换。核对是否还有遗漏。")
    il.append("")
    if sanitize_log:
        for fname, hits in sanitize_log.items():
            il.append(f"### {fname}")
            il.append("")
            il.append("命中并已净化的内部术语：")
            il.append("")
            for h in hits[:40]:
                il.append(f"- `{h}`")
            il.append("")
    else:
        il.append("（无命中——送审包无残留内部术语）")
        il.append("")
    (internal_dir / "00_内部说明.md").write_text("\n".join(il))

    # 工作区 json 也归入内部包
    workzone = internal_dir / "_原始工作区"
    workzone.mkdir(exist_ok=True)
    for f in s.iterdir():
        if f.is_file() and f.name in WORKFILES:
            shutil.copy2(f, workzone / f.name)

    # ---- 中间产物收纳：mode 根目录只保留 交付物/ + 输入(quote/standard.json) ----
    keep_at_root = {"quote.json", "standard.json"}
    mid_dir = s / "_中间产物"
    if mid_dir.exists():
        shutil.rmtree(mid_dir)
    mid_dir.mkdir()
    moved = 0
    for f in list(s.iterdir()):
        if f.name in ("交付物", "_中间产物"):
            continue
        if f.is_file() and f.name not in keep_at_root:
            shutil.move(str(f), str(mid_dir / f.name))
            moved += 1
    (mid_dir / "00_说明.md").write_text(
        "# 中间产物\n\n"
        "本目录为 pipeline 运行中间态（optimize-suggestions / review / 未净化原始产物等），"
        "仅供调试与追溯。\n\n"
        "**最终交付看 `../交付物/`**：\n"
        "- `送审包/` — 对外（已净化，正式版命名）\n"
        "- `内部包/` — 对内（含模式标签 + 净化对照 + 工作区）\n"
    )

    n_z = sum(1 for r in manifest_rows if r["seq"].startswith("Z"))
    n_f = sum(1 for r in manifest_rows if r["seq"].startswith("F"))
    n_n = sum(1 for r in manifest_rows if r["seq"].startswith("N"))
    n_san = sum(len(v) for v in sanitize_log.values())
    print(f"交付物/ → 送审包(Z{n_z}+F{n_f}, 净化{n_san}处) | 内部包(N{n_n}) | _中间产物/收纳{moved}个")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/保守")
