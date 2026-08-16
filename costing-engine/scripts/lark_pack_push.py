"""把 standard-packs/ 推到**标准包 base**，让财评口径在飞书可查。

## 为什么要推

标准包是 YAML，只有工程侧看得到。但「这个系数凭什么取 1.5」「人月单价
17000 出自哪一条」是财评现场最常被问的两类问题，答案得是**能当场翻出来的
条款原文**，不是「我回去查一下」。

## 三张表，各答一问

    1 参数表    这个标准的取值是什么      pack.yaml 全量摊平，路径即出处
    2 条款原文  凭什么取这个值            每个 citation 一行，带页码与章节
    3 词表      这些词在本仓里什么意思    bom/vocabulary.yaml

**单向推送**。这个 base 是标准与 schema 的投影，不是共创对象 —— 飞书侧改了
不会被拉回，改标准要改 pack.yaml。这一点写在表描述里，免得有人在上面改完
以为生效了。

用法：
    python3 lark_pack_push.py --root <digital-costing> [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

import lark_table as lt

TEXT = "text"

SPEC = {
    "1 参数表": [
        {"name": "标准包", "type": TEXT},
        {"name": "分区", "type": TEXT, "description": "pack.yaml 的顶层键"},
        {"name": "路径", "type": TEXT, "description": "分区内的点分路径，就是 YAML 里的位置"},
        {"name": "值", "type": TEXT},
    ],
    "2 条款原文": [
        {"name": "标准包", "type": TEXT},
        {"name": "路径", "type": TEXT, "description": "这条原文支撑的是哪个取值"},
        {"name": "页码", "type": TEXT},
        {"name": "章节", "type": TEXT},
        {"name": "原文", "type": TEXT, "description": "标准正文原句，财评现场可直接引用"},
    ],
    "3 词表": [
        {"name": "字段", "type": TEXT},
        {"name": "取值", "type": TEXT},
        {"name": "含义", "type": TEXT},
        {"name": "来源", "type": TEXT},
    ],
}

VIS = {
    "1 参数表": ["标准包", "分区", "路径", "值"],
    "2 条款原文": ["标准包", "路径", "页码", "章节", "原文"],
    "3 词表": ["字段", "取值", "含义", "来源"],
}


def _scalar(v: Any) -> str:
    if v is None:
        return "（空）"
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def flatten(obj: Any, path: str, params: list[dict], cites: list[dict],
            pack: str, section: str) -> None:
    """摊平 pack.yaml。citation 单独抽走 —— 它是依据，不是取值，混在参数表里
    会让「这个标准有哪些参数」这个问题淹没在大段原文中。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if k == "citation" and isinstance(v, dict):
                cites.append({
                    "标准包": pack,
                    "路径": f"{section}.{path}" if path else section,
                    "页码": _scalar(v.get("page")),
                    "章节": _scalar(v.get("section")),
                    "原文": _scalar(v.get("quote")),
                })
                continue
            flatten(v, p, params, cites, pack, section)
        return
    if isinstance(obj, list):
        if all(not isinstance(x, (dict, list)) for x in obj):
            params.append({"标准包": pack, "分区": section, "路径": path,
                           "值": " / ".join(_scalar(x) for x in obj) or "（空表）"})
            return
        for i, x in enumerate(obj):
            flatten(x, f"{path}[{i}]", params, cites, pack, section)
        return
    # 顶层标量（pack_id、scope 这类）没有分区内路径，写「—」而不是留空 ——
    # 空格子在飞书里和「没填」长得一样。
    params.append({"标准包": pack, "分区": section, "路径": path or "—",
                   "值": _scalar(obj)})


def build(root: Path) -> dict[str, list[dict]]:
    params: list[dict] = []
    cites: list[dict] = []
    packs = sorted(p for p in (root / "standard-packs").iterdir()
                   if p.is_dir() and (p / "pack.yaml").exists())
    if not packs:
        raise SystemExit("⛔ standard-packs/ 下没有任何 pack.yaml")
    for d in packs:
        data = yaml.safe_load((d / "pack.yaml").read_text(encoding="utf-8")) or {}
        for k, v in data.items():
            flatten(v, "", params, cites, d.name, k)

    vocab: list[dict] = []
    vp = root / "bom/vocabulary.yaml"
    if vp.exists():
        vy = yaml.safe_load(vp.read_text(encoding="utf-8")) or {}
        for fname, f in (vy.get("fields") or {}).items():
            label = f.get("label") or fname
            for val, mean in (f.get("values") or {}).items():
                vocab.append({"字段": f"{fname}（{label}）", "取值": val,
                              "含义": _scalar(mean), "来源": "bom/vocabulary.yaml"})
            if not f.get("values"):
                vocab.append({"字段": f"{fname}（{label}）", "取值": "—",
                              "含义": _scalar(f.get("note")),
                              "来源": "bom/vocabulary.yaml"})
    return {"1 参数表": params, "2 条款原文": cites, "3 词表": vocab}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    root = a.root.resolve()
    cfgp = root / "standard-packs/lark.json"
    cfg = yaml.safe_load(cfgp.read_text(encoding="utf-8"))
    bt = cfg["base_token"]

    rows = build(root)
    for t, r in rows.items():
        print(f"  {t:12}{len(r):>6} 行")
    if a.dry_run:
        print("\n--dry-run，未连飞书。")
        return

    from lark_bom_split_push import ensure_table
    tables = {}
    for t, spec in SPEC.items():
        tid = ensure_table(bt, t, spec)
        n = lt.replace_all(bt, tid, rows[t], label=t)
        print(f"  {t:12}写入 {n} 行　{tid}")
        tables[t] = tid
        exist = {f if isinstance(f, str) else f["name"] for f in lt.fields(bt, tid)}
        cols = [c for c in VIS[t] if c in exist]
        vs = lt.lark("base", "+view-list", "--base-token", bt,
                     "--table-id", tid, "--as", "user")
        for v in ((vs.get("data") or {}).get("items")
                  or (vs.get("data") or {}).get("views") or []):
            vid = v.get("view_id") or v.get("id")
            lt.lark("base", "+view-set-visible-fields", "--base-token", bt,
                    "--table-id", tid, "--view-id", vid,
                    "--json", json.dumps({"visible_fields": cols},
                                         ensure_ascii=False), "--as", "user")

    cfg["tables"] = tables
    cfgp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
    print(f"\n→ https://lcna31l6eggn.feishu.cn/base/{bt}")


if __name__ == "__main__":
    main()
