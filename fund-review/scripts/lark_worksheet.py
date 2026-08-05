"""lark_worksheet — BOM ↔ 飞书《功能点计算书》双向同步。

与 `lark_bom_sync` 的分工：那个同步的是**质检裁决表**（占位/实体核对/类型裁决），
这个同步的是**方案人员的工作台** —— 结构对齐行业通用的「功能点计算书」，
他们直接编辑 BOM 本体，而不是回答我的质检问题。

表结构（对齐《样例表.xlsx》）：
    子系统 → 一~四级模块 → 功能点计数项名称 → 功能描述
    → 类别 → UFP(自动) → 应用类型 → 重用程度 → 修改类型 → US(自动)
    → 备注 → 条目ID

    US = UFP × 规模变更因子 × 重用系数 × 修改类型系数 × 应用类型系数，
    与引擎 `adjusted_fp` 口径对齐。**应用类型不能漏** —— AI 类子系统
    因子 1.5、业务处理 1.0，漏了会把 5 个 AI 子系统整体少算三分之一。

**一个 Base、每个子系统一张表**，外加一张「0 参数表」存权重与系数。
UFP/US 是跨表引用参数表的公式字段 —— 改系数只改参数表一处，15 张表联动。

为什么不拆成 15 个 Base：拆文件后「本项目一共多少 UFP」要跨文件手工汇总，
而那正是最容易出错的地方（P0 那 ¥421.6 万就是汇总出的错）。
单 Base 多表保住了汇总能力，飞书高级权限仍可把角色限定到具体表。

**方向仍不对称**：push 覆盖，pull 只出 diff 报告不改 BOM。
飞书上的一次误编辑不应该直接改变报价基线。

三件 `lark_bom_sync` 没有处理的事：
  1. 字段回映射（类别/名称/描述/层级 → BOM 字段）
  2. **方案人员新增的行** —— 无条目ID，需分配新 id
  3. 重用程度/修改类型属 **L2 交付决策**，不写进 BOM，单独导出给 deal 层

用法：
    python3 lark_worksheet.py push --bom <dir> --config <lark-worksheet.json>
    python3 lark_worksheet.py pull --bom <dir> --config <...> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bom_schema import FP_COUNTED_CLASSES, Bom, BomItem, Nesma, Path_

BATCH = 200

#: 样例表用 IFPUG 的 EIF，山东标准用 ELF —— 同一概念，双向映射
TYPE_TO_SHEET = {"ELF": "EIF"}
TYPE_FROM_SHEET = {"EIF": "ELF"}

#: 重用程度/修改类型属 L2 交付决策，不进 BOM
REUSE_COEF = {"低": 1.0, "中": 2 / 3, "高": 1 / 3}
CHANGE_COEF = {"新增": 1.0, "修改": 0.8, "删除": 0.2}

FIELDS = ["子系统", "一级模块", "二级模块", "三级模块", "四级模块",
          "功能点计数项名称", "功能描述", "类别", "应用类型", "重用程度",
          "修改类型", "备注", "条目ID"]


def lark(*args: str) -> dict[str, Any]:
    """调用 lark-cli 并解析 JSON。

    用 `--format json` 而不是 `--json` 指定输出格式 —— 后者在
    `+record-batch-create` / `+record-delete` 等命令上是**载荷参数**，
    追加会变成 `--json <载荷> --json`，末尾那个没有参数直接报错。
    """
    r = subprocess.run(["lark-cli", *args, "--format", "json"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"raw": (r.stdout or r.stderr)[:300]}}


def _row(it: BomItem) -> dict[str, Any]:
    return {
        "子系统": it.path.system, "一级模块": it.path.l1 or "",
        "二级模块": it.path.l2 or "", "三级模块": it.path.l3 or "",
        "四级模块": it.path.l4 or "",
        "功能点计数项名称": it.name, "功能描述": it.description or "",
        "类别": TYPE_TO_SHEET.get(it.nesma.type, it.nesma.type),
        # 应用类型是**产品事实**（BOM 的 app_type），系数取值在「0 参数表」里
        # 按标准规定给。两者分开，换省只改参数表、重新归类只改这一列。
        "应用类型": it.app_type or "业务处理",
        "重用程度": "低", "修改类型": "新增",
        "备注": "【占位待确认】" if "placeholder" in it.tags else "",
        "条目ID": it.id,
    }


# ---- push --------------------------------------------------------------


def push(bom: Bom, cfg: dict[str, Any]) -> dict[str, Any]:
    """按子系统分表覆盖写入。参数表不动 —— 它是人维护的。"""
    bt = cfg["base_token"]
    by_system: dict[str, list[BomItem]] = defaultdict(list)
    for i in bom.active():
        if i.cls in FP_COUNTED_CLASSES and i.nesma:
            by_system[i.path.system].append(i)

    total, detail = 0, {}
    for system, items in sorted(by_system.items()):
        tid = cfg["tables"].get(system)
        if not tid:
            detail[system] = "⚠️ 配置中无对应表，跳过"
            continue
        n = _push_table(bt, tid, [_row(i) for i in items])
        if isinstance(n, dict):
            return {"ok": False, "written": total, "error": n}
        total += n
        detail[system] = n
    return {"ok": True, "written": total, "detail": detail,
            "bom_version": bom.version}


def _push_table(bt: str, tid: str, rows: list[dict[str, Any]]):
    # 清空必须**反复取页直到表空** —— 只取一页 id 删完就退出，
    # 会留下剩余记录与新数据叠加（实测 1764 条只删了 200）
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH))
        ids = (r.get("data") or {}).get("record_id_list", [])
        if not ids:
            break
        d = lark("base", "+record-delete", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--yes",
                 "--json", json.dumps({"record_id_list": ids}))
        if not d.get("ok"):
            return d.get("error")

    written = 0
    for s in range(0, len(rows), BATCH):
        r = lark("base", "+record-batch-create", "--base-token", bt,
                 "--table-id", tid, "--as", "user",
                 "--json", json.dumps({"create_records": rows[s:s + BATCH]},
                                      ensure_ascii=False))
        if not r.get("ok"):
            return r.get("error")
        written += len(r["data"].get("record_id_list", []))
    return written


# ---- pull --------------------------------------------------------------


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, list):
        return "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in v)
    return str(v)


def fetch(bt: str, tid: str) -> list[dict[str, str]]:
    rows, offset = [], 0
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH), "--offset", str(offset))
        if not r.get("ok"):
            break
        d = r.get("data") or {}
        page, hdr = d.get("data", []), d.get("fields", [])
        rows.extend({k: _cell(v) for k, v in zip(hdr, row)} for row in page)
        if not d.get("has_more") or not page:
            break
        offset += len(page)
    return rows


def _next_id(bom: Bom, system: str, used: set[str]) -> str:
    """为方案人员新增的行分配 id —— 沿用该子系统已有条目的前缀。"""
    prefix, mx = None, 0
    for i in bom.items:
        if i.path.system == system:
            p, _, seq = i.id.rpartition(".")
            if seq.isdigit():
                prefix = prefix or p
                if p == prefix:
                    mx = max(mx, int(seq))
    if prefix is None:                      # 全新子系统
        prefix = "FP.NEW"
        mx = max([int(x.rpartition(".")[2]) for x in used
                  if x.startswith("FP.NEW.")] or [0])
    n = mx
    while True:
        n += 1
        cand = f"{prefix}.{n:04d}"
        if cand not in used:
            used.add(cand)
            return cand


def pull(bom: Bom, cfg: dict[str, Any], out: Path) -> dict[str, Any]:
    bt = cfg["base_token"]
    rows: list[dict[str, str]] = []
    for system, tid in sorted(cfg["tables"].items()):
        for r in fetch(bt, tid):
            r.setdefault("子系统", system)
            rows.append(r)
    # 只与「计算书写入范围」比对 —— 硬件等非功能点条目本就不在表里，
    # 拿它们比会把 30 个硬件误报成「疑删除」
    by_id = {i.id: i for i in bom.active()
             if i.cls in FP_COUNTED_CLASSES and i.nesma}
    used = {i.id for i in bom.items}

    changes: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    seen: set[str] = set()
    delivery: list[dict[str, Any]] = []
    stats: Counter = Counter()

    for r in rows:
        iid = r.get("条目ID", "").strip()
        sheet_type = r.get("类别", "").strip()
        btype = TYPE_FROM_SHEET.get(sheet_type, sheet_type)

        # L2 决策：重用程度/修改类型不进 BOM，导给 deal 层
        reuse, change = r.get("重用程度", "低"), r.get("修改类型", "新增")
        if reuse != "低" or change != "新增":
            delivery.append({"id": iid or "(新增)", "系统": r.get("子系统", ""),
                             "重用程度": reuse, "reuse_coef": REUSE_COEF.get(reuse, 1.0),
                             "修改类型": change, "change_coef": CHANGE_COEF.get(change, 1.0)})
            stats["L2 决策偏离默认"] += 1

        if not iid:
            if not r.get("功能点计数项名称", "").strip():
                continue
            new_id = _next_id(bom, r.get("子系统", ""), used)
            added.append({"new_id": new_id, "系统": r.get("子系统", ""),
                          "名称": r.get("功能点计数项名称", ""),
                          "类别": btype, "描述": r.get("功能描述", ""),
                          "一级模块": r.get("一级模块", ""), "二级模块": r.get("二级模块", ""),
                          "三级模块": r.get("三级模块", ""), "四级模块": r.get("四级模块", ""),
                          "备注": r.get("备注", "")})
            stats["新增条目"] += 1
            continue

        seen.add(iid)
        it = by_id.get(iid)
        if it is None:
            stats["条目ID 在 BOM 中不存在"] += 1
            changes.append({"id": iid, "field": "-", "before": "-", "after": "-",
                            "note": "⚠️ 条目ID 无法匹配到 BOM，可能是已废弃条目或 id 被改动"})
            continue

        for field, before, after in (
            ("name", it.name, r.get("功能点计数项名称", "").strip()),
            ("description", it.description or "", r.get("功能描述", "").strip()),
            ("nesma.type", it.nesma.type if it.nesma else "", btype),
            ("path.l1", it.path.l1 or "", r.get("一级模块", "").strip()),
            ("path.l2", it.path.l2 or "", r.get("二级模块", "").strip()),
            ("path.l3", it.path.l3 or "", r.get("三级模块", "").strip()),
            ("path.l4", it.path.l4 or "", r.get("四级模块", "").strip()),
        ):
            if after and after != before:
                changes.append({"id": iid, "system": it.path.system, "field": field,
                                "before": before, "after": after,
                                "note": ("⚠️ released 条目，pull 不自动覆盖"
                                         if it.status == "released" else "")})
                stats[f"改动 {field}"] += 1

    removed = [i for i in by_id if i not in seen]
    stats["计算书中缺失（疑删除）"] = len(removed)

    out.mkdir(parents=True, exist_ok=True)
    spec = {"bom_version": bom.version, "changes": changes, "added": added,
            "removed": removed, "delivery_overrides": delivery}
    (out / "worksheet-changes.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    L = [f"# 功能点计算书拉取报告 — BOM v{bom.version}", "",
         f"计算书 {len(rows)} 行　BOM 有效功能点 {len(by_id)} 条", "",
         "## 概览", ""]
    for k, n in stats.most_common():
        if n:
            L.append(f"- {k}：**{n}**")
    L += ["", "> 本报告**未修改 BOM**。确认后用 `bom_apply --adjustment WS` 落实，"
              "由 CHANGELOG 记录版本变更。", ""]

    if added:
        L += ["## 方案人员新增的功能点", "",
              "| 建议 id | 子系统 | 名称 | 类别 |", "|---|---|---|---|"]
        for a in added[:40]:
            L.append(f"| `{a['new_id']}` | {a['系统']} | {a['名称']} | {a['类别']} |")
        if len(added) > 40:
            L.append(f"| … | 其余 {len(added) - 40} 条 | | |")
        L.append("")
    if changes:
        by_field: dict[str, int] = defaultdict(int)
        for c in changes:
            by_field[c["field"]] += 1
        L += ["## 字段改动", "", "| 字段 | 条数 |", "|---|---:|"]
        for f, n in sorted(by_field.items(), key=lambda kv: -kv[1]):
            L.append(f"| `{f}` | {n} |")
        L += ["", "前 30 条：", "", "| 条目ID | 字段 | 原值 | 新值 |", "|---|---|---|---|"]
        for c in changes[:30]:
            L.append(f"| `{c['id']}` | {c['field']} | {c['before'][:30]} | "
                     f"{c['after'][:30]} | {c.get('note', '')}")
        L.append("")
    if removed:
        L += ["## 计算书中缺失的条目", "",
              f"{len(removed)} 条在 BOM 中存在但计算书里没有。**不自动废弃** —— "
              f"可能是方案人员删了行，也可能是 push 之后 BOM 又新增了条目。逐条确认：", ""]
        L += [f"- `{i}`" for i in removed[:30]]
        if len(removed) > 30:
            L.append(f"- …其余 {len(removed) - 30} 条")
        L.append("")
    if delivery:
        L += ["## L2 交付决策（不进 BOM）", "",
              f"{len(delivery)} 行的「重用程度/修改类型」偏离默认（低/新增）。",
              "这两项属**交付方案层**，不是 BOM 的产品事实 —— 已单独导出，",
              "落实时写进 `deals/<id>/delivery-plan.yaml` 的复用度覆盖，而非 BOM。", ""]

    (out / "worksheet-pull-report.md").write_text("\n".join(L), encoding="utf-8")
    return {"rows": len(rows), "stats": dict(stats), "out": str(out)}


def main() -> None:
    ap = argparse.ArgumentParser(description="BOM ↔ 飞书功能点计算书")
    ap.add_argument("direction", choices=["push", "pull"])
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))

    if args.direction == "push":
        r = push(bom, cfg)
        print(f"push {'成功' if r['ok'] else '失败'}：{r.get('written')} 行"
              f"（BOM v{bom.version}）")
        if not r["ok"]:
            print(f"  错误：{r.get('error')}")
    else:
        r = pull(bom, cfg, args.out or (args.bom / "lark-worksheet-pull"))
        print(f"计算书 {r['rows']} 行")
        for k, n in r["stats"].items():
            if n:
                print(f"  {k}: {n}")
        print(f"报告 → {r['out']}")


if __name__ == "__main__":
    main()
