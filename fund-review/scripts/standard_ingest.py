"""standard_ingest — 解析标准 PDF 并校验标准包。

两件事：
  parse     PDF → standard.parsed.json（章节树 + 页锚点），供 citation 页码核验
  validate  校验 pack.yaml：citation 完整性 + 自带回归用例 + 可选的全链路复算

参数**不做全自动抽取**：一位小数点错了，全盘皆错。LLM 可以提候选，
但必须逐条人工确认后写进 pack.yaml。本脚本负责的是「写完之后能不能自证」。

用法：
    python3 standard_ingest.py parse    --pdf <x.pdf> --out <pack_dir>
    python3 standard_ingest.py validate --pack <pack_dir> [--verify-citations]
    python3 standard_ingest.py replay   --pack <pack_dir> --workbook <xlsx>
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from standard_pack import PackError, StandardPack, get_profile, run_regression

HEADING = [
    re.compile(r"^\s*([一二三四五六七八九十]+)[、.]"),
    re.compile(r"^\s*[（(]([一二三四五六七八九十]+)[)）]"),
    re.compile(r"^\s*(\d+(?:\.\d+){0,3})[.、]\s*\S"),
    re.compile(r"^\s*(表\s?\d+)\s"),
]


def parse(pdf: Path, out: Path) -> dict[str, Any]:
    import fitz

    doc = fitz.open(pdf)
    pages = [doc[i].get_text() for i in range(doc.page_count)]
    headings = []
    for pno, text in enumerate(pages, start=1):
        for line in text.splitlines():
            s = line.strip()
            if not s or len(s) > 60:
                continue
            for pat in HEADING:
                m = pat.match(s)
                if m:
                    headings.append({"page": pno, "marker": m.group(1), "text": s})
                    break
    result = {
        "source": str(pdf), "page_count": doc.page_count,
        "headings": headings,
        "pages": [{"page": i + 1, "text": t} for i, t in enumerate(pages)],
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "standard.parsed.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def verify_citations(pack: StandardPack) -> list[dict[str, Any]]:
    """核验每条 citation：页码在范围内，且 quote 确实出现在该页。

    quote 对不上通常意味着页码写错或抄漏了字 —— 财评现场翻到那页找不到原话，
    比没写 citation 更糟。
    """
    parsed_path = (pack.root or Path(".")) / "standard.parsed.json"
    if not parsed_path.exists():
        return [{"path": "-", "issue": "缺 standard.parsed.json，先跑 parse"}]
    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    page_text = {p["page"]: re.sub(r"\s+", "", p["text"]) for p in parsed["pages"]}
    n_pages = parsed["page_count"]

    problems = []
    for c in pack.citations_index():
        page = c.get("page")
        if not isinstance(page, int) or not (1 <= page <= n_pages):
            problems.append({"path": c["path"], "page": page,
                             "issue": f"页码超出范围 1..{n_pages}"})
            continue
        quote = c.get("quote")
        if quote:
            needle = re.sub(r"\s+", "", quote)
            if needle not in page_text[page]:
                # 原文可能跨页折行，放宽到相邻页
                nearby = "".join(page_text.get(p, "")
                                 for p in (page - 1, page, page + 1))
                if needle not in nearby:
                    problems.append({
                        "path": c["path"], "page": page,
                        "issue": "quote 在该页及相邻页均未找到",
                        "quote": quote[:50]})
    return problems


def replay_workbook(pack: StandardPack, workbook: Path) -> dict[str, Any]:
    """用标准包 + 公式档案复算既有工作簿，验证整条链路。

    标准正文只自带一个算例（设计费），覆盖不到功能点 → 工作量 → 费用这条主链。
    拿一份已双路交叉验证过的工作簿回放，是更强的回归。
    """
    import openpyxl

    profile = get_profile(pack.formula_profile)
    wb = openpyxl.load_workbook(workbook, data_only=True)
    sm = wb["测算汇总"]

    rows, total = [], 0.0
    for r in range(5, 20):
        system = sm.cell(r, 2).value
        if not system:
            continue
        afp = sm.cell(r, 5).value or 0          # 调整后功能点合计
        dev_label = str(sm.cell(r, 12).value or "").strip()
        expect = sm.cell(r, 11).value or 0      # 表内软件开发费用
        actual = profile.software_dev_cost(afp, pack=pack, dev_category=dev_label)
        total += actual
        rows.append({"system": str(system), "afp": afp, "dev_category": dev_label,
                     "expect": expect, "actual": actual,
                     "ok": abs(actual - expect) < 0.01})
    return {"rows": rows, "total": round(total, 2),
            "all_ok": all(x["ok"] for x in rows)}


def main() -> None:
    ap = argparse.ArgumentParser(description="标准 PDF 解析与标准包校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("parse")
    p1.add_argument("--pdf", required=True, type=Path)
    p1.add_argument("--out", required=True, type=Path)

    p2 = sub.add_parser("validate")
    p2.add_argument("--pack", required=True, type=Path)
    p2.add_argument("--verify-citations", action="store_true")

    p3 = sub.add_parser("replay")
    p3.add_argument("--pack", required=True, type=Path)
    p3.add_argument("--workbook", required=True, type=Path)

    args = ap.parse_args()

    if args.cmd == "parse":
        r = parse(args.pdf, args.out)
        print(f"解析完成：{r['page_count']} 页，{len(r['headings'])} 个标题锚点")
        return

    try:
        pack = StandardPack.load(args.pack)
    except PackError as e:
        print(f"标准包校验失败：\n{e}", file=sys.stderr)
        sys.exit(2)

    if args.cmd == "validate":
        print(f"{pack.pack_id}：citation 完整性 ✓（公式档案 {pack.formula_profile}）")
        cites = pack.citations_index()
        print(f"  citation 共 {len(cites)} 条，覆盖第 "
              f"{min(c['page'] for c in cites)}–{max(c['page'] for c in cites)} 页")

        results = run_regression(pack)
        for r in results:
            mark = "✓" if r["ok"] else "✗"
            print(f"  回归 {mark} {r['id']}: 期望 {r['expect']} 实得 {r['actual']}")

        if args.verify_citations:
            probs = verify_citations(pack)
            if probs:
                print(f"  citation 核验：{len(probs)} 处异常")
                for p in probs[:10]:
                    print(f"    {p['path']} (p.{p.get('page')}): {p['issue']}")
            else:
                print("  citation 核验 ✓ 全部 quote 在对应页可查")
        sys.exit(0 if all(r["ok"] for r in results) else 1)

    if args.cmd == "replay":
        r = replay_workbook(pack, args.workbook)
        bad = [x for x in r["rows"] if not x["ok"]]
        for x in r["rows"]:
            if not x["ok"]:
                print(f"  ✗ {x['system'][:36]}: 期望 {x['expect']} 实得 {x['actual']}")
        print(f"全链路复算：{len(r['rows']) - len(bad)}/{len(r['rows'])} 系统一致，"
              f"合计 ¥{r['total']:,.2f}")
        sys.exit(0 if r["all_ok"] else 1)


if __name__ == "__main__":
    main()
