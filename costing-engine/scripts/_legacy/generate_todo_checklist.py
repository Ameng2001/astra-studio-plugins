"""generate_todo_checklist — 送审前待办清单（数据驱动，内部用）.

读 optimize-suggestions + review-findings，自动统计人工待办量，
产出 内部包 N03。含责任人 / 阻塞性 / 来源 / 数量位置。每跑自动更新。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path


def main(session_dir: str) -> None:
    s = Path(session_dir)
    sg = json.loads((s / "optimize-suggestions.json").read_text())["suggestions"]
    fr = json.loads((s / "review-findings-final.json").read_text())

    def n(cat):
        return [x for x in sg if x["category"] == cat]

    brand = n("evidence-missing")
    brand_rows = sum(x["proposed_change"].get("missing_count", 0) for x in brand)
    wd = n("workdays-outlier")
    red = n("redundancy-warning")
    comm = n("commercial-component")
    comm_amt = round(sum(x["proposed_change"]["detach_amount_to_purchase_fee"] for x in comm))
    aci = {x["proposed_change"]["fee_name"]: x["proposed_change"]["amount"] for x in n("add-cost-item")}
    warn = fr["counts"]["warn"]
    mode = sg and Path(session_dir).name

    # (序号, 待办, 量, 责任, 阻塞性, 状态, 对应送审文件位置, 依据)
    rows = [
        ("T1", "选定送审版本（决定提交哪个方案的送审包）", "1 项决策",
         "商务总监", "🔴必须", "待决策", "整个送审包", "方案取舍"),
        ("T2", "设备品牌型号核价（系统已填 ≥3 国产化候选，需核实际型号/单价 + 询价单）",
         f"{brand_rows} 行 / {len(brand)} 园所", "商务", "🔴必须",
         "候选已填·待核价", "Z03 场景设备报价 / 各园所 sheet「参考品牌型号(≥3·待核价)」列",
         "PDF 表7 注3"),
        ("T3", "工作量拆分确认（系统已生成拆分明细，需技术确认子项命名/FP 分配）",
         f"{len(wd)} 行", "技术", "🟡建议",
         "明细已生成·待确认", "F02 可研功能点估算表 /「02_工作量拆分明细」sheet",
         "PDF 2.1 功能点分项"),
        ("T4", "跨工作簿重复内容复核（确认是否真重复，定分摊）",
         f"{len(red)} 处", "技术+商务", "🟡建议", "待业务判断",
         "Z01 平台 vs Z02 大模型（体测/睡眠相关行）", "PDF 2.7"),
        ("T5", "确认是否有商业软件采购（本项目开源/国产栈，预计 ¥0；如有 OS/DB/中间件商业授权由技术据实补）",
         "开源栈预计 ¥0", "技术", "🟡建议", "待技术确认（默认¥0）",
         "Z04 其他费用 /「02_软件产品购置费明细」sheet（蓝斜体待补行）",
         "PDF 表6 软件产品购置"),
        ("T6", "差旅费测算依据补充（自有项目/交付人员差旅明细）",
         f"¥{aci.get('直接非人力成本-差旅费', 0):,}", "商务", "🔴必须", "待商务补",
         "Z04 其他费用与预备费 /「直接非人力成本-差旅费」行", "PDF 2.1.⑤"),
        ("T7", "培训费引用柳州市相关培训费具体文件号",
         f"¥{aci.get('培训费', 0):,}", "商务", "🟡建议", "待查证",
         "Z04 其他费用与预备费 /「培训费」行", "PDF 2.8"),
        ("T8", "评审应答逐项核对自动填充内容 + 商务/技术签字",
         f"{warn} 项", "商务+技术", "🔴必须", "初稿已填·待核对签字",
         "F03 评审应答说明", "—"),
        ("T9", "项目类型定性（综合类，影响设计/监理费率系数）",
         "1 项", "项目经理", "—", "✅ 已完成（系统）",
         "F01 取值依据说明 /「项目类型定性」节", "PDF 表11/12"),
        ("T10", "合计闭环核对（各表合计 == Z00 汇总）",
         "全部主表", "技术", "—", "✅ 已自动（系统）",
         "N04 合计闭环校验报告（内部包）", "—"),
        ("T11", "可行性研究报告正文撰写（功能点表仅为附件，正文另写）",
         "1 文档", "技术", "视招标要求", "待撰写",
         "独立文档（非本送审包）", "可研要求"),
        ("T12", "正式盖章 / 法人签字 / 装订成册", "—", "商务", "🔴必须",
         "待办", "整个送审包", "投标手续"),
    ]

    must = sum(1 for r in rows if "必须" in r[4])
    done = sum(1 for r in rows if "✅" in r[5])
    d = datetime.now()
    L = []
    L.append("# 送审前待办清单（投标方内部）")
    L.append("")
    L.append("> ⚠️ 本清单仅供投标方内部跟踪，**不随标书提交甲方**。")
    L.append("")
    L.append(f"编制日期：{d.year}年{d.month}月{d.day}日　|　配套方案：{mode}　|　"
             f"阻塞项（🔴必须）：**{must}** 项　|　系统已完成：**{done}** 项")
    L.append("")
    L.append("> 送审文件中所有待补位置已用**蓝色斜体**标注，按「对应送审文件位置」列定位。")
    L.append("")
    L.append("## 待办明细")
    L.append("")
    L.append("| 序号 | 待办事项 | 状态 | 对应送审文件位置 | 数量/规模 | 责任 | 阻塞性 | 依据 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for no, task, qty, who, block, status, loc, src in rows:
        L.append(f"| {no} | {task} | {status} | {loc} | {qty} | {who} | {block} | {src} |")
    L.append("")
    L.append("## 送审放行条件")
    L.append("")
    L.append(f"所有 🔴必须 项（共 {must} 项）完成并签字确认后，方可提交财评。")
    L.append("🟡建议 项未完成不阻塞送审，但会增加现场答辩压力。")
    L.append("")
    L.append("## 关键阻塞项说明")
    L.append("")
    L.append(f"- **T2 设备品牌（{brand_rows} 行）**：体力活，建议按园所分工并行；"
             "无 ≥3 品牌对比财评直接挑战「未做询比价」。")
    L.append(f"- **T5 商业组件（¥{comm_amt:,}）**：系统按比例估算剥离，"
             "需技术确认实际占比，商务补品牌询价。")
    L.append("- **T6 差旅依据**：PDF 2.1.⑤ 直接非人力一般为 0，"
             "保留必须有充分测算依据，否则财评要求删除。")
    L.append(f"- **T8 评审应答（{warn} 项）**：系统已自动填充答案初稿，"
             "业务核对约需 0.5-1.5 天，签字后随标书提交。")
    L.append("")

    out = s / "送审前待办清单.md"
    out.write_text("\n".join(L))
    print(f"送审前待办清单.md written — {len(rows)} 项 ({must} 必须)")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/保守")
