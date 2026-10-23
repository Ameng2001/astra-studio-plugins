"""respond-reviews 两个脚本共用：读 markdown 回应表（stdlib only）。"""
from __future__ import annotations

import re
from pathlib import Path


def resolve(ws: Path, p: str) -> Path:
    q = Path(p)
    if q.is_absolute():
        return q
    return ws / q if (ws / q).exists() or not q.exists() else q.resolve()


def read_table(text: str) -> tuple[list[str], list[dict]]:
    """返回第一张含「回应」列的表的 (表头, 行)；没有则 ([], [])。"""
    header: list[str] = []
    rows: list[dict] = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if header and rows:
                break
            continue
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", s)[1:-1]]
        if not header:
            if any("回应" in c for c in cells):
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(dict(zip(header, cells)))
    return header, rows


def col(header: list[str], *names: str) -> str | None:
    for n in names:
        for h in header:
            if h.strip().lower() == n.lower():
                return h
    for n in names:
        for h in header:
            if n.lower() in h.lower():
                return h
    return None
