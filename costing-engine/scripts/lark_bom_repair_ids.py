"""lark_bom_repair_ids —— 就地修飞书条目表被打乱的派生列，不删行。

## 为什么不用 lark_bom_split_push

push 走 `replace_all`：删光整张表再重建。用在这里有两个问题：

  · 维护人 1,228 行的**行序**是他重拆时排的，删了重建就没了；
  · push 没有 dry-run，中途失败留下的是半张表。

而这次要修的只是**派生列**（条目ID / 判型 / UFP / 类别 / 判定理由 / 追溯）。
业务列（层级、功能点名、需求描述）是维护人的产出，一个字都不该动 ——
实测两侧业务列零差异，所以覆盖它们没有收益，只有风险。

## 为什么派生列会坏

`条目ID` 在飞书里是一列普通文本。维护人重排/插行时，那一列**留在原来的
行位置上**，于是整列压到毫不相干的行上：本地 `FP.PSC.OPS.1001` 叫
「用户活跃指标」，飞书同 id 的行变成了「用户运营看板查询」。

`参数·功能点类型` 同理，而且更险：它现在在前 159 行上写着 ILF/ELF，
而那些行实际是 EI/EO/EQ。看上去每行都填得好好的。

## 拿什么重新对上

`(三级功能, 需求描述)`。这两列是维护人保留下来的，且摄入时 BOM 条目的
description 就是从飞书那一行原样拷来的 —— 所以是逐字相等，不是模糊匹配。

同一 l3 下描述逐字相同的（实测 37 行，多为「支持在web端、移动端进行登记」
这类模板句）按**行序对位**：两侧本来同序，摄入时也是按行序切的子号。

## 写之前先把账对平

三个前提全过才动手：

  1. 每一行都连得上本地条目（连不上 = 有行会被写上错 id，比现状更糟）
  2. 每个本地 FP 条目最多被一行占用（一个 id 落在两行上就是重复计列）
  3. 歧义组两侧行数相等（数不等就不能按行序对位）

批量写入的守卫必须是全通过才动手 —— 否则只是把「全错」变成「错一半」，
而错一半更难收拾。

用法：
    python3 lark_bom_repair_ids.py --bom bom/verticals/preschool/apps \\
        --config bom/verticals/preschool/apps/lark.json [--add-missing-lf] [--apply]
    不带 --apply 为 dry-run，只对账。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from lark_table import lark

BATCH = 200
TABLE_RE = re.compile(r"^[ABC]\d+ ")
W = {"ILF": 10, "ELF": 7, "EIF": 7, "EI": 4, "EO": 5, "EQ": 4}

#: 只写这些列。业务列不在其中 —— 那是维护人的产出。
#: `提示·状态` 也不写：它是共创侧的手动标记，回写等于替对方改状态。
DERIVED = ("条目ID", "参数·功能点类型", "参数·UFP", "参数·条目类别",
           "研判·判定理由", "追溯·来源特性", "追溯·引入版本")

#: 多选字段 —— 飞书对 multiple=true 的 select 收数组，收字符串会被忽略
#: （而且**不报错**，只是那一格还是空的）。实测两列都是 multiple=true。
MULTI_SELECT = ("参数·功能点类型",)


def _txt(v: Any) -> str:
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return "".join(str(x.get("text", "")) for x in v).strip()
        return "、".join(str(x) for x in v).strip()
    return "" if v is None else str(v).strip()


def _norm(s: str | None) -> str:
    return re.sub(r"[\s，。；、：,.;:“”\"'（）()]+", "", s or "")


def fetch(bt: str, tid: str) -> list[dict[str, Any]]:
    """全表取回，**带 record_id**。

    `record_id` 不在 `data.data` 里，在同层的 `record_id_list` ——
    两个数组按下标对应。`--format json` 的行数组里没有它，
    只有 markdown 输出才自带 `_record_id` 列，所以必须走这个平行数组。
    """
    out: list[dict] = []
    offset = 0
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH), "--offset", str(offset))
        d = r.get("data") or {}
        cols, rows = d.get("fields") or [], d.get("data") or []
        rids = d.get("record_id_list") or []
        if not rows:
            break
        if len(rids) != len(rows):
            raise SystemExit(
                f"⛔ {tid}: record_id_list {len(rids)} 与行数 {len(rows)} 不等 —— "
                f"两个平行数组对不上就无法确定改的是哪一行，中止。")
        for rid, row in zip(rids, rows):
            rec = dict(zip(cols, row)) if isinstance(row, list) else dict(row)
            rec["_rid"] = rid
            out.append(rec)
        if not d.get("has_more"):
            break
        offset += len(rows)
    return out


def local_items(bom: Path, sysname: str) -> list[dict]:
    f = bom / "items" / f"{sysname}.yaml"
    return [i for i in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("items") or []
            if i.get("status") != "deprecated"]


def join(rows: list[dict], items: list[dict]) -> dict[str, Any]:
    """远端行 → 本地条目。返回 {rid: item}、以及对账用的统计。"""
    fp = [i for i in items if not i["id"].startswith("LF.")]
    by: dict[tuple, list[dict]] = defaultdict(list)
    for i in fp:
        by[(_norm((i.get("path") or {}).get("l3")), _norm(i.get("description")))].append(i)

    # 歧义组按行序对位：先把远端行也按同一个键分组，组内按出现顺序配。
    rgrp: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        rgrp[(_norm(_txt(r.get("三级功能"))), _norm(_txt(r.get("需求描述"))))].append(r)

    pair: dict[str, dict] = {}
    unmatched: list[dict] = []
    mismatched_groups: list[dict] = []
    for k, rs in rgrp.items():
        cand = by.get(k) or []
        if not cand:
            unmatched += rs
            continue
        if len(rs) != len(cand):
            # 数不等就不能按行序对位 —— 硬配会把 id 错位，正是要修的那个病
            mismatched_groups.append({"键": k[0][:20], "远端行": len(rs),
                                      "本地条目": len(cand)})
            continue
        for r, i in zip(rs, cand):
            pair[r["_rid"]] = i

    used = Counter(i["id"] for i in pair.values())
    dup = {k: v for k, v in used.items() if v > 1}
    lf = [i for i in items if i["id"].startswith("LF.")]
    return {"pair": pair, "unmatched": unmatched, "dup": dup,
            "mismatched_groups": mismatched_groups,
            "missing_lf": lf, "本地FP": len(fp), "远端行": len(rows)}


def cells(i: dict) -> dict[str, Any]:
    n = i.get("nesma") or {}
    t = n.get("type") or ""
    out: dict[str, Any] = {
        "条目ID": i["id"],
        "参数·UFP": W.get(t, 0),
        "参数·条目类别": i.get("class") or "",
        "研判·判定理由": n.get("rationale") or "",
        # 带子号的 id 说明这条是从某个特性切出来的，父段就是来源特性
        "追溯·来源特性": i["id"].rsplit(".", 1)[0] if i["id"].count(".") >= 3 else "",
        "追溯·引入版本": i.get("since") or "",
    }
    out["参数·功能点类型"] = [t] if t else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--add-missing-lf", action="store_true",
                    help="把飞书上缺的逻辑文件行补建回去")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    cfg = yaml.safe_load(a.config.read_text(encoding="utf-8"))
    bt = cfg["base_token"]
    sheet = {v: k for k, v in (cfg.get("sheet_names") or {}).items()}

    r = lark("base", "+table-list", "--base-token", bt, "--as", "user")
    tables = {t["name"]: t["id"] for t in ((r.get("data") or {}).get("tables") or [])
              if TABLE_RE.match(t["name"])}
    if not tables:
        raise SystemExit("⛔ 飞书上没有 A/B/C 前缀的条目表")

    plans: list[tuple[str, str, dict]] = []
    blocked: list[str] = []
    for tname, tid in sorted(tables.items()):
        sysname = sheet.get(tname) or tname.split(" ", 1)[-1]
        if not (a.bom / "items" / f"{sysname}.yaml").exists():
            print(f"  跳过 {tname}：本地无 {sysname}.yaml")
            continue
        rows = fetch(bt, tid)
        j = join(rows, local_items(a.bom, sysname))
        plans.append((tname, tid, j))
        print(f"  {tname:12} 远端 {j['远端行']:>4} 行 / 本地 FP {j['本地FP']:>4} 条 → "
              f"连上 {len(j['pair']):>4}　连不上 {len(j['unmatched'])}　"
              f"重复占用 {len(j['dup'])}　数不等的组 {len(j['mismatched_groups'])}　"
              f"飞书缺 LF {len(j['missing_lf'])}")
        if j["unmatched"]:
            blocked.append(f"{tname}: {len(j['unmatched'])} 行连不上本地条目，"
                           f"例「{_txt(j['unmatched'][0].get('功能点'))}」")
        if j["dup"]:
            blocked.append(f"{tname}: {len(j['dup'])} 个条目 id 落在多行上 "
                           f"{list(j['dup'])[:3]}")
        if j["mismatched_groups"]:
            blocked.append(f"{tname}: {len(j['mismatched_groups'])} 个歧义组两侧行数不等 "
                           f"{j['mismatched_groups'][:2]}")

    if blocked:
        raise SystemExit(
            "⛔ 对账不平，**未写任何表**：\n  " + "\n  ".join(blocked)
            + "\n  写一半留下的混合状态（一部分行 id 修好、一部分还是错的）"
              "比现状更难查 —— 现状至少是整列都不可信。")

    total = sum(len(j["pair"]) for _, _, j in plans)
    lfn = sum(len(j["missing_lf"]) for _, _, j in plans)
    print(f"\n对账通过：{total} 行可就地修派生列"
          + (f"，另有 {lfn} 条逻辑文件在飞书上缺失" if lfn else ""))
    if not a.apply:
        print("dry-run，未写飞书。加 --apply 执行。")
        return

    # **两遍：先把所有表的更新做完，再统一补建。**
    # 原先是逐表「更新完就补建」，结果 A1 的补建载荷键名写错时抛出，
    # A2–A4 的更新一行都没做 —— 一张表修好、三张表还是错的，
    # 而错一半正是这次要修的那个病。修复动作自己不该制造同一种状态。
    for tname, tid, j in plans:
        upd = {rid: cells(it) for rid, it in j["pair"].items()}
        keys = list(upd)
        for n in range(0, len(keys), BATCH):
            chunk = {k: upd[k] for k in keys[n:n + BATCH]}
            res = lark("base", "+record-batch-update", "--base-token", bt,
                       "--table-id", tid, "--as", "user",
                       "--json", json.dumps({"update_records": chunk},
                                            ensure_ascii=False))
            if not res.get("ok"):
                raise SystemExit(f"⛔ {tname} 第 {n // BATCH + 1} 批写入失败："
                                 f"{res.get('error')}")
            ig = ((res.get("data") or {}).get("ignored_fields") or [])
            print(f"  {tname:12} 批 {n // BATCH + 1}：{len(chunk)} 行"
                  + (f"　⚠ 被忽略的列 {ig}" if ig else ""))
    for tname, tid, j in plans:
        if not (a.add_missing_lf and j["missing_lf"]):
            continue
        # 补建前先看飞书上有没有 —— 本工具可能被重跑（上一次在这一步失败过），
        # 不查就会把 30 条逻辑文件再插一遍，而重复的 ILF 每条虚增 10 UFP。
        have = {_txt(r.get("条目ID")) for r in fetch(bt, tid)}
        todo = [it for it in j["missing_lf"] if it["id"] not in have]
        if not todo:
            print(f"  {tname:12} 逻辑文件已在飞书上，跳过补建")
            continue
        recs = []
        for it in todo:
            p = it.get("path") or {}
            c = cells(it)
            c.update({"产品线": p.get("product_line") or "",
                      "一级功能": p.get("l1") or "", "二级功能": p.get("l2") or "",
                      "三级功能": p.get("l3") or "", "功能点": it.get("name") or "",
                      "需求描述": it.get("description") or ""})
            recs.append({k: v for k, v in c.items() if v not in (None, "")})
        # 载荷键是 `create_records`，不是 `records` —— 后者报
        # "Unrecognized key(s) in object" 并整批不写。
        res = lark("base", "+record-batch-create", "--base-token", bt,
                   "--table-id", tid, "--as", "user",
                   "--json", json.dumps({"create_records": recs},
                                        ensure_ascii=False))
        if not res.get("ok"):
            raise SystemExit(f"⛔ {tname} 补建逻辑文件行失败：{res.get('error')}")
        print(f"  {tname:12} 补建逻辑文件 {len(recs)} 行")


    # ---- 写完读回来验 ----
    # 「批量更新不校验 record_id 是否存在」（lark-cli 自己的说明），
    # 所以 ok=true 只证明请求被接受，不证明格子真的变了。必须回读。
    print("\n回读校验：")
    bad = 0
    for tname, tid, j in plans:
        rows = fetch(bt, tid)
        want = {rid: it["id"] for rid, it in j["pair"].items()}
        got = {r["_rid"]: _txt(r.get("条目ID")) for r in rows}
        wrong = [rid for rid, iid in want.items() if got.get(rid) != iid]
        empty = sum(1 for r in rows if not _txt(r.get("条目ID")))
        notype = sum(1 for r in rows if not _txt(r.get("参数·功能点类型")))
        print(f"  {tname:12} id 对上 {len(want) - len(wrong)}/{len(want)}　"
              f"仍空的 id {empty}　仍空的判型 {notype}")
        bad += len(wrong)
    print("✅ 全部对上" if not bad else f"⛔ {bad} 行没写进去")


if __name__ == "__main__":
    main()
