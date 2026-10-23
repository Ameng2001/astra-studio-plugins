#!/usr/bin/env python3
"""双盲匿名扫描 —— check-submission 第 3 步。

    anonymity_scan.py <paper-id> <draft.md> [--artifact <dir>]
                      [--authors "张三,Zhang San"] [--orgs "某某大学,Foo Lab"]
                      [--allow "github.com/pytorch,anonymous.4open.science"]

扫稿件与 artifact 目录下的文本文件（每个 ≤ 1 MB，跳过 .git / node_modules / 二进制），查：
  作者名 · 单位 · 邮箱 · 带用户名的 github / gitlab / bitbucket / huggingface 链接 ·
  本机家目录路径（/Users/<name>、/home/<name>）· 自引措辞（our previous work / 我们之前的工作 / 我们在 [x]）·
  致谢节 · 基金编号。
--allow 里的子串命中即放过（例如引用的第三方基线仓库）。

写 submission/anonymity-report.md（含稿件哈希，snapshot.py 会校验它不过期）。
退出码：0 无命中 · 2 有命中（阻塞）· 1 参数错误。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import now_iso, sha256_file, workspace  # noqa: E402

MAX_BYTES = 1 << 20
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints"}

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("仓库链接", re.compile(r"(?i)(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|bitbucket\.org|huggingface\.co|gitee\.com)/[A-Za-z0-9_.-]+")),
    ("本机路径", re.compile(r"(?:/Users|/home)/[A-Za-z0-9_.-]+(?=/)")),
    ("自引措辞", re.compile(r"(?i)\b(?:our|my)\s+(?:previous|prior|earlier|recent)\s+(?:work|paper|study|studies|approach)\b"
                            r"|\bwe\s+(?:previously|earlier)\s+(?:proposed|showed|presented|introduced)\b"
                            r"|\bin\s+our\s+(?:previous|prior)\b"
                            r"|我们(?:之前|此前|先前|以前|前期)(?:的)?(?:工作|研究|论文|提出)"
                            r"|我们在\s*(?:\[[^\]]+\]|［[^］]+］|\\cite\{[^}]+\})")),
    ("致谢节", re.compile(r"(?im)^\s*(?:#{1,6}\s*|\\section\*?\{)\s*(?:acknowledg(?:e)?ments?|致\s*谢)")),
    ("基金编号", re.compile(r"(?i)\b(?:grant|award|project)\s*(?:no\.?|number|#|id)?\s*[:：]?\s*[A-Z]{0,6}[-\s]?\d{5,}"
                            r"|(?:NSFC|NSF|国家自然科学基金|国家重点研发计划|基金(?:项目|编号)?)\s*[:：(（]?\s*[A-Z]*\d[\dA-Z-]{4,}")),
]


def iter_files(root: Path):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = Path(dp) / fn
            try:
                if p.is_symlink() or p.stat().st_size > MAX_BYTES:
                    continue
                raw = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue
            try:
                yield p, raw.decode("utf-8")
            except UnicodeDecodeError:
                continue


def scan_text(label: str, text: str, names: list[tuple[str, re.Pattern]], allow: list[str]) -> list[dict]:
    hits = []
    for ln, line in enumerate(text.splitlines(), 1):
        for cat, pat in names + PATTERNS:
            for m in pat.finditer(line):
                frag = m.group(0)
                if any(a.lower() in frag.lower() for a in allow):
                    continue
                s = max(0, m.start() - 30)
                hits.append({"cat": cat, "where": f"{label}:{ln}", "match": frag.strip(),
                             "ctx": line[s:m.end() + 30].strip()})
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description="双盲匿名扫描")
    ap.add_argument("paper_id")
    ap.add_argument("draft")
    ap.add_argument("--artifact", default=None, help="artifact 目录（代码 / 数据 / README）")
    ap.add_argument("--authors", default="", help="逗号分隔的作者名（中英文都给）")
    ap.add_argument("--orgs", default="", help="逗号分隔的单位 / 实验室名")
    ap.add_argument("--allow", default="", help="逗号分隔的放行子串（第三方仓库等）")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    draft = Path(a.draft).resolve()
    if not draft.is_file():
        print(f"稿件不存在: {draft}", file=sys.stderr)
        return 1
    names = []
    for cat, raw in (("作者名", a.authors), ("单位", a.orgs)):
        for n in [x.strip() for x in re.split(r"[,，]", raw) if x.strip()]:
            parts = n.split()
            # "Lin Wei" 也匹配 linwei / lin.wei / lin-wei / lin_wei（邮箱、用户名、路径里的写法）
            pat = r"[\s._-]?".join(re.escape(x) for x in parts) if len(parts) > 1 else re.escape(n)
            names.append((cat, re.compile(pat, re.I)))
    allow = [x.strip() for x in a.allow.split(",") if x.strip()]

    hits = scan_text(draft.name, draft.read_text(encoding="utf-8"), names, allow)
    n_files = 0
    if a.artifact:
        root = Path(a.artifact).resolve()
        if not root.is_dir():
            print(f"artifact 目录不存在: {root}", file=sys.stderr)
            return 1
        for p, text in iter_files(root):
            n_files += 1
            hits += scan_text(f"artifact/{p.relative_to(root)}", text, names, allow)
        # 文件名本身也可能泄露
        for p, _ in [(q, None) for q in root.rglob("*") if not (set(q.relative_to(root).parts) & SKIP_DIRS)]:
            for cat, pat in names:
                if pat.search(p.name):
                    hits.append({"cat": cat, "where": f"artifact/{p.relative_to(root)}", "match": p.name, "ctx": "（文件名）"})

    if not names:
        print("提醒：未给 --authors / --orgs，作者名与单位没有扫描", file=sys.stderr)
    out = ws / "submission" / "anonymity-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    by_cat: dict[str, int] = {}
    for h in hits:
        by_cat[h["cat"]] = by_cat.get(h["cat"], 0) + 1
    L = [f"# 双盲匿名报告 —— {a.paper_id}", "",
         f"- 生成：{now_iso()} · anonymity_scan.py",
         f"- 稿件：`{draft}`",
         f"- draft_hash: {sha256_file(draft)}",
         f"- artifact：`{a.artifact or '（未给）'}`，扫描文本文件 {n_files} 个（> 1 MB / 二进制 / .git 跳过）",
         f"- 作者名 / 单位词：{len(names)} 个；放行子串：{', '.join(allow) or '无'}",
         f"- 命中 {len(hits)} 处：" + ("、".join(f"{k} {v}" for k, v in by_cat.items()) or "无"),
         "", f"结论：{'阻塞' if hits else '通过'}", ""]
    if hits:
        L += ["| # | 类别 | 位置 | 命中 | 上下文 |", "|---|---|---|---|---|"]
        for k, h in enumerate(hits, 1):
            ctx = h["ctx"].replace("|", "\\|")
            L.append(f"| {k} | {h['cat']} | {h['where']} | `{h['match']}` | {ctx} |")
        L += ["", "PDF 元数据（作者 / 创建工具路径）、补充材料压缩包、图片里的水印不在本扫描内 —— 人工核对。", ""]
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"已写 {out}")
    print(f"命中 {len(hits)} 处" + (("：" + "、".join(f"{k} {v}" for k, v in by_cat.items())) if hits else ""))
    for h in hits[:20]:
        print(f"  [{h['cat']}] {h['where']}  {h['match']}")
    return 2 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
