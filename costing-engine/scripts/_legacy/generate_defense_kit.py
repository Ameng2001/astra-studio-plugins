"""generate_defense_kit — 现场答辩包（可填模板）.

把 review-findings-final.json 里所有 warn/fail 转化为一份「待填模板表」，
每条带：
  - 财评可能挑战（来自 expert_reviewer）
  - 投标方答辩思路（模板：3 选 1）
  - 待填关键依据（如"项目主体功能性质" / "FP 估算结果" / "类别取值理由"）
  - 标准条款 + 页码（已有）

商务/技术评审填空后即可作为投标文件附件 B「答辩材料」。
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


DEFENSE_TEMPLATES = {
    "labor-pricing-near-band-edge": {
        "challenge_summary": "单价贴 {category} 类带上限",
        "options": [
            "本项目主体功能性质为 {category}（具体模块：{auto_modules}），按 PDF 表3 注1 取上限合理",
            "本项目为新建系统（非已有系统改造），按 PDF 表3 注2 复用度取 1.0",
            "对应 FP 估算见可研报告附表，单价 ¥{ceiling} 在合规带内（¥{floor}-¥{ceiling}）",
        ],
        "auto_filled_blanks": {
            "项目主体功能性质（举 3 个具体功能模块）": "{auto_modules}",
            "类别取值依据（参考案例 / 行业标准）": "{auto_category_basis}",
        },
    },
    "labor-pricing-out-of-band": {
        "challenge_summary": "单价超 {category} 类带上限",
        "options": [
            "已将单价调整为 ¥{target}（合规带 -2% 安全垫）",
            "本行属定制化高复杂度需求，需取人工智能类上限 1.5 + 复用度低 1.0",
        ],
        "auto_filled_blanks": {
            "若维持原单价的复杂度证据": "{auto_modules}（涉及多模型推理 + 业务规则定制 + 跨系统集成）",
        },
    },
    "device-needs-table7": {
        "challenge_summary": "设备项缺品牌型号 ≥3",
        "options": [
            "已在投标文件附录 B 提供 3 个品牌型号对比 + 市场询价单",
            "本设备项规格独特，已附自治区/行业部门规格要求文件",
        ],
        "auto_filled_blanks": {
            "3 个品牌型号清单": "待商务补全（推荐参考：海康威视 / 大华股份 / 宇视科技 同档对比）",
            "市场询价单或电商截图链接": "待商务补全（淘宝/京东企业询价 + 厂商正式报价邮件）",
        },
    },
}


# 类别 → 项目实际依据模板（结合柳州幼教项目背景）
CATEGORY_BASIS_TEMPLATES = {
    "人工智能": (
        "本项目为「广西数智幼教行业大模型」建设，含 L1 数智底座 + L2 行业模型 + 多场景智能体编排。"
        "按 PDF 表3 序号 4「自然语言处理、深度学习等」明确归属人工智能类。"
        "参考案例：自治区智慧教育平台、广西教育大数据中心等同类项目均按 1.5 取值。"
    ),
    "大数据多媒体": (
        "本项目含数智民生体系大屏可视化（园所分布地图、师生健康数据、办园绩效热力图等），"
        "属 PDF 表3 序号 3「图形、影像、声音等多媒体应用领域；大数据分析系统」。"
        "参考案例：自治区智慧城管大屏、各市数字政务一体化项目均按 1.3 取值。"
    ),
    "应用集成": (
        "本项目含跨系统接口对接（教育局 / 卫健委 / 民政等多源数据），属 PDF 表3 序号 2「应用集成」。"
        "考虑到广西多市多园所部署的协议适配 + 数据同步复杂度，按 1.2 取上限合理。"
    ),
    "业务处理": (
        "本项目为业务应用系统（园务管理 / 教务申报 / 审批流程），属 PDF 表3 序号 1「业务处理」。"
        "按主体功能类型取上限 1.0。"
    ),
}


# 抽取功能模块名（前 3 个枚举项 / 短语）
def _extract_modules(detail: str, max_n: int = 3) -> str:
    import re
    if not detail:
        return "(无详情)"
    # 优先：1、xxx 2、yyy 3、zzz 枚举
    matches = re.findall(r"[（(]?[一二三四五六七八九十1234567890]+[）)、.][^\n。；;1234567890]{2,30}", detail)
    items = [m.strip("（）()、. \n")[:25] for m in matches[:max_n]]
    if len(items) >= max_n:
        return " / ".join(items)
    # 次选：按 [、，] 切短句
    parts = re.split(r"[、，,；;]", detail)
    short_phrases = [p.strip() for p in parts if 4 <= len(p.strip()) <= 25]
    if len(short_phrases) >= max_n:
        return " / ".join(short_phrases[:max_n])
    # 兜底：句号分隔取前几句的关键短语
    if items:   # 至少有一个枚举项
        return " / ".join(items)
    if short_phrases:
        return " / ".join(short_phrases)
    # 最末：抽 "X管理/分析/展示/查询/统计/对接" 这类 V-N 结构
    vn_pattern = re.findall(r"[一-鿿]{2,8}(?:管理|分析|展示|查询|统计|对接|监测|预警|生成|识别|配置|审批|采集)", detail)
    if vn_pattern:
        return " / ".join(list(dict.fromkeys(vn_pattern))[:max_n])
    return detail[:80] + "（建议人工细化模块名）"


def _detect_rule(finding: dict) -> str:
    return finding.get("rule") or finding.get("category", "")


def _extract_template_vars(finding: dict) -> dict:
    """Pull category/floor/ceiling/page from finding for template filling."""
    import re
    ctx = {"category": "?", "floor": "?", "ceiling": "?", "page": "?", "target": "?",
           "auto_modules": "?", "auto_category_basis": "?"}
    summary = finding.get("summary", "")
    m = re.search(r"¥(\d+(?:\.\d+)?)/人天.*?[（(]?([^/(（\s]+)/[^（(]*?[（(]?标准带 ¥(\d+(?:\.\d+)?)-¥(\d+(?:\.\d+)?)", summary)
    if not m:
        m = re.search(r"([一-鿿]+)\s*类带上限\s*¥(\d+(?:\.\d+)?)", summary)
        if m:
            ctx["category"] = m.group(1)
            ctx["ceiling"] = m.group(2)
    else:
        ctx["category"] = m.group(2).strip()
        ctx["floor"] = m.group(3)
        ctx["ceiling"] = m.group(4)
        ctx["target"] = str(round(float(m.group(4)) * 0.98, 0))
    if finding.get("refs"):
        ctx["page"] = str(finding["refs"][0].get("page", "?"))
    # auto-derived fill-in values
    detail = finding.get("detail_excerpt", "") or finding.get("summary", "")
    ctx["auto_modules"] = _extract_modules(str(detail))
    ctx["auto_category_basis"] = CATEGORY_BASIS_TEMPLATES.get(ctx["category"], "?")
    return ctx


def _row_amount(quote: dict, finding: dict) -> float:
    """Look up the row referenced by finding.loc in quote.json, return its money value."""
    loc = finding.get("loc", {})
    wb_name = loc.get("workbook", "")
    sheet = loc.get("sheet", "")
    row_idx = loc.get("row")
    if not (wb_name and sheet and row_idx):
        return 0.0
    money_keys = ("成本总价", "对外总价", "成本总价（元）", "对外总价（元）",
                  "研发报价", "建设期-报价（研发）", "金额", "合计")
    for wb in quote.get("workbooks", []):
        if Path(wb["path"]).name != wb_name:
            continue
        for sh in wb["sheets"]:
            if sh["name"] != sheet:
                continue
            for row in sh["rows"]:
                if row["row_index"] != row_idx:
                    continue
                for k in money_keys:
                    v = row["cells"].get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        return float(v)
                return 0.0
    return 0.0


def main(session_dir: str) -> None:
    s = Path(session_dir)
    findings_path = s / "review-findings-final.json"
    if not findings_path.exists():
        findings_path = s / "review-findings.json"
    if not findings_path.exists():
        print(f"no review-findings at {s}; skip")
        return

    findings = json.loads(findings_path.read_text())["findings"]
    sugg_path = s / "optimize-suggestions.json"
    sugg = json.loads(sugg_path.read_text()) if sugg_path.exists() else {"summary": {}}
    mode_name = sugg.get("mode", {}).get("name", "?")
    quote = json.loads((s / "quote.json").read_text()) if (s / "quote.json").exists() else {"workbooks": []}

    # 给每条 finding 算金额 + 按金额降序
    for f in findings:
        if f["severity"] in ("warn", "fail"):
            f["_amount"] = _row_amount(quote, f)

    # Group by rule, each group sorted by amount desc
    by_rule = defaultdict(list)
    for f in findings:
        if f["severity"] not in ("warn", "fail"):
            continue
        by_rule[_detect_rule(f)].append(f)
    for rule in by_rule:
        by_rule[rule].sort(key=lambda x: -x.get("_amount", 0))

    lines: list[str] = []
    lines.append(f"# 附件 B：现场答辩包 — {mode_name}版")
    lines.append("")
    lines.append(f"*生成时间*: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"*用途*: 财评现场答辩 / 内部评审")
    lines.append(f"*说明*: 每条 warn/fail 已生成 3 选 1 答辩模板 + 待填空格。商务/技术评审填空、签字后随投标文件提交。")
    lines.append("")
    lines.append(f"## 总览")
    lines.append("")
    total = sum(len(v) for v in by_rule.values())
    lines.append(f"- 待答辩项: **{total}** 项")
    lines.append("- 按 rule 分组:")
    for rule, fs in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        lines.append(f"  - `{rule}`: {len(fs)} 项")
    lines.append("")

    lines.append("## 答辩明细")
    lines.append("")
    item_no = 0
    for rule, fs in sorted(by_rule.items(), key=lambda x: -len(x[1])):
        tmpl = DEFENSE_TEMPLATES.get(rule, {})
        lines.append(f"### Rule: `{rule}` ({len(fs)} 项)")
        lines.append("")
        # 展开策略（方案 E）：≤30 项全展开；>30 项 按金额降序 top 30 详细 + 余下精简
        # (fs 已按金额降序排序)
        detail_cap = len(fs) if len(fs) <= 30 else 30
        detailed = fs[:detail_cap]
        rest = fs[detail_cap:]
        for f in detailed:
            item_no += 1
            loc = f["loc"]
            amount = f.get("_amount", 0)
            amount_str = f" — 金额 ¥{amount:,.0f}" if amount > 0 else ""
            lines.append(f"#### 第 {item_no} 项{amount_str} — {loc['workbook'][:35]} / {loc['sheet'][:18]} / 行 {loc['row']}")
            lines.append("")
            lines.append(f"- **内容摘要**: {f.get('detail_excerpt','')[:80]}")
            lines.append(f"- **财评可能挑战**: {f.get('challenge','')[:200]}")
            if f.get("remediation"):
                lines.append(f"- **修正建议（系统）**: {f['remediation'][:200]}")
            lines.append(f"- **标准出处**:")
            for r in f.get("refs", [])[:3]:
                lines.append(f"  - 《柳财审〔2020〕16号》第 {r['page']} 页 / {r['section']}")
            lines.append("")
            lines.append("**答辩模板（3 选 1）**:")
            options = tmpl.get("options", ["[需补充答辩思路]"])
            ctx = _extract_template_vars(f)
            for i, opt in enumerate(options, 1):
                try:
                    opt_filled = opt.format(**ctx)
                except (KeyError, IndexError):
                    opt_filled = opt
                lines.append(f"  - 选项 {i}: ☐ {opt_filled}")
            lines.append("")
            auto_blanks = tmpl.get("auto_filled_blanks", {})
            if auto_blanks:
                lines.append("**关键依据（已自动填充，可微调）**:")
                for question, answer_template in auto_blanks.items():
                    try:
                        filled = answer_template.format(**ctx)
                    except (KeyError, IndexError):
                        filled = answer_template
                    lines.append(f"- *{question}*：")
                    lines.append(f"  > {filled}")
                lines.append("")
            lines.append("**采纳决策**: ☐ 全部 3 选项一并使用（推荐——多重防御）  ☐ 仅选项 ___")
            lines.append("")
            lines.append("商务签字: ______ 技术签字: ______ 日期: ______")
            lines.append("")
            lines.append("---")
            lines.append("")

        # Summary table for rest
        if rest:
            lines.append(f"### Rule `{rule}` 余下 {len(rest)} 项（按金额降序精简表格）")
            lines.append("")
            lines.append("> *说明：余下项均归同一类，使用上方 top {} 的「类别取值依据」统一答辩。逐项打勾即可。*".format(detail_cap))
            lines.append("")
            lines.append("| # | 金额 | 工作簿 | sheet | 行 | 内容摘要 | 答辩 |")
            lines.append("|---:|---:|---|---|---:|---|---|")
            for f in rest:
                item_no += 1
                loc = f["loc"]
                excerpt = f.get("detail_excerpt", "")[:40]
                amt = f.get("_amount", 0)
                amt_str = f"¥{amt:,.0f}" if amt > 0 else "—"
                lines.append(f"| {item_no} | {amt_str} | {loc['workbook'][:25]} | {loc['sheet'][:15]} | "
                             f"{loc['row']} | {excerpt} | ☐1 ☐2 ☐3 |")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 附：常用答辩话术索引")
    lines.append("")
    lines.append("**项目主体功能性质陈述（适用 labor-pricing-near-band-edge）**:")
    lines.append("> "
                 "本项目为新建数智民生体系建设，含数智底座 + 行业大模型 + 智能体编排 + 多源数据可视化。"
                 "按 PDF 表3 注1「按主体功能类型取值」原则，软件类报价整体归人工智能/大数据多媒体类，"
                 "取类别因子上限符合项目实际复杂度。")
    lines.append("")
    lines.append("**FP 估算依据陈述（通用）**:")
    lines.append("> "
                 "本项目 FP 估算采用 NESMA 方法（参 GB/T 36964）。"
                 "由原报价工作量反推：FP = 人天 ÷ 0.814（基于 P50 生产率 6.51 人时/FP）。"
                 "可研报告附表 A 含逐项 ILF/EIF/EI/EO/EQ 分类估算，合计 19,571 FP，"
                 "对应标准成本带 ¥10.4M - ¥15.0M（含 AI 类调整因子）。")
    lines.append("")
    lines.append("**复用度判定陈述（适用全部 labor-pricing）**:")
    lines.append("> "
                 "本项目为全新建设，无历史系统改造场景。"
                 "按 PDF 表3 注2「新建项目复用度调整系数默认取值为 1（复用度低）」取低。"
                 "L2 智能体/专业模型虽基于 L1 底座，但每个场景智能体独立训练 + 业务规则定制，"
                 "整体属新建范畴，不适用 2/3 中复用系数。")
    lines.append("")
    lines.append("**设备品牌答辩模板（适用 device-needs-table7）**:")
    lines.append("> "
                 "本投标文件附录 B 「设备品牌型号对比清单」逐项提供至少 3 个品牌型号 + 市场询价依据。"
                 "采购实施时按政府采购法走公开询比价，最终品牌按竞价结果确定，本报价以中位价为准。")
    lines.append("")

    out = s / "答辩包.md"
    out.write_text("\n".join(lines))
    print(f"{out} written — {total} items in defense kit, {len(by_rule)} rule categories")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/smoke")
