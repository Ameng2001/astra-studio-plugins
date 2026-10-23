#!/usr/bin/env python3
"""rebuttal 字数 / 页数预算检查 —— respond-reviews 第 6 步。

    budget_check.py <paper-id> rebuttal/responses.md [--limit "5000 chars"]

上限取 config.yaml 的 rebuttal.limit（--limit 可覆盖）：
  整数 → 词；"500 words" / "500词" → 词；"5000 chars" / "5000字符" / "5000c" → 字符；
  "1page" / "1 页" → 每页约 500 词。
计数对象：回应表「回应」列的文字（文件里没有回应表则数整个文件）。
词数 = 英文单词数 + 中日韩字符数（每字计 1）；字符数 = 文字长度（含空格）。

超限 → 退出码 1，并按 priority 列（1 = 最重要，数字越大越先裁）列出裁剪候选与累计可省量。
退出码：0 未超 · 1 超限 · 2 上限未配置或无法解析。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paper_ws import load_config, workspace  # noqa: E402
from rebuttal_table import col, read_table, resolve  # noqa: E402

WORDS_PER_PAGE = 500


def parse_limit(v) -> tuple[int, str] | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v), "words"
    s = str(v).strip().lower()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(pages?|页)$", s)
    if m:
        return int(float(m.group(1)) * WORDS_PER_PAGE), "words"
    m = re.match(r"^(\d+)\s*(chars?|characters?|字符|c)$", s)
    if m:
        return int(m.group(1)), "chars"
    m = re.match(r"^(\d+)\s*(words?|词|字|w)?$", s)
    if m:
        return int(m.group(1)), "words"
    return None


def count(text: str, unit: str) -> int:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    if unit == "chars":
        return len(text.strip())
    cjk = len(re.findall(r"[぀-ヿ㐀-䶿一-鿿가-힯]", text))
    en = len(re.findall(r"[A-Za-z0-9]+(?:[-'.][A-Za-z0-9]+)*", text))
    return cjk + en


def main() -> int:
    ap = argparse.ArgumentParser(description="rebuttal 字数 / 页数预算检查")
    ap.add_argument("paper_id")
    ap.add_argument("responses", help="回应表（相对工作区，如 rebuttal/responses.md）")
    ap.add_argument("--limit", default=None, help="覆盖 config.rebuttal.limit")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    path = resolve(ws, a.responses)
    if not path.is_file():
        print(f"回应表不存在: {path}", file=sys.stderr)
        return 2
    raw_limit = a.limit if a.limit is not None else (load_config(ws).get("rebuttal") or {}).get("limit")
    lim = parse_limit(raw_limit)
    if lim is None:
        print(f"rebuttal.limit 未配置或无法解析: {raw_limit!r} —— 按当年 CFP 写进 config.yaml（如 500 / \"5000 chars\" / \"1page\"）",
              file=sys.stderr)
        return 2
    limit, unit = lim
    text = path.read_text(encoding="utf-8")
    header, rows = read_table(text)
    rc = col(header, "回应") if header else None
    if rc:
        idc = col(header, "ID") or header[0]
        pc = col(header, "priority", "优先级")
        per = [(r.get(idc, "?"), r.get(pc, "") if pc else "", count(r.get(rc, ""), unit)) for r in rows]
        used = sum(n for _, _, n in per)
        scope = f"回应列 {len(rows)} 行"
    else:
        per, pc = [], None
        used = count(text, unit)
        scope = "整个文件（未找到含「回应」列的表）"
    unit_zh = "词" if unit == "words" else "字符"
    pct = used * 100 / limit if limit else 0
    print(f"预算：{used} / {limit} {unit_zh}（{pct:.0f}%）· 计数范围：{scope} · 上限来源：{raw_limit!r}")
    if used <= limit:
        print("未超限。")
        return 0
    over = used - limit
    print(f"超出 {over} {unit_zh}。裁剪候选（priority 数字越大越先裁）：")
    if not per:
        print("  无回应表可排序 —— 手工压缩。")
        return 1

    def pkey(x):
        try:
            return -float(x[1])
        except (TypeError, ValueError):
            return float("-inf")  # 没填 priority 的最先列出，提醒补
    saved = 0
    for rid, pr, n in sorted(per, key=pkey):
        saved += n
        mark = "  ← 裁到这里即可达标" if saved >= over and saved - n < over else ""
        print(f"  {rid:<8} priority={pr or '未填':<4} {n:>5} {unit_zh}  累计可省 {saved}{mark}")
    if not pc:
        print("提示：回应表没有 priority 列，无法按优先级排序。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
