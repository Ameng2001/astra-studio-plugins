"""scan_commercial — 商业组件识别 (填补「软件产品购置费 = ¥0」缺口).

PDF 表6 软件产品购置费要求列出 OS / 数据库 / 中间件 / 安全软件 / 商用工具软件
等，并附 ≥3 个品牌型号对比。当前报价 9 sheet 把所有商业组件「打包进定制
开发人月」——这是财评最容易抓的破绽：

  - 大模型项目用 NVIDIA Triton 推理引擎不可能不付 NVIDIA 钱
  - 数据可视化大屏一般用 ECharts (开源) 或 FineReport / 帆软 (商业)
  - 数据库通常是 PostgreSQL/MySQL (开源) 或 Oracle/DB2 (商业)
  - AI 训练框架 PyTorch (开源) 但 GPU 服务器是商业

本扫描器找出包含商业组件关键词的行，提示：
  - 这些应从「定制软件开发费」剥离
  - 移至「软件产品购置费」科目
  - 配套提供 ≥3 个品牌对比 + 询价单
"""
from __future__ import annotations

from typing import Any


# 商业组件关键词 + 推测的"购置占比"
# 占比 = 该行金额中应剥离到购置费的比例
# 硬件/算力关键词：属 PDF 表7 硬件而非表6 软件购置；
# 按当前策略「暂不剥离」——保持在原报价（大模型底座定制开发费）不动。
HARDWARE_KEYWORDS = {"GPU", "NVIDIA", "A100", "H100", "V100", "服务器", "显卡"}

COMMERCIAL_KEYWORDS = {
    # AI/推理框架
    "Triton": ("NVIDIA Triton 推理服务器", 0.40, "AI 推理"),
    "TensorRT": ("NVIDIA TensorRT", 0.30, "AI 推理"),
    "NVIDIA": ("NVIDIA 商业组件", 0.30, "AI 算力"),
    "GPU": ("GPU 服务器/卡", 0.50, "AI 算力"),
    # 数据库
    "Oracle": ("Oracle Database", 0.50, "数据库"),
    "DB2": ("IBM DB2", 0.50, "数据库"),
    "SQL Server": ("Microsoft SQL Server", 0.50, "数据库"),
    "MongoDB Atlas": ("MongoDB Atlas (商业版)", 0.40, "数据库"),
    # 中间件
    "WebLogic": ("Oracle WebLogic", 0.40, "中间件"),
    "WebSphere": ("IBM WebSphere", 0.40, "中间件"),
    "Tuxedo": ("Oracle Tuxedo", 0.40, "中间件"),
    # 报表/BI
    "FineReport": ("帆软 FineReport", 0.50, "报表 BI"),
    "FineBI": ("帆软 FineBI", 0.50, "BI"),
    "Tableau": ("Tableau Desktop/Server", 0.50, "BI"),
    "PowerBI": ("Microsoft Power BI", 0.40, "BI"),
    # 安全
    "深信服": ("深信服商业安全产品", 0.30, "安全"),
    "天融信": ("天融信商业安全产品", 0.30, "安全"),
    "启明星辰": ("启明星辰商业安全产品", 0.30, "安全"),
    "锐捷": ("锐捷商业网络组件", 0.30, "网络"),
    # 操作系统
    "Windows Server": ("Windows Server", 0.50, "操作系统"),
    "Red Hat": ("Red Hat Enterprise Linux", 0.40, "操作系统"),
    # 文档/办公
    "WPS": ("WPS 商业版", 0.30, "办公套件"),
    "Microsoft Office": ("Microsoft Office", 0.40, "办公套件"),
    # 大屏/可视化
    "DataV": ("阿里 DataV", 0.40, "数据可视化"),
    "Hightopo": ("Hightopo HT for Web", 0.40, "3D 可视化"),
    # 集成 / 流程
    "Camunda": ("Camunda Enterprise", 0.30, "工作流引擎"),
    "Activiti": ("Activiti (商业支持)", 0.20, "工作流引擎"),
    # GPU 相关
    "A100": ("NVIDIA A100", 0.80, "GPU 算力"),
    "H100": ("NVIDIA H100", 0.80, "GPU 算力"),
    "V100": ("NVIDIA V100", 0.70, "GPU 算力"),
}


def scan(quote: dict[str, Any]) -> list[dict[str, Any]]:
    # 【已停用】关键词无法区分开源 vs 商业：Triton/TensorRT/PaddleServing 等
    # 均为开源/免费，本项目数据库亦为开源/国产，无 Oracle。自动剥离全是假阳性，
    # 反而暴露"把开源当采购"硬伤。改为：软件产品购置费纯占位，由技术据实填写。
    return []

    findings: list[dict[str, Any]] = []  # pragma: no cover (以下保留供未来人工核实参考)
    sid = 0
    for wb in quote["workbooks"]:
        if wb["kind"] not in {"platform", "llm"}:
            continue
        for sh in wb["sheets"]:
            if sh["kind"] not in {"items", "deploy", "ops"}:
                continue
            for row in sh["rows"]:
                detail = " ".join(
                    str(v) for k, v in row["cells"].items()
                    if isinstance(v, str) and k in ("建设详情", "详情", "建设详情（定位）",
                                                    "建设内容", "模块", "子模块", "技术方案")
                )
                if not detail:
                    continue
                # 找出命中关键词；硬件/算力关键词跳过（费用留在原软件平台报价，不剥离）
                hits = []
                for kw, (label, ratio, kind) in COMMERCIAL_KEYWORDS.items():
                    if kw in detail and kw not in HARDWARE_KEYWORDS:
                        hits.append({"keyword": kw, "label": label, "ratio": ratio, "kind": kind})
                if not hits:
                    continue   # 仅硬件或无软件组件 → 整行费用保留在大模型/平台报价

                # 计算应剥离金额
                row_money = 0.0
                for k in ("成本总价", "对外总价", "成本总价（元）", "对外总价（元）", "研发报价"):
                    v = row["cells"].get(k)
                    if isinstance(v, (int, float)) and v > 0:
                        row_money = float(v)
                        break

                # 取最高占比作为剥离比例
                max_ratio = max(h["ratio"] for h in hits)
                detach_amount = row_money * max_ratio

                sid += 1
                findings.append({
                    "id": f"X{sid:03d}",
                    "category": "commercial-component",
                    "severity": "medium",
                    "stance_origin": "合规守护",
                    "target": {
                        "workbook": wb["path"],
                        "sheet": sh["name"],
                        "row": row["row_index"],
                    },
                    "proposed_change": {
                        "operation": "split-to-purchase",
                        "detected_components": hits,
                        "row_money": row_money,
                        "detach_ratio": max_ratio,
                        "detach_amount_to_purchase_fee": round(detach_amount),
                        "remaining_in_dev_fee": round(row_money - detach_amount),
                        "advice": (
                            f"检测到商业组件 {[h['keyword'] for h in hits]}。"
                            f"按 PDF 表6 软件产品购置费应独立列示，建议从本行剥离约 "
                            f"¥{detach_amount:,.0f} ({int(max_ratio*100)}%)"
                            f"移至「软件产品购置费」科目，并附 ≥3 个品牌型号对比。"
                        ),
                    },
                    "rationale": (
                        f"本行包含商业组件描述 ({', '.join(h['kind'] for h in hits)})，"
                        f"按 PDF 第三章 (一) 2.4 应独立列「软件产品购置费」。"
                        f"当前合并进人月费率违反「重复内容只计一次」(2.7) + 「商品成品软件不另计开发费」(2.6.1) 原则。"
                    ),
                    "standard_refs": [
                        {"section": "三.(一).2.4", "page": 17, "snippet": "表6 软件产品购置预算支出表"},
                        {"section": "三.(一).2.6.1", "page": 20,
                         "snippet": "商品（成品）软件按相关定额已含安装调试的，不再单独计列系统集成费"},
                    ],
                    "estimated_delta_amount": 0,   # 只是科目重分类，总价不变
                    "auto_applicable": True,
                })
    return findings
