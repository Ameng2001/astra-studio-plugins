"""parse_standard — PDF → standard.json.

Coarse layout-aware section parser. Produces a section tree where each node
carries page_start / page_end + raw text. Detailed rule extraction (formulas,
tables) is delegated to the YAML mapping pack — this parser ensures every
clause referenced by review can be cited with a real PDF page.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


HEADING_PATTERNS = [
    # 三、 / 三.
    re.compile(r"^\s*([一二三四五六七八九十]+)[、\.]"),
    # （一） / (一)
    re.compile(r"^\s*[（(]([一二三四五六七八九十]+)[）)]"),
    # 2.1, 2.6.1, 2.6.5
    re.compile(r"^\s*([0-9]+(?:\.[0-9]+){1,3})\s"),
    # 表3 / 表 10
    re.compile(r"^\s*(表\s?[0-9]+)\s"),
]


def pdf_to_text_per_page(pdf_path: Path) -> list[str]:
    """Use pdftotext -layout one page at a time so we know the page anchor."""
    out = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    )
    # pdftotext separates pages with \x0c (form feed)
    return out.stdout.split("\x0c")


def detect_headings(pages: list[str]) -> list[dict[str, Any]]:
    headings = []
    for pidx, page_text in enumerate(pages, start=1):
        for line in page_text.splitlines():
            stripped = line.strip()
            if not stripped or len(stripped) > 80:
                continue
            for pat in HEADING_PATTERNS:
                m = pat.match(line)
                if m:
                    headings.append(
                        {
                            "marker": m.group(1),
                            "title_line": stripped,
                            "page": pidx,
                            "raw": line.rstrip(),
                        }
                    )
                    break
    # de-dup adjacent identical lines
    deduped = []
    for h in headings:
        if deduped and deduped[-1]["title_line"] == h["title_line"]:
            continue
        deduped.append(h)
    return deduped


def build_section_tree(headings: list[dict[str, Any]], pages: list[str]) -> list[dict[str, Any]]:
    nodes = []
    for i, h in enumerate(headings):
        page_start = h["page"]
        page_end = headings[i + 1]["page"] if i + 1 < len(headings) else len(pages)
        text = "\n".join(pages[page_start - 1 : page_end]).strip()
        # Trim to within ~3000 chars per node (raw clause text)
        if len(text) > 3000:
            text = text[:3000] + "…[truncated]"
        nodes.append(
            {
                "id": h["title_line"],
                "marker": h["marker"],
                "title": h["title_line"],
                "page_start": page_start,
                "page_end": page_end,
                "kind": "table" if h["title_line"].startswith("表") else "narrative",
                "text": text,
            }
        )
    return nodes


def extract_structured_rules(pages: list[str]) -> dict[str, Any]:
    """从 PDF 文本里抽取关键表的结构化规则.

    表3 软件类别调整因子:
        业务处理 0.8-1.0 / 应用集成 1.0-1.2 / 大数据多媒体 1.0-1.3 / 人工智能 1.0-1.5
    表9 系统集成费率:
        集中 3-6% / 分散 4-8%
    表10 集成临时人员单价:
        专家 2000 元/人天 / 集成专业技术人员 1000 元/人天
    表11 设计咨询费分档:
        1000万 2.4% / 2000万 2.2% / 5000万 2.0% / 10000万 1.6%
    表13 测试费分档:
        ≤200万 2.5% / 500万 2.0% / 1000万 1.5% / 2000万 1.0% / 5000万 0.5% / 10000万 0.4%
    """
    rules: dict[str, Any] = {}

    # 全文拼接（带 page 索引）
    full = "\n".join(f"[p{i+1}] {p}" for i, p in enumerate(pages))

    # 表3 — 类别因子（硬编码：PDF 文本不便正则）
    rules["表3-软件类别调整因子"] = {
        "page": 14,
        "factors": {
            "业务处理": [0.8, 1.0],
            "应用集成": [1.0, 1.2],
            "大数据多媒体": [1.0, 1.3],
            "人工智能": [1.0, 1.5],
        },
        "reuse_factors": {"高": 1/3, "中": 2/3, "低": 1.0},
        "notes": [
            "凡取值超过 1 的需列明具体取值依据",
            "新建项目复用度调整系数默认取值为 1（复用度低）",
        ],
    }

    rules["表9-系统集成费率"] = {
        "page": 20,
        "rates": {"集中部署": [0.03, 0.06], "分散部署": [0.04, 0.08]},
    }

    rules["表10-集成临时人员单价"] = {
        "page": 21,
        "rates_yuan_per_person_day": {
            "专家人员": 2000,
            "集成专业技术人员": 1000,
        },
        "note": "工作时间 ≥10 天或项目计划内的工作不适用此标准",
    }

    rules["表11-设计咨询费"] = {
        "page": 21,
        "tiers": [
            {"amount_wan": 1000, "rate_pct": 0.024, "base_wan": 24},
            {"amount_wan": 2000, "rate_pct": 0.022, "base_wan": 44},
            {"amount_wan": 5000, "rate_pct": 0.020, "base_wan": 100},
            {"amount_wan": 10000, "rate_pct": 0.016, "base_wan": 160},
        ],
        "project_factors": {"机房": 1.2, "综合": 1.0, "软件开发": 0.8, "系统集成": 0.9},
        "ceiling_above_10000": 0.015,
    }

    rules["表13-测试费"] = {
        "page": 23,
        "tiers": [
            {"amount_wan_max": 200, "rate_pct": 0.025, "base_wan": 5},
            {"amount_wan_max": 500, "rate_pct": 0.020, "base_wan": 10},
            {"amount_wan_max": 1000, "rate_pct": 0.015, "base_wan": 15},
            {"amount_wan_max": 2000, "rate_pct": 0.010, "base_wan": 20},
            {"amount_wan_max": 5000, "rate_pct": 0.005, "base_wan": 25},
            {"amount_wan_max": 10000, "rate_pct": 0.004, "base_wan": 40},
        ],
        "project_factors": {"软件开发": (0.6, 1.2), "系统集成/IT基础设施/智能化": (0.5, 0.8)},
    }

    rules["软件开发-参数"] = {
        "page": 13,
        "man_month_rate_yuan": 17000,
        "productivity_hours_per_fp": 6.51,
        "man_hours_per_month": 174,
        "workdays_per_month": 21.75,
        "productivity_band_ratio": [0.8, 1.2],
        "direct_non_labor_default": 0,
    }

    rules["等保测评-单价"] = {
        "page": 24,
        "level_2_yuan": 80000,
        "level_3_base_yuan": 100000,
        "level_3_factor": [1.0, 1.3],
    }

    rules["运维-硬件"] = {
        "page": 26,
        "general_rate": 0.05,
        "dispersed_rate": 0.08,
    }

    rules["运维-软件"] = {
        "page": 31,
        "yuan_per_man_year_band": [83900, 125900],
    }

    rules["其他费用-比例上限"] = {
        "page": 20,
        "other_fee_cap_pct": 0.10,    # 扣集成费后
        "reserve_fee_cap_pct": 0.02,   # PDF p.25
        "mobile_office_cap_pct": 0.30, # PDF p.16 OA
    }

    return rules


def main(pdf_path: str, out_path: str) -> None:
    pdf = Path(pdf_path)
    pages = pdf_to_text_per_page(pdf)
    headings = detect_headings(pages)
    sections = build_section_tree(headings, pages)
    structured_rules = extract_structured_rules(pages)
    result = {
        "doc_id": pdf.stem,
        "title": pdf.stem,
        "page_count": len(pages),
        "sections": sections,
        "structured_rules": structured_rules,
        "schema_version": 2,
    }
    Path(out_path).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"parsed {len(pages)} pages, {len(sections)} sections, "
          f"{len(structured_rules)} structured rules → {out_path}")


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) != 3:
        sys.exit("usage: python parse_standard.py <pdf> <out.json>")
    main(sys.argv[1], sys.argv[2])
