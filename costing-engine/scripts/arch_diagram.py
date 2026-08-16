"""arch_diagram —— 从 BOM 直接生成产品架构图（SVG），并可推到飞书画板。

## 为什么要它

架构图以前是人画的，画完就开始过期：BOM 里加了一个模块、停用了一批条目，
图上还是旧的。**图和数对不上时，先信数** —— 但评审现场没人翻 BOM，
大家看的是图。所以这里把图变成 BOM 的产物：模块名、模块数、条目数、UFP
全部从 `bom/` 现算，不写死任何一个数字。改完 BOM 重跑一次，图就是新的。

## 分层从哪来

三层不是在这里定义的，是 `path.product_line` 的既有事实：

    数智平台支撑基座      → 底层：支撑基座
    行业大模型基础设施    → 中层：大模型基础设施
    数智幼教平台 / 幼教行业大模型  → 上层：幼教
    数智康养平台 / 康养行业大模型  → 上层：康养

新产品线出现时**硬失败而不是静默忽略** —— 图上少一块没人会发现，
而「架构图缺了一个产品线」正是那种跑得通的错误。

## 用法

    python3 arch_diagram.py --root <digital-costing> --out arch-product.svg
    python3 arch_diagram.py --root <digital-costing> --out arch-product.svg --push

`--push` 从 `<root>/lark-workspace.json` 的 `arch_diagram.whiteboard` 取画板
token，用 lark-cli 覆盖更新。幂等 token 取 SVG 内容哈希 —— 内容没变时重跑
不会在画板上写第二份。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import yaml

from bom_schema import Bom, is_fp_counted
from nesma_weights import ESTIMATED_WEIGHTS

# ---------------------------------------------------------------- 分层配置

#: product_line → (层, 行业, 卡片配色 kind)。这里是**唯一**要人工维护的映射。
#: 行业为 None 表示该层跨行业共用，不分行业子块。
PRODUCT_LINES = {
    "数智平台支撑基座":   ("base",   None,   "base"),
    "行业大模型基础设施": ("infra",  None,   "infra"),
    "幼教行业大模型":     ("vmodel", "幼教", "llm"),
    "康养行业大模型":     ("vmodel", "康养", "llm"),
    "数智幼教平台":       ("apps",   "幼教", "apps"),
    "数智康养平台":       ("apps",   "康养", "apps"),
}

#: 自上而下四层。行业大模型单独成层 —— 它建在通用引擎之上、业务系统之下，
#: 混进业务系统里画会让「幼教/康养各有一套行业模型，但共用同一批引擎」
#: 这个事实看不出来。
BANDS = ["apps", "vmodel", "infra", "base"]

BAND_TITLE = {
    "apps":   "垂直业务系统",
    "vmodel": "行业大模型",
    "infra":  "大模型基础设施",
    "base":   "支撑基座",
}

#: 各层加载哪些目录。与 deal.compose 无关 —— 架构图画的是**产品全貌**，
#: 不是某个商机交付的范围。
SOURCES = [
    "bom/shared/platform-base",
    "bom/shared/llm-infra",
    "bom/verticals/preschool/apps",
    "bom/verticals/preschool/llm",
    "bom/verticals/eldercare/apps",
    "bom/verticals/eldercare/llm",
]

#: l1 里这个取值不是功能模块，是逻辑文件的收纳桶，单独计数。
LF_BUCKET = "逻辑文件"

# ---------------------------------------------------------------- 取数


def collect(root: Path) -> dict:
    """从 BOM 现算每个子系统的模块、条目与 UFP。"""
    cards: dict[str, dict] = {}
    for rel in SOURCES:
        d = root / rel
        if not d.exists():
            raise SystemExit(f"⛔ BOM 目录不存在：{d}")
        bom = Bom.load(d)
        codes = _sheet_codes(d)
        for it in bom.active():
            pl = it.path.product_line
            if pl not in PRODUCT_LINES:
                raise SystemExit(
                    f"⛔ 未知产品线「{pl}」（{it.id}）。\n"
                    f"  架构图的分层由 PRODUCT_LINES 显式声明。新产品线要么补进映射，"
                    f"要么说明为何不上图 —— 静默漏掉一个产品线，图上看不出来。")
            band, vert, kind = PRODUCT_LINES[pl]
            sysname = it.path.system
            # **键必须带层与行业**。幼教与康养的行业大模型子系统同名（知识工程、
            # 本体工程、数据工程、行业垂直模型、行业智能体），只按名字建卡
            # 会把两个行业的数悄悄合成一张 —— 图还是画得出来，只是少 5 张卡、
            # 数字全是两行业之和。名字是展示字段，不是键。
            c = cards.setdefault((band, vert, sysname), {
                "system": sysname, "code": codes.get(sysname, ""),
                "band": band, "vertical": vert, "kind": kind,
                "modules": Counter(), "items": 0, "ufp": 0, "lf": 0,
            })
            c["items"] += 1
            if is_fp_counted(it):
                c["ufp"] += ESTIMATED_WEIGHTS.get(it.nesma.type, 0)
            l1 = it.path.l1 or "（未归模块）"
            if l1 == LF_BUCKET:
                c["lf"] += 1
            else:
                c["modules"][l1] += 1
    for c in cards.values():
        c["n_modules"] = len(c["modules"])
    return cards


def _sheet_codes(d: Path) -> dict[str, str]:
    """B1/C1/A1/L1 这些编号取自各层 lark.json 的 sheet_names —— 与飞书表名同源，
    图上和共创表上叫同一个名字。"""
    p = d / "lark.json"
    if not p.exists():
        return {}
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = {}
    for sysname, sheet in (cfg.get("sheet_names") or {}).items():
        out[sysname] = sheet.split(" ", 1)[0] if " " in sheet else ""
    return out


# ---------------------------------------------------------------- 画图

CARD_W, CARD_H, GAP = 270, 164, 16
PAD, HDR = 18, 54
MARGIN = 40
MODULE_LINES = 5          # 卡片里最多几行模块
NAME_MAX = 15             # 模块名超过就截断

THEME = {
    "base":  ("#2F80ED", "#EAF2FD", "#9EC2F0"),
    "infra": ("#7C5CE0", "#F0ECFD", "#C0AFF5"),
    "apps":  ("#12A594", "#E6F6F3", "#8CD3C7"),
    "llm":   ("#E8833A", "#FCF0E6", "#F0BE8E"),
}


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _clip(s: str, n: int = NAME_MAX) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def card_svg(c: dict, x: int, y: int) -> list[str]:
    fg, bg, bd = THEME[c["kind"]]
    o = [f'<g filter="url(#sh)"><rect x="{x}" y="{y}" width="{CARD_W}" '
         f'height="{CARD_H}" rx="10" fill="{bg}" stroke="{bd}"/></g>',
         f'<rect x="{x}" y="{y}" width="5" height="{CARD_H}" rx="2.5" fill="{fg}"/>']
    title = f'{c["code"]} {c["system"]}'.strip()
    o.append(f'<text x="{x+16}" y="{y+28}" font-size="15" font-weight="700" '
             f'fill="{fg}">{esc(title)}</text>')
    scale = f'{c["ufp"]:,} UFP' if c["ufp"] else "工作量法"
    stat = f'{c["n_modules"]} 模块 · {c["items"]} 条 · {scale}'
    if c["lf"]:
        stat += f' · LF {c["lf"]}'
    o.append(f'<text x="{x+16}" y="{y+50}" font-size="11.5" '
             f'fill="#7B8794">{esc(stat)}</text>')
    o.append(f'<line x1="{x+16}" y1="{y+60}" x2="{x+CARD_W-16}" y2="{y+60}" '
             f'stroke="{bd}"/>')

    mods = c["modules"].most_common()
    shown = mods if len(mods) <= MODULE_LINES else mods[: MODULE_LINES - 1]
    ty = y + 80
    for name, n in shown:
        o.append(f'<text x="{x+16}" y="{ty}" font-size="11.5" fill="#3A4757">'
                 f'{esc(_clip(name))}</text>')
        o.append(f'<text x="{x+CARD_W-16}" y="{ty}" font-size="11.5" '
                 f'fill="#9AA7B6" text-anchor="end">{n}</text>')
        ty += 18
    if len(mods) > MODULE_LINES:
        o.append(f'<text x="{x+16}" y="{ty}" font-size="11.5" fill="#9AA7B6">'
                 f'另 {len(mods) - len(shown)} 个模块</text>')
    return o


SUBHDR = 30      # 行业子块标题高
COLS = 8         # 一行最多几张卡（由支撑基座的 8 个子系统定宽）


def band_panel(title: str, sub: str, groups: list[tuple[str | None, list[dict]]],
               x: int, y: int, w: int) -> tuple[list[str], int]:
    """一层 = 一条带。带内可按行业分若干子块，每行居中。

    子块横排会让「幼教 4 个业务平台 / 康养 7 个」这种不等宽的行业挤成不同宽度，
    看起来像层级差异；竖排 + 每行居中，行业之间才是并列关系。
    """
    o = [f'<text x="{x+PAD+4}" y="{y+36}" font-size="19" font-weight="700" '
         f'fill="#1B2A3D">{esc(title)}</text>',
         f'<text x="{x+PAD+8+len(title)*19}" y="{y+36}" font-size="12.5" '
         f'fill="#7B8794">{esc(sub)}</text>']
    cy = y + HDR
    inner = w - PAD * 2
    for gi, (label, cards) in enumerate(groups):
        if gi:
            cy += 14
        if label:
            o.append(f'<text x="{x+PAD+4}" y="{cy+18}" font-size="14" '
                     f'font-weight="700" fill="#5C6B7A">{esc(label)}</text>')
            o.append(f'<text x="{x+PAD+8+len(label)*14}" y="{cy+18}" '
                     f'font-size="11.5" fill="#9AA7B6">{esc(_sum(cards))}</text>')
            cy += SUBHDR
        for r in range(0, len(cards), COLS):
            row = cards[r:r + COLS]
            # 一律左对齐。居中会让「幼教 4 个业务平台」这一行飘到中间，
            # 与左侧的行业标签脱节 —— 层内左边界对齐，行业标签才领得住它那一行。
            cx = x + PAD
            for c in row:
                o += card_svg(c, cx, cy)
                cx += CARD_W + GAP
            cy += CARD_H + GAP
        cy -= GAP
    h = cy + PAD - y
    return ([f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" '
             f'fill="#F7F9FC" stroke="#E3E9F0"/>'] + o), h


def _sum(cards: list[dict]) -> str:
    m = sum(c["n_modules"] for c in cards)
    n = sum(c["items"] for c in cards)
    u = sum(c["ufp"] for c in cards)
    s = f'{len(cards)} 子系统 · {m} 模块 · {n:,} 条'
    return s + (f' · {u:,} UFP' if u else '')


def _arrow(x: int, y_top: int, y_bot: int) -> list[str]:
    """两带之间的支撑箭头。朝上 —— 下层支撑上层，上层依赖下层。"""
    return [f'<polyline points="{x},{y_bot-4} {x},{y_top+13}" stroke="#B7C0CC" '
            f'stroke-width="2" fill="none"/>',
            f'<polygon points="{x-6},{y_top+13} {x+6},{y_top+13} {x},{y_top+3}" '
            f'fill="#B7C0CC"/>']


def _legend(x: int, y: int) -> list[str]:
    o = []
    for i, (kind, label) in enumerate([("apps", "业务平台"), ("llm", "行业大模型"),
                                       ("infra", "引擎"), ("base", "基座")]):
        fg, bg, bd = THEME[kind]
        cx = x + i * 108
        o.append(f'<rect x="{cx}" y="{y-11}" width="14" height="14" rx="3" '
                 f'fill="{bg}" stroke="{fg}"/>')
        o.append(f'<text x="{cx+20}" y="{y}" font-size="12.5" fill="#5C6B7A">'
                 f'{esc(label)}</text>')
    return o


def _key(c: dict) -> tuple:
    return (0 if c["kind"] == "apps" else 1, c["code"] or "zz", c["system"])


def render(cards: dict, bom_version: str) -> str:
    by_band: dict[str, dict[str | None, list[dict]]] = {}
    for c in cards.values():
        by_band.setdefault(c["band"], {}).setdefault(c["vertical"], []).append(c)
    for verts in by_band.values():
        for v in verts.values():
            v.sort(key=_key)

    for b in BANDS:
        if b not in by_band:
            raise SystemExit(f"⛔ 层「{b}」没有任何子系统 —— BOM 或 PRODUCT_LINES 有问题")

    W_inner = PAD * 2 + COLS * CARD_W + (COLS - 1) * GAP
    W = MARGIN * 2 + W_inner
    body: list[str] = []
    y = 118

    for i, b in enumerate(BANDS):
        if i:
            body += _arrow(MARGIN + W_inner // 2, y, y + 40)
            y += 40
        verts = by_band[b]
        flat = [c for v in verts.values() for c in v]
        # 行业为 None 的层不分子块；分行业的层按 幼教 → 康养 固定次序，
        # 免得两张图之间行业上下颠倒。
        if list(verts) == [None]:
            groups = [(None, verts[None])]
        else:
            groups = [(k, verts[k]) for k in sorted(verts, key=str)]
        o, h = band_panel(BAND_TITLE[b], _sum(flat), groups, MARGIN, y, W_inner)
        body += o
        y += h

    H = y + 78

    head = [
        f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>',
        f'<text x="{MARGIN}" y="52" font-size="26" font-weight="700" '
        f'fill="#1B2A3D">产品架构 · 四层</text>',
        f'<text x="{MARGIN+232}" y="52" font-size="13.5" fill="#7B8794">'
        f'模块与规模由 BOM 现算，改 BOM 重跑本图即同步</text>',
        f'<text x="{MARGIN}" y="80" font-size="13" fill="#9AA7B6">'
        f'上层依赖下层：行业大模型各行业一套，建在共用的引擎之上；'
        f'业务系统再建在行业大模型之上。基座与引擎跨行业共用，一条都不重数</text>',
    ] + _legend(W - MARGIN - 420, 52)
    foot = [
        f'<text x="{MARGIN}" y="{H-30}" font-size="12" fill="#9AA7B6">'
        f'{esc(f"bom {bom_version}　卡片内为该子系统条目数最多的模块，数字是条目数；UFP 按 NESMA 估算法权重 10/7/4/5/4；工作量法子系统不计 UFP")}'
        f'</text>',
    ]
    defs = ('<defs><filter id="sh">'
            '<feDropShadow dx="0" dy="2" stdDeviation="3" '
            'flood-color="#1B2A3D" flood-opacity="0.10"/></filter></defs>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
            f'width="{W}" height="{H}">{defs}'
            + "".join(head + body + foot) + "</svg>")


# ---------------------------------------------------------------- 推送


def push(root: Path, svg_path: Path) -> None:
    cfg = json.loads((root / "lark-workspace.json").read_text(encoding="utf-8"))
    token = (cfg.get("arch_diagram") or {}).get("whiteboard")
    if not token:
        raise SystemExit(
            "⛔ lark-workspace.json 里没有 arch_diagram.whiteboard —— "
            "先在飞书上建好画板块并把 token 记进去，再用 --push")
    # 幂等 token 取内容哈希：内容没变时重跑不会写第二份。
    idem = "arch-" + hashlib.sha1(svg_path.read_bytes()).hexdigest()[:16]
    cmd = ["lark-cli", "whiteboard", "+update",
           "--whiteboard-token", token,
           "--input_format", "svg",
           "--source", f"@{svg_path.name}",
           "--idempotent-token", idem,
           "--overwrite", "--as", "user"]
    r = subprocess.run(cmd, cwd=svg_path.parent)
    if r.returncode:
        raise SystemExit("⛔ 画板更新失败")
    print(f"✓ 已推送画板 {token}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path, help="digital-costing 数据仓")
    ap.add_argument("--out", required=True, type=Path, help="SVG 输出路径")
    ap.add_argument("--push", action="store_true", help="推到飞书画板")
    a = ap.parse_args()

    root = a.root.resolve()
    cards = collect(root)
    ver = (root / "bom/shared/VERSION").read_text(encoding="utf-8").strip()
    svg = render(cards, ver)
    a.out.write_text(svg, encoding="utf-8")

    n_sys = len(cards)
    n_mod = sum(c["n_modules"] for c in cards.values())
    n_it = sum(c["items"] for c in cards.values())
    n_ufp = sum(c["ufp"] for c in cards.values())
    print(f"✓ {a.out}　{n_sys} 子系统 / {n_mod} 模块 / {n_it:,} 条 / {n_ufp:,} UFP")

    if a.push:
        push(root, a.out.resolve())


if __name__ == "__main__":
    main()
