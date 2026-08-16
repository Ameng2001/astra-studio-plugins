"""bom_decompose —— 把「功能特性」粒度的 BOM 拆到「基本过程」粒度。

## 为什么要拆

NESMA 数的是**基本过程**（elementary process）—— 用户可识别的、自包含的、
使被计数应用处于一致状态的最小活动单元。源清单一行往往是一个**功能特性**，
里面含好几个基本过程：

    「园所分布」一行的描述里有：以地图展示园所位置 / 悬浮查看片区统计 /
      查看区域园所概况 / 查看师生概况 / 逐层下钻 —— 五个查询，不是一个。

一行数一个功能点，等于把五个基本过程当成一个。柳州幼教项目上这个偏差
让 L0 基座 136 条只数出 464 FP（3.4 FP/条），支撑不起 673 万的建设内容。

## 拆分不是切句子

三类文本长得像基本过程但不是，必须先剔掉，否则拆出来的东西判不了型
（原型阶段 19% 未判型，全是这三类）：

  定义句      「成长足迹是对幼儿在园一日动态的总结，含教师评价、在园健康…」
              —— 讲这是什么，不讲做什么。无动作动词。
  标题行      「成长足迹设置」后跟「用于设置家长端成长足迹的相关规则…」
              —— 一个基本过程被排版成两行，切开就成了两个。
  技术说明    「基于 BPMN 标准构建可视化编排界面」
              —— 讲怎么实现，不是用户可识别的活动。保留但标记待复核。

## 判型仍然由规则引擎独立做

本模块只负责**切**，不负责判。切完每一段交 `nesma_classify.classify()`，
rationale 记命中的规则与原文片段。切不出动作的段落留 `type: null` 进人工
裁决清单 —— 留空会被 bom_validate 拦住，比塞一个 EI 蒙混过关强。

## 逻辑文件不在这里补

事务功能（EI/EO/EQ）能从描述切出来，**逻辑文件（ILF/EIF）不能** ——
「园所」这个数据组是不是本子系统维护的，要看跨子系统的全局关系，
不是读一段描述能定的。那一步走 `bom_logical_files.py`，在本模块之后。

用法：
    python3 bom_decompose.py --bom <bom_dir> --scope <split.yaml> \\
        [--version 0.2.0] [--apply]
    不带 --apply 为 dry-run，只出报告。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

import nesma_classify as nc

#: 动作信号 —— 有其一才可能是基本过程。
#: 「用于」单列：它是标题行下方的说明句起头，合并时要认出来。
ACTION = ("支持", "可以", "可查看", "可对", "提供", "实现", "自动", "允许",
          "需具备", "需要", "用于", "能够", "展示", "查看", "统计", "生成",
          "新增", "编辑", "删除", "导出", "导入", "配置", "设置", "管理",
          "录入", "审核", "推送", "同步", "对接", "上传", "下载", "打印")

#: 定义句：「X 是 …」「X 指 …」且不含动作信号 —— 讲这是什么，不讲做什么
_DEFINITION = re.compile(r"^.{0,24}?(是指|是一[种个]|是对|是|指)[^，。]{4,}")

#: 技术实现说明 —— 保留但标记，判不出型时进人工裁决而非丢弃
_TECHNICAL = ("基于", "采用", "通过", "集成", "内置", "标准化", "架构")

#: 标题行：短名词短语，无标点、无动作词
_TITLE_MAX = 14


def split_processes(desc: str) -> list[dict[str, Any]]:
    """把一段描述切成候选基本过程。返回 [{text, kind}]。

    kind: process（基本过程）/ definition（定义句，剔除）/ technical（技术说明，保留待复核）
    """
    if not desc or not desc.strip():
        return []

    raw = re.split(r"\n+|(?<=[。；;])\s*(?=\d+\s*[、.）)])", re.sub(r"\r", "", desc))
    chunks: list[str] = []
    for p in raw:
        p = p.strip()
        if not p:
            continue
        # 行内按动作词起头再切一层
        for s in re.split(r"(?<=[。；;])\s*(?=支持|可以|提供|实现|自动|允许|需)", p):
            s = re.sub(r"^\d+\s*[、.）)]\s*", "", s).strip(" 　；;。、")
            if s:
                chunks.append(s)

    # 标题行 + 说明行合并：短名词短语后面紧跟以「用于/支持/可」起头的句子
    merged: list[str] = []
    i = 0
    while i < len(chunks):
        cur = chunks[i]
        is_title = (len(cur) <= _TITLE_MAX
                    and not re.search(r"[，。；、]", cur)
                    and not any(a in cur for a in ACTION[:12]))
        if is_title and i + 1 < len(chunks):
            merged.append(f"{cur}：{chunks[i + 1]}")
            i += 2
            continue
        merged.append(cur)
        i += 1

    out: list[dict[str, Any]] = []
    for t in merged:
        if len(t) < 6:
            continue
        has_action = any(a in t for a in ACTION)
        if not has_action and _DEFINITION.match(t):
            out.append({"text": t, "kind": "definition"})
            continue
        if not has_action and any(t.startswith(k) for k in _TECHNICAL):
            out.append({"text": t, "kind": "technical"})
            continue
        if not has_action:
            out.append({"text": t, "kind": "technical"})
            continue
        out.append({"text": t, "kind": "process"})
    return out


def decompose(bom_dir: Path, scope: dict, version: str,
              pack=None) -> dict[str, Any]:
    effort_systems = {x["name"] for x in scope["effort_method"]["systems"]}
    # 不计数规则**按区域授权**启用。柳州全文无不计数条款，
    # 套用山东的 X-SEC 会把「统一认证/登录日志」判成不计数而少算。
    excl = nc.exclusions_from_pack(pack) if pack is not None else None

    files = sorted(bom_dir.glob("items/*.yaml"))
    new_files: dict[Path, list[dict]] = {}
    stats = Counter()
    undecided: list[dict] = []
    dropped: list[dict] = []

    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        out_items: list[dict] = []
        for it in data["items"]:
            if it["path"]["system"] in effort_systems:
                # 走工作量法的部分不拆 —— 拆了也不用于计价，只会制造复核负担
                out_items.append(it)
                stats["保留(工作量法)"] += 1
                continue

            parts = split_processes(it.get("description") or "")
            procs = [p for p in parts if p["kind"] != "definition"]
            dropped += [{"id": it["id"], "text": p["text"][:60]}
                        for p in parts if p["kind"] == "definition"]

            if not procs:
                # 切不出任何东西 —— 保留原条目，进人工裁决
                out_items.append(it)
                stats["未能拆分"] += 1
                undecided.append({"id": it["id"], "name": it["name"],
                                  "reason": "描述中切不出基本过程"})
                continue

            for n, p in enumerate(procs, start=1):
                v = nc.classify(p["text"], excl)
                child = dict(it)
                child["id"] = f"{it['id']}.{n:02d}"
                child["name"] = _gist(p["text"])
                child["description"] = p["text"]
                child["path"] = {**it["path"], "l3": it["name"],
                                 "l4": _gist(p["text"])}
                child["since"] = version
                child["source"] = f"{it.get('source', '')}#proc{n}"
                # 特性级的人天不能逐个基本过程复制一份 —— 那会让交叉校验虚增
                lq = dict(it.get("legacy_quote") or {})
                if lq:
                    lq = {**lq, "note": lq.get("note", "") + "；特性级取值，"
                                        "已拆分为多个基本过程，勿逐条累加",
                          "split_into": len(procs)}
                    child["legacy_quote"] = lq
                if v.type:
                    hits = "、".join(v.hits[:4]) if v.hits else ""
                    child["nesma"] = {
                        "type": v.type,
                        "rationale": (
                            f"判为 {v.type}：{v.rule.summary}（{v.rule.citation}）"
                            + (f"；命中原文「{hits}」" if hits else "")
                            + f"。※ 由 bom_decompose 从特性「{it['name']}」"
                              f"切出的第 {n}/{len(procs)} 个基本过程，"
                              f"规则引擎独立判型 —— 进入 reviewed 前须双人复核（G-02c）"),
                        "counted_by": f"bom_decompose+nesma_classify:{v.rule.id}@{version}",
                    }
                    stats[v.type] += 1
                else:
                    child.pop("nesma", None)
                    stats["未判型"] += 1
                    undecided.append({
                        "id": child["id"], "name": child["name"],
                        "text": p["text"][:80],
                        "reason": ("命中不计数规则" if v.excluded
                                   else f"规则引擎无命中（{p['kind']}）")})
                if p["kind"] == "technical":
                    child.setdefault("tags", [])
                    child["tags"] = list(child["tags"]) + ["技术说明-待复核"]
                out_items.append(child)
            stats["拆出"] += len(procs)
            stats["被拆特性"] += 1

        data["items"] = out_items
        data["bom_version"] = version
        new_files[f] = data

    return {"files": new_files, "stats": dict(stats),
            "undecided": undecided, "dropped_definitions": dropped}


def _gist(text: str) -> str:
    """取一句话的要点做名字。**不做语义压缩** —— 截断即可，全文在 description 里。"""
    t = re.split(r"[，。；、：]", text.strip())[0]
    return (t[:24] + "…") if len(t) > 24 else t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--scope", required=True, type=Path)
    ap.add_argument("--pack", type=Path,
                    help="标准包 —— 决定启用哪些不计数规则；不给则全开（旧行为）")
    ap.add_argument("--version", default="0.2.0")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    scope = yaml.safe_load(a.scope.read_text(encoding="utf-8"))
    pack = None
    if a.pack:
        from standard_pack import StandardPack
        pack = StandardPack.load(a.pack)
        print(f"标准包 {pack.pack_id}　启用的不计数规则："
              f"{sorted(nc.exclusions_from_pack(pack)) or '无（本标准全文无不计数条款）'}")
    rep = decompose(a.bom, scope, a.version, pack)
    st = rep["stats"]

    W = {"ILF": 10, "ELF": 7, "EI": 4, "EO": 5, "EQ": 4}
    ufp = sum(W.get(k, 0) * v for k, v in st.items())

    print(f"拆分 {'（已写入）' if a.apply else '（dry-run，未写入）'}")
    print(f"  被拆特性 {st.get('被拆特性', 0)} 条 → 基本过程 {st.get('拆出', 0)} 个"
          f"（均 {st.get('拆出',0)/max(st.get('被拆特性',1),1):.1f}/条）")
    print(f"  保留（工作量法）{st.get('保留(工作量法)', 0)} 条，未能拆分 {st.get('未能拆分', 0)} 条")
    print(f"  判型：" + "　".join(f"{k} {st.get(k,0)}" for k in
                                  ("ILF", "ELF", "EI", "EO", "EQ", "未判型")))
    print(f"  事务功能 UFP {ufp:,}（逻辑文件另由 bom_logical_files 补）")
    print(f"  剔除定义句 {len(rep['dropped_definitions'])} 条，待人工裁决 {len(rep['undecided'])} 条")

    if a.apply:
        for path, data in rep["files"].items():
            path.write_text(yaml.safe_dump(data, allow_unicode=True,
                                           sort_keys=False, width=200),
                            encoding="utf-8")
        (a.bom / "VERSION").write_text(a.version + "\n", encoding="utf-8")
        (a.bom / "decompose-report.json").write_text(
            json.dumps({"stats": st, "ufp_transactions": ufp,
                        "undecided": rep["undecided"],
                        "dropped_definitions": rep["dropped_definitions"]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {a.bom}")


if __name__ == "__main__":
    main()
