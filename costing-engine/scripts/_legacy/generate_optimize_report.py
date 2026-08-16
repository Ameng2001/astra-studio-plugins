"""generate_optimize_report — 给投标方内部评审用的优化对比文档.

读取 optimize-suggestions.json + approved-plan.json + quote.json，按
工作簿/sheet 分组，逐条列出"改前 → 改后 + 原因 + 标准依据 + 财务影响"，
并在文末给出投标方决策建议（采纳 / 部分采纳 / 拒绝 + 理由模板）。

输出: optimize-{mode}.md
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _detail(row_cells: dict) -> str:
    return (
        row_cells.get("建设详情")
        or row_cells.get("详情")
        or row_cells.get("建设详情（定位）")
        or row_cells.get("建设内容")
        or row_cells.get("分项名称")
        or row_cells.get("名称")
        or ""
    )


def _get_row(quote: dict, wb_path: str, sheet: str, row_idx: int) -> dict | None:
    for w in quote["workbooks"]:
        if w["path"] != wb_path:
            continue
        for s in w["sheets"]:
            if s["name"] != sheet:
                continue
            for r in s["rows"]:
                if r["row_index"] == row_idx:
                    return r
    return None


def main(session_dir: str) -> None:
    s = Path(session_dir)
    sugg = json.loads((s / "optimize-suggestions.json").read_text())
    plan = json.loads((s / "approved-plan.json").read_text()) if (s / "approved-plan.json").exists() else {"decisions": []}
    quote = json.loads((s / "quote.json").read_text())

    mode_info = sugg.get("mode", {"name": "?"})
    mode_name = mode_info.get("name", "?")
    summary = sugg["summary"]
    bb = summary["budget_band"]
    fp = summary["fp_summary"]

    # decision lookup
    by_id = {d["id"]: d for d in plan.get("decisions", [])}

    # Group suggestions by workbook/sheet
    by_sheet: dict[tuple, list[dict]] = defaultdict(list)
    for sg in sugg["suggestions"]:
        by_sheet[(sg["target"]["workbook"], sg["target"]["sheet"])].append(sg)

    lines: list[str] = []
    lines.append(f"# 优化对比与依据 — {mode_name}版")
    lines.append("")
    lines.append(f"*生成时间*: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"*模式*: **{mode_name}** — {mode_info.get('description', '')}")
    lines.append(f"*G1 区间*: ±{int(mode_info.get('band_pct', 0)*100)}%")
    buf = mode_info.get("target_buffer", 1.0)
    buf_label = "贴墙根" if buf >= 1.0 else f"-{(1.0 - buf) * 100:.0f}% 安全垫"
    lines.append(f"*单价缓冲*: ×{buf:.2f}（{buf_label}）")
    lines.append("")
    lines.append("## 投标方内部决策摘要")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|---|---:|")
    lines.append(f"| 原始合计 | ¥{bb['original_total']:,.0f} |")
    lines.append(f"| 优化后预计 | ¥{bb['projected_total_after_apply']:,.0f} |")
    lines.append(f"| 跌幅 | **{(bb['projected_total_after_apply']-bb['original_total'])/bb['original_total']*100:+.2f}%** |")
    lines.append(f"| G1 区间 | ¥{bb['lower_bound']:,.0f} ~ ¥{bb['upper_bound']:,.0f} |")
    lines.append(f"| 守在 G1 区间 | {'✅' if bb['within_band'] else '❌'} |")
    lines.append(f"| 建议总数 | {summary['total_suggestions']} |")
    lines.append(f"| 类别分布 | {summary['by_category']} |")
    lines.append(f"| 严重度分布 | {summary['by_severity']} |")
    if "by_stance" in summary:
        stance_str = " / ".join(f"{k} {v}" for k, v in summary["by_stance"].items())
        lines.append(f"| 立场归因 | {stance_str} |")
    lines.append(f"| G1 自动降级 | {len(bb.get('demoted_suggestions', []))} 条 |")
    lines.append(f"| 可研 FP 总量 | {fp['total_fp']} |")
    lines.append("")

    lines.append("## 该如何决策（投标方视角）")
    lines.append("")
    if mode_name == "激进":
        lines.append("- **场景**：竞争对手多、价格敏感度低、内部利润底线吃紧 → 选**激进**")
        lines.append("- **代价**：财评一次过率较低，可能要现场答辩或返工。需准备充分的"
                     "依据材料（FP 表 + 类别取值说明）")
        lines.append("- **节省偏少**，留出更多利润空间")
    else:
        lines.append("- **场景**：项目重要、不希望返工、内部已有合理利润空间 → 选**保守**")
        lines.append("- **代价**：跌幅较大，需要确认利润底线还守得住")
        lines.append("- **财评一次过率高**，减少答辩成本")
    lines.append("")
    lines.append("### 决策决策（部分采纳）")
    lines.append("")
    lines.append("- 如果想保留某条建议的 *依据* 但不动金额 → 拒绝该 `labor-pricing`，但保留对应的 `subject-mapping`（加列只是挂科目）")
    lines.append("- 如果想保留某行单价不变 → 在 approved-plan.json 里把这行的 `decision` 改为 `reject`，并写明理由（最常见：客户已认可单价、有合同先例、特殊定制工作量）")
    lines.append("")

    # ---- 完整性补全（add-cost-item）单独一节 ----
    completeness = [sg for sg in sugg["suggestions"] if sg["category"] == "add-cost-item"]
    if completeness:
        lines.append("## 完整性补全 — 9 大费用科目（新增到 `quote-additional-fees.xlsx`）")
        lines.append("")
        lines.append("> 当前 3 张报价表只覆盖软件开发费 + 硬件购置费。下列科目按 PDF 必有但缺失，按公式估算补齐。")
        lines.append("")
        lines.append("| 费用名称 | 金额（元）| 计算依据 | 标准条款 | PDF 页 | 备注 |")
        lines.append("|---|---:|---|---|---:|---|")
        total_add = 0
        for sg in completeness:
            pc = sg["proposed_change"]
            ref = sg["standard_refs"][0] if sg["standard_refs"] else {}
            total_add += pc["amount"]
            lines.append(f"| **{pc['fee_name']}** | "
                         f"¥{pc['amount']:,.0f} | "
                         f"{pc.get('formula_explain','')} | "
                         f"{ref.get('section','')} | "
                         f"{ref.get('page','')} | "
                         f"{pc.get('note','')} |")
        lines.append(f"| **合计** | **¥{total_add:,.0f}** | — | — | — | — |")
        lines.append("")

    # ---- 跨工作簿重复（redundancy-warning）单独一节 ----
    redundancy = [sg for sg in sugg["suggestions"] if sg["category"] == "redundancy-warning"]
    if redundancy:
        lines.append("## 跨工作簿重复检测（财评红队视角）")
        lines.append("")
        lines.append("> *PDF 2.7：重复内容只计一次。本节预演财评可能以"
                     "「平台 + 大模型 同一功能重复计列」挑战的位置，按相似度排序。*")
        lines.append("")
        lines.append("| # | 相似度 | 平台位置 | 大模型位置 | 平台金额 | 大模型金额 | 建议 |")
        lines.append("|---:|---:|---|---|---:|---:|---|")
        for i, sg in enumerate(redundancy, 1):
            pc = sg["proposed_change"]
            p_loc = sg["target"]["primary_loc"]
            l_loc = sg["target"]["secondary_loc"]
            sev_icon = "🔴" if sg["severity"] == "high" else "🟡"
            advice_short = pc["advice"][:60]
            lines.append(f"| {i} | {sev_icon} {pc['similarity']:.2f} | "
                         f"{p_loc['sheet'][:18]}/行{p_loc['row']} | "
                         f"{l_loc['sheet'][:18]}/行{l_loc['row']} | "
                         f"¥{pc['primary_money']:,.0f} | "
                         f"¥{pc['secondary_money']:,.0f} | "
                         f"{advice_short} |")
        lines.append("")
        lines.append("**示例（前 3 条具体内容）**:")
        lines.append("")
        for sg in redundancy[:3]:
            pc = sg["proposed_change"]
            lines.append(f"- *相似度 {pc['similarity']:.2f}*")
            lines.append(f"  - 平台：`{pc['primary_excerpt']}…`")
            lines.append(f"  - 大模型：`{pc['secondary_excerpt']}…`")
            lines.append(f"  - 建议：{pc['advice']}")
            lines.append("")

    # ---- 比例守恒（compliance-cap）单独一节 ----
    caps = [sg for sg in sugg["suggestions"] if sg["category"] in {"compliance-cap", "compliance-cap-info"}]
    if caps:
        lines.append("## 比例守恒检查")
        lines.append("")
        for sg in caps:
            if sg["category"] == "compliance-cap-info":
                info = sg["proposed_change"]["summary"]
                lines.append("### 当前比例摘要")
                lines.append("")
                lines.append(f"- 项目总价（含补全）: ¥{info['proj_total']:,.0f}")
                lines.append(f"- 系统集成费: ¥{info['integration_fee']:,.0f} ({info['integration_pct']}%)")
                lines.append(f"- 其他费用合计（设计/监理/测试/等保/风险/密码）: "
                             f"¥{info['other_fee_sum']:,.0f} ({info['other_fee_pct_of_excl_integration']}% 扣集成费后) — *上限 10%*")
                lines.append(f"- 预备费: ¥{info['reserve_fee']:,.0f} ({info['reserve_pct']}%) — *上限 2%*")
                lines.append("")
            else:
                lines.append(f"### ❌ {sg['proposed_change']['what']} 超上限")
                lines.append(f"- 当前: {sg['proposed_change']['current_ratio']*100:.2f}% > 上限 {sg['proposed_change']['cap']*100}%")
                lines.append(f"- 应降至: ¥{sg['proposed_change']['max_allowed']:,.0f}")
                lines.append("")

    # ---- 逐项明细，按 sheet ----
    lines.append("## 逐项优化明细")
    lines.append("")

    # sort sheets by total absolute delta desc
    sheet_deltas = {
        k: sum(abs(sg.get("estimated_delta_amount", 0)) for sg in v) for k, v in by_sheet.items()
    }
    sorted_sheets = sorted(by_sheet.keys(), key=lambda k: -sheet_deltas[k])

    for (wb_path, sheet_name) in sorted_sheets:
        sgs = by_sheet[(wb_path, sheet_name)]
        sheet_delta = sum(sg.get("estimated_delta_amount", 0) for sg in sgs)
        by_cat = defaultdict(int)
        for sg in sgs:
            by_cat[sg["category"]] += 1

        lines.append(f"### {Path(wb_path).name} / **{sheet_name}**")
        lines.append("")
        lines.append(f"小计变动: **¥{sheet_delta:+,.0f}** | "
                     f"labor-pricing {by_cat['labor-pricing']} / "
                     f"subject-mapping {by_cat['subject-mapping']} / "
                     f"semantic-split {by_cat['semantic-split']}")
        lines.append("")

        # --- labor-pricing (most impactful) ---
        labors = sorted([sg for sg in sgs if sg["category"] == "labor-pricing"],
                        key=lambda s: s.get("estimated_delta_amount", 0))
        if labors:
            lines.append("#### labor-pricing 单价调整")
            lines.append("")
            lines.append("| 行 | 内容摘要 | 改前单价 | 改后单价 | FP | 类别/复用 | 节省 | 依据 |")
            lines.append("|---:|---|---:|---:|---:|---|---:|---|")
            for sg in labors:
                row = _get_row(quote, wb_path, sheet_name, sg["target"]["row"])
                detail_short = (_detail(row["cells"]) if row else "")[:50].replace("\n", " ")
                pc = sg["proposed_change"]
                refs_short = "; ".join(f"p{r['page']}" for r in sg["standard_refs"][:2])
                lines.append(f"| {sg['target']['row']} | {detail_short} | "
                             f"¥{sg['target']['current_value']:.0f} | "
                             f"¥{pc['new_value']:.0f} | "
                             f"{pc['fp_estimate']} | "
                             f"{pc['category']}/{pc['reuse']} | "
                             f"¥{sg.get('estimated_delta_amount',0):+,.0f} | "
                             f"{refs_short} |")
            lines.append("")

        # --- subject-mapping (informational, group) ---
        subjects = [sg for sg in sgs if sg["category"] == "subject-mapping"]
        if subjects:
            sec_count: dict[str, int] = defaultdict(int)
            for sg in subjects:
                sec_count[sg["proposed_change"]["new_value"]] += 1
            lines.append(f"#### subject-mapping 科目对齐 ({len(subjects)} 行)")
            lines.append("")
            for sec, n in sorted(sec_count.items(), key=lambda x: -x[1]):
                lines.append(f"- ×{n} 行 → **{sec}** *({_section_title(sec)})*")
            lines.append("")
            lines.append("> *仅增加「标准科目」列，不动金额。建议全部采纳：财评不会反对、且让自己的报价更可读。*")
            lines.append("")

        # --- semantic-split (textual rewrite) ---
        semantics = [sg for sg in sgs if sg["category"] == "semantic-split"]
        if semantics:
            lines.append(f"#### semantic-split 语义切分 ({len(semantics)} 行)")
            lines.append("")
            for sg in semantics:
                row = _get_row(quote, wb_path, sheet_name, sg["target"]["row"])
                detail_orig = (_detail(row["cells"]) if row else "")[:60].replace("\n", " ")
                advisory = sg["proposed_change"]["new_value"][:60].replace("\n", " ")
                lines.append(f"- **行{sg['target']['row']}**: `{detail_orig}…` → `{advisory}…`")
                lines.append(f"  - 原因：{sg['rationale'][:120]}")
            lines.append("")
            lines.append("> *不动金额，避免被财评以「与大模型描述重复」挑战。建议全部采纳。*")
            lines.append("")

    # ---- 末尾：建议决策模板 ----
    lines.append("## 决策模板（供商务/技术评审使用）")
    lines.append("")
    lines.append("```")
    lines.append("□ 全部采纳（推荐）")
    lines.append("□ 仅采纳 subject-mapping + semantic-split（不动金额，只加合规外衣）")
    lines.append("□ 仅采纳 labor-pricing（只动金额，不改描述）")
    lines.append("□ 部分采纳：保留以下 ID（写在下方），其他拒绝：______")
    lines.append("□ 全部拒绝（保持原报价送审）")
    lines.append("")
    lines.append("决策依据/备注：")
    lines.append("")
    lines.append("商务签字：______ 技术签字：______ 日期：______")
    lines.append("```")
    lines.append("")

    out = s / f"optimize-{mode_name}.md"
    out.write_text("\n".join(lines))
    print(f"{out} written — {summary['total_suggestions']} suggestions documented")


SECTION_TITLES = {
    "三.(一).2.1": "定制软件开发费",
    "三.(一).2.1.表3": "软件类别调整因子",
    "三.(一).2.2": "OA 系统",
    "三.(一).2.5": "硬件设备购置费",
    "三.(一).2.6.1": "系统集成费",
    "三.(三).2": "软件运维",
    "三.(三).2.4.2": "软件运维-规模单价法",
    "三.(三).2.5": "软件运维标准 8.39-12.59 万元/人年",
    "正文一": "厉行节约原则",
}


def _section_title(sec_id: str) -> str:
    return SECTION_TITLES.get(sec_id, "")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/smoke")
