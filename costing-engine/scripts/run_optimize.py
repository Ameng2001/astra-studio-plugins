"""run_optimize — orchestrator for quote-optimize skill.

Steps:
 1. Load quote.json + global.md
 2. Run scanners (subject / labor / semantic)
 3. Enforce G1 budget band (±5%); demote/remove low-severity suggestions if needed
 4. Build feasibility FP table xlsx
 5. Write optimize-suggestions.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl

import quote_shape
import scan_subject
import scan_labor
import scan_semantic
import scan_completeness
import scan_caps
import scan_evidence
import scan_brand_models
import scan_redundancy
import scan_workdays
import scan_summary_sheets
import scan_ops
import scan_commercial

# 立场归因：每条建议来自哪种角色
CATEGORY_TO_STANCE = {
    "subject-mapping":      "投标方军师",   # 帮投标方挂科目显专业
    "labor-pricing":        "投标方军师",   # 帮投标方取合规带上限
    "semantic-split":       "投标方军师",   # 避免被红队抓重复
    "add-cost-item":        "投标方军师",   # 补全显专业 + 利润重塑
    "evidence-supplement":  "投标方军师",   # 答辩话术
    "compliance-cap":       "合规守护",     # 硬上限超界报警
    "compliance-cap-info":  "合规守护",
    "evidence-missing":     "合规守护",     # PDF 表7 注3 硬要求
    "add-summary-sheet":    "合规守护",     # PDF 表7 编制要求
    "redundancy-warning":   "财评红队",     # 模拟财评挑战
    "workdays-outlier":     "财评红队",     # 拆分子项挑战
    "ops-attribution":      "合规守护",     # 大模型运维 token 归属
    "commercial-component": "合规守护",     # PDF 表6 软件产品购置费应剥离
}
from derive_fp import derive_fp_from_person_days, classify_fp_row, fp_to_cost_range
from formula_engine import CATEGORY_FACTOR, REUSE_FACTOR
from nesma_classifier import classify_row as nesma_classify

from mode_config import current as _current_mode

def _band_pct() -> float:
    return _current_mode().band_pct



def compute_original_total(quote: dict) -> float:
    """报价原总额。认不出金额列时**硬失败**，见 quote_shape 模块头。"""
    rs = quote_shape.Resolver()
    total = 0.0
    for _wb, _sh, row in quote_shape.iter_priceable_rows(quote):
        rs.observe(row["cells"])
        v = rs.get(row["cells"], "total")
        if v is not None:
            total += v
    rs.assert_recognized("total")
    return round(total, 2)


def enforce_budget_band(suggestions: list[dict], original_total: float) -> tuple[list[dict], dict]:
    upper, lower = original_total * (1 + _band_pct()), original_total * (1 - _band_pct())
    delta_sum = sum(s.get("estimated_delta_amount", 0) for s in suggestions)
    projected = original_total + delta_sum

    removed: list[str] = []
    if projected < lower:
        # 砍小不砍大：先砍 medium（微调，不影响 fail 清零）。
        # high severity 建议只在迫不得已时（medium 砍完仍越下限）才动。
        for sev_to_cut in ("medium", "high"):
            if projected >= lower:
                break
            cands = sorted(
                [s for s in suggestions if s["category"] == "labor-pricing"
                 and s["severity"] == sev_to_cut and s["id"] not in removed],
                key=lambda s: s.get("estimated_delta_amount", 0),   # 砍降价最多的（让 projected 上升最快）
            )
            for s in cands:
                if projected >= lower:
                    break
                projected -= s.get("estimated_delta_amount", 0)
                removed.append(s["id"])
        suggestions = [s for s in suggestions if s["id"] not in removed]
    elif projected > upper:
        # 砍小不砍大：先砍 medium severity（这些是带内贴边的微调，不是 fail-preventing）
        # 保留 high severity（这些是 fail-preventing，砍了风险点不能清零，违背目标）
        candidates = sorted(
            [s for s in suggestions if s["category"] == "labor-pricing" and s["severity"] != "high"],
            key=lambda s: -s.get("estimated_delta_amount", 0),
        )
        for s in candidates:
            if projected <= upper:
                break
            projected -= s.get("estimated_delta_amount", 0)
            removed.append(s["id"])
        suggestions = [s for s in suggestions if s["id"] not in removed]

    delta_sum = sum(s.get("estimated_delta_amount", 0) for s in suggestions)
    return suggestions, {
        "original_total": original_total,
        "upper_bound": round(upper, 2),
        "lower_bound": round(lower, 2),
        "projected_total_after_apply": round(original_total + delta_sum, 2),
        "delta_sum": round(delta_sum, 2),
        "within_band": lower <= original_total + delta_sum <= upper,
        "demoted_suggestions": removed,
    }


def _append_split_detail(xlsx_path: str, outliers: list[dict]) -> None:
    """T3：把人天>100 行的拆分建议落为「工作量拆分明细」sheet 附入 FP 表。"""
    if not outliers:
        return
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path)
    ws = wb.create_sheet("02_工作量拆分明细")
    ws.append(["序号", "原行位置", "原内容摘要", "原人天", "拆分子项", "子项人天", "子项FP"])
    seq = 0
    for o in outliers:
        pc = o["proposed_change"]
        loc = f"{o['target']['sheet']}/行{o['target']['row']}"
        subs = pc.get("suggested_subitems", [])
        for i, sub in enumerate(subs):
            seq += 1
            ws.append([
                seq,
                loc if i == 0 else "",
                (pc.get("detail_excerpt", "") if i == 0 else ""),
                (pc.get("current_workdays", "") if i == 0 else ""),
                sub.get("name", ""),
                sub.get("workdays", ""),
                sub.get("fp", ""),
            ])
    ws.append([])
    ws.append([None, "说明：单行工作量 > 100 人天，按 ≤60 人天/子项拆分以便逐项功能点核算"
               "（PDF 第三章 2.1 功能点分项）。子项命名取自建设详情枚举段，待技术确认。"])
    wb.save(xlsx_path)


def build_fp_table(quote: dict, out_xlsx: str) -> dict:
    wb_out = openpyxl.Workbook()
    ws = wb_out.active
    ws.title = "功能点估算表"
    ws.append([
        "序号", "工作簿", "Sheet", "行号", "功能描述",
        "人天", "FP（反推）", "NESMA 分类", "ILF", "EIF", "EI", "EO", "EQ",
        "类别", "复用度",
        "标准成本下限", "标准成本上限", "标准成本中位",
    ])
    seq = 0
    by_cat: dict[str, int] = {}
    sum_min, sum_max = 0.0, 0.0
    rs = quote_shape.Resolver()
    for wb, sh, row in quote_shape.iter_priceable_rows(quote):
        # 语义角色取值 + kind 排除法都收在 quote_shape ——
        # 此前这里用 wb.kind ∈ {platform,llm} 的白名单，kind=unknown 全被排除，
        # 而那正是最常见的情况：实测一份真实报价整簿 unknown，反推 FP 归零。
        rs.observe(row["cells"])
        # 表里已经写明 FP 就直接用 —— 倒推是给「只给人天不给 FP」的外部报价
        # 用的兜底。我们自己生成的送审表直接给 FP、没有人天列，一律走倒推
        # 会得 0：这正是本文件顶上那份「静默出 0」清单的第六种写法。
        explicit_fp = rs.get(row["cells"], "fp")
        person_days = rs.get(row["cells"], "person_days")
        if isinstance(explicit_fp, (int, float)) and explicit_fp > 0:
            fp = int(round(explicit_fp))
            person_days = person_days if isinstance(person_days, (int, float)) else ""
        elif isinstance(person_days, (int, float)) and person_days > 0:
            fp = derive_fp_from_person_days(person_days)
        else:
            continue
        detail = rs.get(row["cells"], "detail", numeric=False) or ""
        category, reuse = classify_fp_row(str(detail), sh["name"], wb["kind"])
        lo, hi = fp_to_cost_range(fp, category, reuse)
        seq += 1
        by_cat[category] = by_cat.get(category, 0) + fp
        sum_min += lo
        sum_max += hi
        nesma = nesma_classify(str(detail), fp)
        ws.append([
            seq, Path(wb["path"]).name, sh["name"], row["row_index"],
            str(detail)[:200],
            person_days, fp,
            nesma["summary"],
            nesma["ILF"], nesma["EIF"], nesma["EI"], nesma["EO"], nesma["EQ"],
            category, reuse,
            round(lo), round(hi), round((lo + hi) / 2),
        ])
    ws.append([])
    ws.append([None, None, None, None, "合计", None, sum(by_cat.values()),
               None, None, None, None, None, None,
               None, None,
               round(sum_min), round(sum_max), round((sum_min + sum_max) / 2)])
    for cat, fp in by_cat.items():
        ws.append([None, None, None, None, f"  - {cat}", None, fp])
    ws.append([])
    ws.append([None, None, None, None,
               "[说明] 表内已写明功能点的行直接取用；只给人天的行按 PDF p.13 "
               "公式 (FP×6.51/8) 反推。NESMA 分类按描述文本启发式估算，"
               "研发期应按 SJ/T 11619-2016 重新计数。"])
    wb_out.save(out_xlsx)
    return {
        "fp_table_path": out_xlsx,
        "total_fp": sum(by_cat.values()),
        "by_category": by_cat,
        "min_cost_yuan": round(sum_min),
        "max_cost_yuan": round(sum_max),
    }


def main(session_dir: str) -> None:
    s = Path(session_dir)
    quote = json.loads((s / "quote.json").read_text())

    all_sugg = []
    all_sugg.extend(scan_subject.scan(quote))
    all_sugg.extend(scan_labor.scan(quote))
    all_sugg.extend(scan_semantic.scan(quote))
    all_sugg.extend(scan_completeness.scan(quote))
    all_sugg.extend(scan_brand_models.scan(quote))
    all_sugg.extend(scan_redundancy.scan(quote))
    all_sugg.extend(scan_workdays.scan(quote))
    all_sugg.extend(scan_summary_sheets.scan(quote))
    all_sugg.extend(scan_ops.scan(quote))
    all_sugg.extend(scan_commercial.scan(quote))
    # evidence + caps must run AFTER others so they can see all decisions
    all_sugg.extend(scan_evidence.scan(quote, all_sugg))
    all_sugg.extend(scan_caps.scan(quote, all_sugg))

    # Stamp stance_origin on each suggestion (default by category)
    for sg in all_sugg:
        if "stance_origin" not in sg:
            sg["stance_origin"] = CATEGORY_TO_STANCE.get(sg["category"], "未归类")

    original_total = compute_original_total(quote)
    pruned, budget_band = enforce_budget_band(all_sugg, original_total)

    fp_table = build_fp_table(quote, str(s / "feasibility-fp-table.xlsx"))
    if fp_table["total_fp"] == 0:
        # 同上：0 个功能点是**可能的**（纯硬件报价），但绝大多数情况是没认出来。
        # 不硬失败（免得挡住合法场景），但必须显式说，不能让它混进 summary 装作正常。
        print("  ⚠ 功能点合计为 0 —— 若本报价确有软件开发内容，"
              "说明「人天/人/天」列未被识别，或 sheet.kind 判定有误。"
              "检查 parse_quote 的 kind 判定与列名。", file=sys.stderr)
    _append_split_detail(str(s / "feasibility-fp-table.xlsx"),
                         [x for x in all_sugg if x["category"] == "workdays-outlier"])

    by_cat: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    by_stance: dict[str, int] = {}
    for sg in pruned:
        by_cat[sg["category"]] = by_cat.get(sg["category"], 0) + 1
        by_sev[sg["severity"]] = by_sev.get(sg["severity"], 0) + 1
        st = sg.get("stance_origin", "未归类")
        by_stance[st] = by_stance.get(st, 0) + 1

    mode = _current_mode()
    result = {
        "session_dir": str(s),
        "mode": {"name": mode.name, "band_pct": mode.band_pct, "target_buffer": mode.target_buffer, "description": mode.description},
        "summary": {
            "global_guideline_version": "1.0",
            "mode_name": mode.name,
            "total_suggestions": len(pruned),
            "by_category": by_cat,
            "by_severity": by_sev,
            "by_stance": by_stance,
            "estimated_impact_amount": budget_band["delta_sum"],
            "budget_band": budget_band,
            "fp_table_path": fp_table["fp_table_path"],
            "fp_summary": {k: v for k, v in fp_table.items() if k != "fp_table_path"},
        },
        "suggestions": pruned,
    }
    (s / "optimize-suggestions.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/smoke")
