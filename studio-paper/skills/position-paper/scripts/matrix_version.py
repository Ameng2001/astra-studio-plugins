#!/usr/bin/env python3
"""matrix_version.py <paper-id> {init|bump|diff|show} — 文献矩阵的版本与重扫比对。

positioning/literature-matrix.md 格式：
    > version: N
    | key | 题目 | 年份 | 问题 | 方法 | 数据 | 结论 | 与本题关系 | 来源 | 核查 |
    核查 ∈ verified / UNVERIFIED（paper-search / arxiv MCP 或 WebFetch 回查，标题 + 第一作者都对得上才算 verified）

  init               建矩阵（v1，空表）；同时从模板补 nearest-diff.md / thesis.md（已存在则不动）
  bump [--note ..]   把当前文件存档为 positioning/history/matrix-v{N}.md，版本 +1，记一行变更
  diff               当前表 vs 最新存档：新增 / 移除 / 核查状态变化 → positioning/rescan-{date}.md
                     新增条目里有 UNVERIFIED → exit 1
  show               条目数、UNVERIFIED 数、版本号、thesis.md 哈希（签字时用）
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import now_iso, sha256_text, workspace  # noqa: E402

COLS = ["key", "题目", "年份", "问题", "方法", "数据", "结论", "与本题关系", "来源", "核查"]
VERIFY = {"verified", "UNVERIFIED"}
VER_RE = re.compile(r"^>\s*version:\s*(\d+)\s*$", re.M)
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"

MATRIX_TMPL = """# 文献矩阵

> version: 1

> 规则：每篇一行；`来源` 写 DOI / arXiv id / URL；`核查` 只能是 verified 或 UNVERIFIED。
> verified = 已用 Semantic Scholar 或 Crossref 回查到同标题 + 同第一作者；查不到 = UNVERIFIED，不得进 nearest-diff.md。
> 不猜引用：检索接口不可达时由用户手工给出来源，仍标 UNVERIFIED 直到回查通过。

| key | 题目 | 年份 | 问题 | 方法 | 数据 | 结论 | 与本题关系 | 来源 | 核查 |
|---|---|---|---|---|---|---|---|---|---|

## 变更记录

- v1 · {ts} · 初始化
"""


def matrix_path(ws: Path) -> Path:
    return ws / "positioning" / "literature-matrix.md"


def history_dir(ws: Path) -> Path:
    return ws / "positioning" / "history"


def read_version(text: str) -> int:
    m = VER_RE.search(text)
    if not m:
        raise SystemExit("literature-matrix.md 缺版本头 `> version: N`")
    return int(m.group(1))


def parse_rows(text: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    in_table = False
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            in_table = False
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and cells[0] == "key":
            if cells[:len(COLS)] != COLS:
                raise SystemExit(f"矩阵表头应为 {COLS}，实为 {cells}")
            in_table = True
            continue
        if not in_table or set("".join(cells)) <= set("-: "):
            continue
        cells += [""] * (len(COLS) - len(cells))
        row = dict(zip(COLS, cells))
        if not row["key"]:
            continue
        if row["key"] in rows:
            raise SystemExit(f"矩阵 key 重复: {row['key']}")
        rows[row["key"]] = row
    return rows


def latest_history(ws: Path, below: int) -> tuple[int, Path] | None:
    best = None
    for p in history_dir(ws).glob("matrix-v*.md"):
        m = re.fullmatch(r"matrix-v(\d+)\.md", p.name)
        if m and int(m.group(1)) < below and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), p)
    return best


def load_current(ws: Path) -> tuple[str, int, dict]:
    p = matrix_path(ws)
    if not p.exists():
        raise SystemExit(f"矩阵不存在: {p} —— 先跑 init")
    text = p.read_text(encoding="utf-8")
    return text, read_version(text), parse_rows(text)


def cmd_init(ws: Path, a) -> int:
    p = matrix_path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        print(f"矩阵已存在（v{read_version(p.read_text(encoding='utf-8'))}），不覆盖: {p}")
    else:
        p.write_text(MATRIX_TMPL.format(ts=now_iso()), encoding="utf-8")
        print(f"✓ 新建 {p}（v1，空表）")
    for name in ["nearest-diff.md", "thesis.md"]:
        dst = ws / "positioning" / name
        if not dst.exists():
            shutil.copy2(TEMPLATES / f"{name}.tmpl", dst)
            print(f"✓ 新建 {dst}（模板）")
    return 0


def cmd_bump(ws: Path, a) -> int:
    text, v, rows = load_current(ws)
    hd = history_dir(ws)
    hd.mkdir(parents=True, exist_ok=True)
    arch = hd / f"matrix-v{v}.md"
    if arch.exists():
        raise SystemExit(f"{arch} 已存在：v{v} 已存档过，当前文件版本头可能被手改")
    shutil.copy2(matrix_path(ws), arch)
    new = VER_RE.sub(f"> version: {v + 1}", text, count=1)
    line = f"- v{v + 1} · {now_iso()} · {a.note or '版本递增'}（v{v} 存档 {len(rows)} 条）"
    if "## 变更记录" in new:
        new = new.rstrip("\n") + "\n" + line + "\n"
    else:
        new = new.rstrip("\n") + "\n\n## 变更记录\n\n" + line + "\n"
    matrix_path(ws).write_text(new, encoding="utf-8")
    print(f"✓ v{v} 存档 → {arch}\n✓ 当前矩阵 → v{v + 1}")
    return 0


def cmd_diff(ws: Path, a) -> int:
    _, v, cur = load_current(ws)
    prev = latest_history(ws, below=v + 1)
    if not prev:
        raise SystemExit("没有历史版本可比 —— 重扫前先 `bump --note rescan` 存档上一版，再加新条目")
    pv, pp = prev
    old = parse_rows(pp.read_text(encoding="utf-8"))
    added = [cur[k] for k in cur if k not in old]
    removed = [old[k] for k in old if k not in cur]
    changed = [(k, old[k]["核查"], cur[k]["核查"]) for k in cur if k in old and old[k]["核查"] != cur[k]["核查"]]
    bad_new = [r for r in added if r["核查"] != "verified"]

    print(f"矩阵 v{v} vs 存档 v{pv}")
    print(f"新增 {len(added)}：" + ("、".join(f"{r['key']}[{r['核查'] or '空'}]" for r in added) or "无"))
    print(f"移除 {len(removed)}：" + ("、".join(r["key"] for r in removed) or "无"))
    print(f"核查状态变化 {len(changed)}：" + ("、".join(f"{k} {o}→{n}" for k, o, n in changed) or "无"))

    date = now_iso()[:10]
    L = [f"# 近邻重扫 {date}", "", f"- 比对：当前矩阵 v{v} vs 存档 v{pv}（`positioning/history/{pp.name}`）",
         f"- 生成：{now_iso()}（matrix_version.py diff）", "", "## 新增近邻", ""]
    if added:
        L += ["| key | 题目 | 年份 | 与本题关系 | 来源 | 核查 |", "|---|---|---|---|---|---|"]
        L += [f"| {r['key']} | {r['题目']} | {r['年份']} | {r['与本题关系']} | {r['来源']} | {r['核查']} |" for r in added]
    else:
        L.append("_（无）_")
    L += ["", "## 移除", ""] + ([f"- {r['key']} — {r['题目']}" for r in removed] or ["_（无）_"])
    L += ["", "## 核查状态变化", ""] + ([f"- {k}：{o} → {n}" for k, o, n in changed] or ["_（无）_"])
    if bad_new:
        L += ["", "## ⚠ 未核查的新增条目", "",
              "以下新增条目未通过回查（UNVERIFIED），不得进入 nearest-diff.md，也不能据此判断论点句：", ""]
        L += [f"- {r['key']} — {r['题目']}" for r in bad_new]
    L += ["", "## 论点句是否需改", "", "待导师判断。", "",
          "（判断依据：新增近邻是否与 `positioning/nearest-diff.md` 的三篇同样近或更近；"
          "若是，按 position-paper 正常模式更新差异表与论点句并重走 D2 闸门。）"]
    out = ws / "positioning" / f"rescan-{date}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"→ {out}")
    if bad_new:
        print(f"❌ {len(bad_new)} 条新增为 UNVERIFIED —— 先回查（Semantic Scholar / Crossref）或手工补来源")
        return 1
    return 0


def cmd_show(ws: Path, a) -> int:
    _, v, rows = load_current(ws)
    unv = [r for r in rows.values() if r["核查"] != "verified"]
    invalid = [r for r in rows.values() if r["核查"] not in VERIFY]
    hist = sorted(p.name for p in history_dir(ws).glob("matrix-v*.md")) if history_dir(ws).exists() else []
    print(f"文献矩阵 v{v}：{len(rows)} 条，UNVERIFIED {len(unv)} 条；存档 {hist or '无'}")
    for r in rows.values():
        print(f"  {'✓' if r['核查'] == 'verified' else '✗'} {r['key']:<16} {r['年份']:<5} {r['题目'][:48]}")
    if invalid:
        print(f"⚠ 核查列非法（应为 verified / UNVERIFIED）：{', '.join(r['key'] for r in invalid)}")
    th = ws / "positioning" / "thesis.md"
    if th.exists():
        print(f"thesis.md 哈希（签字用）：{sha256_text(th.read_text(encoding='utf-8'))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="文献矩阵版本管理与重扫比对")
    ap.add_argument("paper_id")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="新建 v1 空矩阵 + nearest-diff / thesis 模板")
    b = sub.add_parser("bump", help="存档当前版本并 +1")
    b.add_argument("--note", default="")
    sub.add_parser("diff", help="与最新存档比对，写 rescan-{date}.md")
    sub.add_parser("show", help="矩阵摘要")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    return {"init": cmd_init, "bump": cmd_bump, "diff": cmd_diff, "show": cmd_show}[a.cmd](ws, a)


if __name__ == "__main__":
    sys.exit(main())
