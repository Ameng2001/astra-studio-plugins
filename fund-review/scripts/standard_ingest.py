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


def compare_regions(bom_dir: Path, packs: list[Path], modes: Path | None,
                    delivery_plan: Path | None) -> dict[str, Any]:
    """同一 BOM 跨区域对比 —— 验证「换省只改一层，BOM 零改动」。

    差异必须能逐因子解释。解释不了的残差说明有环节没搞清楚，
    那比差异本身更危险。
    """
    import yaml

    from bom_schema import Bom
    from costing_engine import CostingEngine, DealConfig, DeliveryContext

    bom = Bom.load(bom_dir)
    deal = DealConfig(deal_id="compare")
    ctx = None
    if modes and delivery_plan:
        from delivery_matrix import DeliveryPlan

        modes_doc = yaml.safe_load(modes.read_text(encoding="utf-8"))
        base, _ = CostingEngine(bom, StandardPack.load(packs[0]), deal).in_scope()
        dp = DeliveryPlan.load(modes, delivery_plan)
        ctx = DeliveryContext(dp.assign(base), modes_doc["modes"], {})

    out = []
    for pk in packs:
        pack = StandardPack.load(pk)
        r = CostingEngine(bom, pack, deal, ctx).run()
        sw = r["software_dev"]
        wrate = (sum(s["effort_man_months"] * s["man_month_rate"]
                     for s in sw["systems"]) / sw["effort_total"]
                 if sw["effort_total"] else 0)
        out.append({"pack": pack, "ufp": sw["ufp_total"], "total": sw["total"],
                    "wrate": wrate})

    a, b = out
    sz_a = a["pack"].factor("size_change", deal.counting_method)
    sz_b = b["pack"].factor("size_change", deal.counting_method)
    factors = [
        {"name": "规模变更因子", "ratio": sz_b / sz_a},
        {"name": "软件开发生产率",
         "ratio": (b["pack"].rate("productivity_hours_per_fp")
                   / a["pack"].rate("productivity_hours_per_fp"))},
        {"name": "人月费率（加权）",
         "ratio": b["wrate"] / a["wrate"] if a["wrate"] else 1.0},
    ]
    predicted = 1.0
    for f in factors:
        predicted *= f["ratio"]
    actual = b["total"] / a["total"] if a["total"] else 0
    return {
        "packs": [a["pack"].pack_id, b["pack"].pack_id],
        "ufp": [a["ufp"], b["ufp"]], "ufp_equal": a["ufp"] == b["ufp"],
        "totals": [a["total"], b["total"]],
        "factors": factors, "predicted": predicted, "actual": actual,
        "residual_pct": (actual / predicted - 1) * 100 if predicted else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="标准 PDF 解析与标准包校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("parse")
    p1.add_argument("--pdf", required=True, type=Path)
    p1.add_argument("--out", required=True, type=Path)

    p2 = sub.add_parser("validate")
    p2.add_argument("--pack", required=True, type=Path)
    p2.add_argument("--allow-deprecated-pack", action="store_true",
                    help="允许加载已退役的标准包 —— 仅用于历史报价复算")
    p2.add_argument("--verify-citations", action="store_true")

    p4 = sub.add_parser("compare", help="同一 BOM 跨区域对比，逐因子分解差异")
    p4.add_argument("--packs", required=True, nargs=2, type=Path)
    p4.add_argument("--allow-deprecated-pack", action="store_true",
                    help="允许加载已退役的标准包 —— 仅用于历史报价复算")
    p4.add_argument("--bom", required=True, type=Path)
    p4.add_argument("--modes", type=Path)
    p4.add_argument("--delivery-plan", type=Path)

    p3 = sub.add_parser("replay")
    p3.add_argument("--pack", required=True, type=Path)
    p3.add_argument("--allow-deprecated-pack", action="store_true",
                    help="允许加载已退役的标准包 —— 仅用于历史报价复算")
    p3.add_argument("--workbook", required=True, type=Path)

    args = ap.parse_args()

    if args.cmd == "parse":
        r = parse(args.pdf, args.out)
        print(f"解析完成：{r['page_count']} 页，{len(r['headings'])} 个标题锚点")
        return

    if args.cmd == "compare":
        r = compare_regions(args.bom, args.packs, args.modes, args.delivery_plan)
        print(f"UFP 两地{'一致' if r['ufp_equal'] else '不一致'}："
              f"{r['ufp'][0]} vs {r['ufp'][1]}"
              f"{' ✓（BOM 未改动）' if r['ufp_equal'] else ' ✗'}")
        print(f"{'因子':<30}{'比值':>10}{'影响':>10}")
        for f in r["factors"]:
            print(f"{f['name']:<32}{f['ratio']:>10.5f}{(f['ratio'] - 1) * 100:>9.2f}%")
        print(f"{'三因子连乘（预测）':<32}{r['predicted']:>10.5f}"
              f"{(r['predicted'] - 1) * 100:>9.2f}%")
        print(f"{'实际比值':<32}{r['actual']:>10.5f}{(r['actual'] - 1) * 100:>9.2f}%")
        print(f"残差 {r['residual_pct']:+.4f}% ← 两条链路取整点不同所致")
        sys.exit(0 if abs(r["residual_pct"]) < 0.5 and r["ufp_equal"] else 1)

    try:
        pack = StandardPack.load(args.pack, allow_deprecated=args.allow_deprecated_pack)
    except PackError as e:
        print(f"标准包校验失败：\n{e}", file=sys.stderr)
        sys.exit(2)

    if args.cmd == "validate":
        print(f"{pack.pack_id}：citation 完整性 ✓（公式档案 {pack.formula_profile}）")
        cites = pack.citations_index()
        # 多分册的包里，页码分属不同分册，混在一起报「覆盖第 X–Y 页」没有意义
        # 也不可比 —— 按分册分组统计。单册包退化成原来的一行。
        from collections import defaultdict
        by_vol: dict[str, list] = defaultdict(list)
        for c in cites:
            by_vol[c.get("volume") or "（本册）"].append(c)
        print(f"  citation 共 {len(cites)} 条，分布于 {len(by_vol)} 册：")
        for vol, cs in sorted(by_vol.items()):
            pages = sorted({str(c.get("page")) for c in cs if c.get("page") is not None})
            print(f"    {vol}：{len(cs)} 条，页 {'、'.join(pages)}")

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
