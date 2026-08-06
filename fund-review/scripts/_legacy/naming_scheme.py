"""naming_scheme — 标准化命名方案（送审包）.

文件命名: 数智幼教_{类别码}{序号}_{文档名}_{报价版本}_v{版本}_{日期}.{ext}
  类别码 Z=主送审件  F=财评附件  N=内部材料(不送审)

Sheet 命名: {两位序号}_{sheet内容}，首 sheet 为汇总。
"""
from __future__ import annotations

PROJECT_SHORT = "数智幼教"
PLUGIN_VERSION = "0.5"

# 原文件名（在 session 目录内）→ (类别码序号, 标准文档名, 是否送审)
FILE_MAP = {
    "项目总报价汇总.xlsx":                ("Z00", "项目总报价汇总",       True),
    # quote-final-* 用前缀匹配（含中文长名）
    "__platform__":                       ("Z01", "平台软件功能报价",     True),
    "__llm__":                            ("Z02", "行业大模型功能报价",   True),
    "__device__":                         ("Z03", "场景设备报价",         True),
    "quote-additional-fees.xlsx":         ("Z04", "其他费用与预备费",     True),
    "取值依据说明.md":                    ("F01", "取值依据说明",         True),
    "feasibility-fp-table.xlsx":          ("F02", "可研功能点估算表",     True),
    "答辩包.md":                          ("F03", "评审应答说明",         True),
    "review-report-final.md":             ("N01", "财评模拟评审报告",     False),
    "送审前待办清单.md":                  ("N03", "送审前待办清单",       False),
    "闭环校验报告.md":                    ("N04", "合计闭环校验报告",     False),
}
# optimize-{mode}.md 单独处理（文件名含 mode）
OPTIMIZE_DOC = ("N02", "优化决策记录", False)

# 工作区文件（不进送审包）
WORKFILES = {
    "quote.json", "standard.json", "optimize-suggestions.json",
    "approved-plan.json", "review-findings.json", "review-findings-final.json",
    "review-report.md",
}

# Sheet 重命名映射（原名 → 标准名）。未列出的按规则补两位序号。
SHEET_MAP = {
    # 平台软件
    "幼教综合服务平台":                                 "01_幼教综合服务平台功能明细",
    "平台部署&交付":                                    "02_系统集成费测算依据(已计入其他费用·不重复)",
    # 行业大模型
    "幼教行业大模型报价清单-汇总":                      "00_行业大模型报价汇总",
    "附1.数智民生体系数智底座":                         "01_数智底座(L1建设)",
    "附2.数智幼教行业大模型知识工程（L1层）":           "02_知识工程(L1建设)",
    "附3.数智幼教行业大模型数据建设（L1层）":           "03_数据建设(L1建设)",
    "附4.数智幼教行业专业模型（L2）":                   "04_行业专业模型(L2建设)",
    "附5.数智幼教场景智能体（L2）":                     "05_场景智能体(L2建设)",
    "附6.数智幼教行业大模型知识工程构建及运维（L1）":   "06_知识工程运维(L1)",
    "附7.数智幼教行业大模型数据集建设及运维（L1）":     "07_数据集运维(L1)",
    "附8.数智幼教行业专业模型迭代训练及运维（L2）":     "08_专业模型迭代运维(L2)",
    "附9.数智幼教行业智能体迭代及运维（L2）":           "09_智能体迭代运维(L2)",
    # 场景设备
    "六园所设备汇总":          "00_六园所设备汇总",
    "宝骏城园":                "01_宝骏城园",
    "滨江幼儿园":              "02_滨江幼儿园",
    "柳东一幼":                "03_柳东一幼",
    "市直属机关园":            "04_市直属机关园",
    "广西科大幼儿园":          "05_广西科大幼儿园",
    "市直属机关园分园":        "06_市直属机关园分园",
    # 其他费用
    "其他费用与预备费":        "01_其他费用与预备费",
    "软件产品购置费明细":      "02_软件产品购置费明细",
}


def std_filename(category_seq: str, doc_name: str, mode: str, date: str, ext: str) -> str:
    return f"{PROJECT_SHORT}_{category_seq}_{doc_name}_{mode}_v{PLUGIN_VERSION}_{date}.{ext}"
