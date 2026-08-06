"""run_review — generate financial review report.

Walks quote.json + optimize-suggestions.json (or approved-plan.json), runs
compliance checks against the standard, and produces a markdown report
where every conclusion cites a PDF page anchor.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import quote_shape

from formula_engine import check_per_day_rate, recommend_per_day_rate, check_reserve_fee_rate
from derive_fp import classify_fp_row, derive_fp_from_person_days, fp_to_cost_range
from expert_reviewer import enhance_challenge


SECTION_TITLES = {
    "三.(一).2.1": "定制软件开发费",
    "三.(一).2.1.表3": "软件类别调整因子",
    "三.(一).2.2": "OA 系统",
    "三.(一).2.3": "政府门户网站",
    "三.(一).2.5": "硬件设备购置费",
    "三.(一).2.5.(4)": "LED 显示屏",
    "三.(一).2.6.1": "系统集成费",
    "三.(一).2.6.2": "设计/咨询费",
    "三.(一).2.6.3": "工程监理费",
    "三.(一).2.6.4": "测试检测费",
    "三.(一).2.6.5": "等保测评费",
    "三.(一).2.9": "预备费",
    "三.(三).2": "软件运维",
    "三.(三).2.5": "软件运维预算支出标准",
    "正文一": "厉行节约原则",
}


def review_row(wb_kind: str, sheet_name: str, sheet_kind: str, row_cells: dict) -> list[dict]:
    """Return a list of finding dicts for this single quote row."""
    findings = []
    detail = quote_shape.Resolver().get(row_cells, "detail", numeric=False) or ""
    # If a previous rewrite stashed the pre-semantic-split text, prefer it for classification.
    # Reason: semantic-split removes AI keywords from platform rows for narrative cleanup,
    # but the row's substantive nature didn't change. Reviewers classify on substance, not on
    # post-rewrite wording.
    orig = row_cells.get("_orig_detail")
    if orig:
        detail = orig
    _rs = quote_shape.Resolver()
    person_days = _rs.get(row_cells, "person_days")
    unit_price = _rs.derive_unit_price(row_cells)

    # Check 1: 软件类报价 → 单价 vs 标准带
    # `wb_kind` 由 parse_quote 按文件名/表头猜，猜不出就是 "unknown"。
    # 把 unknown 排除在检查之外，等于「认不出的报价一律判为合格」——
    # 实测一份 1215 行的真实报价因此得到 0 findings / 0 分而命令正常退出。
    # 改为：只排除**明确不是报价**的 kind，其余都查。
    if (quote_shape.is_priceable(wb_kind, sheet_kind)
            and isinstance(unit_price, (int, float))
            and isinstance(person_days, (int, float)) and person_days > 0):
        category, reuse = classify_fp_row(str(detail), sheet_name, wb_kind)
        verdict = check_per_day_rate(unit_price, category, reuse)
        floor, ceiling = recommend_per_day_rate(category, reuse)
        fp = derive_fp_from_person_days(person_days)
        lo, hi = fp_to_cost_range(fp, category, reuse)
        if verdict == "fail":
            findings.append({
                "rule": "labor-pricing-out-of-band",
                "severity": "fail",
                "summary": f"单价 ¥{unit_price}/人天 越界（{category}/{reuse} 标准带 ¥{floor:.0f}-¥{ceiling:.0f}）",
                "challenge": f"按 1.7 万/人月 × 类别因子[{category}] × 复用度[{reuse}] / 21.75 工作日 = ¥{floor:.0f}-¥{ceiling:.0f}；现 ¥{unit_price} 越界，未提供 FP 估算支撑。建议补 FP 表并按公式核算（FP={fp} → 标准成本 ¥{lo:,.0f}-¥{hi:,.0f}）",
                "refs": [
                    {"section": "三.(一).2.1", "page": 13, "snippet": "定制软件开发费公式：FP×6.51/174×17000×类别×复用 + 直接非人力"},
                    {"section": "三.(一).2.1.表3", "page": 14, "snippet": f"{category} 类别调整因子"},
                ],
            })
        elif verdict == "warn":
            findings.append({
                "rule": "labor-pricing-near-band-edge",
                "severity": "warn",
                "summary": f"单价 ¥{unit_price}/人天 接近 {category} 类带上限 ¥{ceiling:.0f}",
                "challenge": f"如归 {category} 类需在可研报告说明取上限依据；否则降为 ¥{ceiling:.0f} 内更稳健",
                "refs": [
                    {"section": "三.(一).2.1.表3", "page": 14, "snippet": "凡取值超过 1 的需列明具体取值依据"},
                ],
            })

        else:
            # verdict == "ok" 也要记一条 pass。
            # 不记的话，「全部合规」与「一行都没查到」在计分上完全一样 ——
            # 都是 0 findings → score 0/100。实测这份真实报价 1131 行单价
            # 全部落在标准带内，却报「综合得分 0/100」，读的人只会以为烂透了。
            findings.append({
                "rule": "labor-pricing-in-band",
                "severity": "pass",
                "summary": f"单价 ¥{unit_price}/人天 在 {category}/{reuse} 标准带 "
                           f"¥{floor:.0f}-¥{ceiling:.0f} 内",
                "challenge": "",
                "refs": [
                    {"section": "三.(一).2.1", "page": 13,
                     "snippet": "定制软件开发费公式：FP×6.51/174×17000×类别×复用 + 直接非人力"},
                ],
            })

    # Check 2: 设备类 → 必走 表7 + 厉行节约
    if wb_kind == "device":
        if isinstance(unit_price, (int, float)) and unit_price > 0:
            findings.append({
                "rule": "device-needs-table7",
                "severity": "warn",
                "summary": f"设备项 ¥{unit_price} 需列明品牌型号 ≥3 个并附定额依据",
                "challenge": "表 7 要求设备购置必须列明品牌型号（不少于 3 个），并附市场询价；当前清单可能未提供",
                "refs": [
                    {"section": "三.(一).2.5", "page": 18, "snippet": "参考品牌型号一般不少于 3 个；如有相关价格依据的一并提供"},
                    {"section": "正文一", "page": 1, "snippet": "厉行节约 / 从严编制"},
                ],
            })

    # Check 3: 大模型 ops sheet → 软件运维标准
    if wb_kind == "llm" and sheet_kind == "ops":
        cells_text = str(row_cells)
        if "token" in cells_text or "推理" in cells_text or "调用" in cells_text:
            findings.append({
                "rule": "llm-ops-uses-correct-section",
                "severity": "pass",
                "summary": "大模型运营/运维属软件运维 2.5 标准范畴",
                "challenge": "—",
                "refs": [
                    {"section": "三.(三).2.4.2", "page": 30, "snippet": "软件运维预算支出 = (规模×运维功能点单价)×级别×能力×特征 + 直接非人力"},
                    {"section": "三.(三).2.5", "page": 31, "snippet": "运维预算支出标准 8.39-12.59 万元/人年"},
                ],
            })

    return findings


def _apply_plan_in_memory(quote: dict, plan: dict) -> dict:
    """Synthesize the post-rewrite quote view without touching xlsx.

    Important: a single row may have multiple decisions (subject + labor +
    semantic). Group them as a list per (wb, sheet, row) and apply in an
    order that preserves intent:
      1. subject-mapping (adds column; no interaction)
      2. labor-pricing (changes price + totals)
      3. semantic-split (rewrites text — applied LAST so it doesn't fool
         downstream category re-classification)
    """
    by_loc: dict[tuple, list[dict]] = {}
    for d in plan.get("decisions", []):
        if d.get("decision") != "accept":
            continue
        # skip meta items (completeness / caps — handled separately, no row in quote)
        if d["target"]["workbook"].startswith("@") or d.get("category") in {"add-cost-item", "compliance-cap", "compliance-cap-info"}:
            continue
        key = (d["target"]["workbook"], d["target"]["sheet"], d["target"]["row"])
        by_loc.setdefault(key, []).append(d)

    order = {"subject-mapping": 0, "labor-pricing": 1, "semantic-split": 2}

    for wb in quote["workbooks"]:
        for sh in wb["sheets"]:
            for row in sh["rows"]:
                key = (wb["path"], sh["name"], row["row_index"])
                decisions = by_loc.get(key, [])
                if not decisions:
                    continue
                # Preserve original detail text for downstream classification (review uses it via _classify_text override)
                orig_detail = (
                    row["cells"].get("建设详情")
                    or row["cells"].get("详情")
                    or row["cells"].get("建设详情（定位）")
                    or row["cells"].get("建设内容")
                    or ""
                )
                row["cells"]["_orig_detail"] = orig_detail

                for d in sorted(decisions, key=lambda d: order.get(d["category"], 99)):
                    if d["category"] == "labor-pricing":
                        new_unit = d["proposed_change"]["new_value"]
                        col_header = d["target"].get("col_header") or "成本单价"
                        row["cells"][col_header] = new_unit
                        qty = row["cells"].get("人/天") or row["cells"].get("人天") or row["cells"].get("数量")
                        if isinstance(qty, (int, float)):
                            for ph, th in [("成本单价", "成本总价"),
                                            ("对外单价", "对外总价"),
                                            ("单价（元）", "成本总价（元）")]:
                                p = row["cells"].get(ph)
                                if isinstance(p, (int, float)) and th in row["cells"]:
                                    row["cells"][th] = round(p * qty, 2)
                    elif d["category"] == "subject-mapping":
                        row["cells"]["标准科目"] = d["proposed_change"]["new_value"]
                    elif d["category"] == "semantic-split":
                        col_header = d["target"].get("col_header") or "建设详情"
                        row["cells"][col_header] = d["proposed_change"]["new_value"]
    return quote


def main(session_dir: str, target: str = "original") -> None:
    s = Path(session_dir)
    quote = json.loads((s / "quote.json").read_text())
    sugg = json.loads((s / "optimize-suggestions.json").read_text())
    # standard.json 原本加载在函数末尾、却在中段被用于报告抬头 —— 提到这里
    standard = json.loads((s / "standard.json").read_text())

    if target == "final":
        plan_path = s / "approved-plan.json"
        if not plan_path.exists():
            print(f"WARN: --target final requested but {plan_path} missing; falling back to original")
            target = "original"
        else:
            plan = json.loads(plan_path.read_text())
            quote = _apply_plan_in_memory(quote, plan)

    findings_by_section: dict[str, list[dict]] = defaultdict(list)
    counts = {"pass": 0, "warn": 0, "fail": 0}
    ref_index: dict[tuple[str, int], int] = defaultdict(int)

    shape = quote_shape.Resolver()
    for wb, sh, row in quote_shape.iter_priceable_rows(quote):
            shape.observe(row["cells"])
            fs = review_row(wb["kind"], sh["name"], sh["kind"], row["cells"])
            for f in fs:
                counts[f["severity"]] += 1
                # Enhance challenge via expert_reviewer (LLM or template)
                if f["severity"] in ("warn", "fail"):
                    expert = enhance_challenge(f)
                    f["challenge"] = expert["challenge_text"] or f.get("challenge", "")
                    f["remediation"] = expert.get("remediation", "")
                    f["expert_source"] = expert.get("source", "")
                # Group by primary section
                primary = f["refs"][0]["section"] if f["refs"] else "其他"
                findings_by_section[primary].append({
                    **f,
                    "loc": {"workbook": Path(wb["path"]).name, "sheet": sh["name"], "row": row["row_index"]},
                    "detail_excerpt": (str(row["cells"].get("建设详情") or row["cells"].get("详情") or row["cells"].get("建设内容") or row["cells"].get("分项名称") or "")[:80]),
                })
                for r in f["refs"]:
                    ref_index[(r["section"], r["page"])] += 1

    total = sum(counts.values()) or 1
    # weighted compliance: pass=1, warn=0.5, fail=0
    score = round((counts["pass"] + counts["warn"] * 0.5) / total * 100)

    # ---- compose markdown ----
    lines = []
    lines.append("# 报价财评评审报告")
    lines.append("")
    lines.append(f"*评审目标*: **{'优化后报价 (post-rewrite)' if target == 'final' else '原始报价 (pre-optimization)'}**")
    lines.append(f"*会话*: `{s}`")
    # 评审基准必须来自本次实际解析的 standard.json ——
    # 此前硬编码「柳财审〔2020〕16号」，喂山东标准也照样这么写，
    # 那是**报告在声称一个不是它所用的依据**，比数字错更严重。
    std_title = (standard.get("title") or standard.get("doc_title")
                 or standard.get("source_pdf") or "（standard.json 未记标题）")
    lines.append(f"*评审基准*: {std_title}")
    lines.append(f"*生成时间*: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## 总评")
    lines.append("")
    lines.append(f"- **综合得分**：{score}/100")
    lines.append(f"- **通过**：{counts['pass']} 项 / **警告**：{counts['warn']} 项 / **否决**：{counts['fail']} 项")
    bb = sugg["summary"].get("budget_band", {})
    if bb:
        _ot = bb.get("original_total") or 0
        _pct = round((bb["upper_bound"] / _ot - 1) * 100) if _ot else "?"
        lines.append(f"- **±{_pct}% 区间**：¥{bb['lower_bound']:,.0f} ~ ¥{bb['upper_bound']:,.0f}")
        lines.append(f"- **报价合计**：¥{bb['projected_total_after_apply']:,.0f}（落入区间：{'是' if bb['within_band'] else '否'}）")
    fp = sugg["summary"].get("fp_summary", {})
    if fp:
        lines.append(f"- **可研 FP 估算**：合计 {fp.get('total_fp', 0)} FP；标准成本带 ¥{fp.get('min_cost_yuan', 0):,.0f} ~ ¥{fp.get('max_cost_yuan', 0):,.0f}")
    lines.append("")
    lines.append("## 主要风险摘要")
    lines.append("")
    for sec, fs in sorted(findings_by_section.items(), key=lambda kv: -sum(1 for f in kv[1] if f["severity"] == "fail")):
        nfail = sum(1 for f in fs if f["severity"] == "fail")
        nwarn = sum(1 for f in fs if f["severity"] == "warn")
        if nfail + nwarn == 0:
            continue
        title = SECTION_TITLES.get(sec, sec)
        lines.append(f"- **{sec} {title}**：否决 {nfail} 项 / 警告 {nwarn} 项")
    lines.append("")

    lines.append("## 逐项明细")
    lines.append("")
    # Show only fail + first 3 warn per section to keep report digestible
    for sec, fs in sorted(findings_by_section.items()):
        sec_fails = [f for f in fs if f["severity"] == "fail"]
        sec_warns = [f for f in fs if f["severity"] == "warn"][:3]
        if not sec_fails and not sec_warns:
            continue
        title = SECTION_TITLES.get(sec, sec)
        lines.append(f"### {sec} {title}")
        lines.append("")
        for f in sec_fails + sec_warns:
            icon = "❌" if f["severity"] == "fail" else "⚠️"
            lines.append(f"#### {icon} {f['loc']['workbook'][:50]} / {f['loc']['sheet']} / 行 {f['loc']['row']}")
            lines.append(f"- **内容摘要**：{f['detail_excerpt']}")
            lines.append(f"- **评分**：{f['severity']}")
            lines.append(f"- **审查意见**：{f['summary']}")
            lines.append(f"- **挑战**：{f['challenge']}")
            if f.get("remediation"):
                lines.append(f"- **修正建议**：{f['remediation']}")
            lines.append(f"- **标准出处**：")
            for r in f["refs"]:
                lines.append(f"  - 《{std_title}》第 {r['page']} 页 / {r['section']} — {r['snippet']}")
            lines.append("")
        if len([f for f in fs if f["severity"] == "warn"]) > 3:
            lines.append(f"> *本节另有 {len(fs) - len(sec_fails) - 3} 条警告，详见 review-findings.json*")
            lines.append("")

    lines.append("## 附录 A — 条款引用索引")
    lines.append("")
    lines.append("| 条款 | 页码 | 引用次数 |")
    lines.append("|---|---|---|")
    for (sec, page), n in sorted(ref_index.items(), key=lambda kv: -kv[1]):
        title = SECTION_TITLES.get(sec, "")
        lines.append(f"| {sec} {title} | {page} | {n} |")
    lines.append("")

    report = "\n".join(lines)
    out_md = "review-report-final.md" if target == "final" else "review-report.md"
    out_json = "review-findings-final.json" if target == "final" else "review-findings.json"
    (s / out_md).write_text(report)

    # machine-readable mirror
    findings_flat = []
    for sec, fs in findings_by_section.items():
        for f in fs:
            findings_flat.append({**f, "primary_section": sec})
    (s / out_json).write_text(json.dumps({
        "score": score,
        "counts": counts,
        "ref_index": {f"{k[0]}#p{k[1]}": v for k, v in ref_index.items()},
        "findings": findings_flat,
    }, ensure_ascii=False, indent=2))

    # ---- self-check: every persisted finding must have non-empty refs with valid pages ----
    max_page = standard["page_count"]
    bad = 0
    for f in findings_flat:
        if not f.get("refs") or any(not (1 <= r["page"] <= max_page) for r in f["refs"]):
            bad += 1
    print(f"{out_md} written — target={target}, score {score}/100, "
          f"{counts['fail']} fail / {counts['warn']} warn / {counts['pass']} pass; "
          f"{len(findings_flat)} findings, {bad} with bad page anchors")


if __name__ == "__main__":  # pragma: no cover
    args = sys.argv[1:]
    target = "original"
    if "--target" in args:
        i = args.index("--target")
        target = args[i + 1]
        args = args[:i] + args[i + 2:]
    session = args[0] if args else ".fund-review/smoke"
    main(session, target=target)
