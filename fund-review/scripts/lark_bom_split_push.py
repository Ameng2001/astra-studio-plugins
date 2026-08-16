"""lark_bom_split_push —— 按子系统分表推送 BOM，另建参数表与词表。

## 为什么分表

单表 962 行、九个子系统混在一起，共创时每个人都要先筛一遍才能开始干活，
而且改动互相盖。分表之后每个子系统一张表，**谁负责哪块就打开哪张表**，
表名前缀带序号所以侧边栏顺序稳定。

命名：`1 市平台` … `5 支撑基座`（走功能点法）、
`6 知识工程` … `9 行业智能体`（走工作量法，不数功能点）。
序号不是装饰 —— 飞书侧边栏按名称排，没有序号时九张表的顺序随机。

## 两张只读参照表

`0 参数表`  本次计价用到的每一个标准取值 + 页码 + 章节 + 原文。
            共创时最常见的争论是「这个系数凭什么是 1.0」，
            把原文摆在同一个 base 里，比让人去翻 PDF 快得多。
`0 词表`    每个受控字段的合法取值与含义。方案人员填错值时，
            引擎报错是「算到那一条才报」，词表是「填之前就能看」。

两张表都**由 git 单向推出**，飞书侧改了也不会被 pull 回去 ——
它们是标准和 schema 的投影，不是共创对象。表名里的 `0` 前缀让它们排在最前。

用法：
    python3 lark_bom_split_push.py --bom <dir> --pack <pack_dir> \\
        --config <lark-sync.json> [--scope <split.yaml>]
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from bom_schema import Bom
from lark_table import LarkTableError, lark, replace_all
from nesma_weights import ESTIMATED_WEIGHTS as W
from standard_pack import StandardPack

#: 子系统 → 表名前缀序号。**显式登记**，不按字典序自动编 ——
#: 自动编号会在新增子系统时把已有表重排，而飞书表名一改，
#: 别人收藏的链接和正在编辑的视图都对不上。
SHEET_ORDER: dict[str, str] = {
    # A 应用平台
    "市平台": "A1 市平台", "园平台": "A2 园平台",
    "家平台": "A3 家平台", "运营平台": "A4 运营平台",
    # B 支撑基座（L0）
    "AIOT平台": "B1 AIOT平台", "全域数据平台": "B2 全域数据平台",
    # 原「AI计算平台」的 16 条建设内容实为微服务注册发现/网关/可观测，
    # 已按主体功能更名为「微服务治理平台」；「AI计算平台」这个名字让给了
    # 新建的模型开发能力（CV 模型训练 / AutoML / 数据集管理 / 数据标注）。
    # **表号 B3 跟着内容走，不跟着名字走** —— 远端 B3 里那 16 行就是微服务治理
    # 的内容，所以 B3 改名、内容原地保留；新的 AI计算平台 另开 B8。
    "微服务治理平台": "B3 微服务治理平台",
    "统一身份与权限": "B4 统一身份与权限",
    "应用开发平台": "B5 应用开发平台", "连接与开放平台": "B6 连接与开放平台",
    # 2026-08-14 飞书侧更名：原「B7 运营支撑平台」→「B7 安全治理平台」。
    # 表名是推送的对齐键 —— 这里不改，下次推送会当成「表不存在」而新建一张，
    # 旧表留在飞书上没人动，于是同一子系统又变成两张表（这次的两个 B7 就是这么来的）。
    "安全治理平台": "B7 安全治理平台", "AI计算平台": "B8 AI计算平台",
    # C 行业大模型（L0 引擎 → L1 → L2）
    "Harness引擎": "C1 Harness引擎(L0)", "本体引擎": "C2 本体引擎(L0)",
    "智能体引擎": "C3 智能体引擎(L0)",
    "知识工程": "C4 知识工程(L1)", "本体工程": "C5 本体工程(L1)",
    "数据工程": "C6 数据工程(L1)",
    "行业垂直模型": "C7 行业垂直模型(L2)", "行业智能体": "C8 行业智能体(L2)",
}

#: 字段顺序与分组 —— **一处定义，push 与 pull 都从这里取**。
#:
#: 飞书栅格视图没有原生的字段分组，唯一能让分组看得见的是字段名前缀。
#: 所以用 `参数·` / `研判·` 两个前缀把三段分开：
#:
#:   标识区   条目ID → 产品线 → 一~三级功能 → 功能点 → 需求描述
#:            回答「这是哪一条」。顺序与产品树一致，从左往右越来越具体。
#:   参数区   进入计价的取值。改这几个格子会改金额，所以聚在一起、离标识区远
#:   研判区   共创的工作面：状态、建议、判定理由
#:   追溯区   来源与版本，平时折叠
#:
#: 「名称」改叫「功能点」—— 拆到基本过程粒度之后，一行就是一个功能点，
#: 叫「名称」会让人以为它还是源清单里那个功能特性。
_SEC_ID, _SEC_PARAM, _SEC_JUDGE, _SEC_TRACE = "标识区", "参数区", "研判与建议区", "追溯区"
#: 提示区 —— 网格里**唯一**可见的非维护字段。
#:
#: 多维表格没有单元格批注（lark-cli base 只有字段/记录/视图，无 comment API），
#: 也没有逐格背景色 —— 能染色的只有单选字段的选项颜色。所以「我方对这一行的
#: 提示」只能收敛成一个彩色单选列；具体建议文字留在**隐藏字段**里，
#: 点开行详情的「隐藏字段」折叠区可见。网格保持干净，要细节才展开。
_SEC_HINT = "提示区"
#: 废弃区 —— 已停用但**不删除**的字段。
#:
#: 「工作量·阶段 / 人员类型 / 活动分解」三项已由 WBS 活动模板按子系统推导
#: （bom/effort-wbs.yaml），不再是条目属性。但删字段是不可逆操作，而这三个
#: 字段在远端 165 行上确认无人填写过 —— 留着并隐藏，成本为零、随时可翻回，
#: 比删掉再发现某处还在引用要安全。
#: 它们仍留在 spec 里，`_assert_fields_match` 才对得上；`_row` 跳过不写值。
_SEC_LEGACY = "废弃区"

#: 工作量估算法的阶段 —— 决定表1 的单价，所以是单选而不是自由文本
_SELECT_STAGE = {"type": "select", "multiple": False, "options": [
    {"name": "需求分析", "hue": "Blue", "lightness": "Lighter"},
    {"name": "系统设计", "hue": "Wathet", "lightness": "Lighter"},
    {"name": "软件开发（编码）", "hue": "Turquoise", "lightness": "Lighter"},
    {"name": "系统测试", "hue": "Lime", "lightness": "Lighter"},
    {"name": "实施部署", "hue": "Green", "lightness": "Lighter"}]}

#: 复用度档位（表3 注2 借用于表1 注3）。降价方向，故「高」「中」用暖色标出 ——
#: 无依据的折扣在评审那里是「随意定价」，比不打折更被动。
_SELECT_REUSE = {"type": "select", "multiple": False, "options": [
    {"name": "高", "hue": "Red", "lightness": "Light"},
    {"name": "中", "hue": "Orange", "lightness": "Light"},
    {"name": "低", "hue": "Gray", "lightness": "Lighter"}]}

#: 共创状态的选项 —— **取值来自 bom_annotate，不在这里另抄一份**。
#: 抄一份的下场刚发生过：`bom_annotate` 加了「★待补工作量依据」，
#: 这里没跟上，preflight 直接拦停整批推送（幸好拦住了 —— 不拦的话
#: 那 132 条的状态会写不进去，而表面上推送成功）。
#: 同一个枚举写在两处，迟早漏一处；从源头引就没有这个问题。
_STATUS_HUE = {
    "★待补描述": ("Red", "Light"),
    "★建议再拆": ("Orange", "Light"),
    "★待补工作量依据": ("Carmine", "Light"),
    # 「填了待确认」用冷色，与 ★ 系列的暖色拉开 —— 共创的人靠颜色分优先级，
    # 这一档不拦路，不该和「拦路」混在同一色系里。
    "工作量依据待确认": ("Wathet", "Lighter"),
    "技术说明待复核": ("Yellow", "Lighter"),
    "已判型": ("Green", "Lighter"),
    "已处理": ("Gray", "Lighter"),
}


def _status_options() -> list[dict]:
    import bom_annotate as ba
    names = [getattr(ba, n) for n in dir(ba) if n.startswith("STATUS_")]
    names.append("已处理")          # 只在飞书侧使用，本地不产出
    missing = [n for n in names if n not in _STATUS_HUE]
    if missing:
        raise ValueError(
            f"bom_annotate 新增了状态 {missing} 但 _STATUS_HUE 没登记配色 —— "
            f"补一行即可。**不给默认色**：默认色会让新状态混进已有配色里，"
            f"共创的人靠颜色分优先级，混了就分不出来")
    seen, opts = set(), []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        hue, light = _STATUS_HUE[n]
        opts.append({"name": n, "hue": hue, "lightness": light})
    return opts


_SELECT_STATUS = {"type": "select", "multiple": False,
                  "options": _status_options()}
_SELECT_FPTYPE = {"type": "select", "multiple": False, "options": [
    {"name": "ILF", "hue": "Purple", "lightness": "Light"},
    {"name": "ELF", "hue": "Wathet", "lightness": "Light"},
    {"name": "EI", "hue": "Blue", "lightness": "Lighter"},
    {"name": "EO", "hue": "Turquoise", "lightness": "Lighter"},
    {"name": "EQ", "hue": "Lime", "lightness": "Lighter"}]}

#: (显示名, 分组, 字段 spec, 取值函数)
FIELD_SPEC: list[tuple[str, str, dict, Any]] = [
    ("条目ID",       _SEC_ID,    {"type": "text"},   lambda i: i.id),
    ("产品线",       _SEC_ID,    {"type": "text"},   lambda i: i.path.product_line),
    ("一级功能",     _SEC_ID,    {"type": "text"},   lambda i: i.path.l1 or ""),
    ("二级功能",     _SEC_ID,    {"type": "text"},   lambda i: i.path.l2 or ""),
    ("三级功能",     _SEC_ID,    {"type": "text"},   lambda i: i.path.l3 or ""),
    ("功能点",       _SEC_ID,    {"type": "text"},   lambda i: i.name),
    ("需求描述",     _SEC_ID,    {"type": "text"},   lambda i: i.description or ""),
    ("参数·功能点类型", _SEC_PARAM, _SELECT_FPTYPE,
     lambda i: i.nesma.type if i.nesma else ""),
    ("参数·UFP",     _SEC_PARAM, {"type": "number"},
     lambda i: W[i.nesma.type] if i.nesma else 0),
    # 「参数·软件类别」已移除：软件类别按**子系统**取值（表3 注1「按主体功能的
    # 类型取值」），权威值在 bom/taxonomy.yaml 的子系统节点。逐行摊出来是同一个值
    # 抄几百遍，看着像逐条判定，其实是把结论抄了一遍 —— 拿它举证等于用结论证明结论。
    # 更实际的害处：这一列在飞书里可编辑，改了要么不生效、要么让引擎拒绝出表，
    # 而人已经白改了几十行。归类的共创在丙本 02_二层试算（一子系统一行）上做。
    ("参数·条目类别", _SEC_PARAM, {"type": "text"},   lambda i: i.cls),
    ("提示·状态", _SEC_HINT, _SELECT_STATUS,
     lambda i: i.coauthor_status or "已判型"),
    ("研判·处理建议", _SEC_JUDGE, {"type": "text"},   lambda i: i.coauthor_advice),
    ("研判·判定理由", _SEC_JUDGE, {"type": "text"},
     lambda i: (i.nesma.rationale or "") if i.nesma else ""),
    ("追溯·来源特性", _SEC_TRACE, {"type": "text"},
     lambda i: i.id.rsplit(".", 1)[0] if i.id.count(".") >= 3 else ""),
    ("追溯·原属子系统", _SEC_TRACE, {"type": "text"},
     lambda i: i.path.l0_source or ""),
    ("追溯·引入版本", _SEC_TRACE, {"type": "text"},  lambda i: i.since),
]

#: 状态字段的显示名 —— 视图筛选与 pull 都要用，写死在多处必然漂
STATUS_FIELD = "提示·状态"

#: 工作量估算法的字段组 —— **与功能点法不是同一套**。
#: C4–C8 那五张表里「参数·功能点类型」「参数·UFP」永远是空的（工作量法不数
#: 功能点），而真正该填的「人月」「测算依据」一个字段都没有。
#: 同一套字段推 19 张表，等于让工作量法那 5 张既填不了该填的、又摆着填不了的。
_FIELD_SPEC_EFFORT: list[tuple[str, str, dict, Any]] = [
    ("条目ID",   _SEC_ID, {"type": "text"}, lambda i: i.id),
    ("产品线",   _SEC_ID, {"type": "text"}, lambda i: i.path.product_line),
    ("一级功能", _SEC_ID, {"type": "text"}, lambda i: i.path.l1 or ""),
    ("二级功能", _SEC_ID, {"type": "text"}, lambda i: i.path.l2 or ""),
    ("三级功能", _SEC_ID, {"type": "text"}, lambda i: i.path.l3 or ""),
    ("建设内容", _SEC_ID, {"type": "text"}, lambda i: i.name),
    ("需求描述", _SEC_ID, {"type": "text"}, lambda i: i.description or ""),
    # ---- 取数口径：`effort_basis`，**不是 `effort` / `legacy_quote`** ----
    # 旧字段组接的是 `effort`（阶段/人月/角色/metric）与 `legacy_quote`，
    # 那批人月来自源估算表，实测 132 条全部满足 `人月 = 报价 ÷ 17,000`，
    # 即由对外报价倒算而来。2026-08-09 已改为自下而上按「量纲 × 单位工作量」
    # 逐条重估，权威值在 `effort_basis`。字段组不跟着改，飞书上给方案侧看的
    # 就是**我们自己已经废弃的那版数**，而且本体工程 17 条会显示 0（它们
    # 从来没有 `effort` / `legacy_quote`）—— 显示错数比藏错列更有害。
    #
    # 「阶段」「人员类型」「活动分解」三项已移除：它们现在由 WBS 活动模板
    # 按子系统推导（bom/effort-wbs.yaml），不是条目自身的属性。逐条摆出来
    # 是把同一组模板值抄几百遍，且在飞书里可编辑 —— 改了不生效。
    ("工作量·可核查量纲", _SEC_PARAM, {"type": "text"},
     lambda i: (i.effort_basis or {}).get("unit", "")),
    ("工作量·数量", _SEC_PARAM, {"type": "number"},
     lambda i: (i.effort_basis or {}).get("qty") or 0),
    ("工作量·单位工作量", _SEC_PARAM, {"type": "number"},
     lambda i: (i.effort_basis or {}).get("per_unit_mm") or 0),
    ("工作量·人月", _SEC_PARAM, {"type": "number"},
     lambda i: (i.effort_basis or {}).get("man_months") or 0),
    ("工作量·测算依据", _SEC_PARAM, {"type": "text"},
     lambda i: (i.effort_basis or {}).get("basis", "")),
    # 复用度（表1 注3）—— 粒度为建设对象，非「低」必须有依据，否则引擎拒绝出表
    ("参数·复用度", _SEC_PARAM, _SELECT_REUSE,
     lambda i: (i.reuse or {}).get("level", "低")),
    ("参数·复用依据", _SEC_PARAM, {"type": "text"},
     lambda i: (i.reuse or {}).get("basis", "")),
    ("提示·状态", _SEC_HINT, _SELECT_STATUS,
     lambda i: i.coauthor_status or "已判型"),
    ("研判·处理建议", _SEC_JUDGE, {"type": "text"}, lambda i: i.coauthor_advice),
    ("追溯·源表人月", _SEC_TRACE, {"type": "number"},
     lambda i: (i.legacy_quote or {}).get("man_months") or 0),
    ("追溯·原属子系统", _SEC_TRACE, {"type": "text"},
     lambda i: i.path.l0_source or ""),
    ("追溯·引入版本", _SEC_TRACE, {"type": "text"}, lambda i: i.since),
    # ---- 以下三项已停用，保留字段但不写值、不显示（见 _SEC_LEGACY） ----
    ("工作量·阶段", _SEC_LEGACY, _SELECT_STAGE, None),
    ("工作量·人员类型", _SEC_LEGACY, {"type": "text"}, None),
    ("工作量·活动分解", _SEC_LEGACY, {"type": "text"}, None),
]

def _fields_of(spec_list):
    return [{**spec, "name": n, "description": f"【{sec}】"}
            for n, sec, spec, _ in spec_list]


#: 字段改名迁移表：{旧名: 新名}。**用 field-update 改名，不是删了重建** ——
#: 删除会连同 A/B 组 686 行上的共创状态一起丢掉，而重建出来的空列看起来
#: 和「大家还没开始填」一模一样。
FIELD_RENAMES = {"研判·共创状态": "提示·状态"}

#: 表改名迁移：{旧表名: 新表名}。**改名不是新建** —— 直接换 SHEET_ORDER 的话
#: `ensure_table` 找不到新名就建一张空表，而旧表连同它那 16 行留在侧边栏，
#: 两张表都叫得上名字、都有数据，没人分得清哪张是活的。
TABLE_RENAMES = {"B3 AI计算平台": "B3 微服务治理平台"}
#: 「B7 运营支撑」与「B7 运营支撑平台」两张同名同内容表（各 97 行），
#: 2026-08-10 业务侧定：保留「运营支撑平台」，删「运营支撑」。
#: 配置原本指着要删的那张 —— 若先删表再改配置，配置会指向不存在的 tid，
#: 下次推送会**新建一张空表**而不是报错。故先改指向，再由人删。


def migrate_tables(bt: str, cfg: dict) -> None:
    """按 TABLE_RENAMES 原地改远端表名，并同步 lark-sync.json 的键。"""
    r = lark("base", "+table-list", "--base-token", bt, "--as", "user")
    have = {x["name"]: x["id"]
            for x in ((r.get("data") or {}).get("tables")
                      or (r.get("data") or {}).get("items") or [])}
    for old, new in TABLE_RENAMES.items():
        if old in have and new not in have:
            lark("base", "+table-update", "--base-token", bt,
                 "--table-id", have[old], "--name", new, "--as", "user")
            print(f"  ↻ 表改名 {old} → {new}（原地改名，记录保留）")
            cfg.setdefault("tables", {})[new] = cfg["tables"].pop(old, have[old])


_ITEM_FIELDS = _fields_of(FIELD_SPEC)
_ITEM_FIELDS_EFFORT = _fields_of(_FIELD_SPEC_EFFORT)


#: 工作量法各表的**逐表规格微调** —— 2026-08-14 方案侧在飞书重构了 C 组表的
#: 计量口径列，经营侧拍板**表规格跟着飞书列名走**（更直观），不把对方改回旧名：
#:   C4  数量列改叫「词元数目档位」，新增「语料量级（落档依据）」作落档举证
#:   C7  单位工作量拆成「模型基准工作量 + 模型单位工作量」显式化模型公式
#:   C5/C6/C8  「说明」（C6 叫「工作量.数量.说明」）为逐条测算构成
#:   「父记录」是飞书层级视图的副产物，保留占位、不写值（同 _SEC_LEGACY 机制）
#: 这些列的值 pull 已按同名映射收进 effort_basis（corpus_scale / model_base_mm /
#: model_unit_mm / counted_from），推送在这里按同一映射写出 —— 推得出就拉得回。
_T = {"type": "text"}
_N = {"type": "number"}
_eb = lambda k, d="": (lambda i: (i.effort_basis or {}).get(k, d))
_EFFORT_TWEAKS: dict[str, dict] = {
    "知识工程": {
        "drop": ["工作量·数量"],
        "add": [("词元数目档位", _SEC_PARAM, _N, _eb("qty", 0)),
                ("语料量级（落档依据）", _SEC_PARAM, _T, _eb("corpus_scale")),
                ("父记录", _SEC_LEGACY, _T, None)]},
    "本体工程": {
        "add": [("说明", _SEC_PARAM, _T, _eb("counted_from")),
                ("父记录", _SEC_LEGACY, _T, None)]},
    "数据工程": {
        "add": [("工作量.数量.说明", _SEC_PARAM, _T, _eb("counted_from"))]},
    "行业垂直模型": {
        "drop": ["工作量·单位工作量"],
        "add": [("工作量·模型基准工作量", _SEC_PARAM, _N, _eb("model_base_mm", 0)),
                ("工作量·模型单位工作量", _SEC_PARAM, _N, _eb("model_unit_mm", 0)),
                ("说明", _SEC_PARAM, _T, _eb("counted_from"))]},
    "行业智能体": {
        "add": [("说明", _SEC_PARAM, _T, _eb("counted_from")),
                ("父记录", _SEC_LEGACY, _T, None)]},
}


def _spec_for(system: str, effort_systems: set[str]):
    if system not in effort_systems:
        return FIELD_SPEC
    tw = _EFFORT_TWEAKS.get(system)
    if not tw:
        return _FIELD_SPEC_EFFORT
    drop = set(tw.get("drop") or [])
    return ([f for f in _FIELD_SPEC_EFFORT if f[0] not in drop]
            + list(tw.get("add") or []))


def _row(i, spec_list=None) -> dict[str, Any]:
    # fn 为 None 的是废弃字段：字段保留（不删），但不写值。
    return {n: fn(i) for n, _, _, fn in (spec_list or FIELD_SPEC) if fn}


def param_rows(pack: StandardPack) -> list[dict[str, Any]]:
    """参数表 —— 本次计价用到的标准取值，逐条带页码/章节/原文。"""
    out: list[dict[str, Any]] = []

    def add(group, name, value, unit, cite, note=""):
        c = cite or {}
        out.append({
            "参数组": group, "参数名": name, "取值": str(value), "单位": unit,
            "页码": c.get("page", ""), "章节": c.get("section", ""),
            "标准原文": (c.get("quote") or "")[:400], "说明": note})

    for m, spec in (pack.data.get("fp_counting") or {}).items():
        add("功能点权重", m,
            " ".join(f"{k}={v}" for k, v in spec["weights"].items()),
            "分/项", spec.get("citation"), spec.get("applies_to", ""))

    node = (pack.data.get("factors") or {}).get("app_type") or {}
    for cat, val in (node.get("values") or {}).items():
        rng = (node.get("value_range") or {}).get(cat)
        add("软件类别调整因子", cat, val, "系数", node.get("citation"),
            (f"表3 区间 {rng[0]}–{rng[1]}；取值超过 1.0 须列明依据（注1）"
             if rng else "") + "　范围：" + (node.get("ranges") or {}).get(cat, ""))

    node = (pack.data.get("factors") or {}).get("reuse") or {}
    for k, v in (node.get("values") or {}).items():
        add("复用系数", k, v, "系数", node.get("citation"), "")

    for name, spec in (pack.data.get("rates") or {}).items():
        add("费率", name, spec.get("value"), spec.get("unit", ""),
            spec.get("citation"),
            (f"可浮动 ±{int((spec['adjustable_range'][1]-1)*100)}%"
             if spec.get("adjustable_range") else "")
            + "　" + str(spec.get("basis") or spec.get("note") or ""))

    em = pack.data.get("effort_method") or {}
    for stage, spec in (em.get("stage_rates") or {}).items():
        add("工作量法阶段单价", stage, spec["value"], spec["unit"],
            em.get("citation"),
            "⚠️ 表1 无国标背书；功能点法挂 GB/T 36964-2018")

    for f in pack.data.get("other_fees") or []:
        v = (f"{f.get('max_rate')}" if f.get("max_rate") is not None
             else (json.dumps(f.get("max_rate_by_layout"), ensure_ascii=False)
                   if f.get("max_rate_by_layout")
                   else json.dumps(f.get("table"), ensure_ascii=False)))
        add("其他费用", f["name"], v[:120], f.get("unit", ""), f.get("citation"),
            f.get("base_note", "") or f.get("rate_note", ""))
    return out


def vocab_rows(bom_dir: Path) -> list[dict[str, Any]]:
    """词表 —— 受控字段的合法取值。填之前就能看，而不是算到那一条才报错。"""
    p = bom_dir / "vocabulary.yaml"
    if not p.exists():
        return []
    v = yaml.safe_load(p.read_text(encoding="utf-8"))
    out = []
    for fname, spec in (v.get("fields") or {}).items():
        for val, desc in (spec.get("values") or {}).items():
            out.append({
                "字段": spec.get("label", fname), "字段名": fname,
                "合法取值": val, "含义": " ".join(str(desc).split())[:300],
                "是否必填": "必填" if spec.get("required") else "选填",
                "备注": " ".join(str(spec.get("note", "")).split())[:300]})
    return out


#: 飞书对重名表的处理：不报错，而是建一张 `<原名>_conflict_<tableid>`。
#: 所以「建表失败」和「建了一张重名的」长得一模一样 —— 都是 `_find` 找不到原名。
_CONFLICT = "_conflict_"


def _scan(bt: str, name: str) -> tuple[str | None, list[tuple[str, str]]]:
    """返回 (同名表 id, 该名字的 conflict 变体列表)。"""
    r = lark("base", "+table-list", "--base-token", bt, "--as", "user")
    exact, variants = None, []
    for t in ((r.get("data") or {}).get("tables") or []):
        if t["name"] == name:
            exact = t["id"]
        elif t["name"].startswith(name + _CONFLICT):
            variants.append((t["id"], t["name"]))
    return exact, variants


def _find(bt: str, name: str) -> str | None:
    return _scan(bt, name)[0]


def _row_count(bt: str, tid: str) -> int:
    r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
             "--as", "user", "--limit", "200")
    d = r.get("data") or {}
    # **响应形状是 `data.fields` + `data.data`（行数组）**，不是 `data.items`，
    # 也没有 `record_id_list`。原来按后者取，任何表都返回 0 ——
    # 于是「读到 0 行」这句提示对每张表都成立，等于没提示。
    # （同一处 24 张表 952 行被我读成全空，就是这个形状搞错的。）
    return len(d.get("data") or []) + (1 if d.get("has_more") else 0)


def ensure_table(bt: str, name: str, fields: list[dict]) -> str:
    """建表（幂等）。**建之前多探几次，建之后清重名。**

    飞书这边是最终一致的：`+table-create` 返回 ok 之后，`+table-list`
    未必立刻列得到 —— 实测推到第 4 张表时报 `建表后仍找不到 A4 运营平台`。
    `replace_all` 早就为删除做了轮询，建表这里当初漏了。

    加了轮询之后还剩一个更隐蔽的坑：**一次陈旧的「找不到」会让我们重复建表，
    而飞书遇到重名不报错，它建一张 `A4 运营平台_conflict_tbl4GHw6sbVTGOFK`。**
    那张空表混在侧边栏最后，没人会注意；下次再推又多一张。
    上一轮就是这么留下一张空表，靠手工清掉的。

    所以两头都堵：

      建之前  连探 3 次再决定要不要建 —— 单次陈旧读是重复建表的唯一来源
      建之后  扫 conflict 变体，**只报告，不删**

    ## 为什么不自动删空的变体

    起初写的是「空的才删，有数据的不删」。**测出来是错的**：造一张带数据的
    变体、立刻跑 ensure_table，那张表被删了 —— 因为记录刚写完还没传播，
    行数读回来是 0。飞书这边一读定论从来不成立，这次的代价是毁数据。

    所以不做「读一次再删」这件事。行数只用来给人看，删不删由人决定，
    命令直接印出来。少一次自动化，换不会误删。
    """
    for i in range(3):
        tid, variants = _scan(bt, name)
        if tid:
            _report_variants(bt, name, variants)
            migrate_fields(bt, name, tid, fields)
            _assert_fields_match(bt, name, tid, fields)
            return tid
        if i < 2:
            time.sleep(1 + i)

    # 关联字段（link）建表时给不了 link_table —— 目标表可能还没建。
    # 所以建表时不传它，由调用方在两张表都在之后补建；
    # 但**字段比对时它算数**，否则每次都会报「多了一个关联物料」。
    lark("base", "+table-create", "--base-token", bt, "--name", name,
         "--fields", json.dumps([f for f in fields if f.get("type") != "link"],
                                ensure_ascii=False), "--as", "user")
    for i in range(8):
        tid, variants = _scan(bt, name)
        if tid:
            _report_variants(bt, name, variants)
            return tid
        time.sleep(1 + i)

    tid, variants = _scan(bt, name)
    if variants and not tid:
        # 原名一直没出现、却有 conflict 变体 —— 说明原表存在但列不出来，
        # 这种状态自动收拾会更乱，交人。
        raise LarkTableError(
            f"{name}：列不到同名表，却有 {len(variants)} 张 conflict 变体 "
            f"{[v[1] for v in variants]} —— 飞书侧状态不一致，请人工确认后再推")
    raise LarkTableError(
        f"建表后轮询 8 次仍找不到 {name} —— 这次不是延迟，检查表名是否非法或已达上限")


def migrate_fields(bt: str, name: str, tid: str, want: list[dict]) -> None:
    """把已有表的字段结构迁到当前规格 —— **只改名与新建，永不删除**。

    ## 为什么不删

    删字段是不可逆的，而「这一列没人填过」这个判断依赖一次远端读取 ——
    本项目已经栽过：`_row_count` 把响应形状读错，24 张表 952 行被判成全空。
    一次读错就删掉整列，代价不对称。留着且隐藏，成本为零、随时翻回。

    ## 为什么改名不能用「删了重建」

    `研判·共创状态` 在 A/B 组 686 行上是真实的共创成果。删了重建出来的空列
    与「大家还没开始填」长得一模一样，没有任何地方会提示数据没了。
    所以走 `+field-update` 原地改名。
    """
    r = lark("base", "+field-list", "--base-token", bt, "--table-id", tid,
             "--as", "user")
    have = {(f.get("name") or f.get("field_name")): f
            for f in ((r.get("data") or {}).get("items")
                      or (r.get("data") or {}).get("fields") or [])}
    for old, new in FIELD_RENAMES.items():
        if old in have and new not in have:
            cur = have[old]
            fid = cur.get("id") or cur.get("field_id")
            # **`+field-update` 是全量 PUT，不是 patch**，且是 high-risk-write。
            # 两个坑各踩过一次：
            #   ① 不传 `--yes` → 命令返回 confirmation_required 而 `lark()`
            #      不抛错，于是打印「改名成功」而远端一动没动。
            #   ② body 只带 type/description → 单选字段顶层的 `options`
            #      被 PUT 掉，6 个带颜色的选项清空，786 行的状态列变成空单选，
            #      **而这一步会报成功**。选项在顶层 `options`，不在 `property`。
            # 所以：整份定义原样带上，只换 name；改完复查，不信返回值。
            body = {k: v for k, v in cur.items()
                    if k not in ("id", "field_id", "name", "field_name")}
            body["field_name"] = new
            body["name"] = new
            lark("base", "+field-update", "--base-token", bt, "--table-id", tid,
                 "--field-id", fid, "--json", json.dumps(body, ensure_ascii=False),
                 "--as", "user", "--yes")
            # **复查要轮询。** 飞书是最终一致的（本文件 ensure_table 早有
            # 建表前探 3 次、建表后探 8 次的先例）。改名后立刻读一次就断言，
            # 实测在 B2 全域数据平台 上误判成「改名未生效」并停掉整次推送 ——
            # 而复查它时改名早已生效。守卫方向没错（宁可停也不能默认成功），
            # 错在只读一次就定论。
            now: dict = {}
            for _try in range(6):
                chk = lark("base", "+field-list", "--base-token", bt,
                           "--table-id", tid, "--as", "user")
                now = {(f.get("name") or f.get("field_name")): f
                       for f in ((chk.get("data") or {}).get("fields")
                                 or (chk.get("data") or {}).get("items") or [])}
                if new in now:
                    break
                time.sleep(1 + _try)
            if new not in now:
                raise LarkTableError(
                    f"{name}：字段改名 {old} → {new} 轮询 6 次仍未生效"
                    f"（远端仍无 {new}）。改名失败但命令不一定报错 —— "
                    f"已复查拦下，不继续推送。")
            if len(now[new].get("options") or []) != len(cur.get("options") or []):
                raise LarkTableError(
                    f"{name}：字段 {new} 改名后选项数由 "
                    f"{len(cur.get('options') or [])} 变成 "
                    f"{len(now[new].get('options') or [])} —— 全量 PUT 冲掉了选项。")
            print(f"    ↻ {name}：字段改名 {old} → {new}"
                  f"（选项 {len(cur.get('options') or [])} 个已保留）")
            have[new] = have.pop(old)
    # ---- 已有单选字段的**选项**同步 ----
    # `migrate_fields` 原来只管「字段在不在」，不管选项对不对。于是本地
    # 词表新增一个状态、远端那个 select 还是旧的 6 项，preflight 报
    # 「单选列没有这些选项」并停掉整次推送 —— 字段明明「存在且匹配」。
    # 取**并集**：远端已有的连颜色一起保留（可能有人在飞书上手工加过项），
    # 本地新增的按 spec 的配色补进去。不做减法：删选项会把已经用了该选项
    # 的行清空，而那是别人填的。
    for f in want:
        cur = have.get(f["name"])
        if not cur or not f.get("options"):
            continue
        rn = {o["name"] for o in (cur.get("options") or [])}
        add = [o for o in f["options"] if o["name"] not in rn]
        if not add:
            continue
        body = {k: v for k, v in cur.items()
                if k not in ("id", "field_id", "name", "field_name")}
        body["options"] = (cur.get("options") or []) + add
        body["field_name"] = f["name"]
        body["name"] = f["name"]
        lark("base", "+field-update", "--base-token", bt, "--table-id", tid,
             "--field-id", cur.get("id") or cur.get("field_id"),
             "--json", json.dumps(body, ensure_ascii=False), "--as", "user", "--yes")
        for _try in range(6):
            chk = lark("base", "+field-list", "--base-token", bt,
                       "--table-id", tid, "--as", "user")
            got = {c["name"]: c for c in ((chk.get("data") or {}).get("fields")
                                          or (chk.get("data") or {}).get("items") or [])}
            if not ({o["name"] for o in add}
                    - {o["name"] for o in (got.get(f["name"], {}).get("options") or [])}):
                break
            time.sleep(1 + _try)
        else:
            raise LarkTableError(
                f"{name}：字段 {f['name']} 补选项 "
                f"{[o['name'] for o in add]} 轮询 6 次仍未生效")
        print(f"    ＋ {name}：{f['name']} 补选项 {[o['name'] for o in add]}")

    # ---- 已有字段的**说明**同步 ----
    # 说明只在建字段时给过一次，之后再没同步过。后果之一刚发生：
    # 「研判·共创状态」改名成「提示·状态」，说明还写着「【研判与建议区】」——
    # 字段说明是飞书上唯一能逐列讲清楚「这一列是干嘛的、改了会不会生效」
    # 的地方，说错比不写更容易误导。
    for f in want:
        cur = have.get(f["name"])
        if not cur or not f.get("description"):
            continue
        if (cur.get("description") or "") == f["description"]:
            continue
        body = {k: v for k, v in cur.items()
                if k not in ("id", "field_id", "name", "field_name", "description")}
        body["description"] = f["description"]
        body["field_name"] = f["name"]
        body["name"] = f["name"]
        lark("base", "+field-update", "--base-token", bt, "--table-id", tid,
             "--field-id", cur.get("id") or cur.get("field_id"),
             "--json", json.dumps(body, ensure_ascii=False), "--as", "user", "--yes")
        print(f"    ✎ {name}：{f['name']} 更新字段说明")

    made = []
    for f in want:
        if f["name"] in have or f.get("type") == "link":
            continue
        lark("base", "+field-create", "--base-token", bt, "--table-id", tid,
             "--json", json.dumps({k: v for k, v in f.items()
                                   if k != "description"}, ensure_ascii=False),
             "--as", "user")
        made.append(f["name"])
        print(f"    ＋ {name}：新建字段 {f['name']}")
    # **建完也要轮询。** 与改名同一类：飞书最终一致，建完 4 个字段紧接着
    # 读回来可能只看到 3 个，于是下游 `_assert_fields_match` 报「缺 X」
    # 并停掉整次推送 —— 实测发生在 C4 知识工程 的「参数·复用依据」上。
    # 这里等它们都出现，而不是把下游那道比对放宽（放宽会把真正的规格漂移
    # 一起放过去，那才是这道检查存在的理由）。
    if made:
        for _try in range(6):
            chk = lark("base", "+field-list", "--base-token", bt,
                       "--table-id", tid, "--as", "user")
            now = {(f.get("name") or f.get("field_name"))
                   for f in ((chk.get("data") or {}).get("fields")
                             or (chk.get("data") or {}).get("items") or [])}
            if not (set(made) - now):
                break
            time.sleep(1 + _try)
        else:
            raise LarkTableError(
                f"{name}：新建字段 {sorted(set(made) - now)} 轮询 6 次仍未出现")


def _assert_fields_match(bt: str, name: str, tid: str,
                         want: list[dict]) -> None:
    """已有表的字段必须与当前规格一致，否则报错并给出修法。

    `ensure_table` 原来只保证「表在」，不管字段对不对 —— 字段规格一变，
    已有表就成了哑弹：每次都「成功命中」，直到 replace_all 的 preflight
    才发现列对不上。本项目连挂三次都是这个形状。

    **不自动重建。** 重建会毁掉飞书侧的共创编辑 —— 那正是「先 pull 再重推」
    这条纪律存在的理由。所以这里只报错，把顺序说清楚。
    """
    r = lark("base", "+field-list", "--base-token", bt, "--table-id", tid,
             "--as", "user")
    have = {f.get("name") or f.get("field_name")
            for f in ((r.get("data") or {}).get("items")
                      or (r.get("data") or {}).get("fields") or [])}
    need = {f["name"] for f in want}
    missing, extra = sorted(need - have), sorted(have - need)
    if not missing and not extra:
        return
    raise LarkTableError(
        f"{name} 的字段与当前规格不一致 —— 缺 {missing}；多 {extra}。\n"
        f"  已有表不会自动改字段，也**不自动重建**（重建会毁掉飞书侧的共创编辑）。\n"
        f"  正确顺序：① 先 pull 保住已有改动 ② 删该表 ③ 重跑推送\n"
        f"    lark-cli base +table-delete --base-token {bt} "
        f"--table-id {tid} --yes --as user")


def _report_variants(bt: str, name: str,
                     variants: list[tuple[str, str]]) -> None:
    """报告重名变体，**不删**。行数只供参考 —— 刚写入的记录可能还没传播，
    读到 0 不等于空（实测：带数据的变体被读成 0 行）。"""
    for vid, vname in variants:
        n = _row_count(bt, vid)
        print(f"    ⚠ 发现重名表 {vname}（读到 {n}{'+' if n else ''} 行）—— "
              f"多为上一轮推送重复建表的产物。确认无用后手工删：\n"
              f"      lark-cli base +table-delete --base-token {bt} "
              f"--table-id {vid} --yes --as user")


TEXT = lambda n: {"type": "text", "name": n}          # noqa: E731
NUM = lambda n: {"type": "number", "name": n}         # noqa: E731


def set_field_order(bt: str, tables: dict[str, str],
                    effort_tables: set[str] | None = None) -> None:
    """把每张条目表的默认视图字段顺序设成 FIELD_SPEC 的顺序。

    **建表时给的字段顺序不作数** —— 飞书的字段顺序是按视图存的，
    `+table-create` 的 fields 数组只决定有哪些字段，不决定怎么排。
    实测建完之后 C1 的顺序是「研判·处理建议, 功能点, 参数·软件类别, 产品线…」，
    随机得像没排过。
    """
    effort_tables = effort_tables or set()
    for name, tid in tables.items():
        spec = _FIELD_SPEC_EFFORT if name in effort_tables else FIELD_SPEC
        # **可见 = 标识 + 参数 + 提示。** 研判与追溯整组隐藏 ——
        # 它们是我方产出的判断与血缘，不是方案侧要维护的字段，摆在网格里
        # 让人要在自查结论里找输入格。隐藏是**视图级**的：字段仍在、仍随
        # push 写入、仍能被 pull 读回（record-list 不受视图可见性影响），
        # 点开行详情的「隐藏字段」折叠区也仍看得到 —— 这是「处理建议」
        # 那种逐行文字唯一的去处，多维表格没有单元格批注。
        order = [n for n, sec, _, _ in spec
                 if sec in (_SEC_ID, _SEC_PARAM, _SEC_HINT)]
        if not any(name.startswith(p) for p in ("A", "B", "C")) or "-" in name:
            continue
        r = lark("base", "+view-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user")
        for v in ((r.get("data") or {}).get("items")
                  or (r.get("data") or {}).get("views") or []):
            vid = v.get("view_id") or v.get("id")
            if not vid:
                continue
            # **分两步：先隐藏（保持现有顺序），再重排。**
            # 直接提交目标顺序在「既要隐藏又要重排」时被 API 拒掉
            # （code 800070003「no operation produced」，hint 明说
            # 「submit only fields that actually need to change」）。
            # 一步式只在当前顺序恰好吻合时成功 —— 实测 A/B 组过、C 组全挂，
            # 而失败是 rc=1 且 `lark()` 不抛错，于是「设置成功」只是没报错。
            def _vis():
                d = lark("base", "+view-get-visible-fields", "--base-token", bt,
                         "--table-id", tid, "--view-id", vid, "--as", "user")
                return (d.get("data") or {}).get("visible_fields") or []

            cur = _vis()
            keep = ([x for x in cur if x in set(order)]
                    + [x for x in order if x not in cur])
            for payload in ([keep] if keep != cur else []) + \
                           ([order] if order != keep else []):
                lark("base", "+view-set-visible-fields", "--base-token", bt,
                     "--table-id", tid, "--view-id", vid,
                     "--json", json.dumps({"visible_fields": payload},
                                          ensure_ascii=False), "--as", "user")
            got = _vis()
            if len(got) != len(order):
                print(f"    ⚠ {name}/{vid}：可见字段 {len(got)} ≠ 期望 "
                      f"{len(order)} —— 视图未收敛到目标，请手工核对")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--pack", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    # **逐表推送。** 整库全量推有个致命前提：本地必须是各表的最新版。
    # 实测踩到过：飞书侧把 20 张表都重拆了（B1 68→590 行、C3 11→499 行…），
    # 而本地只跟上了 A1~A4；此时整库推会用本地旧内容覆盖掉约 5,800 行
    # 别人的工作，且 `replace_all` 是删了重建，覆盖后无从恢复。
    # 有了这个开关，「哪张表本地是新的就只推哪张」变成可执行的纪律。
    ap.add_argument("--only", action="append", default=[],
                    help="只推这些表；可给表名或前缀（如 A1 / 'A1 市平台'），可重复")
    a = ap.parse_args()

    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    bt = cfg["base_token"]
    bom = Bom.load(a.bom)
    pack = StandardPack.load(a.pack)
    tax0 = yaml.safe_load((a.bom / "taxonomy.yaml").read_text(encoding="utf-8"))
    effort_systems = {
        s for ls in (tax0.get("product_lines") or {}).values()
        for s, sp in (ls.get("systems") or {}).items()
        if ((sp if isinstance(sp, dict) else {}).get("costing_method")
            or ls.get("costing_method")) == "工作量估算法"}

    migrate_tables(bt, cfg)
    tables: dict[str, str] = dict(cfg.get("tables") or {})
    counts: dict[str, int] = dict(cfg.get("record_counts") or {})

    def _want(name: str) -> bool:
        return (not a.only) or any(
            name == o or name.startswith(o) for o in a.only)

    if a.only:
        print(f"**逐表模式**：只推 {a.only} —— 其余表一行不动")

    # ---- 0 参数表 / 0 词表：只读参照，git 单向推出 ----
    if _want("0 参数表"):
        tid = ensure_table(bt, "0 参数表", [
            TEXT("参数组"), TEXT("参数名"), TEXT("取值"), TEXT("单位"),
            NUM("页码"), TEXT("章节"), TEXT("标准原文"), TEXT("说明")])
        rows = param_rows(pack)
        counts["0 参数表"] = replace_all(bt, tid, rows, "0 参数表")
        tables["0 参数表"] = tid

    vrows = vocab_rows(a.bom) if _want("0 词表") else []
    if vrows:
        tid = ensure_table(bt, "0 词表", [
            TEXT("字段"), TEXT("字段名"), TEXT("合法取值"), TEXT("含义"),
            TEXT("是否必填"), TEXT("备注")])
        counts["0 词表"] = replace_all(bt, tid, vrows, "0 词表")
        tables["0 词表"] = tid

    # ---- 逐子系统 ----
    by_sys: dict[str, list] = defaultdict(list)
    for i in bom.active():
        by_sys[i.path.system].append(i)
    # taxonomy 里声明了但一条都没有的系统，**照样建表**并留空 ——
    # 「声明了待补内容」和「根本没这个系统」在飞书上必须能分辨。
    tax = yaml.safe_load((a.bom / "taxonomy.yaml").read_text(encoding="utf-8"))
    for ls in (tax.get("product_lines") or {}).values():
        for sysname in (ls.get("systems") or {}):
            by_sys.setdefault(sysname, [])

    # ---- **写之前先把所有目标表检查一遍** ----
    # 原来是边查边写：`ensure_table` 在循环里逐表做字段比对，第 N 张表不合规时
    # 前 N-1 张已经写完了。实测代价：B4 推完之后才在 B5 上发现两张表都多了
    # 4 个错位列「字段 1~4」，而 B4 的正规列已被覆盖成另一套值，同一行里
    # 出现两套 ID、两个产品线。
    # 批量写入的守卫必须是**全通过才动手**，否则它只是把「全错」变成「错一半」，
    # 而错一半比全错更难收拾。
    _pre = []
    for _s, _it in by_sys.items():
        _n = SHEET_ORDER.get(_s)
        if not _n or not _want(_n) or _n not in tables:
            continue
        try:
            _assert_fields_match(bt, _n, tables[_n],
                                 _fields_of(_spec_for(_s, effort_systems)))
        except LarkTableError as e:
            _pre.append(str(e).split("\n")[0])
    if _pre:
        raise LarkTableError(
            "**预检不通过，未写任何表**：\n  " + "\n  ".join(_pre)
            + "\n  批量推送要么全做要么不做 —— 写一半留下的混合状态"
              "（一行里两套 ID、两个产品线）比全不写更难恢复。")

    unknown = sorted(set(by_sys) - set(SHEET_ORDER))
    if unknown:
        raise ValueError(
            f"未登记的子系统 {unknown} —— 请在 SHEET_ORDER 显式登记表名。"
            f"不自动编号：自动编号会在新增子系统时把已有表重排，"
            f"而表名一改，别人收藏的链接和正在编辑的视图都对不上")

    # **字段顺序与可见性必须放在建表循环之后。** 放在前面时，本轮要新建的
    # 字段还不存在，`visible_fields` 里含不存在的名字 → 视图退回默认「全显」，
    # 随后字段才建出来，于是也跟着可见。实测 C5 本体工程 可见 22 列而不是 15。
    for system, items in sorted(by_sys.items(), key=lambda x: SHEET_ORDER[x[0]]):
        name = SHEET_ORDER[system]
        if not _want(name):
            continue
        spec_list = _spec_for(system, effort_systems)
        tid = ensure_table(bt, name, _fields_of(spec_list))
        if items:
            counts[name] = replace_all(
                bt, tid, [_row(i, spec_list) for i in items], name)
        else:
            # 空系统：写一行占位说明，而不是留一张白表 ——
            # 白表看起来像「推送漏了」，占位行能说清它是「待补内容」
            # 占位行的列名**从当前规格取**，不写死 —— 写死的「功能点」在
            # 工作量法那套里叫「建设内容」，C5 本体工程就是这么挂的。
            cols = [n for n, _, _, _ in spec_list]
            counts[name] = replace_all(bt, tid, [{
                cols[0]: "（本系统当前无条目）",
                cols[5]: "待补内容 —— 源表无对应科目",
                cols[6]: "见 bom/taxonomy.yaml 的 pending_note："
                         "需产品侧确认内容来源，补齐前不进入任何金额汇总",
                STATUS_FIELD: "★待补描述"}], name)
        tables[name] = tid
        print(f"  {name:<16}{counts[name]:>5} 条{'　（空系统占位）' if not items else ''}")

    # 旧的单表清空但保留（历史链接指向它，直接删会让别人的收藏 404）
    old = tables.get("功能点清单")
    if old:
        try:
            replace_all(bt, old, [{"条目ID": "（本表已停用）", "名称":
                                   "已按子系统拆表，请打开侧边栏 1～9 各表"}],
                        "功能点清单（停用）")
        except LarkTableError:
            pass

    set_field_order(bt, {k: v for k, v in tables.items() if _want(k)},
                    {SHEET_ORDER[s] for s in effort_systems
                     if s in SHEET_ORDER})

    # 表改名后残留的旧键：`migrate_tables` 弹掉了，但中途失败的那几轮又把它
    # 写了回来，于是同一个 tid 挂在两个表名下 —— pull 会把这张表读两遍，
    # 产生一批看不出来源的重复差异。
    _live = {v for k, v in tables.items() if k not in TABLE_RENAMES}
    for _old in list(TABLE_RENAMES):
        if _old in tables and tables[_old] in _live:
            print(f"  ✕ 清理改名残留键 {_old}（与 {TABLE_RENAMES[_old]} 同指一张表）")
            tables.pop(_old)

    cfg["tables"] = tables
    cfg["record_counts"] = counts
    cfg["pushed_bom_version"] = bom.version
    cfg["split_by_subsystem"] = True
    a.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    if a.only:
        print(f"\npush 完成（逐表模式 {a.only}）：BOM v{bom.version}。"
              f"未指定的表**一行未动**。")
    else:
        print(f"\npush 完成：BOM v{bom.version}，"
              f"{len(by_sys)} 个子系统表 + 参数表 {counts.get('0 参数表',0)} 条"
              f" + 词表 {counts.get('0 词表',0)} 条")


if __name__ == "__main__":
    main()
