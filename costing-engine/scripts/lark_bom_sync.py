"""lark_bom_sync — BOM 与飞书多维表格的双向同步。

**git 是发布层，飞书是评审层。** 两个方向语义不对称：

  push  git released/draft 版本 → 覆盖飞书主表，重建裁决表。
        飞书主表视为只读快照，结构性变更走「变更提案」表。

  pull  飞书裁决结果 → 生成本地 diff 报告，**不直接落 BOM**。
        人工 approve 后再由 bom_apply 落实。飞书上的一次误点击
        不应该直接改变报价基线。

冲突策略：pull 永不自动覆盖 `status: released` 的条目；产生冲突时
写入 conflicts.md 列出双方取值，人工裁决。

用法：
    python3 lark_bom_sync.py push --bom <dir> --config <lark-sync.json>
    python3 lark_bom_sync.py pull --bom <dir> --config <lark-sync.json> --out <report-dir>
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from bom_schema import Bom
from lark_table import BATCH, LarkTableError, lark, replace_all
from nesma_weights import ESTIMATED_WEIGHTS as W

PLACEHOLDER_TAG = "placeholder"


# ---- push --------------------------------------------------------------


def push(bom: Bom, cfg: dict[str, Any]) -> dict[str, Any]:
    """把 BOM 快照写入飞书主表。

    先清空再重写 —— 主表是只读快照，增量合并没有意义且容易产生幽灵行。
    """
    bt = cfg["base_token"]
    tid = cfg["tables"]["功能点清单"]

    try:
        written = replace_all(bt, tid, [_to_row(i) for i in bom.active()], "功能点清单")
    except LarkTableError as e:
        return {"ok": False, "written": 0, "error": str(e)}
    return {"ok": True, "written": written, "bom_version": bom.version}


def _to_row(i) -> dict[str, Any]:
    return {
        "条目ID": i.id, "名称": i.name, "系统": i.path.system,
        "产品线": i.path.product_line,
        "一级功能": i.path.l1 or "", "二级功能": i.path.l2 or "",
        "三级功能": i.path.l3 or "", "描述": i.description or "",
        "功能点类型": i.nesma.type if i.nesma else "",
        "UFP": W[i.nesma.type] if i.nesma else 0,
        "类别": i.cls, "成熟度": i.maturity,
        "标记": "占位待确认" if PLACEHOLDER_TAG in i.tags else "正常",
        "判定理由": (i.nesma.rationale or "") if i.nesma else "",
        "引入版本": i.since,
        # 共创字段 —— 由 bom_annotate 打上。单选 + 预筛视图 = 可聚合的工作队列，
        # 比标颜色加批注强的地方在于：能回答「还剩多少条待拆」，且 pull 得回来。
        "共创状态": i.coauthor_status or "已判型",
        "处理建议": i.coauthor_advice,
        "来源特性": i.id.rsplit(".", 1)[0] if i.id.count(".") >= 3 else "",
    }


# ---- pull --------------------------------------------------------------

#: 各裁决表的「结论」列与未处理值
DECISION_TABLES = {
    "C-逻辑文件维护方": ("裁决结论", "未裁决", "数据组"),
    "占位待确认": ("确认结论", "未确认", "条目ID"),
    "C-ILF实体核对": ("核对结论", "未核对", "建议实体名"),
    "F-类型裁决": ("裁决结论", "未裁决", "条目ID"),
    "变更提案": ("处理状态", "待处理", "涉及条目ID"),
}


def pull(bom: Bom, cfg: dict[str, Any], out: Path) -> dict[str, Any]:
    """拉取飞书裁决结果，生成 diff 报告。**不改 BOM。**"""
    bt = cfg["base_token"]
    out.mkdir(parents=True, exist_ok=True)
    by_id = {i.id: i for i in bom.active()}
    summary: dict[str, Any] = {}
    conflicts: list[str] = []
    lines = [f"# 飞书共创拉取报告 — BOM v{bom.version}", ""]

    for table, (col, pending, key) in DECISION_TABLES.items():
        tid = cfg["tables"].get(table)
        if not tid:
            continue
        rows = _list_all(bt, tid)
        decided = [r for r in rows if _cell(r, col) and _cell(r, col) != pending]
        counts = Counter(_cell(r, col) for r in decided)
        summary[table] = {"total": len(rows), "decided": len(decided),
                          "by_decision": dict(counts)}

        lines += [f"## {table}", "",
                  f"- 共 {len(rows)} 条，已裁决 **{len(decided)}** 条，"
                  f"待处理 {len(rows) - len(decided)} 条",
                  f"- 裁决分布：{dict(counts) or '（无）'}", ""]
        if decided:
            lines.append(f"| {key} | {col} | 说明 |")
            lines.append("| --- | --- | --- |")
            for r in decided[:60]:
                note = _cell(r, "确认说明") or _cell(r, "备注") or _cell(r, "处理说明")
                lines.append(f"| {_cell(r, key)} | {_cell(r, col)} | {note[:60]} |")
            if len(decided) > 60:
                lines.append(f"| … | 其余 {len(decided) - 60} 条 | |")
            lines.append("")

        # released 条目不接受飞书侧改写
        for r in decided:
            iid = _cell(r, "条目ID")
            item = by_id.get(iid)
            if item is not None and item.status == "released":
                conflicts.append(
                    f"- `{iid}`（{table}）：本地为 released，飞书裁决 "
                    f"`{_cell(r, col)}` —— pull 不自动覆盖，需人工裁定")

    if conflicts:
        (out / "conflicts.md").write_text(
            "# 冲突：飞书裁决触及 released 条目\n\n" + "\n".join(conflicts),
            encoding="utf-8")
        lines += ["## ⚠️ 冲突", "",
                  f"{len(conflicts)} 条裁决触及 released 条目，见 `conflicts.md`。", ""]

    lines += ["## 下一步", "",
              "本报告**未修改 BOM**。确认裁决无误后，用 `bom_apply` 落实相应调整项，",
              "由 CHANGELOG 记录版本变更。飞书上的一次误点击不应直接改变报价基线。"]
    (out / "pull-report.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "pull-summary.json").write_text(
        json.dumps({"bom_version": bom.version, "tables": summary,
                    "conflicts": len(conflicts)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return {"ok": True, "tables": summary, "conflicts": len(conflicts)}


def _list_all(bt: str, tid: str) -> list[dict[str, Any]]:
    """拉全表。

    注意返回结构：`data.data` 是**位置数组**的列表，列顺序由 `data.fields`
    给出，不是字段名 → 值的字典。这里就地 zip 成字典，调用方按字段名取值。
    """
    rows, offset = [], 0
    while True:
        r = lark("base", "+record-list", "--base-token", bt, "--table-id", tid,
                 "--as", "user", "--limit", str(BATCH), "--offset", str(offset))
        if not r.get("ok"):
            break
        data = r.get("data") or {}
        page, headers = data.get("data", []), data.get("fields", [])
        rows.extend(dict(zip(headers, row)) for row in page)
        if not data.get("has_more") or not page:
            break
        offset += len(page)
    return rows


def _cell(row: dict[str, Any], name: str) -> str:
    """飞书单元格取值 —— select 返回数组，text 可能是字符串或富文本数组。"""
    v = row.get(name)
    if v is None:
        return ""
    if isinstance(v, list):
        parts = [x.get("text", "") if isinstance(x, dict) else str(x) for x in v]
        return "".join(parts)
    return str(v)


def main() -> None:
    ap = argparse.ArgumentParser(description="BOM ↔ 飞书多维表格同步")
    ap.add_argument("direction", choices=["push", "pull"])
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--config", required=True, type=Path,
                    help="lark-sync.json，含 base_token 与各表 table_id")
    ap.add_argument("--out", type=Path, help="pull 报告输出目录")
    args = ap.parse_args()

    bom = Bom.load(args.bom)
    cfg = json.loads(args.config.read_text(encoding="utf-8"))

    if args.direction == "push":
        r = push(bom, cfg)
        print(f"push {'成功' if r['ok'] else '失败'}：写入 {r.get('written')} 条"
              f"（BOM v{bom.version}）")
        if not r["ok"]:
            print(f"  错误：{r.get('error')}")
    else:
        out = args.out or (args.bom / "lark-pull")
        r = pull(bom, cfg, out)
        for t, s in r["tables"].items():
            print(f"  {t}: {s['decided']}/{s['total']} 已裁决 {s['by_decision']}")
        print(f"冲突 {r['conflicts']} 条。报告写入 {out}")


if __name__ == "__main__":
    main()
