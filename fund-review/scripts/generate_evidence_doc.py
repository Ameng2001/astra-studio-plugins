"""generate_evidence_doc — 财评附件：取值依据说明.md.

从 optimize-suggestions.json 中提取所有 evidence-supplement 项，
组织成一份独立的财评附件文档，直接作为投标文件的"附件 A：取值依据说明"。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def main(session_dir: str) -> None:
    s = Path(session_dir)
    sugg = json.loads((s / "optimize-suggestions.json").read_text())
    evidence = [x for x in sugg["suggestions"] if x["category"] == "evidence-supplement"]
    brand = [x for x in sugg["suggestions"] if x["category"] == "evidence-missing"]
    if not evidence and not brand:
        print("no evidence/brand items found; skip")
        return

    mode_name = sugg.get("mode", {}).get("name", "?")
    fp = sugg["summary"].get("fp_summary", {})
    bb = sugg["summary"].get("budget_band", {})

    lines = []
    lines.append("# 附件 A：取值依据说明")
    lines.append("")
    lines.append(f"*配套报价版本*: **{mode_name}**版")
    lines.append(f"*生成时间*: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"*评审基准*: 柳财审〔2020〕16号《柳州市本级信息化建设项目预算支出标准（试行）》")
    lines.append("")
    lines.append("## 总论")
    lines.append("")

    # 项目性质（首选 project-nature）
    nature = next((x for x in evidence if x["proposed_change"].get("kind") == "project-nature"), None)
    if nature:
        lines.append(nature["proposed_change"]["evidence_text"])
        for r in nature["standard_refs"]:
            lines.append(f"- *依据*：《柳财审〔2020〕16号》第 {r['page']} 页 / {r['section']} — {r['snippet']}")
        lines.append("")

    lines.append(f"项目报价合计 ¥{bb.get('projected_total_after_apply', 0):,.0f}，"
                 f"采用功能点法（NESMA）核算软件类费用，配套可研功能点表 {fp.get('total_fp', '?')} FP。")
    lines.append("")
    lines.append("### 项目类型定性")
    lines.append("")
    lines.append("本项目同时包含三类建设内容：①定制软件开发（数智平台 + 行业大模型 L1/L2）；"
                 "②系统集成（多系统部署调试、跨系统数据对接）；③专用硬件设备购置（六园所场景设备）。"
                 "按《柳财审〔2020〕16号》表11 注4，「综合类项目指系统集成内容外还包含软件开发的部分内容」，"
                 "**本项目定性为「综合类」**。设计费/咨询费、工程监理费按综合类项目类型调整系数计取。")
    lines.append("")
    lines.append("- *依据*：《柳财审〔2020〕16号》第 21-22 页 表11/表12 项目类型调整系数（综合类）")
    lines.append("")

    # 类别因子取值
    cats = [x for x in evidence if x["proposed_change"].get("kind") == "category-factor"]
    if cats:
        lines.append("## 一、软件类别调整因子取值")
        lines.append("")
        lines.append("> *PDF 表3 注1：凡取值超过 1 的需列明具体取值依据。*")
        lines.append("")
        lines.append("| 类别 | 行数 | 取值带 | 取值结论 | 依据 |")
        lines.append("|---|---:|---|---|---|")
        for c in cats:
            pc = c["proposed_change"]
            lines.append(f"| **{pc['category']}** | {pc['applies_to_rows']} | "
                         f"{pc['factor_band']} | {pc['evidence_text']} | "
                         f"《柳财审〔2020〕16号》p.14 表3 |")
        lines.append("")
        lines.append("### 各类别的功能样例")
        lines.append("")
        for c in cats:
            pc = c["proposed_change"]
            lines.append(f"- **{pc['category']}**: {pc['examples']}")
        lines.append("")

    # 复用度
    reuses = [x for x in evidence if x["proposed_change"].get("kind") == "reuse-factor"]
    if reuses:
        lines.append("## 二、复用度调整系数取值")
        lines.append("")
        lines.append("> *PDF 表3 注2：新建项目默认取低（1.0）；已有系统改造默认取中（2/3）。*")
        lines.append("")
        lines.append("| 复用度 | 行数 | 系数 | 取值结论 | 依据 |")
        lines.append("|---|---:|---|---|---|")
        for r in reuses:
            pc = r["proposed_change"]
            lines.append(f"| **{pc['reuse']}** | {pc['applies_to_rows']} | "
                         f"{pc['factor']} | {pc['evidence_text']} | "
                         f"《柳财审〔2020〕16号》p.14 表3 注2 |")
        lines.append("")

    # FP 估算方法
    fpm = next((x for x in evidence if x["proposed_change"].get("kind") == "fp-method"), None)
    if fpm:
        lines.append("## 三、功能点（FP）估算方法")
        lines.append("")
        lines.append(fpm["proposed_change"]["evidence_text"])
        lines.append("")
        for r in fpm["standard_refs"]:
            lines.append(f"- *依据*：《柳财审〔2020〕16号》第 {r['page']} 页 / {r['section']}")
        lines.append("")
        lines.append("详细 FP 估算逐项见 `feasibility-fp-table.xlsx`。")
        lines.append("")

    # 单价计算公式示例
    lines.append("## 四、单价计算公式示例")
    lines.append("")
    lines.append("以「人工智能 / 复用度低 / 取上限」为例：")
    lines.append("")
    lines.append("```")
    lines.append("每人天单价 = 人月费率 × 类别因子 × 复用度 ÷ 月工作日")
    lines.append("        = 17,000 × 1.5 × 1.0 ÷ 21.75")
    lines.append("        ≈ 1,172 元/人天")
    lines.append("```")
    lines.append("")
    lines.append("依据：《柳财审〔2020〕16号》第 13-14 页 第三章 (一) 2.1：")
    lines.append("- 人月费率 17,000 元/人月（广西取值）")
    lines.append("- 月工作日 21.75 天")
    lines.append("- 类别因子 1.5（人工智能上限，表3）")
    lines.append("- 复用度 1.0（新建项目，表3 注2）")
    lines.append("")

    # 直接非人力成本
    lines.append("## 五、直接非人力成本")
    lines.append("")
    lines.append("本项目直接非人力成本（办公费、差旅费、培训费、采购费、设备折旧）按 PDF 第三章 2.1.⑤ 「一般情况不进行计列（通常为 0）」 取 0。")
    lines.append("特殊需要时已在「其他费用与预备费」补全表中独立列示（如培训费按柳州市相关培训费文件标准）。")
    lines.append("")

    # 设备品牌
    if brand:
        lines.append("## 六、设备品牌型号对比 — 待补充清单")
        lines.append("")
        lines.append("> *PDF 表7 注3：参考品牌型号一般不少于 3 个；如有相关价格依据的一并提供。*")
        lines.append("")
        lines.append("以下设备表/sheet 检测到缺失「≥3 个品牌型号对比」证据，需在送审前由商务补全市场询价（电商截图、厂商报价单、行业询价记录均可）：")
        lines.append("")
        lines.append("| 工作簿 | sheet | 总行数 | 缺失行数 | 缺失率 | 严重度 |")
        lines.append("|---|---|---:|---:|---:|:---:|")
        total_miss = 0
        for b in brand:
            pc = b["proposed_change"]
            total_miss += pc["missing_count"]
            severity_icon = "🔴" if b["severity"] == "high" else "🟡"
            lines.append(f"| {Path(b['target']['workbook']).name} | "
                         f"{b['target']['sheet']} | "
                         f"{pc['total_rows']} | "
                         f"{pc['missing_count']} | "
                         f"{pc['miss_rate']*100:.0f}% | "
                         f"{severity_icon} |")
        lines.append("")
        lines.append(f"**总计待补充行数: {total_miss}**")
        lines.append("")
        lines.append("### 补全模板")
        lines.append("")
        lines.append("在每个设备 sheet 新增一列「品牌/型号对比」，填写：")
        lines.append("")
        lines.append("```")
        lines.append("品牌A 型号X / 品牌B 型号Y / 品牌C 型号Z")
        lines.append("（如：海康威视 DS-2CD3T46 / 大华 IPC-HFW2439 / 宇视 IPC2124LB）")
        lines.append("```")
        lines.append("")
        lines.append("附市场询价单或电商页截图（投标文件附录 B）。")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 关联附件")
    lines.append("- `feasibility-fp-table.xlsx` — 可研功能点估算表（19,571 FP 逐项）")
    lines.append(f"- `quote-additional-fees.xlsx` — 其他费用与预备费补全表")
    lines.append("- `quote-final-*.xlsx` — 优化后报价主表")
    lines.append("- `review-report-final.md` — 内部模拟财评报告")

    out = s / "取值依据说明.md"
    out.write_text("\n".join(lines))
    print(f"{out} written — {len(evidence)} evidence items, {len(brand)} brand-missing items")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/smoke")
