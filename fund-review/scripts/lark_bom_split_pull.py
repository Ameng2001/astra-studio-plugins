"""lark_bom_split_pull —— 从分表结构的飞书 base 拉取共创改动，出 diff 报告。

**不改 BOM。** 飞书上的一次误点击不该直接改变报价基线；落实走人工确认 +
`bom_apply` 并记 CHANGELOG。

## 靠 id 对齐，不靠位置

条目 id 在产品架构重挂时**一律不动**（`bom_retax` 的纪律），所以哪怕
本地已经从 5 个系统重组成 15 个、条目也换了文件，飞书上那一行仍然能
凭 `条目ID` 找回对应条目。这正是当初不重编 id 的原因。

## 比什么

  描述        方案/产品补写的功能描述 —— 这是共创的主要产出，
              122 条「★待补描述」补完后功能点才数得出来
  共创状态    改成「已处理」的，说明那一条对方认为已经完成
  功能点类型  飞书侧改判的类型（少见但会有）
  裁决表      C-逻辑文件维护方 的裁决结论、变更提案的新增行

只报**有差异的**。全表逐行贴出来没人看，也淹没真正要处理的。

用法：
    python3 lark_bom_split_pull.py --bom <dir> --config <lark-sync.json> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import re
import yaml
from collections import Counter
from pathlib import Path
from typing import Any

from bom_schema import Bom
from lark_bom_split_push import STATUS_FIELD
from lark_table import lark

BATCH = 200


def list_all(bt: str, tid: str) -> list[dict[str, Any]]:
    """全表取回，**取到取空为止**。只取一页会让「没有改动」和
    「改动在第二页」看起来一样。"""
    out: list[dict] = []
    offset = 0
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH), "--offset", str(offset))
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


def _isnum(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _txt(v: Any) -> str:
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            return "".join(str(x.get("text", "")) for x in v).strip()
        return "、".join(str(x) for x in v).strip()
    return "" if v is None else str(v).strip()


def pull(bom: Bom, cfg: dict, out: Path) -> dict[str, Any]:
    bt = cfg["base_token"]
    by_id = {i.id: i for i in bom.active()}
    changes: list[dict] = []
    stats = Counter()
    seen_ids = set()

    # 条目表 = A/B/C 前缀那些。**判据要跟着表名走** —— 之前写的是
    # `n[:1].isdigit()`（旧的「1 市平台」命名），改成 A/B/C 之后
    # 它一张表都读不到，然后报「飞书侧无改动」。那是静默零，不是没改动。
    item_tables = {n: t for n, t in cfg["tables"].items()
                   if re.match(r"^[ABC]\d+ ", n)}
    if not item_tables:
        raise ValueError(
            f"配置里没有条目表（A/B/C 前缀）—— 现有 {sorted(cfg['tables'])}。"
            f"读不到表和「没有改动」看起来一样，所以直接报错")
    for name, tid in sorted(item_tables.items()):
        for r in list_all(bt, tid):
            iid = _txt(r.get("条目ID"))
            if not iid:
                continue
            seen_ids.add(iid)
            it = by_id.get(iid)
            if it is None:
                stats["飞书有本地无"] += 1
                changes.append({"表": name, "条目ID": iid, "字段": "-",
                                "本地": "（本地已删除或改版）",
                                "飞书": _txt(r.get("功能点")), "类型": "孤儿行"})
                continue
            for field, local in (("需求描述", it.description or ""),
                                 ("参数·功能点类型",
                                  it.nesma.type if it.nesma else "")):
                remote = _txt(r.get(field))
                if remote and remote != (local or "").strip():
                    stats[field] += 1
                    changes.append({"表": name, "条目ID": iid, "字段": field,
                                    "本地": (local or "")[:120],
                                    "飞书": remote[:120], "飞书全文": remote,
                                    "类型": "改写"})
            # ---- 工作量测算参数与复用度（C 组表才有这几列） ----
            # 不读回来的字段，在飞书上就是「看着能填、填了没用」——
            # `参数·软件类别` 当初正是这个形状，最后靠整列删掉才解决。
            # 推上去可编辑，就必须读得回来。
            eb = it.effort_basis or {}
            # 「词元数目档位」是 C4 方案侧 2026-08-14 重构口径时建的数量列
            # （人月 = 档位 × 单位工作量），语义就是 qty，只是列名跟着新量纲走。
            # 映射到同一个键：两列并存时以新列为准，报告字段名保留飞书叫法。
            for field, key, cast in (
                    ("工作量·可核查量纲", "unit", str),
                    ("工作量·数量", "qty", float),
                    ("词元数目档位", "qty", float),
                    ("工作量·单位工作量", "per_unit_mm", float),
                    # C7 把单位工作量拆成「基准 + 单位」两列显式化（模型公式
                    # 数量×[基准3.0+数据复杂度×0.5+模型复杂度×0.5]），收作举证
                    # 字段，**不参与人月计算** —— 人月仍由 qty × per_unit_mm 得出。
                    ("工作量·模型基准工作量", "model_base_mm", float),
                    ("工作量·模型单位工作量", "model_unit_mm", float),
                    # C4 的落档举证：语料量级决定词元档位，是档位数唯一的证据来源
                    ("语料量级（落档依据）", "corpus_scale", str),
                    ("工作量·测算依据", "basis", str),
                    # 「说明」是**逐条的复杂度量化说明**（取值规则 + 本条落在
                    # 哪一档 + 为什么），最长 1447 字，是这批人月最实的举证。
                    # ⚠ 一律不截断：早先某次同步用了 [:300]，34 条被砍在
                    # 句子中间，"结构复杂度 = 1" 后面直接没了。举证被截半句
                    # 比没有还糟 —— 评审看到的是一个明显写了一半的理由。
                    ("说明", "counted_from", str),
                    ("工作量.数量.说明", "counted_from", str)):
                if field not in r:
                    continue
                remote = _txt(r.get(field))
                if remote == "":
                    continue
                local = eb.get(key)
                same = (abs(float(remote) - float(local or 0)) < 1e-9
                        if cast is float and _isnum(remote)
                        else remote == str(local or "").strip())
                if not same:
                    stats[field] += 1
                    changes.append({"表": name, "条目ID": iid, "字段": field,
                                    "本地": str(local or "")[:120],
                                    "飞书": remote[:120], "飞书全文": remote,
                                    "类型": "工作量参数"})
            for field, key in (("参数·复用度", "level"), ("参数·复用依据", "basis")):
                if field not in r:
                    continue
                remote = _txt(r.get(field))
                local = (it.reuse or {}).get(key) or ("低" if key == "level" else "")
                if remote and remote != str(local).strip():
                    stats[field] += 1
                    changes.append({"表": name, "条目ID": iid, "字段": field,
                                    "本地": str(local)[:120],
                                    "飞书": remote[:120], "飞书全文": remote,
                                    "类型": "复用度"})
            st = _txt(r.get(STATUS_FIELD))
            if st and st != (it.coauthor_status or "已判型"):
                stats[STATUS_FIELD] += 1
                changes.append({"表": name, "条目ID": iid, "字段": STATUS_FIELD,
                                "本地": it.coauthor_status or "已判型",
                                "飞书": st, "类型": "状态"})

    missing = sorted(set(by_id) - seen_ids)
    decisions: dict[str, Any] = {}
    for tname, col, pending in (("C-逻辑文件维护方", "裁决结论", "未裁决"),
                                ("变更提案", "处理状态", "待处理")):
        tid = cfg["tables"].get(tname)
        if not tid:
            continue
        rows = list_all(bt, tid)
        done = [r for r in rows if _txt(r.get(col)) and _txt(r.get(col)) != pending]
        decisions[tname] = {"total": len(rows), "decided": len(done),
                            "rows": [{k: _txt(v) for k, v in r.items()}
                                     for r in done[:60]]}
    return {"changes": changes, "stats": dict(stats), "decisions": decisions,
            "missing_in_lark": missing}


def write_back(bom_dir: Path, changes: list[dict]) -> list[str]:
    """把飞书侧改的**工作量参数与复用度**写回 bom/items/*.yaml。

    守卫与丙本 03 那条链路同构（quote_sync.write_effort_basis）：

      · 改了数量/单位工作量 → 「工作量·测算依据」必须一起改。
        留在表上的旧依据解释的是改之前那个数，比空着更容易被当成编造。
      · 复用度非「低」→ 必须有「参数·复用依据」。
        表1 注3 借表3 注2 档位属推断，偏离缺省必须列明依据；
        这是降价方向，没出处的折扣评审当「随意定价」看。

    **两个录入面同一套守卫**，否则从飞书进来的数会绕过丙本那条链的检查。
    """
    import glob as _g
    byid: dict[str, dict[str, str]] = {}
    for c in changes:
        if c["类型"] in ("工作量参数", "复用度"):
            # **写盘取「飞书全文」，不取「飞书」。** 后者为了 pull-diff.md
            # 可读已截到 120 字 —— 拿它写回等于把飞书的长文本裁短再存，
            # 实测「说明」被从 300 字再砍到 120 字，而屏幕上一切正常：
            # 报告显示的就是那 120 字，看不出少了东西。展示副本永远不能回流。
            byid.setdefault(c["条目ID"], {})[c["字段"]] = c.get("飞书全文",
                                                              c["飞书"])
    if not byid:
        return []
    _by_local: dict[str, dict] = {}
    for _fp in _g.glob(str(bom_dir / "items" / "*.yaml")):
        for _i in (yaml.safe_load(open(_fp, encoding="utf-8")) or {}).get("items") or []:
            if _i.get("id") in byid:
                _by_local[_i["id"]] = _i
    NUMY = {"工作量·数量", "词元数目档位", "工作量·单位工作量"}
    for iid, f in byid.items():
        if (f.keys() & NUMY) and "工作量·测算依据" not in f:
            raise SystemExit(
                f"⛔ {iid} 在飞书改了 {sorted(f.keys() & NUMY)}，"
                f"但「工作量·测算依据」一个字没动。\n"
                f"  标准不规定人月如何估算（表1 注1 只给方法），这个数唯一的"
                f"支撑就是依据本身；留着解释旧数的那句话比空着更危险。")
        # **反向同样要拦**：依据改了、数量没改。原先只有正向守卫，结果飞书
        # 把 C4 的叙述从「知识主题域」改写成「词元数目挡位 × …= 1.50 人月」
        # 而 `工作量·数量` 一列没动，回写后 BOM 里 qty=1、man_months=0.75，
        # 依据却自称 1.50 —— 乙本会把这两个数印在同一行上。
        # 依据里自称的人月与本地实际人月对不上，就是同一件事被判了两次。
        if "工作量·测算依据" in f and not (f.keys() & NUMY):
            _m = re.search(r"=\s*([\d.]+)\s*人月", str(f["工作量·测算依据"]))
            _mm = (_by_local.get(iid, {}).get("effort_basis") or {}).get("man_months")
            if _m and _mm is not None and abs(float(_m.group(1)) - float(_mm)) > 1e-6:
                raise SystemExit(
                    f"⛔ {iid} 飞书改了「工作量·测算依据」，但「工作量·数量 / "
                    f"单位工作量」一个都没动。\n"
                    f"  新依据自称 {_m.group(1)} 人月，本地实际是 {_mm} 人月 —— "
                    f"落盘会让 BOM 自相矛盾：数据一个值、举证另一个值。\n"
                    f"  改口径要连数量一起改。请在飞书把「工作量·数量」按新口径"
                    f"（如词元数目挡位）填上再拉。")
        if f.get("参数·复用度") in ("高", "中") and not f.get("参数·复用依据"):
            raise SystemExit(
                f"⛔ {iid} 复用度取「{f['参数·复用度']}」但没写「参数·复用依据」。\n"
                f"  表1 注3「复用系数根据开发内容确定」，档位借用表3 注2（属推断），"
                f"偏离缺省必须列明依据。")
    # **依据无处可挂的预检放在写盘之前。** 原先这一条在逐文件的写循环里 raise，
    # 先处理的文件已经落盘 —— 半写比不写更难查：下次 pull 只看到剩下的差异，
    # 已落的那部分看起来像"本来就一致"。
    # 「低 + 依据」是合法状态：方案侧评估后判定不打折（如 C5/C6 65 条
    # "需重新建模、仅可复用通用模板"→ 低）。依据此时的价值是**证明评估过了**，
    # 不是为折扣作证 —— 存进 reuse 节点（level=低，系数 1，不影响计价）。
    # 只拦真正无处可挂的：飞书连「参数·复用度」这一列都没有的表。
    # seen = 在本地找到的（用于 stray 检查）；wrote = **真的改了字段的**。
    # 两者必须分开统计：原先日志报的是 seen，于是 65 条被静默丢弃的
    # 「复用依据」照样计进"已写入"，日志说 109 实际只落了 44。
    notes, seen, wrote = [], set(), set()
    for fp in map(Path, _g.glob(str(bom_dir / "items" / "*.yaml"))):
        doc = yaml.safe_load(fp.read_text(encoding="utf-8"))
        dirty = False
        for i in doc.get("items") or []:
            f = byid.get(i.get("id"))
            if not f:
                continue
            seen.add(i["id"])
            eb = i.setdefault("effort_basis", {})
            for fld, key, cast in (("工作量·可核查量纲", "unit", str),
                                   ("工作量·数量", "qty", float),
                                   ("词元数目档位", "qty", float),
                                   ("工作量·单位工作量", "per_unit_mm", float),
                                   ("工作量·模型基准工作量", "model_base_mm", float),
                                   ("工作量·模型单位工作量", "model_unit_mm", float),
                                   ("语料量级（落档依据）", "corpus_scale", str),
                                   ("工作量·测算依据", "basis", str),
                                   ("说明", "counted_from", str),
                                   ("工作量.数量.说明", "counted_from", str)):
                if fld in f:
                    eb[key] = cast(f[fld]); dirty = True
            if f.keys() & NUMY:
                # **人月由引擎算，不从飞书读** —— 飞书上那一列是展示值，
                # 数量/单位工作量改了它不会自动重算，读回来会写进一个对不上的人月。
                eb["man_months"] = round(float(eb.get("qty") or 0)
                                         * float(eb.get("per_unit_mm") or 0), 4)
            if "参数·复用度" in f:
                lv = f["参数·复用度"]
                if lv == "低":
                    _bs = f.get("参数·复用依据")
                    if _bs:
                        # 低档也存依据：这是「已评估、结论不打折」的凭证，
                        # 丢掉它，下次 pull 又会报 109 条"未同步"。
                        i["reuse"] = {"level": "低", "basis": _bs}
                    else:
                        i.pop("reuse", None)
                else:
                    i["reuse"] = {"level": lv,
                                  "basis": f.get("参数·复用依据",
                                                 (i.get("reuse") or {}).get("basis", ""))}
                dirty = True
            elif "参数·复用依据" in f:
                # 复用度列在飞书有值但与本地一致（如都是「低」），diff 只剩依据。
                # 本地无 reuse 节点时按「低 + 依据」建节点存凭证。
                # **只补依据、档位没动的也要写。** 原先这一支挂在「复用度变了」
                # 里面，于是飞书补的 65 条依据被静默丢弃，而回写日志数的是
                # 「碰到的条目数」，照样报成功 —— 与「uploaded 计数当成写入成功」
                # 同一类错：计数证明的是尝试，不是结果。
                # 补依据是**必须落**的方向：表1 注3 借表3 注2 的档位属推断，
                # 复用度非「低」而无依据的折扣，评审按「随意定价」看。
                _r = i.get("reuse")
                if _r:
                    _r["basis"] = f["参数·复用依据"]
                else:
                    i["reuse"] = {"level": "低", "basis": f["参数·复用依据"]}
                dirty = True

            if dirty:
                eb["source"] = "飞书 BOM 共创录入（lark_bom_split_pull --write-bom）"
                wrote.add(i["id"])
        if dirty:
            fp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                                         default_flow_style=False, width=100),
                          encoding="utf-8")
    stray = sorted(set(byid) - seen)
    if stray:
        raise SystemExit(
            f"⛔ 飞书侧改动的条目 {stray} 在 bom/items/*.yaml 里找不到 —— "
            f"条目ID 对不上不会报错，只会静默不生效。")
    _skip = sorted(set(byid) - wrote)
    if _skip:
        raise SystemExit(
            f"⛔ {len(_skip)} 条飞书改动**一个字段都没落盘**，例如 {_skip[:5]}。\n"
            f"  报「已写入」而实际没写，比报错更危险 —— 下次重推会用本地旧值"
            f"覆盖飞书这些改动，而两边都以为已经同步。")
    notes.append(f"[飞书回写] {len(wrote)} 条条目的工作量参数/复用度已写入 bom/items")
    return notes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--write-bom", action="store_true",
                    help="把飞书侧改的工作量参数与复用度写回 bom/items（默认只报差异）")
    a = ap.parse_args()

    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    bom = Bom.load(a.bom)
    rep = pull(bom, cfg, a.out)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "pull-report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(rep["changes"])
    print(f"pull 完成（**未改 BOM**）　本地 v{bom.version}")
    print(f"  飞书侧改动 {n} 处：{rep['stats'] or '（无）'}")
    for t, d in rep["decisions"].items():
        print(f"  {t}：{d['decided']}/{d['total']} 已裁决")
    if rep["missing_in_lark"]:
        print(f"  本地有、飞书无 {len(rep['missing_in_lark'])} 条 —— "
              f"多为本地新增（逻辑文件重建、拆分）；重推后即同步")
    if n:
        lines = ["# 飞书共创改动（未落 BOM）", "",
                 "| 表 | 条目ID | 字段 | 本地 | 飞书 |", "| - | - | - | - | - |"]
        lines += [f"| {c['表']} | {c['条目ID']} | {c['字段']} | "
                  f"{c['本地'][:60]} | {c['飞书'][:60]} |" for c in rep["changes"][:200]]
        (a.out / "pull-diff.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"  → {a.out}/pull-diff.md")
    else:
        print("  飞书侧无改动 —— 可以直接重推")
    if a.write_bom:
        for ln in write_back(a.bom, rep["changes"]):
            print(f"  {ln}")
    elif any(c["类型"] in ("工作量参数", "复用度") for c in rep["changes"]):
        print("  ⚠ 其中有工作量参数/复用度改动 —— 加 --write-bom 才会写回 bom/items；"
              "不写回就重推的话，这些改动会被本地值覆盖掉。")


if __name__ == "__main__":
    main()
