"""bom_validate — 对 BOM 执行质量门禁 G-01..G-10 并出报告。

用法：
    python3 bom_validate.py --bom <bom_dir> [--previous <old_bom_dir>] [--json <out.json>]

退出码：
    0  当前版本可晋升到 released
    1  可晋升到 reviewed（尚有阻断 released 的问题）
    2  只能停在 draft
    3  结构性错误（BomError）—— 必须先修
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import nesma_rules
from bom_schema import Bom, BomError
from nesma_weights import ufp_by_system, ufp_total

EXIT_BY_STATUS = {"released": 0, "reviewed": 1, "draft": 2}


def main() -> None:
    ap = argparse.ArgumentParser(description="BOM 质量门禁")
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--previous", type=Path, help="上一 released 版本目录，用于漂移门禁 G-10")
    ap.add_argument("--json", type=Path, help="机器可读结果输出路径")
    ap.add_argument("--md", type=Path, help="markdown 报告输出路径")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    try:
        bom.validate()
    except BomError as e:
        print(f"结构性错误：{e}", file=sys.stderr)
        sys.exit(3)

    previous = Bom.load(args.previous) if args.previous else None
    has_changelog = (args.bom / "CHANGELOG.md").exists()
    report = nesma_rules.run(bom, previous, has_changelog)

    report.stats["ufp_total"] = ufp_total(bom)
    report.stats["ufp_by_system"] = ufp_by_system(bom)

    md = nesma_rules.format_report(report, bom.version)
    if args.md:
        args.md.write_text(md, encoding="utf-8")
    else:
        print(md)

    if args.json:
        args.json.write_text(json.dumps(
            {"version": bom.version, "stats": report.stats,
             "highest_reachable_status": report.highest_reachable_status(),
             "findings": [asdict(f) for f in report.findings]},
            ensure_ascii=False, indent=2), encoding="utf-8")

    reachable = report.highest_reachable_status()
    print(f"\n最高可晋升状态：{reachable}"
          f"（阻断 reviewed {len([f for f in report.findings if f.blocks == 'reviewed'])} 条 / "
          f"阻断 released {len([f for f in report.findings if f.blocks == 'released'])} 条）",
          file=sys.stderr)
    sys.exit(EXIT_BY_STATUS[reachable])


if __name__ == "__main__":
    main()
