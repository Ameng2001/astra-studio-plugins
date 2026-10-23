"""bom_ingest_lark_rows —— 把飞书侧**重拆过的**条目表规范化回本地 BOM。

## 与 lark_bom_split_pull 的分工

`lark_bom_split_pull` 回答「飞书上改了哪些字段」，靠 `条目ID` 对齐，只报
描述/类型/状态/工作量参数的**改写**。它有个前提：飞书那张表的行还是本地
那些条目，只是内容被编辑过。

维护人**重拆**一张表时这个前提就没了 —— 一级/二级/三级功能保持不变，但
每个三级功能下面多铺了几行更细的功能点。此时：

  · 新行**没有 `条目ID`**，而 pull 对无 id 的行是 `continue` 跳过的。
    647 行新增在 pull 报告里一个字都不会出现 —— 不报错，只是看不见。
  · 更糟的是 `条目ID` 那一列**留在原来的行位置上**。重排后它压在了
    毫不相干的行上：本地 `FP.PSC.OPS.1001` 叫「用户活跃指标」，飞书上
    同一个 id 的行变成了「用户运营看板查询」。此时按 id 回写，等于把
    581 条描述配错条目 —— 这正是 ARCHITECTURE 第 98 行记的那次事故。

所以本模块**先验伤，再决定拿什么当键**，两件事都不猜。

## 锚点是 (l1, l2, l3)，不是 id，也不是行序

实测三地四张表：飞书侧 l3 集合与本地 l3 集合重合 291/304。维护人保留了
层级、只在层级底下加细行 —— 所以三级功能是这次唯一没被动过的键。

组内再按描述相似度把远端行挂到本地条目上：

    某 l3 组   本地 1 条「设备规范设置」（描述里列了 7 个方面）
               远端 7 行「信息技术设备」「卫生保健室」「安全饮水」…

    → 本地那条是**功能特性**，远端 7 行是它含的 7 个基本过程。

## 拆出来的条目用 `<父id>.NN`，不重新编号

与 `bom_decompose` 同一套约定（见 bom_schema.ID_RE 的注释）：id 上就看得出
这条来自哪个特性。评审问「这 7 条是不是同一件事重复计了」时，id 本身是答案；
重新编号会把这个问题变成一次考古。

父条目随之置 `deprecated` 并记 `superseded_by` —— **不物理删除**，
历史报价凭 deal.lock.json 仍要能精确重算。

## 判型由规则引擎独立做，判不出的按最低权重占位并打标签

描述变了就重判（`nesma_classify`），描述没变就保留本地既有结论 —— 给既有
结论换一份理由是事后合理化。

判不出的**不能留空**：`bom_schema` 对 SOFTWARE_FP 缺 nesma 段是结构性硬失败，
留空的 BOM 连 validate 都过不去（`bom_decompose` 与 `nesma_classify` 的注释
都写着「留 type: null 进人工裁决」，那条路其实走不通 —— schema 不允许）。

所以按**权重最低档 EQ（4）**占位，rationale 第一句就写「⛔ 待裁决占位
（非判定结论）」，并打 tag「待裁决-判型」。三点都是必要的：占位猜错的方向
必须是少算；理由里不能伪装成判定结论；标签让丁本质检册点得到名。
少算比错算容易发现，而一个猜出来的 EO 写成既成事实，就是那种「跑得通、
金额也像样」的错误。

## 逻辑文件不在这里动

飞书那四张表里 30 条 `LF.*` 一条都没剩（维护人重建时没带上）。但逻辑文件
是不是本子系统维护的，要看跨子系统的全局关系，不是读一段描述能定的
（那一步走 `bom_logical_files.py`）。它们**原样留下**，既不改也不废：
按飞书「没有了」就删掉，等于把 2026-08-07 那次单一维护方裁定和 92 处
引用关系一起丢掉，而 UFP 会静静地少 300 点。

用法：
    python3 bom_ingest_lark_rows.py --bom bom/verticals/preschool/apps \\
        --config bom/verticals/preschool/apps/lark.json \\
        --pack standard-packs/liuzhou-2020 --out /tmp/ingest [--version 0.2.0] [--apply]
    不带 --apply 为 dry-run，只出报告。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

import nesma_classify as nc
from lark_table import lark

#: `条目ID` 列失真判定线 —— 超过这个比例的 id 对不上本地内容，就不认这一列。
#: 取 0.2 而不是 0：少量对不上是正常的共创改写，整列对不上是重排事故，
#: 两者的处置完全相反（前者按 id 回写，后者按 id 回写就是灾难）。
ID_STALE_RATIO = 0.20

#: 组内把远端行挂到本地条目上的相似度线。低于此线视为「组内新增」，
#: 挂到该组第一条上而不是硬配 —— 硬配出来的父子关系会写进 id，比不挂更难查。
ANCHOR_MIN = 0.35

#: 判不出型时的保守占位类型。EQ 与 EI 同为最低权重 4，低于 EO 的 5 ——
#: 占位猜错的方向必须是少算，不能是多算。
PLACEHOLDER_TYPE = "EQ"

TABLE_RE = re.compile(r"^[ABC]\d+ ")


def _txt(v: Any) -> str:
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return "".join(str(x.get("text", "")) for x in v).strip()
        return "、".join(str(x) for x in v).strip()
    return "" if v is None else str(v).strip()


def _norm(s: str | None) -> str:
    """比文本时去掉标点与空白。飞书富文本会在换行处塞空格，
    逐字比会把「同一句话」判成改写过。"""
    return re.sub(r"[\s，。；、：,.;:“”\"'（）()]+", "", s or "")


def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def list_all(bt: str, tid: str) -> list[dict[str, Any]]:
    """全表取回，取到取空为止。只取一页会让「没有新增」和「新增在第二页」
    看起来一样。"""
    out: list[dict] = []
    offset = 0
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", "200", "--offset", str(offset))
        d = r.get("data") or {}
        cols, rows = d.get("fields") or [], d.get("data") or []
        if not rows:
            break
        for row in rows:
            out.append(dict(zip(cols, row)) if isinstance(row, list) else row)
        if not d.get("has_more"):
            break
        offset += len(rows)
    return out


def resolve_tables(bt: str, cfg: dict) -> dict[str, str]:
    """表名 → table_id。**表名是对齐键**（见 ARCHITECTURE 五）——
    飞书侧改了表名，lark.json 的 sheet_names 必须跟着改，否则这里认不出来。"""
    r = lark("base", "+table-list", "--base-token", bt, "--as", "user")
    remote = {t["name"]: t["id"] for t in ((r.get("data") or {}).get("tables") or [])}
    declared = set((cfg.get("sheet_names") or {}).values()) or set(cfg.get("tables") or {})
    out = {n: i for n, i in remote.items() if TABLE_RE.match(n)}
    missing = sorted(d for d in declared if TABLE_RE.match(d) and d not in remote)
    if missing:
        raise SystemExit(
            f"⛔ lark.json 声明的表在飞书上找不到：{missing}\n"
            f"  飞书现有条目表：{sorted(out)}\n"
            f"  表名是对齐键 —— 若飞书侧改了表名，请同步改 lark.json 的 sheet_names，"
            f"不要让工具去猜。")
    if not out:
        raise SystemExit(f"⛔ 飞书上没有 A/B/C 前缀的条目表。现有：{sorted(remote)}")
    return out


# ---- 一、先验伤：`条目ID` 那一列还能不能当键 ----------------------------

def audit_id_column(rows: list[dict], local: list[dict]) -> dict[str, Any]:
    """检查带 id 的行，其内容是否还对得上本地同 id 的条目。

    判据用「名字或描述至少一个对得上」而不是「两个都对得上」——
    共创正常会改写描述，那不是失真；名字和描述**同时**换成另一条的内容，
    才说明这一列压在了别的行上。
    """
    by_id = {i["id"]: i for i in local}
    checked = ok = 0
    examples: list[dict] = []
    for r in rows:
        iid = _txt(r.get("条目ID"))
        if not iid or iid not in by_id:
            continue
        checked += 1
        it = by_id[iid]
        if (_norm(_txt(r.get("功能点"))) == _norm(it.get("name"))
                or _sim(_txt(r.get("需求描述")), it.get("description") or "") > 0.6):
            ok += 1
        elif len(examples) < 5:
            examples.append({"id": iid, "本地名": it.get("name", ""),
                             "飞书名": _txt(r.get("功能点"))})
    ratio = 1 - (ok / checked) if checked else 0.0
    return {"带id行": checked, "内容对得上": ok, "失真比": round(ratio, 3),
            "stale": ratio > ID_STALE_RATIO, "示例": examples}


# ---- 二、按 (l1,l2,l3) 分组，组内按相似度挂锚 --------------------------

def _key(l1: str, l2: str, l3: str) -> tuple[str, str, str]:
    return (_norm(l1), _norm(l2), _norm(l3))


def anchor(rows: list[dict], local_fp: list[dict]) -> dict[str, Any]:
    """把远端行挂到本地条目上。返回

        pairs   [(local_item | None, [remote_row, ...]), ...]
        orphans [local_item, ...]        组还在但没有远端行挂上来
        gone    [local_item, ...]        整个 l3 组在飞书上消失了
        fresh   [(key, [remote_row,...])]  飞书上新出现的 l3 组
    """
    L: dict[tuple, list[dict]] = defaultdict(list)
    for i in local_fp:
        p = i.get("path") or {}
        L[_key(p.get("l1", ""), p.get("l2", ""), p.get("l3", ""))].append(i)
    R: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        R[_key(_txt(r.get("一级功能")), _txt(r.get("二级功能")),
               _txt(r.get("三级功能")))].append(r)

    pairs: list[tuple[dict | None, list[dict]]] = []
    orphans: list[dict] = []
    gone: list[dict] = []
    fresh: list[tuple[tuple, list[dict]]] = []

    for k in sorted(set(L) | set(R)):
        li, ri = L.get(k, []), R.get(k, [])
        if li and not ri:
            gone += li
            continue
        if ri and not li:
            fresh.append((k, ri))
            continue
        # 组内：每个远端行挂到最相似的本地条目
        buckets: dict[int, list[dict]] = {n: [] for n in range(len(li))}
        for r in ri:
            rd = _txt(r.get("需求描述"))
            rn = _txt(r.get("功能点"))
            best, score = 0, -1.0
            for n, it in enumerate(li):
                s = max(_sim(rd, it.get("description") or ""),
                        _sim(rn, it.get("name") or ""))
                if s > score:
                    best, score = n, s
            # 低于锚定线也要挂 —— 挂到组内第一条并标记，不能凭空丢。
            # 「组还在、行也在、就是配不上」不是删除的理由。
            buckets[best if score >= ANCHOR_MIN else 0].append(r)
        for n, it in enumerate(li):
            if buckets[n]:
                pairs.append((it, buckets[n]))
            else:
                orphans.append(it)
    return {"pairs": pairs, "orphans": orphans, "gone": gone, "fresh": fresh}


# ---- 三、判型 -----------------------------------------------------------

def _nesma_for(text: str, excl: set[str] | None, version: str,
               origin: str) -> tuple[dict | None, str]:
    # allow_data=False —— 逐行只判事务功能。数据功能走 bom_logical_files.py，
    # 理由见 nesma_classify.classify 的注释（台账/知识库是被操作的对象，
    # 不是这一行本身；替它判 ILF 会与逻辑文件那条链重复计列）。
    v = nc.classify(text, excl, allow_data=False)
    if not v.type:
        return None, ("命中不计数规则" if v.excluded else "规则引擎无命中")
    hits = "、".join(v.hits[:4])
    r = (f"判为 {v.type}：{v.rule.summary}（{v.rule.citation}）"
         + (f"；命中原文「{hits}」" if hits else "")
         + f"。※ {origin}，规则引擎独立判型 —— 进入 reviewed 前须双人复核（G-02c）")
    if v.competing:
        r += ("。⚠ 同时命中 "
              + "；".join(f"{t}({'、'.join(h[:3])})" for t, h in v.competing)
              + "，该行可能仍含多个基本处理，需复核是否再拆")
    return {"type": v.type, "rationale": r,
            "counted_by": f"bom_ingest_lark_rows+nesma_classify:{v.rule.id}@{version}"}, ""


def _gist(text: str, fallback: str) -> str:
    t = re.split(r"[，。；、：]", (text or "").strip())[0]
    if not t:
        return fallback
    return (t[:24] + "…") if len(t) > 24 else t


# ---- 四、生成新条目集 ---------------------------------------------------

def build(local: list[dict], rows: list[dict], *, sysname: str, version: str,
          excl: set[str] | None) -> dict[str, Any]:
    lf = [i for i in local if i["id"].startswith("LF.")]
    fp = [i for i in local if not i["id"].startswith("LF.")]
    a = anchor(rows, fp)

    out: list[dict] = list(lf)          # 逻辑文件原样留下
    stats = Counter()
    undecided: list[dict] = []
    seq = max([int(i["id"].rsplit(".", 1)[-1]) for i in fp
               if not re.search(r"\.\d{2}$", i["id"])] or [1000])

    def emit(base: dict, r: dict, iid: str, origin: str) -> None:
        desc = _txt(r.get("需求描述"))
        name = _txt(r.get("功能点")) or _gist(desc, base.get("name", iid))
        it = dict(base)
        it["id"] = iid
        it["name"] = name
        it["description"] = desc
        it["path"] = {"product_line": _txt(r.get("产品线")) or
                      (base.get("path") or {}).get("product_line", ""),
                      "system": sysname,
                      "l1": _txt(r.get("一级功能")),
                      "l2": _txt(r.get("二级功能")),
                      "l3": _txt(r.get("三级功能"))}
        it["since"] = version
        it["status"] = "draft"
        it["coauthor_status"] = _txt(r.get("提示·状态")) or "已判型"
        it["source"] = f"飞书 BOM「{sysname}」维护人重拆版（{version}）"
        # 特性级的对外报价不能逐个基本过程复制一份 —— 那会让交叉校验虚增
        lq = dict(base.get("legacy_quote") or {})
        if lq:
            lq["note"] = (lq.get("note", "") + "；特性级取值，已按飞书重拆版拆为"
                                               "多个基本过程，勿逐条累加")
            it["legacy_quote"] = lq
        it.pop("referenced_by", None)
        n, why = _nesma_for(f"{name}。{desc}", excl, version, origin)
        if n:
            it["nesma"] = n
            stats[n["type"]] += 1
        else:
            # **不能留空。** `bom_schema` 对 SOFTWARE_FP 缺 nesma 段是结构性
            # 硬失败（第 319 行），留空的 BOM 连 validate 都过不去，出不了表。
            # 所以给权重最低的保守占位 + 显式标签，让它在表里藏不住：
            #   · EQ 权重 4，与 EI 同为最低档，比 EO(5) 低 —— 猜错的方向是少算
            #   · rationale 第一句就写「待裁决占位」，不伪装成判定结论
            #   · tags 打「待裁决-判型」，丁本质检册按标签点名
            # 少算比错算容易发现；把一个猜出来的 EO 写成既成事实，
            # 就是那种「跑得通、金额也像样」的错误。
            it["nesma"] = {
                "type": PLACEHOLDER_TYPE,
                "rationale": (
                    f"⛔ 待裁决占位（非判定结论）：规则引擎按表6–表10 对本条描述"
                    f"{why}。为使 BOM 通过结构校验，暂按权重最低档 "
                    f"{PLACEHOLDER_TYPE}（权重 4）计列 —— 猜错方向为少算。"
                    f"须由业务侧按表8.1/表9.3/表10.5 逐条裁定；"
                    f"若含派生数据应改判 EO（权重 5）。{origin}"),
                "counted_by": f"bom_ingest_lark_rows:placeholder@{version}",
            }
            it["tags"] = list(it.get("tags") or []) + ["待裁决-判型"]
            stats["未判型占位"] += 1
            undecided.append({"id": iid, "name": name, "desc": desc[:80],
                              "reason": why})
        out.append(it)

    # 4.1 有本地父条目的
    for parent, rs in a["pairs"]:
        pid = parent["id"]
        if len(rs) == 1 and _norm(_txt(rs[0].get("需求描述"))) == _norm(parent.get("description")):
            out.append(parent)                       # 一字未改，原样留下
            stats["未变"] += 1
            continue
        if len(rs) == 1:
            emit(parent, rs[0], pid, f"飞书侧改写了特性「{parent.get('name')}」的描述")
            stats["改写"] += 1
            continue
        for n, r in enumerate(rs, start=1):
            emit(parent, r, f"{pid}.{n:02d}",
                 f"由飞书重拆版从特性「{parent.get('name')}」切出的第 {n}/{len(rs)} 个基本过程")
        # 父条目退役而非删除，历史报价仍要能精确重算
        dep = dict(parent)
        dep["status"] = "deprecated"
        dep["deprecated_in"] = version
        dep["superseded_by"] = [f"{pid}.{n:02d}" for n in range(1, len(rs) + 1)]
        dep["deprecated_reason"] = (
            f"飞书 BOM 维护人在同一三级功能「{(parent.get('path') or {}).get('l3','')}」"
            f"下重拆为 {len(rs)} 个基本过程；本条为拆分前的功能特性，"
            f"UFP 由子条目计列，勿与子条目同时计入")
        out.append(dep)
        stats["被拆特性"] += 1
        stats["拆出"] += len(rs)

    # 4.2 组还在但没有远端行挂上来的本地条目 —— 退役，但说清是哪一种
    for it in a["orphans"] + a["gone"]:
        dep = dict(it)
        dep["status"] = "deprecated"
        dep["deprecated_in"] = version
        dep["deprecated_reason"] = (
            "飞书 BOM 维护人重拆后，本条目在同一三级功能下已无对应行"
            if it in a["orphans"] else
            f"飞书 BOM 维护人重拆后，三级功能「{(it.get('path') or {}).get('l3','')}」"
            f"整组已不存在")
        out.append(dep)
        stats["退役"] += 1

    # 4.3 飞书上新出现的 l3 组 —— 真新增，给新号
    tmpl = fp[0] if fp else {"class": "SOFTWARE_FP", "maturity": "new"}
    for k, rs in a["fresh"]:
        seq += 1
        base = {"class": tmpl.get("class", "SOFTWARE_FP"), "maturity": "new",
                "maturity_evidence": "新建项目 —— 复用度默认取 1（复用度低）"}
        pid = f"{_prefix(fp, tmpl)}.{seq:04d}"
        if len(rs) == 1:
            emit(base, rs[0], pid, "飞书重拆版新增的三级功能，本地此前没有")
        else:
            for n, r in enumerate(rs, start=1):
                emit(base, r, f"{pid}.{n:02d}",
                     f"飞书重拆版新增的三级功能，切出的第 {n}/{len(rs)} 个基本过程")
        stats["新增三级功能"] += 1
        stats["新增行"] += len(rs)

    return {"items": out, "stats": dict(stats), "undecided": undecided,
            "audit": audit_id_column(rows, local),
            "shape": {"本地FP": len(fp), "本地LF": len(lf), "远端行": len(rows),
                      "产出条目": len(out)}}


def _prefix(fp: list[dict], tmpl: dict) -> str:
    """新号的命名空间前缀从既有条目上取，不从系统名推导 ——
    推导出来的前缀会随源表改名而漂移，而 id 是跨版本追溯的锚。"""
    for i in fp:
        if i["id"].startswith("FP."):
            return i["id"].rsplit(".", 1)[0]
    raise SystemExit("⛔ 该系统没有任何 FP.* 条目，无法确定新号前缀 —— 请手工指定")


# ---- 五、报告 -----------------------------------------------------------

def report(res: dict[str, dict], version: str) -> str:
    L = [f"# 飞书 BOM 重拆版摄入报告 · {version}", ""]
    W = {"ILF": 10, "ELF": 7, "EIF": 7, "EI": 4, "EO": 5, "EQ": 4}
    tot = Counter()
    L += ["## 一、`条目ID` 列可信度", "",
          "| 表 | 带 id 行 | 内容对得上 | 失真比 | 判定 |", "|---|---|---|---|---|"]
    for t, r in res.items():
        a = r["audit"]
        L.append(f"| {t} | {a['带id行']} | {a['内容对得上']} | {a['失真比']:.0%} | "
                 f"{'**整列失真，不可作键**' if a['stale'] else '可用'} |")
    stale = [t for t, r in res.items() if r["audit"]["stale"]]
    if stale:
        L += ["", f"⚠ {len(stale)} 张表的 `条目ID` 列已与内容脱钩。"
                  "本次摄入**不按 id 对齐**，改按 `(一级/二级/三级功能)` 分组 + "
                  "组内描述相似度挂锚。", "",
              "失真示例（同一 id，两侧内容无关）：", ""]
        for t in stale:
            for e in res[t]["audit"]["示例"][:3]:
                L.append(f"- `{e['id']}` 本地「{e['本地名']}」 → 飞书「{e['飞书名']}」")
    L += ["", "## 二、条目变化", "",
          "| 表 | 本地FP | 本地LF | 远端行 | 未变 | 改写 | 被拆特性 | 拆出 | "
          "新增三级功能 | 新增行 | 退役 | 待裁决占位 | 产出 |", "|" + "---|" * 13]
    for t, r in res.items():
        s, sh = r["stats"], r["shape"]
        L.append(f"| {t} | {sh['本地FP']} | {sh['本地LF']} | {sh['远端行']} | "
                 f"{s.get('未变',0)} | {s.get('改写',0)} | {s.get('被拆特性',0)} | "
                 f"{s.get('拆出',0)} | {s.get('新增三级功能',0)} | "
                 f"{s.get('新增行',0)} | {s.get('退役',0)} | "
                 f"{s.get('未判型占位',0)} | {sh['产出条目']} |")
        for k, v in s.items():
            tot[k] += v
    L += ["", "## 三、判型分布与 UFP", "", "| 类型 | 权重 | 条数 | UFP |",
          "|---|---|---|---|"]
    ufp = 0
    for ty, w in W.items():
        n = tot.get(ty, 0)
        if n:
            ufp += n * w
            L.append(f"| {ty} | {w} | {n} | {n * w} |")
    L += [f"| **本次新判型合计** | | {sum(tot.get(t,0) for t in W)} | **{ufp}** |",
          "", f"待裁决占位 {tot.get('未判型占位', 0)} 条 —— 规则引擎无命中，"
              f"暂按权重最低的 {PLACEHOLDER_TYPE}（4）计列并打 tag「待裁决-判型」，"
              f"猜错方向为少算；须业务侧逐条裁定。"]
    und = [u for r in res.values() for u in r["undecided"]]
    if und:
        L += ["", "## 四、待人工裁决（规则引擎无命中，已按 EQ 占位）", "",
              "| 条目ID | 名称 | 描述 | 原因 |", "|---|---|---|---|"]
        for u in und:
            L.append(f"| `{u['id']}` | {u['name']} | {u['desc']} | {u['reason']} |")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--pack", type=Path, help="决定启用哪些不计数规则")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--version", default="0.2.0")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(a.config.read_text(encoding="utf-8"))
    bt = cfg["base_token"]
    excl = None
    if a.pack:
        from standard_pack import StandardPack
        excl = nc.exclusions_from_pack(StandardPack.load(str(a.pack)))
        print(f"不计数规则（{a.pack.name} 授权）："
              f"{sorted(excl) if excl is not None else '包里未声明，全开'}")

    tables = resolve_tables(bt, cfg)
    names = {v: k for k, v in (cfg.get("sheet_names") or {}).items()}
    res: dict[str, dict] = {}
    files: dict[Path, list[dict]] = {}
    for tname, tid in sorted(tables.items()):
        sysname = names.get(tname) or tname.split(" ", 1)[-1]
        f = a.bom / "items" / f"{sysname}.yaml"
        if not f.exists():
            print(f"  跳过 {tname}：本地无 {f.name}")
            continue
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        rows = list_all(bt, tid)
        r = build(data.get("items") or [], rows, sysname=sysname,
                  version=a.version, excl=excl)
        res[tname] = r
        files[f] = r["items"]
        print(f"  {tname:12} 远端 {len(rows):>4} 行 → 产出 {len(r['items']):>4} 条  "
              f"{ {k: v for k, v in r['stats'].items()} }")

    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "ingest-report.md").write_text(report(res, a.version), encoding="utf-8")
    (a.out / "ingest-raw.json").write_text(
        json.dumps({t: {"stats": r["stats"], "audit": r["audit"],
                        "shape": r["shape"], "undecided": r["undecided"]}
                    for t, r in res.items()}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n报告 → {a.out / 'ingest-report.md'}")

    if not a.apply:
        print("dry-run，未改 BOM。加 --apply 落盘。")
        return
    for f, items in files.items():
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        data["items"] = items
        data["bom_version"] = a.version
        f.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                                    width=100), encoding="utf-8")
        print(f"  写入 {f}")
    ver = a.bom.parent / "VERSION"
    if ver.exists():
        ver.write_text(a.version + "\n", encoding="utf-8")
        print(f"  写入 {ver} = {a.version}")


if __name__ == "__main__":
    main()
