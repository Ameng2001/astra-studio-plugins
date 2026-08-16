"""lark_device_push —— 把设备 BOM（第一层）推到飞书共创。

## 与软件 BOM 同一套纪律

    git 是发布层，飞书是评审层。
    push 覆盖整表；pull 只出 diff，不直接改 BOM。
    共创状态与处理建议用**单选字段**，不用颜色和批注 ——
    单选能聚合（回答「还剩多少条待补口径」）、能 pull 回来做 diff，颜色和批注都不能。

## 表的划分

软件 BOM 按子系统拆了 19 张表，因为 926 条放一张里没法分工。设备这边
205 个位点、109 个物料，放得下，所以按**对象**分而不按场景分：

    0 场景树       22 行   场景怎么归类、哪些是手工补充待确认的
    1 物料主数据   109 行  设备是什么、多少钱、价格依据齐不齐
    2 配置目录     205 行  哪个场景配哪台设备、按什么口径配　← 共创主战场
    9 变更提案     空      结构性变更（加场景、加设备）走这里，不直接改主表

## 工作队列用视图

按共创状态建过滤视图，人点进去就是自己那一摞：

    2 配置目录/★待补配置口径       148 条 —— 最大的一块
    2 配置目录/★场景待归类          21 条
    1 物料主数据/★待核价            32 个
    1 物料主数据/★参考机型不足3个    76 个

**状态有优先级，队列不重叠。** 未归类的场景先归类再谈口径，所以那 21 条
不进「待补口径」队列（169 个无口径位点 = 148 + 21）；待核价的物料先定价再谈
比选机型，所以那 32 个不进「参考机型不足」队列（108 个不足 = 76 + 32）。
重叠的话同一条活会在两个队列里各出现一次，两边的人都以为对方在做。

用法：
    python3 lark_device_push.py --root . --folder <folder_token> [--base-token <bt>]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import yaml

import lark_table as lt
from lark_bom_split_push import ensure_table
from lark_table import lark

BASE_NAME = "可售设备BOM"


def _transient(r: dict) -> bool:
    """这次失败是不是「等一下再试就好」。

    限流（OpenAPISetVisibleFields limited）和网络传输失败都属于瞬时 ——
    只认限流的话，一次 TLS 抖动就会把整轮推送打断在中途，
    留下一半视图建好一半没建，而那种半成品状态最难看出来。
    """
    if r.get("ok"):
        return False
    raw = json.dumps(r, ensure_ascii=False)
    return any(k in raw for k in ("limited", '"transport"', "API call failed",
                                  "timeout", "EOF", "connection reset"))


def _retry(*args: str, tries: int = 6) -> dict:
    """带退避的调用。返回最后一次结果，由调用方判定成败。"""
    r: dict = {}
    for i in range(tries):
        r = lark(*args)
        if not _transient(r):
            return r
        time.sleep(2 + i * 3)
    return r

#: 状态配色。**★ 系列用暖色，已完成用绿灰** —— 这是网格里唯一的视觉提示通道：
#: 多维表格没有单元格批注、也没有逐格背景色，能染色的只有单选字段的选项。
#: 逐行的处理建议放在隐藏字段里，点开行详情的「隐藏字段」折叠区可见。
#: 原来 `_sel` 只传 `{"name": o}`，所有选项一律灰白 —— 等于「按颜色分优先级」
#: 这件事根本没发生。
_HUE = {
    "★待补配置口径": ("Red", "Light"), "★场景待归类": ("Orange", "Light"),
    "★待核价": ("Red", "Light"), "★参考机型不足3个": ("Orange", "Light"),
    "★字段冲突": ("Carmine", "Light"), "★待申请料号": ("Violet", "Light"),
    "★未登记": ("Red", "Light"),
    "口径待复核": ("Yellow", "Lighter"), "手工补充(待确认)": ("Yellow", "Lighter"),
    "待确认": ("Yellow", "Lighter"),
    "已有口径": ("Green", "Lighter"), "已定价": ("Green", "Lighter"),
    "已确认": ("Green", "Lighter"), "Z01建设内容": ("Blue", "Lighter"),
    "可售": ("Green", "Lighter"), "停售": ("Gray", "Lighter"),
    "内部专用": ("Purple", "Lighter"),
    # 运营服务费的口径状态
    "★待补触发口径": ("Red", "Light"),
    "已定口径": ("Green", "Lighter"),
    "已定口径·单位串待订正": ("Yellow", "Lighter"),
    # 变更提案的处理状态（工作流态，不是提示态，故仍留在研判区）
    "待评审": ("Yellow", "Lighter"), "采纳": ("Green", "Lighter"),
    "不采纳": ("Gray", "Lighter"),
}

#: 共创状态。**能枚举的就做成单选** —— 手打的状态聚合不出来，也 pull 不回来做 diff。
ST_CATALOG = ["已有口径", "★待补配置口径", "★场景待归类", "口径待复核", "已确认"]
ST_MATERIAL = ["已定价", "★待核价", "★参考机型不足3个", "★字段冲突",
               "★待申请料号", "已确认"]
#: 是否可售。**全部预填「可售」** —— 目录里躺着一台已停产的设备，
#: 配上去之前没人会发现；做成单选就能按它筛、按它聚合。
ST_SELLABLE = ["可售", "停售", "内部专用", "待确认"]
ST_SCENE = ["Z01建设内容", "手工补充(待确认)", "★未登记", "已确认"]

KIND_LABEL = {"per_garden": "每园 N", "per_class": "每班 N", "per_room": "每教室 N",
              "per_child": "每人 N", "per_facility": "每点位 N",
              "composite": "复合（多段）", "internal_only": "内部口径（不外发）",
              "unparsed": ""}


#: 字段类型用**字符串判别符**（本 CLI 的口径），不是原生 API 的整数编号。
#: 写整数会报 `Invalid discriminator value` —— 而那是在建表时才发现的。
TEXT, NUM = "text", "number"


#: 关联字段建表时给不了 link_table（那时物料表可能还没建），所以先从 SPEC 里
#: 摘出来，等两张表都在了再补建。
LINK_FIELDS = {"2 配置目录": [("场景", "0 场景树"),
                             ("物料", "1 物料主数据")],
               "3 运营服务费": [("依赖·触发设备", "1 物料主数据")]}


def _fold(e: dict) -> tuple[set, set, set]:
    """把一条目录条目的多个 placement 折成 (kinds, qtys, ats)。"""
    rules = [p["rule"] for p in e["placements"]
             if p["rule"]["kind"] not in ("unparsed",)]
    kinds = {KIND_LABEL.get(r["kind"], "") for r in rules}
    kinds.discard("")
    qtys = {x.get("qty") for r in rules
            for x in (r["parts"] if r["kind"] == "composite" else [r])
            if x.get("qty")}
    ats = {x["at"] for r in rules
           for x in (r["parts"] if r["kind"] == "composite" else [r])
           if x.get("at")}
    return kinds, qtys, ats


def derived_rule(e: dict) -> dict:
    """push 写进飞书「口径」三列的值。**一处定义，push 与 pull 共用。**

    pull 要拿它当比对基线 —— 飞书那三列的初值就是它。基线算错的代价实测过：
    我在 pull 里照着重写了一份、三个分支跟这里不一致（多 kind 的
    「复合（多段）」漏了、qty/at 没限制在「唯一时才写」），135 行里 55 行
    被判成「飞书改过」而人一个字没动。假阳性淹掉真改动，比不报还糟。

    所以不复制，只导出。
    """
    kinds, qtys, ats = _fold(e)
    kind = (sorted(kinds)[0] if len(kinds) == 1 else
            ("复合（多段）" if len(kinds) > 1 else ""))
    return {
        **({"口径·配置方式": kind} if kind in KIND_OPTS else {}),
        **({"口径·每单位数量": float(sorted(qtys)[0])} if len(qtys) == 1 else {}),
        **({"口径·点位": sorted(ats)[0]}
           if len(ats) == 1 and sorted(ats)[0] in FACILITY_OPTS else {}),
    }


def _f(name: str, sec: str, ftype: str, desc: str = "", **kw) -> dict:
    """一个字段。`sec` 是分区，会同时进字段名前缀和字段说明。

    分区前缀不是好看 —— 方案人员打开一张 14 列的平表，没有任何东西告诉他
    哪几列该他动。前缀一加，「口径·」三列就是他的活，别的都不是。
    """
    d = {"name": (f"{sec}·{name}" if sec else name), "type": ftype,
         "description": f"【{sec or '键'}】{desc}"}
    d.update(kw)
    return d


def _sel(name: str, sec: str, opts: list[str], desc: str = "",
         colored: bool = True) -> dict:
    """单选字段。`colored=True` 的按 `_HUE` 上色并强制登记。

    **配色守卫只管状态类枚举**，不管输入词表。「配置方式」「点位」是让人选的
    取值，不承担优先级信号；给它们配色只会和状态色抢注意力，
    而强制登记会让加一个配置方式变成还要想一个颜色。
    """
    if not colored:
        return _f(name, sec, "select", desc, multiple=False,
                  options=[{"name": o} for o in opts])
    miss = [o for o in opts if o not in _HUE]
    if miss:
        raise ValueError(
            f"状态取值 {miss} 没在 _HUE 登记配色 —— 补一行即可。"
            f"**不给默认色**：默认色会让新状态混进已有配色里，"
            f"共创的人靠颜色分优先级，混了就分不出来。")
    return _f(name, sec, "select", desc, multiple=False,
              options=[{"name": o, "hue": _HUE[o][0], "lightness": _HUE[o][1]}
                       for o in opts])


TEXT, NUM = "text", "number"

#: 分区。顺序即列序。
S_KEY, S_SCENE, S_DEV, S_RULE, S_DIAG, S_JUDGE = (
    "", "场景", "设备", "口径", "诊断", "研判")
S_ID, S_PARAM, S_BIZ, S_SRC = "标识", "参数", "商务", "来源"
#: 提示区 —— 网格里**唯一**保留的非维护字段，承担「背景颜色提示」。
#: 与软件 BOM 同一条规则：只暴露产品信息与因子/口径维护字段，
#: 研判（我方判断）、质检、血缘（来源与字典对照）整组隐藏。
S_HINT = "提示"

#: 配置口径的候选。**做成下拉，不让手打** —— 手打的口径下游认不出来，
#: 而认不出来的口径会被当成「没填」，人却以为填过了。
KIND_OPTS = ["每园 N", "每班 N", "每教室 N", "每人 N", "每点位 N",
             "复合（多段）", "直接给总量", "本场景不配", "内部口径（不外发）"]
FACILITY_OPTS = ["门口", "厨房", "园医室", "体育老师", "多功能厅", "其他"]

#: 触发口径。「★待补触发口径」不是占位 —— 有两条费用（语音转写 / 通用大模型调用）
#: 的触发关系**源表就没有**：只有 05 园、数量 70 在同场景内有 585 种组合能凑出、
#: 备注只有「待补单价」。编一个凑得上的触发设备，就是又一个说不出来源的数。
TRIGGER_OPTS = ["每台触发设备1份", "每园1份", "★待补触发口径"]
ST_FEE = ["已定口径", "已定口径·单位串待订正", "★待补触发口径"]

SPEC: dict[str, list[dict]] = {
    "0 场景树": [
        _f("规范子场景", S_KEY, TEXT, "场景的正式名称"),
        _f("一级场景", S_SCENE, TEXT, "五维分类，系统归类"),
        _f("场景码", S_SCENE, TEXT, "稳定标识，勿改"),
        _f("涵盖的原始叫法", S_SRC, TEXT, "历史别名，供对照"),
        _f("来源", S_SRC, TEXT, "该场景名从哪来"),
        _f("目录内设备数", S_SRC, NUM, "本场景下有多少台设备"),
        _sel("状态", S_HINT, ST_SCENE,
             "处理到哪一步。逐行的处理建议在隐藏字段里，点开行详情可见"),
        _f("处理建议", S_JUDGE, TEXT, "系统给的建议，可覆盖"),
    ],
    "1 物料主数据": [
        # 设备名称放第一列 = 飞书主字段，配置目录的关联格显示的就是它。
        _f("设备名称", S_KEY, TEXT, "主字段。改名在这里改，配置目录会跟着变"),
        _f("物料编码", S_ID, TEXT, "连接键，勿改"),
        _f("品牌", S_PARAM, TEXT, ""),
        _f("型号", S_PARAM, TEXT, ""),
        _f("性能参数", S_PARAM, TEXT, "表7 例表要求的「性能参数」列"),
        _f("单位", S_PARAM, TEXT, ""),
        # **不设「单价」列。** 曾经有过，是这次事故的来源：早期构建器取的是
        # 字典的 ★canonical单价（成本价），推上来却标成「对外市场单价」；
        # 构建器后来改取「建议市场单价」并加了 77/77 一致性守卫，飞书那一列
        # 却停在旧值 —— 实测与成本价逐条相等 62/94，与市场价相等 0/76，
        # 全表比在用报价低约五成。一列谁也判不出口径的价，比没有价危险。
        # 价格是**商机级**的（deals/<id>/device-config.yaml），不属产品级目录 ——
        # 与「配置数量不放这里」同一条理由，见 lark-sync.json 的 notes。
        # 设备多少钱只留一条路径：Z03 建议市场单价 → bom_build_devices → materials.yaml。
        _f("是否自研", S_BIZ, TEXT, ""),
        _sel("是否可售", S_BIZ, ST_SELLABLE, "**请填**：停售/内部专用的在这里标出来"),
        _f("参考机型数", S_BIZ, NUM, "本机型 + 参考厂家数；表7 注3 要求 ≥3"),
        _f("参考对厂家型号", S_BIZ, TEXT, "**请填**：补到 3 个以上"),
        _f("出现于场景数", S_SRC, NUM, "被几个场景用到"),
        _f("在物料字典中", S_SRC, TEXT, ""),
        _f("字典冲突标记", S_SRC, TEXT, ""),
        _f("字典处置方式", S_SRC, TEXT, ""),
        _sel("状态", S_HINT, ST_MATERIAL,
             "处理到哪一步。★ 系列是拦路项：待核价 / 参考机型不足3个（表7 注3 要求 ≥3）"
             " / 待申请料号。逐行的处理建议在隐藏字段里，点开行详情可见"),
        _f("处理建议", S_JUDGE, TEXT, "系统给的建议，可覆盖"),
    ],
    # 配置目录 = 场景与物料的**关联表**，一行 = 一个场景里的一台设备（135 行）。
    #
    # 原先按「场景 × 物料 × 位点」出 205 行，是照搬源表的行。但那 49 个多位点里
    # 大多不是真位点 —— 是同一设备同一场景在不同园所行写了不同备注的产物
    # （「（空）」+「每个幼儿园配1套」）。方案人员脑子里是「这个场景有哪几台设备」，
    # 一台设备两三行，他不知道该改哪一行。
    #
    # 场景与物料都做成**关联字段**：改名在源表改，这里自动跟着变，
    # 不再是各存一份文本副本。条目ID 去掉 —— 键是（场景, 物料），
    # 机器键对填表的人没有意义，只占第一列。
    "2 配置目录": [
        _f("设备名称", S_KEY, TEXT, "主字段。引自「1 物料主数据」，改名请去那张表"),
        _f("场景", S_KEY, "link", "关联「0 场景树」。改场景名在那张表改"),
        _f("物料", S_KEY, "link", "关联「1 物料主数据」。点开看参数/是否可售/参考机型"),
        _sel("配置方式", S_RULE, KIND_OPTS,
             "**请填**：这台设备在这个场景按什么配。　⚠ 本列**当前不进造价**：造价读的是每园实配数量，不读本列。它是把 205 条逐园硬编码数量收敛成规则的入口，填了不改钱，但不填就一直收不拢。", colored=False),
        _f("每单位数量", S_RULE, NUM,
           "**请填**：配置方式对应的数量。如「每班 N」填 N。　⚠ 本列**当前不进造价**：造价读的是每园实配数量，不读本列。它是把 205 条逐园硬编码数量收敛成规则的入口，填了不改钱，但不填就一直收不拢。"),
        _sel("点位", S_RULE, FACILITY_OPTS,
             "**请填**：仅当配置方式为「每点位 N」时填。　⚠ 本列**当前不进造价**：造价读的是每园实配数量，不读本列。它是把 205 条逐园硬编码数量收敛成规则的入口，填了不改钱，但不填就一直收不拢。", colored=False),
        _f("源表原文", S_RULE, TEXT,
           "源表备注（同场景同设备的多条已合并），判断依据，勿改"),
        _sel("状态", S_HINT, ST_CATALOG,
             "处理到哪一步。本列是网格里唯一的提示通道，逐行的处理建议在隐藏字段里，"
             "点开行详情的「隐藏字段」折叠区可见"),
        _f("处理建议", S_JUDGE, TEXT, "系统给的建议，可覆盖"),
    ],
    #: 运营服务费。**不是设备**，按年/按周期收，柳州标准 三.(三) 运维为独立
    #: 预算科目、不列入建设期预算。单独一张表而不是在物料表加个标记，是因为
    #: 它的核心信息是**触发关系**（挂在哪台设备上），物料表没有这个维度。
    #: 本表**不设单价列** —— 与物料表同一条理由，见那边的注释。
    "3 运营服务费": [
        _f("服务名称", S_KEY, TEXT, "主字段。按年/按周期收取的经常性费用，不是设备"),
        _f("依赖·触发设备", "", "link",
           "**这笔费用挂在哪台设备上**。丙附里选了该设备才产生费用，数量随它派生"),
        _f("物料编码", S_ID, TEXT, "连接键，勿改"),
        _sel("触发口径", S_RULE, TRIGGER_OPTS,
             "**核心**：这笔费用什么时候发生。丙附按此派生数量 —— "
             "没配触发设备就不出现，不需要人另填一遍", colored=False),
        _f("随动系数", S_RULE, NUM, "数量 = 触发设备数量 × 本系数（每园口径固定 1）"),
        _f("触发场景", S_RULE, TEXT, "「每园1份」口径用：该园开了这个场景就计 1"),
        _sel("计费周期", S_BIZ, ["按年", "按3年", "一次性"], "", colored=False),
        _f("计费单位", S_BIZ, TEXT, "源表单位原文；与实际数量不符的已在处理建议里标出"),
        _sel("状态", S_HINT, ST_FEE, "处理到哪一步"),
        _f("处理建议", S_JUDGE, TEXT, "触发关系的判定依据；口径未定的写明为什么推不出来"),
    ],
    "9 变更提案": [
        _f("提案类型", S_KEY, TEXT, "加场景 / 加设备 / 改归类 …"),
        _f("对象", S_KEY, TEXT, ""),
        _f("现状", S_KEY, TEXT, ""),
        _f("建议", S_KEY, TEXT, ""),
        _f("理由", S_KEY, TEXT, ""),
        _f("提出人", S_KEY, TEXT, ""),
        _sel("状态", S_JUDGE, ["待评审", "采纳", "不采纳"], ""),
    ],
}

#: 工作队列视图：按共创状态过滤，人点进去就是自己那一摞。
#: 队列视图。值可以是**多个状态** —— 性质相同的活合并成一个队列，别开一堆 tab。
#: 「★场景待归类」不单开：它与「场景·未归类（待补场景归属）」筛的是同一批 21 条。
VIEWS = {
    "2 配置目录": [("★口径待处理", "提示·状态",
                    ["★待补配置口径", "口径待复核", "★场景待归类"])],
    "1 物料主数据": [("★待核价", "提示·状态", "★待核价"),
                     ("★参考机型不足3个", "提示·状态", "★参考机型不足3个"),
                     ("★待申请料号", "提示·状态", "★待申请料号")],
    "0 场景树": [("★待确认归类", "提示·状态", "手工补充(待确认)")],
    "3 运营服务费": [("★待补触发口径", "提示·状态", "★待补触发口径")],
}

#: 不再按一级场景各开一个视图。那 6 个视图是**没有分组时的替代品** ——
#: 现在 Grid View 直接按「场景」分组，一屏就是一个场景带它的设备清单，
#: 再开 6 个 tab 只是让人多找一遍。配置目录因此只剩 2 个 tab。

#: 场景视图/工作队列里给方案人员看的列。**诊断列和键列不显示** ——
#: 14 列平铺时人不知道从哪下手；只留 9 列，「口径·」三列就是他的活。
#: 可见列 —— **与软件 BOM 同一条规则**：只暴露产品信息与因子/口径维护字段；
#: 研判（我方判断）、质检、血缘（来源与字典对照）整组隐藏。
#:
#: 隐藏是**视图级**的：字段仍在、仍随 push 写入、仍能被 pull 读回
#: （record-list 不受视图可见性影响），点开行详情的「隐藏字段」折叠区也仍看得到 ——
#: 这是「研判·处理建议」那种逐行文字唯一的去处，多维表格没有单元格批注。
#:
#: 两处判断值得写明：
#:   商务·参考机型数 **留**。它是导出值（本机型 + 参考厂家数），按「质检」该藏，
#:     但它是表7 注3「≥3 个」的达标指示器、就在「参考对比厂家」旁边 ——
#:     藏了填的人不知道自己填够没有。
#:   口径·源表原文 **藏**。它是血缘（源表备注原文），与软件侧的「研判·判定理由」
#:     同性质，那边藏了这边就藏，口径一致优先于单表的填写便利。
VIS = {
    "0 场景树": ["规范子场景", "场景·一级场景", "场景·场景码", "提示·状态"],
    "1 物料主数据": ["标准设备名称", "标识·物料编码", "参数·品牌", "参数·型号",
                     # 两个价格列**并排**：定价的人要同时看报价与成本才能定，
                     # 隔开放等于让他左右横跳。成本列的边界靠列名上的「成本」
                     # 二字和字段说明，不靠位置 —— 位置挡不住看得见的人。
                     "参数·性能参数", "参数·单位",
                     "商务·市场单价(元)", "商务·成本单价(元)",
                     "商务·是否自研",
                     "商务·是否可售", "商务·参考机型数", "商务·参考对厂家型号",
                     "提示·状态"],
    "2 配置目录": ["设备名称", "场景", "物料", "口径·配置方式", "口径·每单位数量",
                   "口径·点位", "提示·状态"],
    "3 运营服务费": ["服务名称", "标识·物料编码", "依赖·触发设备", "口径·触发口径",
                     "口径·随动系数", "口径·触发场景", "商务·计费周期",
                     "商务·计费单位", "提示·状态"],
    "9 变更提案": ["提案类型", "对象", "现状", "建议", "理由", "提出人", "研判·状态"],
}


def build_rows(root: Path) -> dict[str, list[dict]]:
    d = root / "bom" / "devices"
    tax = yaml.safe_load((d / "taxonomy.yaml").read_text(encoding="utf-8"))["scenes"]
    mats = yaml.safe_load((d / "materials.yaml").read_text(encoding="utf-8"))["materials"]
    cat = yaml.safe_load((d / "catalog.yaml").read_text(encoding="utf-8"))["entries"]

    # 合法的一级场景取自场景树。源表里「✓」「基准补录」是业务侧的补录标记，
    # 不是场景名 —— 不归一的话它们会各自变成一个「一级场景」，
    # 视图里就多出两条不存在的产品线，而那看起来完全像真的。
    valid_l1 = {s["l1"] for s in tax if s["l1"] and not s["l1"].startswith("**")}
    UNCLS = "未归类（待补场景归属）"

    n_dev: dict[str, set] = {}
    n_scene: dict[str, set] = {}
    for e in cat:
        n_dev.setdefault(e["scene"], set()).add(e["material"])
        n_scene.setdefault(e["material"], set()).add(e["scene"])

    rows_scene = []
    for s in tax:
        st = ("★未登记" if s["origin"].startswith("**未登记**")
              else ("手工补充(待确认)" if "待确认" in s["origin"] else "Z01建设内容"))
        rows_scene.append({
            "规范子场景": s["scene"], "场景·一级场景": s["l1"],
            "场景·场景码": s["code"],
            "来源·涵盖的原始叫法": "、".join(s.get("aliases") or []),
            "来源·来源": s["origin"],
            "来源·目录内设备数": len(n_dev.get(s["scene"], ())),
            "提示·状态": st,
            "研判·处理建议": ("本场景在 99 表未声明但各园表在用，须补声明并定一级场景"
                              if st == "★未登记" else
                              "该场景名为手工补充，须业务侧确认归类与命名"
                              if st == "手工补充(待确认)" else ""),
        })

    # 未归类是个**伪场景**，但它得在场景树里露面 —— 那 18 条要归类的活
    # 是从这里进的；不建这行，配置目录的场景关联连不上，而且没人看得到这摞活。
    if any(e["scene"].startswith("未归类") for e in cat):
        uncls = next(e["scene"] for e in cat if e["scene"].startswith("未归类"))
        rows_scene.append({
            "规范子场景": uncls, "场景·一级场景": UNCLS, "场景·场景码": "S99",
            "来源·涵盖的原始叫法": "✓、基准补录",
            "来源·来源": "**未登记**：源表场景名为补录标记，非场景名",
            "来源·目录内设备数": len({e["material"] for e in cat
                                      if e["scene"] == uncls}),
            "提示·状态": "★未登记",
            "研判·处理建议": "这不是一个场景，是源表的补录标记。"
                              "请把其下设备逐台归到真实场景，本行随后删除",
        })

    rows_mat = []
    for m in mats:
        nref = (1 if m["model"] and not m["model"].startswith("**") else 0) \
            + len(m.get("ref_vendors") or [])
        if m["code"].startswith("NOCODE"):
            st, adv = ("★待申请料号",
                       "源表无物料编码 —— 须先申请料号，否则同名不同物的两条会被并成一条")
        elif m["price_status"].startswith("**"):
            st, adv = "★待核价", "数量非零但无单价 —— 须补价格依据（询价单/公开成交价）"
        elif nref < 3:
            st, adv = ("★参考机型不足3个",
                       f"表7 注3「参考品牌型号一般不少于3个」，现 {nref} 个 —— "
                       f"须补同档次比选机型及价格依据")
        elif "conflict" in m:
            st, adv = "★字段冲突", f"同一编码出现多个取值：{m['conflict']}"
        else:
            st, adv = "已定价", ""
        rows_mat.append({
            "设备名称": m["name"], "标识·物料编码": m["code"],
            "参数·品牌": m.get("brand", ""), "参数·型号": m["model"],
            "参数·性能参数": (m.get("spec") or "")[:1000], "参数·单位": m["unit"],
            "商务·是否自研": m.get("self_made", ""),
            "商务·是否可售": m.get("sellable", "待确认"),
            "商务·参考机型数": nref,
            "商务·参考对厂家型号": "、".join(m.get("ref_vendors") or []),
            "来源·出现于场景数": len(n_scene.get(m["code"], ())),
            "来源·在物料字典中": "是" if m.get("in_dict") else "否",
            "来源·字典冲突标记": m.get("dict_flag", ""),
            "来源·字典处置方式": m.get("dict_disposal", ""),
            "提示·状态": st, "研判·处理建议": adv,
        })

    # ---- 配置目录：一个 (场景, 物料) 一行，位点折进「源表原文」与口径 ----
    #
    # 折叠的判据：多位点里绝大多数不是真位点，是同一设备同一场景在不同园所行
    # 写了不同备注。真位点（PAD 的 教室/厨房/园医）折起来后由「配置方式=复合」
    # + 源表原文里的多句话表达 —— 方案人员看得懂，而三行同名设备他不知道改哪行。
    rows_cat = []
    for e in cat:
        notes = [p["note"] for p in e["placements"] if p["note"]]
        rules = [p["rule"] for p in e["placements"]
                 if p["rule"]["kind"] not in ("unparsed",)]
        kinds, qtys, ats = _fold(e)
        covered = all(r.get("covered") for r in rules) if rules else False

        unscened = e["scene"].startswith("未归类")
        if unscened:
            st, adv = "★场景待归类", "源表场景名为「✓」或「基准补录」，须补场景归属"
        elif not rules:
            st, adv = ("★待补配置口径",
                       "缺口径：" + ("备注为空" if not notes
                                     else "备注是过程痕迹或未命中已登记句式"))
        elif len(kinds) > 1 or not covered:
            st, adv = ("口径待复核",
                       "口径未解析干净或含多段（" + "、".join(sorted(kinds)) + "）—— "
                       "按现口径推数量会偏少，请据「源表原文」核定")
        else:
            st, adv = "已有口径", ""

        rows_cat.append({
            "设备名称": e["name"],
            "_scene": e["scene"], "_material": e["material"],   # 关联用，写前替换
            **derived_rule(e),
            # 同场景同设备的多条备注合并，去重保序 —— 丢掉哪一条都可能丢掉一个位点
            "口径·源表原文": "；".join(dict.fromkeys(notes))[:1000],
            "提示·状态": st, "研判·处理建议": adv,
        })

    rows_fee = []
    fees = root / "bom" / "devices" / "service-fees.yaml"
    if fees.exists():
        KIND = {"per_device": "每台触发设备1份", "per_garden": "每园1份",
                "unknown": "★待补触发口径"}
        for f in (yaml.safe_load(fees.read_text(encoding="utf-8"))
                  or {}).get("fees") or []:
            tr = f.get("trigger") or {}
            r = {"服务名称": f["name"], "标识·物料编码": f["code"],
                 "口径·触发口径": KIND.get(tr.get("kind"), "★待补触发口径"),
                 "商务·计费周期": f.get("period", "按年"),
                 "商务·计费单位": f.get("unit", ""),
                 "提示·状态": f.get("status", "★待补触发口径"),
                 "研判·处理建议": (f.get("basis") or "")[:1000]}
            if tr.get("kind") == "per_device":
                r["口径·随动系数"] = float(tr.get("factor", 1))
                r["_trigger"] = tr["material"]        # 关联用，写前替换
            elif tr.get("kind") == "per_garden":
                r["口径·随动系数"] = 1.0
                r["口径·触发场景"] = tr.get("scene", "")
            rows_fee.append(r)

    return {"0 场景树": rows_scene, "1 物料主数据": rows_mat,
            "2 配置目录": rows_cat, "3 运营服务费": rows_fee,
            "9 变更提案": []}


def ensure_view(bt: str, tid: str, name: str, field: str,
                val: str | list[str], want: int) -> str:
    """建过滤视图（幂等）并**核对条数**。

    条数不核的话，筛选条件或状态取值有偏差时视图会静默筛出一个错的集合，
    而共创的人会照着那个错队列干活 —— 干完还以为清完了。
    """
    r = lark("base", "+view-list", "--base-token", bt, "--table-id", tid, "--as", "user")
    have = {v.get("view_name") or v.get("name"): v.get("view_id") or v.get("id")
            for v in ((r.get("data") or {}).get("items")
                      or (r.get("data") or {}).get("views") or [])}
    vid = have.get(name)
    if not vid:
        r = _retry("base", "+view-create", "--base-token", bt, "--table-id", tid,
                 "--json", json.dumps({"name": name, "type": "grid"},
                                      ensure_ascii=False), "--as", "user")
        v = (r.get("data") or {})
        v = v.get("view") or (v.get("views") or [{}])[0]
        vid = v.get("view_id") or v.get("id")
    if not vid:
        raise SystemExit(f"{name}：视图建不出来 —— 建不了视图等于那些活没人看得到")
    # 运算符按**字段类型**选：单选用 intersects（值是数组），文本用 is（值是标量）。
    # 对文本字段用 intersects 时飞书不报错，只是**筛选不生效** ——
    # 6 个场景视图曾因此每个都显示全表 205 条，而视图名写着「场景·综合评价」。
    is_select = field.endswith("状态") or field.endswith("是否可售")
    vals = val if isinstance(val, list) else [val]
    cond = [field, "intersects", vals] if is_select else [field, "is", vals[0]]
    r = _retry("base", "+view-set-filter", "--base-token", bt, "--table-id", tid,
               "--view-id", vid, "--json",
               json.dumps({"logic": "and", "conditions": [cond]},
                          ensure_ascii=False), "--as", "user")
    if not r.get("ok"):
        raise SystemExit(f"{name}：设筛选失败 {str(r.get('error'))[:200]}")
    # 计数必须真数一遍。原先用 `--page-size`（这个 CLI 用的是 `--limit`）读
    # `data.total`，永远是 None，而守卫写的是「不为 None 才比」——
    # 于是一路静默通过，6 个筛坏的视图全都「核对通过」。
    got, off = 0, 0
    while True:
        rr = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                  "--view-id", vid, "--as", "user", "--limit", "200",
                  "--offset", str(off))
        d = rr.get("data") or {}
        k = len(d.get("record_id_list") or [])
        got += k; off += k
        if not d.get("has_more") or not k:
            break
    if got != want:
        raise SystemExit(
            f"{name}：视图筛出 {got} 条，本地 {want} 条 —— "
            f"筛选条件或状态取值有偏差。共创的人会照着一个错的队列干活，"
            f"干完还以为清完了。")
    return vid


def ensure_base(folder: str, name: str) -> str:
    r = lark("drive", "files", "list", "--folder-token", folder)
    for f in ((r.get("data") or {}).get("files") or []):
        if f.get("name") == name and f.get("type") == "bitable":
            print(f"  已有 Base：{name}　{f['token']}")
            return f["token"]
    r = lark("base", "+base-create", "--name", name, "--folder-token", folder)
    # 返回体两种形态都认：`data.base.base_token`（本 CLI）与
    # `data.app.app_token`（原生 API）。只认一种的话，Base **已经建出来了**
    # 而这里报「建失败」，重跑就会建第二张同名 Base —— 飞书不拦重名。
    d = r.get("data") or {}
    tok = ((d.get("base") or {}).get("base_token")
           or (d.get("app") or {}).get("app_token"))
    if not tok:
        raise SystemExit(f"建 Base 失败：{r}")
    print(f"  新建 Base：{name}　{tok}")
    return tok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--folder", help="放 Base 的 Drive 文件夹 token")
    ap.add_argument("--base-token", help="已有 Base；给了就不建新的")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    rows = build_rows(a.root)
    print("设备 BOM 待推：")
    for t, rs in rows.items():
        print(f"  {t:14s} {len(rs):4d} 行")
    if a.dry_run:
        print("\n--dry-run，未连飞书。")
        return
    if not (a.base_token or a.folder):
        raise SystemExit("要么给 --base-token，要么给 --folder（在其中建 Base）")

    bt = a.base_token or ensure_base(a.folder, BASE_NAME)

    # 三段顺序不能乱：
    #   ① 建全部表 —— 关联字段的目标表得先存在
    #   ② 补建关联字段 —— 建表时给不了 link_table
    #   ③ 按依赖顺序写记录 —— 物料表先写，拿到 record_id 才连得上目录的关联
    # 上一版把①②③揉成一个循环，结果关联字段在记录写完之后才建，
    # 205 行的关联全是空的 —— 而表面上「写入 205 行」一切正常。
    made: dict[str, str] = {}
    for t, spec in SPEC.items():
        made[t] = ensure_table(bt, t, spec)
    for t, links in LINK_FIELDS.items():
        for fname, target in links:
            if fname not in lt.fields(bt, made[t]):
                lt.lark("base", "+field-create", "--base-token", bt,
                        "--table-id", made[t], "--json",
                        json.dumps({"name": fname, "type": "link",
                                    "link_table": made[target]},
                                   ensure_ascii=False), "--as", "user")
                print(f"  建关联字段 {t}.{fname} → {target}")
    #: 成本价 2026-08-13 起**进 VIS 并与市场价相邻**（用户要求便于对照）。
    #: 早先把它藏进行详情，是因为出过「一列成本标成『对外市场单价』」的事故；
    #: 现在两列都在、列名各带「市场」「成本」二字、字段说明也写明了口径，
    #: 藏与不藏的差别不再是「会不会拿错」，而是「定价的人要不要左右横跳」。
    #: **真正的防线在下游**：甲/甲附/乙 读 materials.yaml，其中不含成本价；
    #: 成本价另存 bom/devices/cost-reference.yaml，只有出丙附的模块读它。
    #: 这些列**只在飞书维护**，本地没有对应数据源。replace_all 是「写新删旧」，
    #: 新记录里没写的列就是空的 —— 不接住就等于每次推送清一次。
    #: 「商务·成本单价(元)」是成本口径，本地 materials.yaml 存的是市场价，
    #: 拿市场价去写一列叫「成本单价」的格子比清空还糟。只能原样接住。
    #: 按主键（物料编码 / 规范子场景 / 设备名称）回填。
    PRESERVE = {
        "1 物料主数据": (["商务·成本单价(元)", "商务·市场单价(元)", "特殊说明"],
                         "标识·物料编码"),
    }
    for t, (keepcols, keyfld) in PRESERVE.items():
        if t not in rows or not rows[t]:
            continue
        have = lt.fields(bt, made[t])
        cols = [c for c in keepcols if c in have]
        if not cols:
            continue
        old = {str(f.get(keyfld) or "").strip(): f
               for _rid, f in lt.read_rows_with_id(bt, made[t], cols + [keyfld])}
        hit = 0
        for r in rows[t]:
            o = old.get(str(r.get(keyfld) or "").strip())
            if not o:
                continue
            for c in cols:
                if o.get(c) not in (None, ""):
                    r[c] = o[c]; hit += 1
        print(f"  接住人工列 {t}：{cols} 共 {hit} 个格子")

    for t in SPEC:
        if t == "2 配置目录":
            mat_id = {c: rid for rid, f in
                      lt.read_rows_with_id(bt, made["1 物料主数据"], ["标识·物料编码"])
                      for c in [str(f.get("标识·物料编码") or "").strip()] if c}
            sc_id = {n: rid for rid, f in
                     lt.read_rows_with_id(bt, made["0 场景树"], ["规范子场景"])
                     for n in [str(f.get("规范子场景") or "").strip()] if n}
            miss_m = {r["_material"] for r in rows[t]} - set(mat_id)
            miss_s = {r["_scene"] for r in rows[t]} - set(sc_id)
            if miss_m or miss_s:
                raise SystemExit(
                    f"配置目录引用了别表没有的记录：物料 {sorted(miss_m)[:4]}，"
                    f"场景 {sorted(miss_s)[:4]} —— 关联连不上。\n"
                    f"  三张表出自同一份 bom/devices，对不上说明写入顺序或口径有问题。")
            for r in rows[t]:
                r["物料"] = [mat_id[r.pop("_material")]]
                r["场景"] = [sc_id[r.pop("_scene")]]
        if t == "3 运营服务费" and rows[t]:
            mat_id2 = {c: rid for rid, f in
                       lt.read_rows_with_id(bt, made["1 物料主数据"],
                                            ["标识·物料编码"])
                       for c in [str(f.get("标识·物料编码") or "").strip()] if c}
            miss = {r["_trigger"] for r in rows[t] if "_trigger" in r} - set(mat_id2)
            if miss:
                raise SystemExit(
                    f"运营服务费的触发设备在物料表里不存在：{sorted(miss)} —— "
                    f"关联连不上。触发设备被拆进服务费表了？两者不能同时成立。")
            for r in rows[t]:
                if "_trigger" in r:
                    r["依赖·触发设备"] = [mat_id2[r.pop("_trigger")]]
        if rows[t]:
            n = lt.replace_all(bt, made[t], rows[t], label=t)
            print(f"  {t:14s} 写入 {n} 行")
        else:
            print(f"  {t:14s} 建表（0 行，留给人填）")

    vmap = {}
    for t, vs in VIEWS.items():
        for vname, field, val in vs:
            vals = val if isinstance(val, list) else [val]
            n = sum(1 for r in rows[t] if r.get(field) in vals)
            if not n:
                continue
            vid = ensure_view(bt, made[t], vname, field, val, n)
            vmap[f"{t}/{vname}"] = vid
            print(f"  视图 {t}/{vname:18s} {n:4d} 条　{vid}")

    # 按场景分组。方案人员脑子里是「这个场景有哪几台设备」，
    # 平铺 135 行他得自己找边界；分组之后一屏就是一个场景带它的设备清单。
    for t, tid in made.items():
        gf = {"2 配置目录": "场景", "1 物料主数据": "商务·是否可售"}.get(t)
        if not gf:
            continue
        r = lt.lark("base", "+view-list", "--base-token", bt, "--table-id", tid,
                    "--as", "user")
        for v in ((r.get("data") or {}).get("items")
                  or (r.get("data") or {}).get("views") or []):
            vid = v.get("view_id") or v.get("id")
            nm = v.get("view_name") or v.get("name") or ""
            if not vid or t == "2 配置目录" and nm.startswith("场景·"):
                continue          # 已经按场景筛过的视图不必再分组
            r2 = _retry("base", "+view-set-group", "--base-token", bt,
                        "--table-id", tid, "--view-id", vid, "--json",
                        json.dumps({"group_config": [{"field": gf,
                                                      "desc": False}]},
                                   ensure_ascii=False), "--as", "user")
            raw = json.dumps(r2, ensure_ascii=False)
            if not (r2.get("ok") or "no operation produced" in raw):
                raise SystemExit(f"{t}/{nm} 设分组失败：{raw[:240]}")
        print(f"  已按「{gf}」分组：{t}")

    # 列序与可见列。**建表时给的字段顺序不作数** —— 飞书按视图存列序，
    # 实测建完是随机的（条目ID / 配置口径 / 研判·处理建议 / 设备名称…）。
    # 一张 14 列随机排的表，人不知道从哪下手，这本身就是「杂乱」的一半原因。
    FULL = {t: [f["name"] for f in spec] for t, spec in SPEC.items()}
    for t, tid in made.items():
        done = 0
        r = lt.lark("base", "+view-list", "--base-token", bt, "--table-id", tid,
                    "--as", "user")
        for v in ((r.get("data") or {}).get("items")
                  or (r.get("data") or {}).get("views") or []):
            vid = v.get("view_id") or v.get("id")
            vname = v.get("view_name") or v.get("name") or ""
            if not vid:
                continue
            # **所有视图一律用 VIS**，默认 Grid View 也不例外。
            # 原来默认视图给全字段「我们自己核对用」—— 但那正是共创的人打开
            # 看到的第一张表，14 列平铺、研判与血缘混在里面，等于规则没生效。
            # 我方自己核对看本地 yaml 与套表，不靠飞书的默认视图。
            cols = VIS.get(t, FULL[t])
            # 这个接口限流很紧（800004135 OpenAPISetVisibleFields limited）。
            # 退避重试，别把限流当成失败 —— 也别把它吞了当成功。
            # **分两步：先隐藏（保持现有顺序），再重排。**
            # 直接提交目标顺序在「既要隐藏又要重排」时被 API 拒掉
            # （800070003「no operation produced」，hint 明说「submit only
            # fields that actually need to change」）。一步式只在当前顺序
            # 恰好吻合时成功 —— 软件 BOM 那边实测 A/B 组过、C 组全挂。
            def _vis():
                d = lt.lark("base", "+view-get-visible-fields", "--base-token",
                            bt, "--table-id", tid, "--view-id", vid, "--as", "user")
                return (d.get("data") or {}).get("visible_fields") or []

            cur = _vis()
            keep = ([c for c in cur if c in set(cols)]
                    + [c for c in cols if c not in cur])
            for payload in ([keep] if keep != cur else []) + \
                           ([cols] if cols != keep else []):
                _retry("base", "+view-set-visible-fields", "--base-token", bt,
                       "--table-id", tid, "--view-id", vid, "--json",
                       json.dumps({"visible_fields": payload}, ensure_ascii=False),
                       "--as", "user")
            # **回读复查。** 原注释说「视图的可见列读不回来（view-get 的
            # property 是 null）」—— 那是用错了接口：`+view-get-visible-fields`
            # 读得到。既然读得到，就不该再靠返回值猜。
            got = _vis()
            if len(got) != len(cols):
                raise SystemExit(
                    f"{t}/{vname} 可见列 {len(got)} ≠ 期望 {len(cols)}：{got}\n"
                    f"  设失败会让研判与血缘字段继续摆在共创面上，所以硬失败。")
            done += 1
        print(f"  列序/可见列：{t} {done} 个视图")

    sync = a.root / "bom" / "devices" / "lark-sync.json"
    sync.write_text(json.dumps({
        "base_name": BASE_NAME, "base_token": bt,
        "url": f"https://lcna31l6eggn.feishu.cn/base/{bt}",
        "tables": made, "views": vmap,
        "record_counts": {t: len(rs) for t, rs in rows.items()},
        "notes": [
            "git 是发布层，飞书是评审层。push 覆盖整表；pull 只出 diff，不直接改 BOM。",
            "共创状态用单选字段而非颜色/批注 —— 单选能聚合、能 pull 回来做 diff。",
            "结构性变更（加场景、加设备）走「9 变更提案」，不直接改主表。",
            "配置数量不在这里 —— 那是第二层（deals/<id>/device-config.yaml），"
            "由丙附录入后 quote_sync 同步。本 Base 只管产品级目录。",
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ {sync}")
    print(f"→ https://lcna31l6eggn.feishu.cn/base/{bt}")


if __name__ == "__main__":
    main()
