"""bom_annotate —— 给 BOM 条目打「共创状态」与「拆分建议」，供飞书共创用。

## 为什么不是标颜色加批注

颜色和批注**不聚合**。方案人员问「还剩多少条待拆」「基座那边补完没有」，
颜色答不了；批注也拉不回来做 diff。而且两者都活不过一次结构性编辑 ——
排序、插行、复制表就丢。

飞书多维表格里能聚合的是**字段**：一个单选字段 `共创状态` + 几个预筛视图，
每个人打开自己那个视图就是工作队列，改完状态自动出队，`pull` 回来能逐条做 diff。
这也是本工具在康养项目上已经建立的模式（占位待确认 / C-ILF实体核对 / F-类型裁决
各带一个 ★ 视图）。颜色是免费附赠的 —— 单选字段的选项本来就带色。

## 五种状态

    已判型          规则引擎判出了类型，进入双人复核队列即可
    ★待补描述       描述是能力罗列（「用户管理、员工管理、顾客管理」），
                    数不出基本过程。**这是产品侧的活**，不是造价侧的
    ★建议再拆       描述里还剩多个动作子句没切开，切了会涨功能点
    ★待定维护方     该逻辑文件跨多个子系统，谁记 ILF 未定
    技术说明待复核   讲的是实现方式不是用户可识别活动，需确认是否计数

`拆分建议` 字段给的是**可执行的具体动作**，不是「请检查」——
「描述含 4 个『支持…』子句但只切出 1 个基本过程，建议拆为 4 条」
比「建议拆分」有用得多。

用法：
    python3 bom_annotate.py --bom <dir> --scope <split.yaml> [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

import bom_decompose as bd

#: 能力罗列的特征：并列的名词短语，动作动词后面跟的是「X管理、Y管理、Z管理」
#: 而不是一个具体动作。这类描述数不出基本过程 —— 不是判型判不出，是没得判。
_ENUMERATION = re.compile(r"[、，]\s*[^、，。；]{2,10}(管理|体系|机制|能力|框架|规范|引擎|模型)")

STATUS_TYPED = "已判型"
STATUS_NEED_DESC = "★待补描述"
STATUS_SPLIT_MORE = "★建议再拆"
STATUS_TECH = "技术说明待复核"
STATUS_EFFORT = "★待补工作量依据"
#: 工作量依据已按「可核查量纲 × 单位工作量」填出草稿，待业务侧逐条确认。
#: 与 ★待补工作量依据 的区别：那个是**空的**（人月来自源表倒算，无依据），
#: 这个是**填了但未确认**（量纲与数量可核查，待确认的是单位工作量那一列）。
#: 不带 ★ —— ★ 在本词表里表示「拦路、必须处理」，这一档不拦路。
STATUS_EFFORT_DRAFT = "工作量依据待确认"

#: 各类建设的复杂度维度 —— **替代「规模/条数」**。
#: 数据集建设干的是搭管道（采集接口、清洗规则、字段映射、入库联调），
#: 管道搭好之后过 100 条还是 3000 条差别不大，所以人天与条数弱相关。
#: 实测：四个数据集规模差 30 倍（100～3000 条），人天只差 17%（71～83），
#: 同一活动的隐含单产跨 33 倍。引导人去填条数，是把他往一条一除就露馅的路上带。
_COMPLEXITY_DIMS = {
    "数据工程": "源系统数、字段数、标注类目数、质检轮次、对外接口数",
    "知识工程": "资料来源数、文档格式种类、切分策略数、审校轮次、需专家复核比例",
    "行业垂直模型": "数据源数、算法方案难度（现成模型微调/自研）、评测指标数、"
                    "评测轮次、需集成的业务系统数",
    "行业智能体": "接入知识库数、工具/接口数、提示词迭代轮次、业务规则条数、"
                  "需对接的前后端触点数",
    "_default": "源系统数、接口数、迭代轮次、需人工复核的环节数",
}



def annotate(bom_dir: Path, scope: dict) -> dict[str, Any]:
    effort_systems = {x["name"] for x in scope["effort_method"]["systems"]}
    decl_path = bom_dir / "logical-files.yaml"
    decl = (yaml.safe_load(decl_path.read_text(encoding="utf-8"))
            if decl_path.exists() else {"groups": []})
    # 跨 ≥2 个子系统的数据组 = 维护方需要裁定的那些
    ambiguous = set()   # 保留形参位置；维护方裁定已移到独立裁决表

    files: dict[Path, Any] = {}
    stats: Counter = Counter()
    for f in sorted(bom_dir.glob("items/*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        for it in data["items"]:
            st, advice = _judge(it, effort_systems, ambiguous)
            it["coauthor_status"] = st
            if advice:
                it["coauthor_advice"] = advice
            elif "coauthor_advice" in it:
                del it["coauthor_advice"]
            stats[st] += 1
        files[f] = data
    return {"files": files, "stats": dict(stats)}


def _ambiguous_owners(bom_dir: Path, decl: dict,
                      effort_systems: set[str]) -> set[str]:
    """维护方**有歧义**的数据组：不止一个子系统对它有维护动作，或声明里带 ⚠️。

    跨子系统 ≠ 有歧义。「园所档案」跨三个子系统，但只有市平台在维护它，
    那不需要开会裁定 —— 证据已经指向唯一答案。真正要裁的是两方都在改的那些。
    """
    import bom_logical_files as blf
    out = {g["name"] for g in decl.get("groups", [])
           if "⚠" in (g.get("basis") or "")}
    scope = {"effort_method": {"systems": [{"name": s} for s in effort_systems]}}
    an = blf.analyze(bom_dir, scope, decl)
    for r in an["results"]:
        maintainers = {s for s, rs in r["refs"].items()
                       if any(x["maintains"] for x in rs)}
        if len(maintainers) > 1:
            out.add(r["group"]["name"])
    for p in an["problems"]:
        out.add(p["group"])
    return out


def _judge(it: dict, effort_systems: set[str],
           multi_owner: set[str]) -> tuple[str, str]:
    desc = it.get("description") or ""

    if it["id"].startswith("LF."):
        # 逻辑文件的维护方裁定**不在主表做**，粒度不对：该裁的单位是「数据组」
        # （31 个），不是「条目」（94 条 —— 同一个数据组在每个引用它的子系统
        # 各占一行）。在主表标 93 条待办，那不是工作队列，是把整张表标成待办。
        # 走独立的裁决表，按康养 `C-ILF实体核对` 的先例。
        return STATUS_TYPED, ""

    if it["path"]["system"] in effort_systems or it.get("class") == "SOFTWARE_EFFORT":
        # ⚠️ 以前这里返回「已判型」—— 那是错的，而且错得危险：
        # 这些条目从来没判过型（工作量法不数功能点），只是不在功能点法范围内。
        # 标成「已判型」让 164 条看起来做完了，而 Z04 的头号提示恰恰是
        # 「人月的测算依据」没有。标签把缺口盖住了。
        eff = it.get("effort") or {}
        if eff.get("basis") and eff.get("metric"):
            return STATUS_TYPED, ""
        mm = (it.get("legacy_quote") or {}).get("man_months")
        pd = (eff.get("person_days")
              or (it.get("legacy_quote") or {}).get("person_days"))
        dim = _COMPLEXITY_DIMS.get(it["path"]["system"],
                                   _COMPLEXITY_DIMS["_default"])
        return (STATUS_EFFORT,
                f"本条按工作量估算法计价，{pd} 人天（{mm} 人月），"
                f"已有活动分解但**没有测算依据**。标准只规定单价（表1 注3），"
                f"不规定人月怎么估 —— 财评一定会问。"
                f"⚠️ **不要按规模/条数写依据**：实测四个数据集规模差 30 倍"
                f"（100～3000 条）而人天只差 17%（71～83），同一活动的隐含单产"
                f"跨 33 倍 —— 这批人天本来就不是按条数估的，按规模写一除就露馅，"
                f"而规模数字就写在建设详情里，财评自己能除。"
                f"请按**建设复杂度**填「工作量·可核查量纲」：{dim}；"
                f"并在「工作量·测算依据」写明判断来源"
                f"（对标项目 / 历史实测 / 专家判断，任选其一但要说得出来）")

    if "nesma" not in it:
        if _ENUMERATION.search(desc) or len(desc) < 24:
            n = len(re.findall(r"[、，]", desc)) + 1
            return (STATUS_NEED_DESC,
                    f"描述是能力罗列（约 {n} 项并列），数不出基本过程。"
                    f"需按「谁 + 对什么 + 做什么动作」逐项补写，例如把"
                    f"「用户管理、员工管理、顾客管理」写成"
                    f"「支持新增/编辑/停用用户，支持按部门与状态查询用户列表」。"
                    f"补完后重跑 bom_decompose 即可自动判型")
        if "技术说明-待复核" in (it.get("tags") or []):
            return (STATUS_TECH,
                    "本条讲的是实现方式（框架/协议/算法），不是用户可识别的活动。"
                    "请确认：它对外是否暴露可操作的功能？是则补写该功能的描述，"
                    "否则标为不计数并说明")
        return (STATUS_NEED_DESC,
                "规则引擎从描述里判不出功能点类型。请补写用户视角的动作"
                "（新增/查询/统计/导出/对接…）与操作对象")

    # 逗号连接的动作子句 —— 切分器切不动（它按句号/分号切），但**也不该自动切**：
    # 「支持生成PPT大纲，支持选择教学主题，支持选择教学领域」里，
    # 选主题、选领域是**同一个基本过程的输入项**，不是独立基本过程。
    # NESMA 的基本过程是「自包含的、使应用处于一致状态的最小活动单元」，
    # 点一个下拉不是。自动切会多计，不切可能少计 —— 所以交给人判，并把
    # 两种风险都写在建议里。
    acts = re.findall(r"支持[^，。；]{2,28}|提供[^，。；]{2,28}|可[查看编导][^，。；]{2,26}", desc)
    if len(acts) >= 3 or len(desc) > 110:
        return (STATUS_SPLIT_MORE,
                f"本条描述含 {len(acts)} 个动作子句、{len(desc)} 字，当前只计为"
                f"1 个基本过程。请判断这些子句是"
                f"**{len(acts)} 个独立的基本过程**（应拆，每拆一条约 +4～5 功能点），"
                f"还是**同一个过程的输入项/选项**（不拆，拆了就是多计）。"
                f"判据：能否单独完成并使系统处于一致状态 —— 「生成PPT大纲」能，"
                f"「选择教学领域」不能。子句："
                + "｜".join(a[:24] for a in acts[:5]))
    return STATUS_TYPED, ""


def logical_file_decisions(bom_dir: Path, scope: dict) -> list[dict[str, Any]]:
    """逻辑文件维护方裁决表 —— 一个数据组一行。

    **31 组全部入表，不做「有歧义才入表」的筛选。** 试过按「多个子系统都有
    维护动作」筛，30/31 都命中 —— 因为「管理」这个词几乎命中所有描述。
    说明文本判不出谁维护数据组，这正是维护方必须人工声明的原因；
    既然判不出，就不该假装筛出了一个更小的集合。
    """
    import bom_logical_files as blf
    decl = yaml.safe_load((bom_dir / "logical-files.yaml").read_text(encoding="utf-8"))
    an = blf.analyze(bom_dir, scope, decl)
    rows = []
    for r in an["results"]:
        g = r["group"]
        others = [s for s in sorted(r["refs"]) if s != g["maintainer"]]
        basis = (g.get("basis") or "").strip()
        rows.append({
            "数据组": g["name"],
            "拟维护方(记ILF)": g["maintainer"],
            "引用方(记ELF)": "、".join(others) or "（无）",
            "引用次数": r["total"],
            "跨子系统数": len(r["refs"]),
            "本组功能点": 10 + 7 * len(others),
            "判定依据": basis,
            "需要确认": ("⚠ 声明里已标待确认" if "⚠" in basis else
                         "维护方改判会让 ILF 与 ELF 对调，本组功能点变动 3 分/次"),
            "裁决结论": "未裁决",
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--scope", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    scope = yaml.safe_load(a.scope.read_text(encoding="utf-8"))
    rep = annotate(a.bom, scope)
    total = sum(rep["stats"].values())
    print(f"共创标注 {'（已写入）' if a.apply else '（dry-run）'}　共 {total} 条")
    for k, v in sorted(rep["stats"].items(), key=lambda x: -x[1]):
        print(f"  {k:<16}{v:>5}　{v/total:>5.1%}")

    if a.apply:
        for path, data in rep["files"].items():
            path.write_text(yaml.safe_dump(data, allow_unicode=True,
                                           sort_keys=False, width=200),
                            encoding="utf-8")
        (a.bom / "annotate-report.json").write_text(
            json.dumps(rep["stats"], ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"  → {a.bom}")


if __name__ == "__main__":
    main()
