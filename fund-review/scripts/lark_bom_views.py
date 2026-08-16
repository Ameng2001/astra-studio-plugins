"""lark_bom_views —— 按共创状态给每张条目表建预筛视图（工作队列）。

## 为什么视图是工作队列而不是筛选器

方案人员打开自己那张表的 `★待补描述` 视图，看到的就是他今天要干的活；
改完把状态设成「已处理」，那一行自动出队。这件事颜色和批注做不到 ——
它们不聚合（答不了「还剩多少条」）、拉不回来做 diff、活不过一次排序。

## 只给**真有活**的表建视图

一张表如果没有 `★待补描述` 的条目，就不建那个视图。空视图是噪声：
打开一看空的，下次就不会再打开了，而真正有活的那天他也不会打开。
所以视图数量随进度自然收敛 —— 全部处理完，视图就该一个不剩。

## 幂等

同名视图已存在就复用并重设筛选，不重复建。视图 id 写回 lark-sync.json，
方便直接发链接给人。

用法：
    python3 lark_bom_views.py --bom <dir> --config <lark-sync.json> [--verify]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from lark_bom_split_push import STATUS_FIELD
from lark_table import lark

#: 需要建视图的状态。「已判型」「已处理」不建 —— 那是完成态，不是工作队列。
QUEUE_STATUSES = ("★待补描述", "★建议再拆", "★待补工作量依据", "技术说明待复核")


def _views_of(bt: str, tid: str) -> dict[str, str]:
    r = lark("base", "+view-list", "--base-token", bt, "--table-id", tid,
             "--as", "user")
    out = {}
    for v in ((r.get("data") or {}).get("items")
              or (r.get("data") or {}).get("views") or []):
        vid = v.get("view_id") or v.get("id")
        if vid:
            out[v.get("name")] = vid
    return out


def _count(bt: str, tid: str, vid: str) -> int:
    n = 0
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--view-id", vid, "--as", "user", "--limit", "200",
                 "--offset", str(n))
        d = r.get("data") or {}
        got = len(d.get("record_id_list") or [])
        n += got
        if not d.get("has_more") or not got:
            return n


def build(bom_dir: Path, cfg: dict, verify: bool) -> dict[str, str]:
    bt = cfg["base_token"]
    local: dict[str, Counter] = defaultdict(Counter)
    for f in sorted(bom_dir.glob("items/*.yaml")):
        for it in yaml.safe_load(f.read_text(encoding="utf-8"))["items"]:
            st = it.get("coauthor_status", "")
            if st in QUEUE_STATUSES:
                local[it["path"]["system"]][st] += 1

    sys2tab = {}
    for tn in cfg["tables"]:
        m = re.match(r"^[ABC]\d+ (.+?)(\(L\d\))?$", tn)
        if m:
            sys2tab[m.group(1)] = tn

    views: dict[str, str] = {}
    mismatch: list[str] = []
    for system, cnt in sorted(local.items()):
        tn = sys2tab.get(system)
        if not tn:
            raise ValueError(
                f"系统「{system}」有 {sum(cnt.values())} 条待办，"
                f"却找不到对应的飞书表 —— 现有 {sorted(sys2tab)}。"
                f"建不了视图就等于那些活没人看得到")
        tid = cfg["tables"][tn]
        have = _views_of(bt, tid)
        for st, n in sorted(cnt.items(), key=lambda x: -x[1]):
            vid = have.get(st)
            if not vid:
                r = lark("base", "+view-create", "--base-token", bt,
                         "--table-id", tid, "--json",
                         json.dumps({"name": st, "type": "grid"},
                                    ensure_ascii=False), "--as", "user")
                v = (r.get("data") or {})
                v = v.get("view") or (v.get("views") or [{}])[0]
                vid = v.get("view_id") or v.get("id")
            if not vid:
                raise ValueError(f"{tn}/{st}：视图建不出来")
            lark("base", "+view-set-filter", "--base-token", bt,
                 "--table-id", tid, "--view-id", vid, "--json",
                 json.dumps({"logic": "and",
                             "conditions": [[STATUS_FIELD, "intersects", [st]]]},
                            ensure_ascii=False), "--as", "user")
            views[f"{tn}/{st}"] = vid
            if verify:
                got = _count(bt, tid, vid)
                ok = got == n
                if not ok:
                    mismatch.append(f"{tn}/{st}：视图 {got} 条，本地 {n} 条")
                print(f"  {tn}/{st:<14}{got:>4} 条 {'✓' if ok else '✗ 本地 '+str(n)}")
            else:
                print(f"  {tn}/{st:<14}{n:>4} 条")

    if mismatch:
        raise ValueError(
            "视图条数与本地对不上：\n  " + "\n  ".join(mismatch)
            + "\n视图筛出来的数和本地不一致，说明筛选条件或状态取值有偏差 —— "
              "共创的人会照着一个错的队列干活")
    return views


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--verify", action="store_true",
                    help="逐个视图回读条数并与本地核对（慢，但值得）")
    a = ap.parse_args()

    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    views = build(a.bom, cfg, a.verify)
    cfg["views"] = {**{k: v for k, v in (cfg.get("views") or {}).items()
                       if k.startswith("C-")}, **views}
    a.config.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"\n共 {len(views)} 个工作队列视图 → {a.config}")


if __name__ == "__main__":
    main()
