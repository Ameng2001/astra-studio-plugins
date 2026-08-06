"""锁定各类「防静默出错」的守卫。

这个项目吃过的亏几乎都是同一个形状：**不报错，只是给出错误的数字**。
P0 合并单元格击穿 SUMIF 少算 ¥421.6 万；飞书删除只删一页留下新旧叠加；
计算书复用度词表不匹配导致 1763 行 US 全塌成 0；广东 × 全私有化出一份
¥921 万的报价而两个科目无处落账。

为此加了一批守卫。但**守卫本身没有测试，就等于守卫可能静默失效** ——
那会退回到同一个坑，而且更糟：这次还以为已经防住了。

跑法（无需 pytest）：
    PYTHONPATH=fund-review/scripts python3 fund-review/tests/test_guards.py

**这里通过不等于工具是好的。** 本文件用合成数据测单元；这个项目的缺陷
几乎全部只在真实输入上显形（见 `smoke.py` 头部的统计：11 个缺陷本文件
抓到 0 个）。改完工具务必再跑一遍端到端矩阵：

    BOM_PROJECT_ROOT=<项目目录> python3 fund-review/tests/smoke.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import yaml

from bom_schema import (Bom, BomItem, BomError, Nesma, Path_, Vocabulary,
                        is_fp_counted, is_purchase)
from costing_engine import CostingEngine, DealConfig
from standard_pack import PackError, StandardPack

FAILURES: list[str] = []


def check(name: str, actual, expected) -> None:
    if actual != expected:
        FAILURES.append(f"{name}: 实得 {actual!r}，期望 {expected!r}")


def raises(name: str, fn, exc=Exception, contains: str = "") -> None:
    try:
        fn()
    except exc as e:
        if contains and contains not in str(e):
            FAILURES.append(f"{name}: 报错了但信息里没有 {contains!r} —— 实得 {e}")
        return
    except Exception as e:
        FAILURES.append(f"{name}: 抛了 {type(e).__name__} 而非 {exc.__name__}：{e}")
        return
    FAILURES.append(f"{name}: **没有报错** —— 守卫失效")


def item(iid="FP.TEST.0001", cls="SOFTWARE_FP", ntype="EI", app="业务处理",
         dev="大型行业软件开发", maturity="new", spec=None, tags=()) -> BomItem:
    return BomItem(
        id=iid, cls=cls, name="测试条目",
        path=Path_(product_line="P", system="S", l1="M"),
        description="支持测试用途的功能，用于校验守卫是否生效",
        nesma=Nesma(type=ntype) if ntype else None,
        app_type=app, dev_category=dev, maturity=maturity,
        spec=spec or {}, tags=list(tags))


# ---- 1. 造价口径二选一（DUAL_METHOD_CLASSES）---------------------------
# KB/DATASET 两栖：要么按功能点计，要么按购置计。两个都有是重复计列。

raises("DATASET 同时有 nesma 与 spec.subject",
       lambda: item(cls="DATASET", ntype="ELF",
                    spec={"subject": "三(二)2"}).validate(),
       BomError, "同时有")
raises("DATASET 既无 nesma 也无 spec.subject",
       lambda: item(cls="DATASET", ntype=None).validate(),
       BomError, "造价口径未定")
raises("MODEL 不该有 nesma 段",
       lambda: item(cls="MODEL", ntype="EO").validate(),
       BomError, "不应有 nesma")
item(cls="DATASET", ntype="ELF").validate()                      # 功能点口径，合法
item(cls="DATASET", ntype=None, spec={"subject": "三(二)2"}).validate()   # 购置口径，合法

check("is_fp_counted 认功能点口径的 DATASET",
      is_fp_counted(item(cls="DATASET", ntype="ELF")), True)
check("is_fp_counted 不认购置口径的 DATASET",
      is_fp_counted(item(cls="DATASET", ntype=None, spec={"subject": "x"})), False)
check("is_purchase 以 spec.subject 为准而非 class",
      is_purchase(item(cls="SOFTWARE_FP", spec={"subject": "x"})), True)


# ---- 2. 受控词表 G-14 ---------------------------------------------------
# app_type / dev_category 此前完全没有校验，合法值实际借用山东包的 factor 键。

VOCAB = {
    "fields": {
        "app_type": {"values": {"业务处理": "…", "智能信息": "…"}},
        "dev_category": {"values": {"大型行业软件开发": "…"}},
    },
    "purchase_classes_must_omit": ["app_type", "dev_category"],
}
with tempfile.TemporaryDirectory() as td:
    (Path(td) / "vocabulary.yaml").write_text(
        yaml.safe_dump(VOCAB, allow_unicode=True), encoding="utf-8")
    v = Vocabulary.load(Path(td))

check("词表：合法条目无问题", v.check(item()), [])
check("词表：功能点条目 app_type 为空要报",
      any("为空" in m for m in v.check(item(app=None))), True)
check("词表：app_type 不在词表要报",
      any("不在词表" in m for m in v.check(item(app="人工智能"))), True)
check("词表：非功能点条目不该有 app_type",
      any("非功能点条目不该有" in m
          for m in v.check(item(cls="MODEL", ntype=None, spec={"subject": "x"}))), True)
check("无词表文件时不校验（向后兼容）", Vocabulary().check(item(app="随便什么")), [])


# ---- 3. 标准包退役守卫 --------------------------------------------------
# 退役 ≠ 删除：历史报价的 lock 锁着它的 pack_id，删了历史就复算不出来。
# 但也不能让它被随手用于新报价 —— 光在 yaml 里写一句 deprecated 没人会读。

MINI_PACK = {
    "pack_id": "test-pack", "standard_doc": "测试标准", "formula_profile": "shandong_v1",
    "fp_counting": {"估算功能点法": {
        "weights": {"ILF": 10, "ELF": 7, "EI": 4, "EO": 5, "EQ": 4},
        "citation": {"page": 1, "section": "x", "quote": "q"}}},
    "factors": {
        "size_change": {"values": {"估算功能点法": 1.21},
                        "citation": {"page": 1, "section": "x", "quote": "q"}},
        "reuse": {"values": {"新建": 1.0},
                  "citation": {"page": 1, "section": "x", "quote": "q"}},
        "app_type": {"values": {"业务处理": 1.0, "智能信息": 1.5},
                     "citation": {"page": 1, "section": "x", "quote": "q"}},
        "dev_category": {"values": {"大型行业软件开发": 1.1},
                         "citation": {"page": 1, "section": "x", "quote": "q"}},
    },
    "rates": {
        "productivity_hours_per_fp": {"value": 6.71,
                                      "citation": {"page": 1, "section": "x", "quote": "q"}},
        "man_hours_per_month": {"value": 174,
                                "citation": {"page": 1, "section": "x", "quote": "q"}},
        "base_man_month_rate": {"value": 23645,
                                "citation": {"page": 1, "section": "x", "quote": "q"}},
    },
}


def write_pack(d: Path, extra: dict | None = None) -> Path:
    data = {**MINI_PACK, **(extra or {})}
    d.mkdir(parents=True, exist_ok=True)
    (d / "pack.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return d


with tempfile.TemporaryDirectory() as td:
    live = write_pack(Path(td) / "live")
    dead = write_pack(Path(td) / "dead", {
        "pack_id": "old-pack",
        "deprecated": {"since": "2026-01-01", "superseded_by": "new-pack",
                       "reason": "只覆盖一册", "still_valid_for": ["历史复算"]}})

    StandardPack.load(live)                                       # 现行包正常
    raises("退役包默认拒绝加载",
           lambda: StandardPack.load(dead), PackError, "已于")
    check("退役包显式放行",
          StandardPack.load(dead, allow_deprecated=True).pack_id, "old-pack")

    p = StandardPack.load(live)

    # ---- 4. factor 查不到时报错，不静默兜底 ----
    # 样例表的 IF 链嵌套 8 层、fallback 取 1 —— 那正是 P0 的失败机制。
    check("factor 正常取值", p.factor("app_type", "智能信息"), 1.5)
    raises("factor 取不到时报错而非静默取 1",
           lambda: p.factor("app_type", "通信控制"), PackError, "无取值")

    # ---- 5. 生产率三档：由标准包声明，不替标准编造 ----
    check("无 adjustable_range 时不出区间", p.productivity_band(), None)
    banded = write_pack(Path(td) / "band", {
        "pack_id": "band-pack",
        "rates": {**MINI_PACK["rates"],
                  "productivity_hours_per_fp": {
                      "value": 10.0, "adjustable_range": [0.8, 1.2],
                      "citation": {"page": 1, "section": "x", "quote": "q"}}}})
    band = StandardPack.load(banded).productivity_band()
    check("有 range 时出三档", [b[0] for b in band], ["下限", "中值", "上限"])
    check("三档取值", [round(b[1], 2) for b in band], [8.0, 10.0, 12.0])

    # ---- 6. 词表覆盖：缺口在选包时就报，不等算到那一条 ----
    vv = Vocabulary(fields={"app_type": {"values": {
        "业务处理": "", "智能信息": "", "通信控制": ""}}})
    cov = p.vocabulary_coverage(vv)["app_type"]
    check("覆盖检查认出遗漏", cov["missing"], ["通信控制"])
    check("覆盖检查状态", cov["status"], "incomplete")

    unmapped = write_pack(Path(td) / "um", {
        "pack_id": "um-pack",
        "factors": {**MINI_PACK["factors"],
                    "app_type": {"values": {"业务处理": 1.0, "智能信息": 1.5},
                                 "unmapped": ["通信控制"],
                                 "unmapped_note": "本标准无此类",
                                 "citation": {"page": 1, "section": "x", "quote": "q"}}}})
    cov2 = StandardPack.load(unmapped).vocabulary_coverage(vv)["app_type"]
    check("显式声明的无对应不算遗漏", cov2["missing"], [])
    check("显式声明的进 unmapped", cov2["unmapped"], ["通信控制"])

    # ---- 7. 范围过滤与项目特征因子 ----
    bom = Bom("1.0.0", [
        item("FP.AAA.0001", app="业务处理"),
        item("FP.BBB.0001", app="智能信息"),
    ])
    bom.items[1].path = Path_(product_line="P", system="S2", l1="M")

    deal = DealConfig("t")
    kept, _ = CostingEngine(bom, p, deal).in_scope()
    check("无范围过滤器时全量", len(kept), 2)

    deal_s = DealConfig("t", scope_filter=lambda i: (i.path.system == "S", "不在 include"))
    kept2, exc = CostingEngine(bom, p, deal_s).in_scope()
    check("范围过滤生效", len(kept2), 1)
    check("范围外有原因记录", any("范围外" in k for k in exc), True)

    # 项目特征因子：默认关闭 —— 一旦默认生效，报价里就有一段没有地方标准
    # 依据的调整，而清单上看不出来。
    e0 = CostingEngine(bom, p, DealConfig("t"))
    check("项目因子默认关闭", e0.project_factor(), (1.0, []))

    PF = {"id": "test", "factors": {
        "开发语言": {"values": {"JAVA": 1.0, "C": 1.5}},
        "质量特性": {"sub": {"性能": {"低": -1, "中": 0, "高": 1},
                             "可靠性": {"低": -1, "中": 0, "高": 1}}}}}
    e1 = CostingEngine(bom, p, DealConfig("t", project_factors={
        **PF, "selected": {"开发语言": "C", "质量特性": {"性能": "高", "可靠性": "高"}}}))
    val, rows = e1.project_factor()
    check("质量特性 1+0.025×Σ", next(r["取值"] for r in rows if r["因子"] == "质量特性"), 1.05)
    check("项目因子连乘", val, 1.575)          # 1.05 × 1.5

    raises("项目因子词表外取值报错",
           lambda: CostingEngine(bom, p, DealConfig("t", project_factors={
               **PF, "selected": {"开发语言": "COBOL"}})).project_factor(),
           ValueError, "不在")


# ---- 8. 飞书整表写入的写前校验 -----------------------------------------
# 「先清空再写入」曾把 15 张表全清空：脚本还在写一个已被删掉的列，
# 清空成功、写入失败，飞书不报错。

import lark_table

_FIELDS = {
    "名称": {"name": "名称", "type": "text"},
    "UFP": {"name": "UFP", "type": "formula"},
    "类别": {"name": "类别", "type": "select",
             "options": [{"name": "EI"}, {"name": "EO"}]},
}
lark_table.fields = lambda bt, tid: _FIELDS          # 桩掉网络调用

lark_table.preflight("b", "t", [{"名称": "x", "类别": "EI"}])     # 合法
raises("写不存在的列",
       lambda: lark_table.preflight("b", "t", [{"不存在的列": 1}]),
       lark_table.LarkTableError, "表里没有这些列")
raises("往公式列写值",
       lambda: lark_table.preflight("b", "t", [{"UFP": 9}]),
       lark_table.LarkTableError, "算出来的列")
raises("单选写不存在的选项",
       lambda: lark_table.preflight("b", "t", [{"类别": "XYZ"}]),
       lark_table.LarkTableError, "没有这些选项")


# ---- 9. 报价表结构守卫（quote_shape）-----------------------------------
# 同一个 bug 家族在 run_optimize / run_review 里出现过**五次**：认不出列名、
# 认不出 kind，然后静默返回 0 / 跳过整行。全都不报错，报告印出 0/100，
# 读的人只会以为报价烂透了。收敛到 quote_shape 后，这一组钉住它。

import quote_shape as qs

# kind 门控必须是**排除法** —— 白名单会把 unknown 一律排除，
# 而 unknown 恰恰是最常见的情况（实测真实报价整簿判为 unknown）
check("kind=unknown 参与检查", qs.is_priceable("unknown"), True)
check("kind=None 参与检查", qs.is_priceable(None), True)
check("kind=platform 参与检查", qs.is_priceable("platform"), True)
check("standard 不参与", qs.is_priceable("standard"), False)
check("summary sheet 不参与", qs.is_priceable("unknown", "summary"), False)

rs = qs.Resolver()
CELLS_A = {"人天": 200, "报价": 200000, "功能描述": "测试功能"}
rs.observe(CELLS_A)
check("按角色取人天", rs.get(CELLS_A, "person_days"), 200)
check("按角色取总额", rs.get(CELLS_A, "total"), 200000)
check("描述可取非数值", rs.get(CELLS_A, "detail", numeric=False), "测试功能")
check("无显式单价列时由总额÷人天推出", rs.derive_unit_price(CELLS_A), 1000.0)

rs2 = qs.Resolver()
CELLS_B = {"人/天": 10, "成本单价": 1500}
rs2.observe(CELLS_B)
check("有显式单价列时直接取", rs2.derive_unit_price(CELLS_B), 1500.0)

raises("未知语义角色要报错",
       lambda: qs.Resolver().get({}, "不存在的角色"), KeyError, "未知语义角色")

# 整表零命中 = 表结构没认出来 → 硬失败
rs3 = qs.Resolver()
for c in ({"莫名其妙的列": 1}, {"另一个怪列": 2}):
    rs3.observe(c); rs3.get(c, "total")
raises("金额角色全表零命中要硬失败",
       lambda: rs3.assert_recognized("total"), qs.QuoteShapeError, "零命中")
try:
    rs3.assert_recognized("total")
except qs.QuoteShapeError as e:
    check("报错要列出实际列名", "莫名其妙的列" in str(e), True)
    check("报错要给修法", "COLUMN_ROLES" in str(e), True)

# 命中过就不该报错；派生命中也算
rs4 = qs.Resolver(); rs4.observe(CELLS_A); rs4.derive_unit_price(CELLS_A)
rs4.assert_recognized("unit_price")           # 由 total÷person_days 派生，不该炸

# 迭代器把 kind 门控收在一处
QUOTE = {"workbooks": [
    {"path": "a.xlsx", "kind": "unknown", "sheets": [
        {"name": "s1", "kind": "items", "rows": [{"row_index": 1, "cells": CELLS_A}]},
        {"name": "s2", "kind": "summary", "rows": [{"row_index": 1, "cells": CELLS_A}]}]},
    {"path": "std.pdf", "kind": "standard", "sheets": [
        {"name": "x", "kind": "items", "rows": [{"row_index": 1, "cells": CELLS_A}]}]},
]}
got = [(sh["name"]) for _wb, sh, _r in qs.iter_priceable_rows(QUOTE)]
check("迭代器排除 summary 与 standard，保留 unknown", got, ["s1"])


# ============================================================================
# gov_sheet —— 送审表结构守卫
#
# 这一组防的不是「算错」，是「算对了但印成不能送审的样子」：
# Python dict 的 repr 落进单元格、英文枚举没翻译、明细行没有条款出处、
# 末行写了活 =SUM() 却和引擎的数对不上。前三个 excel_styler 那种事后
# 遍历改字体的做法完全看不见，第四个更糟 —— 它把矛盾直接摆给评审。
# ============================================================================
import gov_sheet as gs


def _sheet(**kw):
    wb = gs.new_workbook()
    kw.setdefault("title", "T")
    kw.setdefault("columns", [gs.Col("名称"), gs.Col("金额（元）", "money", sum=True)])
    return wb, gs.GovSheet(wb, kw.pop("name", "01_t"), **kw)


# --- 容器进单元格 ---
_, s = _sheet()
raises("dict 进单元格要报错（Python repr 泄漏到公文）",
       lambda: s.row({"名称": {"EI": 66, "EQ": 48}, "金额（元）": 1}),
       gs.GovSheetError, "容器")
_, s = _sheet()
raises("list 进单元格同样要报错",
       lambda: s.row({"名称": ["a", "b"], "金额（元）": 1}),
       gs.GovSheetError, "容器")
check("flatten_counts 给出可读文本",
      gs.flatten_counts({"EI": 66, "EQ": 48}), "EI 66 / EQ 48")

# --- 未翻译的枚举 ---
_, s = _sheet()
raises("裸小写枚举要报错", lambda: s.row({"名称": "existing", "金额（元）": 1}),
       gs.GovSheetError, "枚举")
check("label() 译得出已登记的枚举", gs.label("existing"), "已有产品")
check("label() 不动非枚举", gs.label("多角色业务应用"), "多角色业务应用")
# 编号不是枚举，不能误伤
_, s = _sheet()
for ident in ("FP.APP.GOV.0001", "D5", "v0.17.0", "R-7"):
    s.row({"名称": ident, "金额（元）": 1})
check("编号形态不被当成枚举误拦", s.seq_count, 4)

# --- 明细行必须带条款出处 ---
_, s = _sheet(clause_required=True)
raises("明细行缺条款出处要报错",
       lambda: s.row({"名称": "某项", "金额（元）": 1}),
       gs.GovSheetError, "标准条款")
s.row({"名称": "某项", "金额（元）": 1}, clause="三.(一).2.1")
check("给了条款就能写", s.seq_count, 1)

# --- 未声明的列不能悄悄丢掉 ---
_, s = _sheet()
raises("写未声明的列要报错（否则那一格静默消失）",
       lambda: s.row({"名称": "某项", "不存在的列": 1}),
       gs.GovSheetError, "未声明")

# raw() 是显式逃生口：确实要照原样印的小写英文
_, s = _sheet()
s.row({"名称": gs.raw("kubernetes"), "金额（元）": 1})
check("raw() 放行确认过的英文专名", s.ws.cell(3, 2).value, "kubernetes")

# --- 合计：活公式必须与引擎值一致 ---
_, s = _sheet()
for v in (100.0, 200.0, 300.0):
    s.row({"名称": "某项", "金额（元）": v})
raises("合计与引擎值不符要硬失败", lambda: s.total(expect={"金额（元）": 999.0}),
       gs.GovSheetError, "合计行与引擎值不符")
_, s2 = _sheet()
for v in (100.0, 200.0, 300.0):
    s2.row({"名称": "某项", "金额（元）": v})
r = s2.total(expect={"金额（元）": 600.0})
check("合计对得上就正常写出", s2.ws.cell(r, 3).value, "=SUM(C3:C5)")
check("合计标签落在第一个非序号列", s2.ws.cell(r, 2).value, "合计")
# 科目行不进合计 —— 进了就会翻倍
_, s3 = _sheet()
s3.group("一、某科目", {"金额（元）": 600.0})
for v in (100.0, 200.0, 300.0):
    s3.row({"名称": "某项", "金额（元）": v})
s3.total(expect={"金额（元）": 600.0})          # 科目行的 600 不该被算进去
check("科目行不占序号", s3.seq_count, 3)
_, s4 = _sheet()
raises("没有数据行就写合计要报错", lambda: s4.total(), gs.GovSheetError, "没有数据行")

# --- 结构：序号列、冻结、打印标题 ---
wb, s5 = _sheet(subtitle="副标题")
s5.row({"名称": "某项", "金额（元）": 1})
s5.finish()
check("序号列自动加在最前", s5.ws.cell(s5.header_row, 1).value, "序号")
check("有副标题时表头在第 3 行", s5.header_row, 3)
check("冻结在表头之下", s5.ws.freeze_panes, "A4")
check("打印时表头逐页重复", s5.ws.print_title_rows, "$3:$3")
check("金额列右对齐千分位",
      s5.ws.cell(4, 3).number_format, "#,##0.00")
# 无副标题时表头上移
_, s6 = _sheet()
check("无副标题时表头在第 2 行", s6.header_row, 2)

# --- 未知 fmt 要当场报错，不能静默按文本处理 ---
raises("未知列格式要报错", lambda: gs.Col("某列", "moneyy"), gs.GovSheetError, "fmt")

# --- 送审文件名 ---
check("送审文件名格式",
      gs.doc_name("数智康养", "Z01", "建设期采购清单", "0.17.0", "20260806"),
      "数智康养_Z01_建设期采购清单_正式版_v0.17.0_20260806.xlsx")

# --- 缺表要报错 ---
wb7 = gs.new_workbook()
raises("空工作簿不能保存", lambda: gs.save(wb7, Path("/tmp/x.xlsx")),
       gs.GovSheetError, "一张表都没有")



# --- 表里已写明 FP 就不该再从人天倒推 ---
# 送审格式改版时撞上的第六种「静默出 0」：新表按功能点给数、没有人天列，
# build_fp_table 一律走倒推 → total_fp 0，而报告照印。
rs_fp = qs.Resolver()
cells_fp = {"调整后功能点": 9.08, "未调整功能点": 5}
check("显式 FP 列能被认出", rs_fp.get(cells_fp, "fp"), 9.08)
check("没有人天列时人天取不到", rs_fp.get(cells_fp, "person_days"), None)
# 优先级：显式 FP 在前，未调整功能点在后
check("优先取调整后功能点",
      qs.Resolver().get({"未调整功能点": 5, "调整后功能点": 9.08}, "fp"), 9.08)



# --- 生成端与解析端的列名词表必须对得上 ---
# 改列名却不同步解析器 → 下游静默出 0。gov_sheet 在每次真实生成末尾结算，
# 这里只测机制本身。
gs._ROLE_REGISTRY.clear()
_, s = _sheet(columns=[gs.Col("金额（元）", "money", sum=True, role="total")])
gs.assert_roles_parseable()                    # 「金额（元）」在 COLUMN_ROLES 里
gs._ROLE_REGISTRY.clear()
_, s = _sheet(columns=[gs.Col("某个新列名", "money", sum=True, role="total")])
raises("解析器认不出的列名要报错",
       gs.assert_roles_parseable, gs.GovSheetError, "改了列名却没同步解析器")
gs._ROLE_REGISTRY.clear()
_, s = _sheet(columns=[gs.Col("金额（元）", "money", role="不存在的角色")])
raises("不存在的角色要报错", gs.assert_roles_parseable, gs.GovSheetError, "不存在")
gs._ROLE_REGISTRY.clear()

# --- 汇总表必须能被 parse_quote 认出来 ---
# 否则它的金额会与被它汇总的明细一起相加 —— 同一笔钱算两遍，不报错。
wb_r = gs.new_workbook()
gs.GovSheet(wb_r, "00_测算汇总", title="T", rollup=True,
            columns=[gs.Col("名称"), gs.Col("金额（元）", "money")])
raises("汇总表改成认不出的名字要报错",
       lambda: gs.GovSheet(wb_r, "00_软件开发费构成", title="T", rollup=True,
                           columns=[gs.Col("名称"), gs.Col("金额（元）", "money")]),
       gs.GovSheetError, "算两遍")
# 非汇总表不受此约束
gs.GovSheet(wb_r, "01_明细", title="T",
            columns=[gs.Col("名称"), gs.Col("金额（元）", "money")])



# ============================================================================
# 共享逻辑文件 —— 一份数据组只有一个维护方
#
# 「长者档案」曾在 5 个子系统各记一次 ILF、「订单」6 次，合计 25 条重复计列
# （250 UFP），而 G-04 只问「有没有 ILF」，答得上就全部放行。
# 真正的风险不是那点 UFP，是评审一眼看穿重复计列后整份计数都要重新举证。
# ============================================================================
import nesma_rules as nr

def _nesma(**kw):
    kw.setdefault("type", "ILF")
    return Nesma(**kw)

# 角色与类型必须自洽 —— 「引用方」却记 ILF 就是在重复计列
raises("标 reference 却记 ILF 要报错",
       lambda: _nesma(type="ILF", logical_file_role="reference",
                      logical_file_note="x").validate("T.1"),
       BomError, "应记 ELF")
raises("标 maintainer 却记 ELF 要报错",
       lambda: _nesma(type="ELF", logical_file_role="maintainer",
                      logical_file_note="x").validate("T.2"),
       BomError, "应记 ILF")
raises("填了角色却没写依据要报错",
       lambda: _nesma(type="ELF", logical_file_role="reference").validate("T.3"),
       BomError, "无从复核")
raises("非法角色值要报错",
       lambda: _nesma(logical_file_role="owner", logical_file_note="x").validate("T.4"),
       BomError, "maintainer")
_nesma(type="ELF", logical_file_role="reference",
       logical_file_note="引用「订单」，维护方为生态中台").validate("T.5")   # 自洽即通过
_nesma().validate("T.6")                       # 不跨子系统共享，不填角色也通过


def _bom_of(*specs):
    """specs: (id, system, name, type, note)"""
    return Bom("0.1.0", [BomItem(
        id=i, cls="SOFTWARE_FP", name=n,
        path=Path_(product_line="P", system=s),
        description="支持" + n + "的维护，描述长度需要够判定类型。",
        nesma=Nesma(type=ty, logical_file_note=note or None,
                    logical_file_role=("reference" if ty == "ELF" and note else
                                       "maintainer" if ty == "ILF" and note else None)),
        since="0.1.0") for i, s, n, ty, note in specs])

# 同名 ILF 跨子系统 → G-15 报
b_dup = _bom_of(("FP.A.0001", "系统甲", "长者档案", "ILF", ""),
                ("FP.B.0001", "系统乙", "长者档案", "ILF", ""))
f = nr.g15_shared_logical_file(b_dup)
check("跨子系统重复记 ILF 要报 G-15", [x.gate for x in f], ["G-15"])
check("G-15 报出多计的 UFP", f[0].detail["excess_ufp"], 10)
check("G-15 阻断 reviewed", f[0].blocks, "reviewed")

# 改判后不再报
b_fix = _bom_of(("FP.A.0001", "系统甲", "长者档案", "ILF", "本组维护方"),
                ("FP.B.0001", "系统乙", "长者档案", "ELF", "引用，维护方为系统甲"))
check("改判 ELF 后不再报", nr.g15_shared_logical_file(b_fix), [])

# 重名不同物：写了依据即视为已裁定
b_col = _bom_of(("FP.A.0001", "系统甲", "文档", "ILF", "行政公文，与智能体文档非同物"),
                ("FP.B.0001", "系统乙", "文档", "ILF", "智能体知识文档，与公文非同物"))
check("重名不同物写了依据就不再报", nr.g15_shared_logical_file(b_col), [])

# G-04 查的是 ILF+ELF 都为 0，不是只查 ILF ——
# 只查 ILF 会误报「正确地全部记为 ELF」的子系统，那是在惩罚正确建模
b_elf = _bom_of(*[(f"FP.C.{n:04d}", "系统丙", f"数据{n}", "ELF", "引用外部")
                  for n in range(1, 7)])
check("全部正确记为 ELF 的子系统不该被 G-04 误报",
      nr.g04_missing_ilf(b_elf), [])
b_none = _bom_of(*[(f"FP.D.{n:04d}", "系统丁", f"输出{n}", "EO", "")
                   for n in range(1, 7)])
check("ILF 与 ELF 皆无要报 G-04", [x.gate for x in nr.g04_missing_ilf(b_none)], ["G-04"])



# --- 活公式必须自验（试算表） ---
# 试算表的公式是给人改参数用的，但初始状态必须复现引擎的数 ——
# 否则打开文件就看到「公式算的」和「汇总页的」两个数打架。
f = gs.formula("=ROUND(C5*1.21,2)", 830.06, 830.06)
check("自验通过的公式可直接进单元格", str(f), "=ROUND(C5*1.21,2)")
check("公式携带自验证据", (f.computed, f.engine), (830.06, 830.06))
raises("公式与引擎值不符要报错",
       lambda: gs.formula("=C5*1.21", 1500.0, 1887.84, where="试算"),
       gs.GovSheetError, "活公式与引擎值不符")
# 逐条取整漂移用相对容差容忍，漏因子仍要拦
gs.formula("=x", 1887.60, 1887.84, rel_tol=0.001)
raises("相对容差不该放过漏因子的错",
       lambda: gs.formula("=x", 1258.56, 1887.84, rel_tol=0.001),
       gs.GovSheetError, "活公式与引擎值不符")

# 活公式要进合计 —— 它是 str 子类，漏掉会让 =SUM() 与逐行公式对不上
_, sf = _sheet(columns=[gs.Col("名称"),
                        gs.Col("金额（元）", "money", sum=True)])
for v in (100.0, 200.0, 300.0):
    sf.row({"名称": "某项",
            "金额（元）": gs.formula(f"=ROUND({v},2)", v, v)})
sf.total(expect={"金额（元）": 600.0})       # 不累加 Formula 就会在这里炸

# cell_role 上色：标准取值/可试算/自动算
raises("未知 cell_role 要报错",
       lambda: gs.Col("某列", cell_role="readonly"), gs.GovSheetError, "cell_role")
_, sc = _sheet(columns=[gs.Col("参数"),
                        gs.Col("取值", "money", cell_role="locked")])
sc.row({"参数": "生产率", "取值": 6.71})
check("locked 列上深灰", sc.ws.cell(3, 3).fill.fgColor.rgb[-6:], "D9D9D9")



# --- G-16 判定理由自述的类型必须等于实际类型 ---
# 「有理由」和「理由对」是两件事。G-02b 只查前者；一条 type=EQ 而理由在
# 论证 ILF 的条目，比没写理由更糟 —— 评审读的正是理由。
b_ok = _bom_of(("FP.A.0001", "系统甲", "某项", "EQ", ""))
b_ok.items[0].nesma.rationale = "判为 EQ：向边界外送出数据且不含派生计算。"
check("理由与类型一致不报", nr.g16_rationale_matches_type(b_ok), [])
b_bad = _bom_of(("FP.A.0001", "系统甲", "某项", "EQ", ""))
b_bad.items[0].nesma.rationale = "判为 ILF：系统边界内维护的逻辑数据组。"
f16 = nr.g16_rationale_matches_type(b_bad)
check("理由与类型分叉要报 G-16", [x.gate for x in f16], ["G-16"])
check("G-16 阻断 reviewed", f16[0].blocks, "reviewed")
# ELF 与 EIF 是同一概念的两地称谓，不算分叉
b_eif = _bom_of(("FP.A.0001", "系统甲", "某项", "ELF", ""))
b_eif.items[0].nesma.rationale = "判为 EIF：他方维护的逻辑数据组。"
check("ELF/EIF 视为同一概念", nr.g16_rationale_matches_type(b_eif), [])
# 没有「判为 X」句式的不报 —— 那是 G-02b 的范围
b_no = _bom_of(("FP.A.0001", "系统甲", "某项", "EQ", ""))
b_no.items[0].nesma.rationale = "沿用原表口径，待共创确认。"
check("无「判为」句式不误报", nr.g16_rationale_matches_type(b_no), [])

# --- 超额累进分档：分解之和必须等于引擎总额 ---
from costing_engine import CostingEngine as _CE
TBL = [[300, 0.02], [500, 0.016], [1000, 0.0128], [None, 0.0102]]
st = _CE._progressive_steps(800.0, TBL, 13.04, "设计费")   # 标准 p.11 自带算例
check("分档数（800 万落在第 3 档）", len(st), 3)
check("逐档累加等于标准算例", round(sum(s["amount_wan"] for s in st), 2), 13.04)
raises("分解与引擎总额不符要报错",
       lambda: _CE._progressive_steps(800.0, TBL, 99.0, "设计费"),
       ValueError, "分档累加")



if FAILURES:
    print("守卫回归 —— 失败 %d 项：" % len(FAILURES))
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("守卫回归：全部通过")
