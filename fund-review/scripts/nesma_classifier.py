"""nesma_classifier — 把功能描述启发式分类为 NESMA 五大类型.

PDF 第三章 2.1 ① 引用《SJ/T 11619-2016 NESMA》方法：
  ILF (Internal Logical File)  内部逻辑文件 — 用户可识别的逻辑数据组
  EIF (External Interface File) 外部接口文件 — 被本系统引用但维护在别处的逻辑数据组
  EI  (External Input)          外部输入   — 维护 ILF / 改变系统行为
  EO  (External Output)         外部输出   — 包含计算/派生数据的输出
  EQ  (External Query)          外部查询   — 不含派生数据的检索

NESMA 估算法权重：
  ILF=10, EIF=7, EI=4, EO=5, EQ=4

按"未调整功能点 = 10×ILF + 7×EIF + 4×EI + 5×EO + 4×EQ"反推：
  对于总 FP=N，启发式分配各类型数量，使加权和 ≈ N。

本模块给每行 detail 文本启发式估算 5 类各自数量，使分配可信、合规且 sum ≈ row_fp.
"""
from __future__ import annotations

import re
from typing import Any


NESMA_WEIGHTS = {"ILF": 10, "EIF": 7, "EI": 4, "EO": 5, "EQ": 4}


# 关键词 → 类型权重（多类型时取各 1）
ILF_KW = ["管理", "档案", "记录", "数据集", "知识库", "知识图谱", "字典", "目录", "台账", "本体"]
EIF_KW = ["对接", "外部系统", "第三方", "同步", "API 接入", "外部接口"]
EI_KW = ["新增", "录入", "上报", "采集", "上传", "导入", "修改", "删除", "编辑",
          "提交", "审批", "发起", "操作", "配置"]
EO_KW = ["生成", "分析", "推理", "计算", "统计", "导出", "报表", "看板", "可视化",
          "图表", "趋势", "预警", "智能"]
EQ_KW = ["查询", "查看", "搜索", "筛选", "检索", "浏览", "列表", "展示"]


def _count_hits(text: str, keywords: list[str]) -> int:
    return sum(1 for k in keywords if k in text)


def classify_row(detail: str, total_fp: int) -> dict:
    """启发式分配 NESMA 五类，使 sum(weight × count) ≈ total_fp.

    步骤：
      1. 数关键词命中数 → 初步比例
      2. 按权重反推数量
      3. 调整使 sum ≈ total_fp

    返回 {ILF: n, EIF: n, EI: n, EO: n, EQ: n, computed_fp: int}
    """
    if not detail or total_fp <= 0:
        return {"ILF": 0, "EIF": 0, "EI": 0, "EO": 0, "EQ": 0, "computed_fp": 0, "summary": ""}

    hits = {
        "ILF": max(1, _count_hits(detail, ILF_KW)),    # 几乎每行都有数据存储
        "EIF": _count_hits(detail, EIF_KW),
        "EI":  _count_hits(detail, EI_KW),
        "EO":  _count_hits(detail, EO_KW),
        "EQ":  _count_hits(detail, EQ_KW),
    }
    # 兜底：若没有任何 EI/EO/EQ 命中，给 EI=1 EO=1（CRUD 基线）
    if hits["EI"] + hits["EO"] + hits["EQ"] == 0:
        hits["EI"] = 1
        hits["EO"] = 1

    weighted_sum = sum(hits[t] * NESMA_WEIGHTS[t] for t in NESMA_WEIGHTS)
    if weighted_sum == 0:
        return {"ILF": 0, "EIF": 0, "EI": 0, "EO": 0, "EQ": 0, "computed_fp": 0, "summary": ""}

    # 缩放使 sum ≈ total_fp
    scale = total_fp / weighted_sum
    scaled = {t: max(0, round(hits[t] * scale)) for t in NESMA_WEIGHTS}
    # 确保至少 1 个 ILF（PDF 要求）
    if scaled["ILF"] == 0:
        scaled["ILF"] = 1

    computed = sum(scaled[t] * NESMA_WEIGHTS[t] for t in NESMA_WEIGHTS)
    # 误差大于 10% 时，把误差补到 EI（最常见类型）
    err = total_fp - computed
    if abs(err) > total_fp * 0.10 and err > 0:
        add_ei = err // NESMA_WEIGHTS["EI"]
        scaled["EI"] += add_ei
        computed = sum(scaled[t] * NESMA_WEIGHTS[t] for t in NESMA_WEIGHTS)

    summary = " + ".join(f"{t}×{scaled[t]}" for t in NESMA_WEIGHTS if scaled[t] > 0)
    return {**scaled, "computed_fp": computed, "summary": summary}


if __name__ == "__main__":  # pragma: no cover
    # smoke test
    test_text = "支持以可视化地图展示区域内所有园所的位置；支持查询园所详情、修改园所信息、新增评估数据"
    print(classify_row(test_text, 98))
