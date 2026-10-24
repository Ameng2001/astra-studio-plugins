#!/usr/bin/env python3
"""声称词扫描 + claim 回指台账 —— review-internally 第 1 步，check-submission 再扫。

    scan_claims.py <paper-id> <draft.md> [--out review/claims.md] [--strict]
    scan_claims.py <paper-id> <draft.md> --compare review/claims.md [--report submission/claims-rescan-{date}.md]

把稿件切成句子（中文 。！？ / 英文 .!?），抽出两类句子：
  1. 含声称词（词表来自 references/claim-words.md，硬编码在下方 CLAIM_WORDS）
  2. 量化句（数字 + % / 倍 / x / ×，或 "n = 60"）
逐句回指证据：表号（表 T3 / Table 2 / Fig 1 / 图 1）、台账 run_id（须在 effective_runs 中）、
或句中数值与某条 formal run 的 metric.value 相差 ≤ 0.005（百分数先 /100 再比）。

判定：
  无证据 —— 既无表号、也回指不到台账（run_id 未入账 / 数值对不上任何 formal run）
  过强   —— 有证据，但含声称词而附近没有比较对象词（than / 比 / 相对 / vs / over …）或没有区间 / 检验
  支持   —— 其余

--out 默认 review/claims.md（相对工作区）。重跑时保留同一句已手填的「最强反例或缺失基线」列。
--compare 模式不覆盖 claims.md，只报告「新出现的命中句」与「已不在稿中的旧句」。

退出码：0 正常 · 2 --strict 下存在「无证据」，或 --compare 下新增句中有「无证据」· 1 参数 / 文件错误。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import now_iso, sha256_file, workspace  # noqa: E402
from ledger_io import effective_runs  # noqa: E402

# references/claim-words.md 的词典（改词表时两处同步）
CLAIM_WORDS_EN = [
    r"first", r"novel", r"significant(?:ly)?", r"state[- ]of[- ]the[- ]art", r"sota",
    r"outperform(?:s|ed|ing)?", r"surpass(?:es|ed|ing)?", r"robust(?:ly|ness)?",
    r"generali[sz](?:e|es|ed|ation|ing)", r"consistent(?:ly)?",
]
CLAIM_WORDS_ZH = ["首次", "首个", "新颖", "显著", "最先进", "优于", "超过", "超越", "鲁棒", "泛化", "一致"]

CLAIM_RE = re.compile(
    r"(?i)(?:\b(?:" + "|".join(CLAIM_WORDS_EN) + r")\b|" + "|".join(CLAIM_WORDS_ZH) + ")")
QUANT_RE = re.compile(r"(?i)(\d+(?:\.\d+)?\s*(?:%|％|倍|×|x\b))|\bn\s*=\s*\d+|(?<![\w.-])\d+\.\d+(?![\w.])|r-\d{4}-\d{2}-\d{2}-\d{4}")
TABLE_RE = re.compile(r"(?i)(表\s*T?-?R?\d+|\bTable\s*T?\d+|\bFig(?:ure|\.)?\s*\d+|图\s*\d+|\bT-R\d+)")
RUN_RE = re.compile(r"r-\d{4}-\d{2}-\d{2}-\d{4}")
COMPARE_RE = re.compile(r"(?i)(\bthan\b|\bvs\.?|\bversus\b|\bover\b|\bcompared\b|\brelative to\b|比|相对|相比|较)")
INTERVAL_RE = re.compile(r"(?i)(\bCI\b|置信|区间|±|\+/-|\bp\s*[<=≤]|\[\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\]|标准差|\bstd\b|seeds?)")
NUM_RE = re.compile(r"(?<![\w-])(\d+(?:\.\d+)?)\s*(%|％)?")
N_RE = re.compile(r"(?i)\bn\s*=\s*(\d+)")
TOL = 0.005

COLS = ["#", "行", "原句", "强度词", "表号", "run_id", "n 与区间", "最强反例或缺失基线", "判定"]


def resolve(ws: Path, p: str, prefer_ws: bool = True) -> Path:
    q = Path(p)
    if q.is_absolute():
        return q
    if prefer_ws and ((ws / q).exists() or not q.exists()):
        return ws / q
    return q.resolve()


def sentences(text: str):
    """yield (line_no, sentence)。跳过代码块、表格行、标题、图片行、参考文献段与文献条目行。"""
    in_code = in_refs = False
    for ln, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if s.startswith("#"):
            # 参考文献段整段跳过：arXiv id / DOI 里的数字不是量化声称
            in_refs = bool(re.match(r"(?i)^#+\s*(参考文献|references?|bibliography)\b", s))
            continue
        if in_code or in_refs or not s or s.startswith(("|", "![", "<!--")):
            continue
        if re.match(r"^[-*+]\s*\[[a-z]+\d{2}\]\s", s):   # 行内文献条目（- [key] 题目. 年份. 来源）
            continue
        s = re.sub(r"^([-*+]|\d+\.)\s+", "", s)
        # 中文句末直接切；英文 .!? 后须跟空白或行尾（避免切开 0.71 / e.g.）
        for part in re.split(r"(?<=[。！？])|(?<=[.!?])\s+", s):
            part = part.strip()
            if len(part) >= 4:
                yield ln, part


def claim_words(sent: str) -> list[str]:
    hits = []
    for m in CLAIM_RE.finditer(sent):
        w = m.group(0)
        # 句首 "First," / "First of all" 是序数，不是声称
        if w.lower() == "first" and re.match(r"(?i)\s*first\s*(,|of all)", sent[m.start():]) and m.start() == 0:
            continue
        hits.append(w.lower())
    return sorted(set(hits), key=hits.index)


def numbers(sent: str) -> list[float]:
    out = []
    clean = N_RE.sub(" ", RUN_RE.sub(" ", sent))
    clean = re.sub(r"(?i)(表\s*T?|Table\s*|Fig(?:ure|\.)?\s*|图\s*|T-R|RQ-?|H-?)\d+", " ", clean)
    for m in NUM_RE.finditer(clean):
        v = float(m.group(1))
        out.append(v / 100 if m.group(2) else v)
        if not m.group(2) and "." in m.group(1) and 1 < v <= 100:  # "71.2 分" 也可能是 0.712 的百分制写法
            out.append(v / 100)
    return out


def analyse(sent: str, runs_by_id: dict, formal: list[dict]) -> dict | None:
    words = claim_words(sent)
    quant = bool(QUANT_RE.search(sent))
    if not words and not quant:
        return None
    tables = sorted(set(re.sub(r"\s+", " ", t) for t in TABLE_RE.findall(sent)))
    cited = RUN_RE.findall(sent)
    linked, notes = [], []
    for rid in cited:
        r = runs_by_id.get(rid)
        if r is None:
            notes.append(f"{rid} 未入账")
        elif r.get("status") != "formal":
            notes.append(f"{rid} 是 {r.get('status')}")
            linked.append(r)
        else:
            linked.append(r)
    if not any(r.get("status") == "formal" for r in linked):
        for v in numbers(sent):
            for r in formal:
                mv = (r.get("metric") or {}).get("value")
                if isinstance(mv, (int, float)) and abs(mv - v) <= TOL and r not in linked:
                    linked.append(r)
                    notes.append(f"数值 {v:g} ≈ {r['run_id']}.{(r.get('metric') or {}).get('name', '')}")
    formal_linked = [r for r in linked if r.get("status") == "formal"]
    # n 与区间：优先句中，其次回指到的 run
    n_txt = []
    m = N_RE.search(sent)
    if m and not formal_linked:
        n_txt.append(f"n={m.group(1)}")
    for r in formal_linked[:2]:
        ci = (r.get("metric") or {}).get("ci")
        n_txt.append(f"n={r.get('n')}" + (f" CI{ci}" if ci else ""))
    has_interval = bool(INTERVAL_RE.search(sent)) or any((r.get("metric") or {}).get("ci") for r in formal_linked)
    has_compare = bool(COMPARE_RE.search(sent))
    if not tables and not formal_linked:
        verdict = "无证据"
    elif words and (not has_compare or not has_interval):
        verdict = "过强"
        notes.append("、".join(x for x, miss in (("缺比较对象", not has_compare), ("缺区间 / 检验", not has_interval)) if miss))
    else:
        verdict = "支持"
    return {
        "sent": sent, "words": words or (["量化"] if quant else []),
        "tables": tables, "runs": [r["run_id"] for r in linked] + [c for c in cited if c not in runs_by_id],
        "n": "; ".join(dict.fromkeys(n_txt)), "verdict": verdict, "notes": notes,
    }


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\\|", "|")).strip()


def parse_table(path: Path) -> dict[str, dict]:
    """读回 claims.md：原句(规范化) → {列名: 值}"""
    rows = {}
    if not path.exists():
        return rows
    header = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if header is None:
            if "原句" in cells:
                header = cells
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        if row.get("原句"):
            rows[norm(row["原句"])] = row
    return rows


def scan(ws: Path, draft: Path) -> list[dict]:
    runs = effective_runs(ws)
    by_id = {r["run_id"]: r for r in runs}
    formal = [r for r in runs if r.get("status") == "formal"]
    out = []
    for ln, s in sentences(draft.read_text(encoding="utf-8")):
        a = analyse(s, by_id, formal)
        if a:
            a["line"] = ln
            out.append(a)
    return out


def render(paper: str, draft: Path, items: list[dict], keep: dict[str, dict]) -> str:
    cnt = {k: sum(1 for i in items if i["verdict"] == k) for k in ("支持", "过强", "无证据")}
    lines = [
        f"# claim ↔ 证据对照表 —— {paper}", "",
        f"- 稿件：`{draft}`（{sha256_file(draft)}）",
        f"- 生成：{now_iso()} · scan_claims.py",
        f"- 合计 {len(items)} 句：支持 {cnt['支持']} · 过强 {cnt['过强']} · 无证据 {cnt['无证据']}",
        "- 「最强反例或缺失基线」列由审稿 agent / 学生手填；重跑扫描时同句保留。",
        "- 判定规则：无表号且回指不到 formal run → 无证据；有证据但声称词缺比较对象或区间 → 过强。",
        "", "| " + " | ".join(COLS) + " |", "|" + "---|" * len(COLS),
    ]
    for k, i in enumerate(items, 1):
        prev = keep.get(norm(i["sent"]), {})
        verdict = i["verdict"] + (f"（{'；'.join(i['notes'])}）" if i["notes"] else "")
        cells = [str(k), str(i["line"]), esc(i["sent"]), ", ".join(i["words"]), ", ".join(i["tables"]),
                 ", ".join(i["runs"]), esc(i["n"]), prev.get("最强反例或缺失基线", ""), verdict]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="声称词扫描 + claim 回指台账")
    ap.add_argument("paper_id")
    ap.add_argument("draft", help="稿件 markdown（或 evidence-pack 中的 md）")
    ap.add_argument("--out", default="review/claims.md", help="输出路径（相对工作区）")
    ap.add_argument("--strict", action="store_true", help="有「无证据」即退出码 2")
    ap.add_argument("--compare", default=None, help="与已存 claims.md 对账，不覆盖它")
    ap.add_argument("--report", default=None, help="--compare 时把对账结果另存（相对工作区）")
    a = ap.parse_args()

    ws = workspace(a.paper_id)
    draft = Path(a.draft).resolve()
    if not draft.is_file():
        print(f"稿件不存在: {draft}", file=sys.stderr)
        return 1
    items = scan(ws, draft)
    cnt = {k: sum(1 for i in items if i["verdict"] == k) for k in ("支持", "过强", "无证据")}

    if a.compare:
        stored_path = resolve(ws, a.compare)
        if not stored_path.exists():
            print(f"对账表不存在: {stored_path}", file=sys.stderr)
            return 1
        stored = parse_table(stored_path)
        cur = {norm(i["sent"]): i for i in items}
        new = [i for k, i in cur.items() if k not in stored]
        gone = [k for k in stored if k not in cur]
        out = [f"# 声称词再扫对账 —— {a.paper_id}", "",
               f"- 稿件：`{draft}` · 对账表：`{stored_path}` · {now_iso()}",
               f"- 当前命中 {len(items)} 句（支持 {cnt['支持']} · 过强 {cnt['过强']} · 无证据 {cnt['无证据']}）；"
               f"新增 {len(new)} · 已删除 {len(gone)}", "", "## 新增命中句（不在 claims.md 中）", ""]
        out += [f"- 行 {i['line']} [{i['verdict']}] {i['sent']}  ← {', '.join(i['words'])}"
                + (f"；{'；'.join(i['notes'])}" if i["notes"] else "") for i in new] or ["- 无"]
        out += ["", "## 已不在稿中的旧句", ""] + ([f"- {g}" for g in gone] or ["- 无"])
        blocking = [i for i in new if i["verdict"] == "无证据"]
        out += ["", f"结论：{'阻塞' if blocking else '通过'}（新增且无证据 {len(blocking)} 句）"]
        text = "\n".join(out) + "\n"
        if a.report:
            rp = resolve(ws, a.report)
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(text, encoding="utf-8")
            print(f"已写 {rp}")
        print(text)
        return 2 if blocking or (a.strict and cnt["无证据"]) else 0

    out_path = resolve(ws, a.out)
    keep = parse_table(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render(a.paper_id, draft, items, keep), encoding="utf-8")
    print(f"已写 {out_path}")
    print(f"命中 {len(items)} 句：支持 {cnt['支持']} · 过强 {cnt['过强']} · 无证据 {cnt['无证据']}")
    for i in items:
        if i["verdict"] != "支持":
            print(f"  行 {i['line']:>4} [{i['verdict']}] {i['sent'][:70]}")
    if a.strict and cnt["无证据"]:
        print("--strict：存在无证据 claim，退出码 2", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
