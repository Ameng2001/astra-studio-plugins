"""sanitize — 对外材料净化模块.

去除送审材料（Z 主送审 / F 财评附件）中暴露投标方内部策略的字眼。
N 类（内部材料）不净化。

术语表与 global.md《对外净化规范》保持一致 —— global.md 是单一事实源，
本模块是其代码实现。修改术语必须同步两处。
"""
from __future__ import annotations

import re

# 顺序敏感：长词在前，避免短词先替换破坏长词
# (pattern, replacement) — 用 re.sub，pattern 是正则
TERM_RULES: list[tuple[str, str]] = [
    # —— 模式标签 ——
    (r"配套报价版本[:：]\s*\*{0,2}(保守|激进)\*{0,2}版?", "报价方案：正式版"),
    (r"报价版本[:：]\s*(保守|激进)", "报价方案：正式版"),
    (r"(保守|激进)版", "正式版"),
    (r"——\s*(保守|激进)版", "—— 正式版"),
    (r"\b(保守|激进)\b", "正式"),
    # —— 工具痕迹元数据（暴露自动生成）——
    # 生成时间 ISO → 编制日期 中文
    (r"\*?生成时间\*?[：:]\s*(\d{4})-(\d{2})-(\d{2})T[\d:]+",
     r"编制日期：\1年\2月\3日"),
    (r"\*?生成时间\*?[：:]\s*(\d{4})-(\d{2})-(\d{2})", r"编制日期：\1年\2月\3日"),
    # 整行删除：配套报价版本 / 用途 / 工具型说明 / 模式 行
    (r"(?m)^\s*[*_]*配套报价版本[*_]*[：:].*$", ""),
    (r"(?m)^\s*[*_]*用途[*_]*[：:].*(应答|答辩|内部|评审|填空|模板).*$", ""),
    (r"(?m)^\s*[*_]*说明[*_]*[：:].*(warn|fail|模板|空格|签字|3\s*选\s*1|逐项).*$", ""),
    (r"(?m)^\s*[*_]*模式[*_]*[：:].*$", ""),
    # rule 代码 / warn-fail / 工具分组
    (r"`?labor-pricing-(near-band-edge|out-of-band)`?", "单价取值事项"),
    (r"`?device-needs-table7`?", "设备品牌依据事项"),
    (r"`?[a-z]+-[a-z]+(?:-[a-z]+)+`?", "评审事项"),
    (r"按\s*rule\s*分组", "按事项分类"),
    (r"待答辩项", "待应答事项"),
    (r"\bwarn/fail\b|\bwarn\b|\bfail\b", "事项"),
    (r"3\s*选\s*1\s*(答辩)?模板", "应答要点"),
    # —— 反推机制（致命泄露）——
    (r"FP（反推）", "功能点（NESMA估算）"),
    (r"FP\(反推\)", "功能点(NESMA估算)"),
    (r"由原报价的?「?人/天」?工作量反推[：:]?\s*FP\s*=\s*人天\s*÷\s*\([^)]*\)\s*≈?\s*人天\s*÷\s*[\d.]+。?",
     "依据 NESMA 功能点计数法（SJ/T 11619-2016）估算。"),
    (r"由.{0,6}人[/／]天.{0,4}反推", "按 NESMA 功能点计数法估算"),
    (r"反推估算", "NESMA 估算"),
    (r"反推得出", "估算得出"),
    (r"（反推）", "（NESMA估算）"),
    (r"反推", "估算"),
    # —— 取上限 / 套标策略 ——
    (r"取上限\s*([\d.]+)", r"取值 \1（依据项目复杂度）"),
    (r"取上限", "取值"),
    (r"贴上限|贴墙根|舔到墙根", "按上限取值"),
    (r"安全垫|安全缓冲", "合理浮动区间"),
    (r"-?\d+%\s*缓冲", "合理浮动区间"),
    (r"套标|套利", "依标准取值"),
    # —— 优化（option A：彻底去除）——
    (r"优化后报价", "报价"),
    (r"优化后预计", "报价合计"),
    (r"优化建议|优化方案", "报价编制说明"),
    (r"优化决策记录", "报价编制说明"),
    (r"优化前", "调整前"),
    (r"本次优化", "本次报价编制"),
    (r"优化", "复核"),
    # —— 答辩 / 对抗色彩 ——
    (r"现场答辩材料|答辩包", "评审应答说明"),
    (r"财评可能挑战|可能挑战|预演挑战", "评审关注事项"),
    (r"答辩话术|答辩思路", "应答说明"),
    (r"答辩模板", "应答要点"),
    (r"红队预演|红队视角|财评红队", "评审视角"),
    (r"答辩", "应答"),
    # —— 内部代号 / 工具痕迹 ——
    (r"立场归因|立场偏向|立场切换", "报价编制原则"),
    (r"投标方军师|军师视角", ""),
    (r"合规守护", ""),
    (r"G1\s*[（(]?±?\d*%?[）)]?\s*区间", "报价编制原则"),
    (r"\bG[123]\b\s*约束?", "报价编制原则"),
    (r"scanner|scan_\w+|fund-review|plugin", ""),
    (r"降级(建议|处理)?|demote", "调整"),
    (r"重分类声明", "费用归集说明"),
    (r"重分类", "费用归集"),
    # —— 兜底：FP 表头中"反推"列名 ——
    (r"NESMA\s*反推", "NESMA估算"),
]

_COMPILED = [(re.compile(p), r) for p, r in TERM_RULES]


def sanitize_text(text: str) -> tuple[str, list[str]]:
    """返回 (净化后文本, 命中的原始片段列表)。"""
    if not isinstance(text, str) or not text:
        return text, []
    hits: list[str] = []
    out = text
    for pat, repl in _COMPILED:
        def _record(m, _r=repl):
            hits.append(m.group(0))
            return m.expand(_r)   # 展开 \1\2 反向引用（函数 repl 不会自动展开）
        out = pat.sub(_record, out)
    return out, hits


def sanitize_md_file(path) -> list[str]:
    from pathlib import Path
    p = Path(path)
    txt = p.read_text()
    new, hits = sanitize_text(txt)
    if new != txt:
        p.write_text(new)
    return hits


def sanitize_xlsx_file(path) -> list[str]:
    import openpyxl
    wb = openpyxl.load_workbook(path)
    all_hits: list[str] = []
    for sn in list(wb.sheetnames):
        ws = wb[sn]
        # sheet 名净化
        new_sn, hits = sanitize_text(sn)
        all_hits += hits
        if new_sn != sn and new_sn and new_sn not in wb.sheetnames:
            ws.title = new_sn
        # 单元格净化（跳过公式）
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and not v.startswith("="):
                    nv, hits = sanitize_text(v)
                    if nv != v:
                        cell.value = nv
                        all_hits += hits
    wb.save(path)
    return all_hits
