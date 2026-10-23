"""bom_apply_reuse_v2 —— 把丙本 03 的复用度档位落到 v2 分层的 taxonomy。

## 与 quote_sync.write_taxonomy_reuse 的关系

同一件事，两处差别：

  · **路径**。v1 的 taxonomy 是一个扁平文件 `bom/taxonomy.yaml`；v2 按层分散
    （`bom/shared/platform-base/`、`bom/verticals/<vert>/apps/` …）。丙本 03 一张表
    横跨所有层，所以要**按子系统路由到拥有它的那份 taxonomy**。
    子系统名在两个行业下可能重名（知识工程/本体工程/数据工程/行业垂直模型/
    行业智能体 在幼教与康养各有一份），所以必须给 `--vertical` 消歧 ——
    猜错了不会报错，只会把幼教的档位写到康养的 taxonomy 上。

  · **无依据的折扣怎么处置**。quote_sync 是整表 SystemExit：一行没写依据，
    整次同步都不落。这在「一张表一个人填」时是对的，但丙本 03 现在是多人
    分模块填，一个人漏写依据会把别人写好的一起挡住。
    这里改成**逐行拒绝**：那一行退回标准缺省「低」，其余照落，并在报告里
    点名。理由是「拒绝一个没依据的折扣」的结果是**回到标准缺省**，
    那是一个完整且保守的状态，不是半写状态 —— 与「写一半」的性质不同。

## 为什么无依据的折扣必须拒绝

复用度低 = 1.0（不打折）是表3 注2 的缺省，**未声明即为低**。取中（2/3）
或高（1/3）是往下砍价，而档位本身属推断（表1 注3 只说「复用系数根据开发
内容确定」）。没有出处的折扣，财评当「随意定价」看 —— 而且这类行在表上
长得和写了依据的一模一样，不点名就看不出来。

反方向不设门槛：改成「低」是**回到缺省**，本就不需要依据，落地方式是
把那个模块从 taxonomy 的 modules 里删掉（未声明即为低）。

## 只重建，不叠加

每个子系统的 modules 块用**该子系统的全部行**重建，不在旧块上增量改。
叠加会把已经不存在的模块名留在里面 —— 而 quote_generate 的 stray 守卫
只查「taxonomy 有、BOM 无」，留下的孤儿会在那道关上炸，离填表已经很远了。
所以这里自己先查一遍。

用法：
    python3 bom_apply_reuse_v2.py --root <数据仓> --vertical preschool \\
        --workbook <丙本.xlsx> [--apply]
    不带 --apply 为 dry-run。
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

import quote_sync as qs

#: 档位 → BOM 的 maturity 词。低是缺省，不写进 taxonomy。
L2M = {"高": "existing", "中": "partial", "低": "new"}


def owners(root: Path, vertical: str) -> dict[str, Path]:
    """子系统名 → 拥有它的 taxonomy 文件。

    重名子系统（幼教与康养都有「知识工程」）按 `--vertical` 取舍；
    共享层没有行业维度，直接收。**不猜** —— 落错层不报错，只是写到了
    另一个行业的 taxonomy 上，那个行业下次出表才会用上一个不属于它的档位。
    """
    out: dict[str, Path] = {}
    for t in sorted(root.glob("bom/**/taxonomy.yaml")):
        rel = t.relative_to(root).as_posix()
        if "/verticals/" in rel and f"/verticals/{vertical}/" not in rel:
            continue
        tax = yaml.safe_load(t.read_text(encoding="utf-8")) or {}
        for line in (tax.get("product_lines") or {}).values():
            for s in ((line or {}).get("systems") or {}):
                if s in out and out[s] != t:
                    raise SystemExit(
                        f"⛔ 子系统「{s}」在两份 taxonomy 里都有："
                        f"{out[s]} 与 {t}。落错一边不会报错 —— 请先消歧。")
                out[s] = t
    return out


def bom_modules(root: Path, tax_path: Path) -> dict[str, set[str]]:
    """该层 BOM 里每个子系统**实际存在**的功能模块（path.l1）。"""
    out: dict[str, set[str]] = defaultdict(set)
    for f in sorted((tax_path.parent / "items").glob("*.yaml")):
        for i in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("items") or []:
            if i.get("status") == "deprecated":
                continue
            p = i.get("path") or {}
            out[p.get("system") or f.stem].add(p.get("l1") or "")
    return out


def plan(rows: list[dict], own: dict[str, Path], root: Path) -> dict[str, Any]:
    fp = [r for r in rows if r.get("kind") == "fp"]
    other = Counter(r.get("kind") for r in rows if r.get("kind") != "fp")

    bysys: dict[str, dict[str, dict]] = defaultdict(dict)
    for r in fp:
        bysys[r["system"]][r["module"]] = r

    unknown = sorted(set(bysys) - set(own))
    if unknown:
        raise SystemExit(
            f"⛔ 丙本 03 里的子系统 {unknown} 在本仓 taxonomy 里找不到归属。\n"
            f"  名字对不上不会报错，只会静默不生效 —— 请核对子系统名或 --vertical。")

    rejected: list[dict] = []
    stray: list[str] = []
    per_file: dict[Path, dict[str, dict]] = defaultdict(dict)
    changes: list[dict] = []
    bmods_cache: dict[Path, dict[str, set[str]]] = {}

    for sysname, mods in sorted(bysys.items()):
        t = own[sysname]
        if t not in bmods_cache:
            bmods_cache[t] = bom_modules(root, t)
        real = bmods_cache[t].get(sysname, set())
        keep: dict[str, dict] = {}
        for mod, r in sorted(mods.items()):
            lvl, basis = r["level"], (r["basis"] or "").strip()
            if lvl != "低" and not basis:
                # 逐行拒绝，退回缺省「低」。**记下来**：这一行在表上
                # 和写了依据的行长得一模一样，不点名就看不出被拒了。
                rejected.append({"system": sysname, "module": mod,
                                 "was": r["was"], "want": lvl, "ufp": r["ufp"]})
                lvl = "低"
            if lvl == "低":
                continue
            if mod not in real:
                stray.append(f"{sysname} · {mod}")
                continue
            keep[mod] = {"maturity": L2M[lvl],
                         **({"maturity_basis": basis} if basis else {})}
        per_file[t][sysname] = keep
        # 与现状对比，报出真正的档位变化
        tax = yaml.safe_load(t.read_text(encoding="utf-8")) or {}
        cur: dict[str, str] = {}
        for line in (tax.get("product_lines") or {}).values():
            node = ((line or {}).get("systems") or {}).get(sysname)
            if isinstance(node, dict):
                cur = {m: (v or {}).get("maturity")
                       for m, v in (node.get("modules") or {}).items()}
        for mod in sorted(set(cur) | set(keep)):
            a = cur.get(mod, "new")
            b = (keep.get(mod) or {}).get("maturity", "new")
            if a != b:
                changes.append({"system": sysname, "module": mod,
                                "from": a, "to": b,
                                "ufp": (mods.get(mod) or {}).get("ufp", 0)})

    if stray:
        raise SystemExit(
            f"⛔ 丙本 03 声明了非缺省档位，但这些功能模块在当前 BOM 里已不存在：\n"
            + "".join(f"    {s}\n" for s in stray)
            + f"  taxonomy 里留下 BOM 没有的模块名，会在出表时被 stray 守卫拦下，\n"
            f"  而那时离填表已经很远。丙本可能是旧版 BOM 生成的 —— 请用当前\n"
            f"  BOM 重出丙本后再填，或核对模块是否改名。")
    return {"per_file": per_file, "rejected": rejected, "changes": changes,
            "fp_rows": len(fp), "other_kinds": dict(other)}


def write(per_file: dict[Path, dict[str, dict]]) -> list[str]:
    """落盘。**语义没变的文件不重写。**

    丙本 03 横跨所有层，所以每次同步都会「重建」每一层的 modules 块 ——
    绝大多数层其实一个字没改。但 `yaml.safe_dump` 会按自己的换行宽度重排
    整个文件，于是 diff 里出现 202 行改动而语义零变化。
    这种噪音的代价是具体的：复核的人要在 200 行格式变动里找那 8 行真改动，
    找不到就只能整块信任 —— 那道复核就白设了。
    """
    notes = []
    for t, bysys in sorted(per_file.items()):
        before = yaml.safe_load(t.read_text(encoding="utf-8"))
        tax = yaml.safe_load(t.read_text(encoding="utf-8"))
        for line in (tax.get("product_lines") or {}).values():
            systems = (line or {}).get("systems") or {}
            for sysname, keep in bysys.items():
                if sysname not in systems:
                    continue
                node = systems[sysname] if isinstance(systems[sysname], dict) else {}
                # 子系统级 maturity 不参与计价，留着只会误导人
                node.pop("maturity", None)
                node.pop("maturity_basis", None)
                if keep:
                    node["modules"] = keep
                else:
                    node.pop("modules", None)
                systems[sysname] = node
                c = Counter(v["maturity"] for v in keep.values())
                notes.append(f"{t.parent.name}/{sysname}: 声明 {len(keep)} 个模块"
                             f"（{dict(c)}），其余按缺省「低」")
        if tax == before:
            notes.append(f"{t.relative_to(t.parents[len(t.parents) - 1])}"
                         f" 语义未变，未重写")
            continue
        t.write_text(yaml.safe_dump(tax, allow_unicode=True, sort_keys=False,
                                    default_flow_style=False, width=100),
                     encoding="utf-8")
        notes.append(f"✎ 写入 {t}")
    return notes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--vertical", required=True)
    ap.add_argument("--workbook", required=True, type=Path)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    root = a.root.resolve()
    rows = qs.read_reuse_modules_all(a.workbook)
    if not rows:
        raise SystemExit(f"⛔ {a.workbook.name} 里没有「03_复用度模块清单」，"
                         f"或表头缺「子系统/功能模块/复用度」列")
    p = plan(rows, owners(root, a.vertical), root)

    print(f"丙本 03：{len(rows)} 行，其中功能点法 {p['fp_rows']} 行"
          + (f"；另有 {p['other_kinds']}（工作量法，本工具不处理）"
             if p["other_kinds"] else ""))

    if p["rejected"]:
        print(f"\n⛔ 拒绝 {len(p['rejected'])} 个无依据的折扣（退回缺省「低」）：")
        for r in p["rejected"]:
            print(f"    {r['system']:8} {r['module']:16} {r['was']} → {r['want']}"
                  f"　UFP {r['ufp']}　「取值依据」列为空")
        print("    表3 注2 的缺省是低（1.0），偏离缺省是降价方向，必须列明依据。")
        print("    请在丙本 03 的「取值依据」列补齐后重跑本工具。")

    if p["changes"]:
        print(f"\n生效的档位变化 {len(p['changes'])} 个：")
        M2L = {v: k for k, v in L2M.items()}
        for c in p["changes"]:
            print(f"    {c['system']:8} {c['module']:16} "
                  f"{M2L.get(c['from'], c['from'])} → {M2L.get(c['to'], c['to'])}"
                  f"　UFP {c['ufp']}")
    else:
        print("\n没有生效的档位变化。")

    if not a.apply:
        print("\ndry-run，未改 taxonomy。加 --apply 落盘。")
        return
    for n in write(p["per_file"]):
        print(f"  {n}")


if __name__ == "__main__":
    main()
