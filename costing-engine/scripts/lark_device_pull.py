"""lark_device_pull — 设备 BOM 的飞书回读。

## 为什么必须有这个脚本

`lark_device_push` 把「口径·配置方式」「商务·参考对比厂家」「商务·是否可售」
这些字段推成**可编辑**的格子，并明写「**请填**」。但在此之前，设备侧
**只有 push、没有任何 pull** —— 方案人员在飞书上填的东西一个字回不到本地，
也没有任何地方会提示。下一次 push 还会把它们原样覆盖掉。

这正是软件 BOM 的 `参数·软件类别` 当初翻的车：可编辑、改了不生效、最后靠
整列删掉才解决。**推上去可编辑，就必须读得回来**，否则那一列是个陷阱。

## 与 push 的语义不对称

    push  本地 yaml → 覆盖飞书（飞书是共创面，不是权威）
    pull  飞书改动 → 报差异；`--write` 才落 yaml

pull 默认只报不写：飞书上的一次误点击不该直接改变报价基线。

## 守卫

与丙本 03 / 软件 BOM 那两条链路同构 —— 三个录入面共用一套规矩，
否则从某一个面进来的数会绕过另外两个面的检查：

  · 配置方式为「每点位 N」→ 必须同时给「点位」
  · 配置方式为「每园/每班/每教室/每人/每点位 N」→ 「每单位数量」必须 > 0
  · 是否可售改为「停售 / 内部专用」→ 该物料若仍被配置目录引用，报冲突
    （停售设备躺在目录里，配上去之前没人会发现）
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from lark_table import lark
# **推导逻辑从 push 导入，不在这里复制**。两份实现必然漂移 ——
# 上一版就是照着重写了一份、三个分支不一致，55 行假阳性。
from lark_device_push import derived_rule

BATCH = 200
#: 需要「每单位数量」的配置方式。「直接给总量」「本场景不配」「复合（多段）」不需要。
NEEDS_QTY = {"每园 N", "每班 N", "每教室 N", "每人 N", "每点位 N"}


def list_all(bt: str, tid: str) -> list[dict[str, Any]]:
    """全表取回，取到取空为止。

    响应形状是 `data.fields`（列名）+ `data.data`（行数组），
    **不是 `data.items`** —— 按后者取任何表都返回 0 行，而「读到 0 行」
    和「没有改动」看起来完全一样。本项目已因此把 24 张表 952 行判成全空一次。
    """
    out: list[dict] = []
    off = 0
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH), "--offset", str(off))
        d = r.get("data") or {}
        cols, rows = d.get("fields") or [], d.get("data") or []
        if not rows:
            break
        for row in rows:
            out.append(dict(zip(cols, row)) if isinstance(row, list) else row)
        if not d.get("has_more"):
            break
        off += len(rows)
    return out


def _txt(v: Any) -> str:
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return "".join(str(x.get("text", "")) for x in v).strip()
        return "、".join(str(x) for x in v).strip()
    return "" if v is None else str(v).strip()


def _num(v: Any) -> float | None:
    s = _txt(v)
    try:
        return float(s)
    except ValueError:
        return None


def pull(root: Path, cfg: dict) -> dict[str, Any]:
    bt = cfg["base_token"]
    d = root / "bom" / "devices"
    mats = yaml.safe_load((d / "materials.yaml").read_text(encoding="utf-8"))["materials"]
    cat = yaml.safe_load((d / "catalog.yaml").read_text(encoding="utf-8"))["entries"]
    by_code = {m["code"]: m for m in mats}
    by_name = {m["name"]: m for m in mats}
    changes: list[dict] = []
    stats: Counter = Counter()

    # ---- 1 物料主数据 ----
    tid = (cfg.get("tables") or {}).get("1 物料主数据")
    if tid:
        for r in list_all(bt, tid):
            code = _txt(r.get("标识·物料编码"))
            m = by_code.get(code)
            if not m:
                stats["飞书有本地无(物料)"] += 1
                changes.append({"表": "1 物料主数据", "键": code or _txt(r.get("设备名称")),
                                "字段": "-", "本地": "（本地无此物料编码）",
                                "飞书": _txt(r.get("设备名称")), "类型": "孤儿行"})
                continue
            for fld, key, cur in (
                    ("商务·是否可售", "sellable", m.get("sellable", "待确认")),
                    ("商务·参考对比厂家", "ref_vendors",
                     "、".join(m.get("ref_vendors") or []))):
                remote = _txt(r.get(fld))
                if remote and remote != str(cur or "").strip():
                    stats[fld] += 1
                    changes.append({"表": "1 物料主数据", "键": code, "字段": fld,
                                    "本地": str(cur or ""), "飞书": remote,
                                    "类型": "物料"})

    # ---- 2 配置目录 ----
    tid = (cfg.get("tables") or {}).get("2 配置目录")
    if tid:
        # **关联字段读回来是 record_id，不是显示名** —— 形如
        # `[{'id': 'recvrH92VyBf9B'}]`。按显示名匹配会让 135 行全判成孤儿
        # （实测 136/136 全错），而「全是孤儿」和「本地全被删了」长得一样。
        # 所以先建 record_id → 场景名 的映射。
        rid2scene: dict[str, str] = {}
        stid = (cfg.get("tables") or {}).get("0 场景树")
        if stid:
            r0 = lark("base", "+record-list", "--base-token", bt,
                      "--table-id", stid, "--as", "user", "--limit", str(BATCH))
            d0 = r0.get("data") or {}
            cols0 = d0.get("fields") or []
            rids0 = d0.get("record_id_list") or []
            i_name = cols0.index("规范子场景") if "规范子场景" in cols0 else 0
            for rid, row in zip(rids0, d0.get("data") or []):
                rid2scene[rid] = _txt(row[i_name])

        def _link(v) -> str:
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return rid2scene.get(v[0].get("id"), "")
            return _txt(v)

        idx = {}
        for e in cat:
            mm = by_code.get(e["material"])
            if mm:
                idx[(e["scene"], mm["name"])] = e
        for r in list_all(bt, tid):
            key = (_link(r.get("场景")), _txt(r.get("设备名称")))
            e = idx.get(key)
            if not e:
                stats["飞书有本地无(目录)"] += 1
                changes.append({"表": "2 配置目录", "键": " / ".join(key),
                                "字段": "-", "本地": "（本地无此场景×设备）",
                                "飞书": "", "类型": "孤儿行"})
                continue
            dr = derived_rule(e)
            rc = {"kind": dr.get("口径·配置方式", ""),
                  "qty": dr.get("口径·每单位数量"),
                  "at": dr.get("口径·点位", ""),
                  **(e.get("rule_confirmed") or {})}
            for fld, key2, cur in (
                    ("口径·配置方式", "kind", rc.get("kind", "")),
                    ("口径·点位", "at", rc.get("at", ""))):
                remote = _txt(r.get(fld))
                if remote and remote != str(cur or "").strip():
                    stats[fld] += 1
                    changes.append({"表": "2 配置目录", "键": " / ".join(key),
                                    "字段": fld, "本地": str(cur or ""),
                                    "飞书": remote, "类型": "口径"})
            rq = _num(r.get("口径·每单位数量"))
            cq = rc.get("qty")
            if rq is not None and (cq is None or abs(rq - float(cq)) > 1e-9):
                stats["口径·每单位数量"] += 1
                changes.append({"表": "2 配置目录", "键": " / ".join(key),
                                "字段": "口径·每单位数量", "本地": str(cq or ""),
                                "飞书": str(rq), "类型": "口径"})
    return {"changes": changes, "stats": dict(stats)}


def check(changes: list[dict]) -> list[str]:
    """写回前的守卫。返回问题列表，非空即拒写。"""
    by_key: dict[str, dict[str, str]] = {}
    for c in changes:
        if c["类型"] in ("口径", "物料"):
            by_key.setdefault(f"{c['表']}||{c['键']}", {})[c["字段"]] = c["飞书"]
    problems = []
    for k, f in by_key.items():
        tbl, key = k.split("||", 1)
        kind = f.get("口径·配置方式")
        if kind == "每点位 N" and not f.get("口径·点位"):
            problems.append(
                f"{key}：配置方式取「每点位 N」但没填「点位」—— "
                f"不知道配在门口还是厨房，算不出数量")
        if kind in NEEDS_QTY and "口径·每单位数量" in f:
            if (float(f["口径·每单位数量"]) or 0) <= 0:
                problems.append(f"{key}：配置方式「{kind}」但每单位数量为 0")
        if f.get("商务·是否可售") in ("停售", "内部专用"):
            problems.append(
                f"{key}：改为「{f['商务·是否可售']}」—— **须确认它是否仍在配置目录里**。"
                f"停售/内部专用的设备留在目录中，配上去之前没人会发现；"
                f"请同时提交移除该设备的变更提案，或说明为何保留。")
    return problems


def write_back(root: Path, changes: list[dict]) -> list[str]:
    """把口径与物料改动写回 devices/*.yaml。"""
    d = root / "bom" / "devices"
    mp, cp = d / "materials.yaml", d / "catalog.yaml"
    mdoc = yaml.safe_load(mp.read_text(encoding="utf-8"))
    cdoc = yaml.safe_load(cp.read_text(encoding="utf-8"))
    by_code = {m["code"]: m for m in mdoc["materials"]}
    name_of = {m["code"]: m["name"] for m in mdoc["materials"]}
    n_m = n_c = 0
    for c in changes:
        if c["类型"] == "物料":
            m = by_code.get(c["键"])
            if not m:
                continue
            if c["字段"] == "商务·是否可售":
                m["sellable"] = c["飞书"]
            elif c["字段"] == "商务·参考对比厂家":
                m["ref_vendors"] = [x for x in
                                    c["飞书"].replace("，", "、").split("、") if x.strip()]
            n_m += 1
        elif c["类型"] == "口径":
            sc, dev = c["键"].split(" / ", 1)
            for e in cdoc["entries"]:
                if e["scene"] == sc and name_of.get(e["material"]) == dev:
                    # **落在条目级的 `rule_confirmed`，不写进 `placements[]`。**
                    # push 时按（场景 × 物料）把多条 placement 合成一行
                    # （205 → 135），合并那一步就丢了「这行对应哪条 placement」；
                    # 硬摊回去只能靠猜，而猜错不会有任何地方报错。
                    # 条目级与推上去的粒度严格对齐，placements 保留各自的原始
                    # note 与解析结果供追溯，两者不互相覆盖。
                    rc = e.setdefault("rule_confirmed", {})
                    if c["字段"] == "口径·配置方式":
                        rc["kind"] = c["飞书"]
                    elif c["字段"] == "口径·点位":
                        rc["at"] = c["飞书"]
                    elif c["字段"] == "口径·每单位数量":
                        rc["qty"] = float(c["飞书"])
                    rc["source"] = "飞书设备 BOM 共创（lark_device_pull --write）"
                    n_c += 1
    if n_m:
        mp.write_text(yaml.safe_dump(mdoc, allow_unicode=True, sort_keys=False,
                                     default_flow_style=False, width=100),
                      encoding="utf-8")
    if n_c:
        cp.write_text(yaml.safe_dump(cdoc, allow_unicode=True, sort_keys=False,
                                     default_flow_style=False, width=100),
                      encoding="utf-8")
    return [f"[设备回写] 物料 {n_m} 处、配置口径 {n_c} 处已落 devices/*.yaml"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--write", action="store_true",
                    help="把飞书改动写回 devices/*.yaml（默认只报差异）")
    a = ap.parse_args()

    cfg = json.loads((a.root / "bom" / "devices" / "lark-sync.json")
                     .read_text(encoding="utf-8"))
    rep = pull(a.root, cfg)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "device-pull-report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    ch = rep["changes"]
    print(f"设备 BOM pull 完成（**未改本地**）")
    print(f"  飞书侧改动 {len(ch)} 处：{rep['stats'] or '（无）'}")
    if ch:
        lines = ["# 设备 BOM · 飞书共创改动（未落本地）", "",
                 "| 表 | 键 | 字段 | 本地 | 飞书 |", "| - | - | - | - | - |"]
        lines += [f"| {c['表']} | {c['键']} | {c['字段']} | "
                  f"{c['本地'][:60]} | {c['飞书'][:60]} |" for c in ch[:300]]
        (a.out / "device-pull-diff.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"  → {a.out}/device-pull-diff.md")
    else:
        print("  飞书侧无改动 —— 可以直接重推")

    problems = check(ch)
    if problems:
        print(f"\n⚠ {len(problems)} 处不满足写回条件：")
        for p in problems[:20]:
            print(f"  · {p}")
    if a.write:
        if problems:
            raise SystemExit("\n⛔ 存在上列问题，未写任何文件。")
        for ln in write_back(a.root, ch):
            print(f"  {ln}")
        print("  下一步：重跑 device_config_build 与 quote_generate_liuzhou")
        print("  ⚠ **口径当前不进造价**：device_config_build 读的是每园实配数量"
              "（`placements[].qty_by_garden`），不读 `rule_confirmed`。"
              "口径是为将来替换逐园硬编码数量准备的共创产物 —— 填了不改钱，"
              "但它是把 205 条逐园数量收敛成规则的唯一入口。")
    elif any(c["类型"] in ("口径", "物料") for c in ch):
        print("\n  ⚠ 加 --write 才会写回 devices/*.yaml；"
              "不写回就重推的话，这些改动会被本地值覆盖掉。")


if __name__ == "__main__":
    main()
