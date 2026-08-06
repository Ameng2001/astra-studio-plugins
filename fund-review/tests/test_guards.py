"""锁定各类「防静默出错」的守卫。

这个项目吃过的亏几乎都是同一个形状：**不报错，只是给出错误的数字**。
P0 合并单元格击穿 SUMIF 少算 ¥421.6 万；飞书删除只删一页留下新旧叠加；
计算书复用度词表不匹配导致 1763 行 US 全塌成 0；广东 × 全私有化出一份
¥921 万的报价而两个科目无处落账。

为此加了一批守卫。但**守卫本身没有测试，就等于守卫可能静默失效** ——
那会退回到同一个坑，而且更糟：这次还以为已经防住了。

跑法（无需 pytest）：
    PYTHONPATH=fund-review/scripts python3 fund-review/tests/test_guards.py
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


if FAILURES:
    print("守卫回归 —— 失败 %d 项：" % len(FAILURES))
    for f in FAILURES:
        print("  ✗ " + f)
    sys.exit(1)
print("守卫回归：全部通过")
