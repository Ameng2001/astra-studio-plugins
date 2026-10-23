#!/usr/bin/env python3
"""合成内审结论 —— review-internally 第 3 步。

    compose_verdict.py <paper-id> --pass N

读 review/internal-{N}/reviewer-*.md（三个审稿 agent 按 templates/review-form.md.tmpl 填的评审表），
从每份的 `## Weaknesses` 节收集弱点条目。条目约定：

    - <弱点一句话> [fatal] → <稿中位置>
    - <弱点一句话> [fixable] → §5.2 表 T3      （→ 位置可省）

排序：fatal 在前；同级按「几位审稿人提了相似的」降序（关键词重合度聚类）。
写 review/internal-{N}/verdict.md（Top-3 拒稿理由 + 全量清单 + 空白「处置」列等导师填），
把 fixable 追加到 ledger/todo-runs.md（同一条不重复追加），status.counts.internal_reviews = max(原值, N)。

前置：gates.results == approved。
退出码：0 写出 · 2 前置不满足或没有评审表 · 1 参数错误。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import gate_require, load_config, load_status, now_iso, save_status, workspace  # noqa: E402

TAG_RE = re.compile(r"[\[【]\s*(fatal|fixable)\s*[\]】]", re.I)
LOC_RE = re.compile(r"\s*(?:→|->)\s*(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)\s*$")
STOP = set("the a an of to in and or for is are be on with this that not no it as by at from paper our we "
           "their they its was were has have does do missing lack lacks table tables figure fig section main "
           "text without but only also than more less".split())
SIM = 0.3


def section(text: str, names: tuple[str, ...]) -> str:
    out, on = [], False
    for line in text.splitlines():
        m = re.match(r"^#{2,3}\s*(.+?)\s*$", line)
        if m:
            on = any(n.lower() in m.group(1).lower() for n in names)
            continue
        if on:
            out.append(line)
    return "\n".join(out)


def parse_form(path: Path) -> dict:
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    items = []
    for line in section(text, ("weakness", "弱点")).splitlines():
        m = BULLET_RE.match(line)
        if not m:
            continue
        body = m.group(1)
        loc = ""
        lm = LOC_RE.search(body)
        if lm:
            loc = lm.group(1)
            body = body[:lm.start()]
        tm = TAG_RE.search(body)
        level = tm.group(1).lower() if tm else "未标级"
        body = TAG_RE.sub("", body).strip(" ；;。")
        if tm is None:
            tl = TAG_RE.search(loc)
            if tl:
                level = tl.group(1).lower()
                loc = TAG_RE.sub("", loc).strip()
        items.append({"text": body, "level": level, "loc": loc})
    score = re.search(r"(?im)^\s*[-*]?\s*overall\s*[:：]\s*(.+)$", text)
    conf = re.search(r"(?im)^\s*[-*]?\s*confidence\s*[:：]\s*(.+)$", text) or \
        re.search(r"(?is)##\s*confidence[^\n]*\n+\s*([^\n#]+)", text)
    return {"items": items, "overall": score.group(1).strip() if score else "?",
            "confidence": conf.group(1).strip() if conf else "?"}


def tokens(s: str) -> set[str]:
    s = s.lower()
    refs = {"tab" + m for m in re.findall(r"(?:表|table)\s*t?(\d+)", s)} | {"fig" + m for m in re.findall(r"(?:图|fig(?:ure|\.)?)\s*(\d+)", s)}
    en = {w for w in re.findall(r"[a-z][a-z0-9-]{2,}", s) if w not in STOP}
    zh = "".join(re.findall(r"[一-鿿]", s))
    return refs | en | {zh[i:i + 2] for i in range(len(zh) - 1)}


def similarity(a: set, b: set) -> float:
    """重合系数 |a∩b| / min(|a|,|b|)，且至少两个共同词，避免短句误并。"""
    common = len(a & b)
    return common / min(len(a), len(b)) if a and b and common >= 2 else 0.0


def cluster(ws_items: list[dict]) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for it in ws_items:
        it["tok"] = tokens(it["text"] + " " + it["loc"])
        best, best_s = None, 0.0
        for c in clusters:
            s = max(similarity(it["tok"], o["tok"]) for o in c)
            if s > best_s:
                best, best_s = c, s
        if best is not None and best_s >= SIM:
            best.append(it)
        else:
            clusters.append([it])
    rank = {"fatal": 0, "fixable": 1, "未标级": 2}

    def key(c):
        return (min(rank[i["level"]] for i in c), -len({i["reviewer"] for i in c}), c[0]["wid"])
    return sorted(clusters, key=key)


def esc(s: str) -> str:
    return s.replace("|", "\\|")


def main() -> int:
    ap = argparse.ArgumentParser(description="合成内审 verdict.md 与 fixable 待补队列")
    ap.add_argument("paper_id")
    ap.add_argument("--pass", dest="n", type=int, required=True, help="第几次内审（1 / 2 …）")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    gate_require(ws, "results")
    d = ws / "review" / f"internal-{a.n}"
    forms = sorted(d.glob("reviewer-*.md"))
    if not forms:
        print(f"{d} 下没有 reviewer-*.md —— 先派三个审稿 agent 写评审表", file=sys.stderr)
        return 2
    roles = (load_config(ws).get("review") or {}).get("roles") or []
    missing = [r for r in roles if not (d / f"{r}.md").exists()]
    if missing:
        print(f"提醒：缺评审表 {missing}（按已有 {len(forms)} 份合成）", file=sys.stderr)

    all_items, summary = [], []
    for f in forms:
        p = parse_form(f)
        summary.append((f.stem, p["overall"], p["confidence"], len(p["items"]),
                        sum(1 for i in p["items"] if i["level"] == "fatal")))
        for i in p["items"]:
            i["reviewer"] = f.stem
            all_items.append(i)
    for k, i in enumerate(all_items, 1):
        i["wid"] = k
    clusters = cluster(all_items)
    for ci, c in enumerate(clusters, 1):
        for i in c:
            i["cid"] = ci
    untagged = [i for i in all_items if i["level"] == "未标级"]

    L = [f"# 内审结论 internal-{a.n} —— {a.paper_id}", "",
         f"- 生成：{now_iso()} · compose_verdict.py · 评审表 {len(forms)} 份 · 弱点 {len(all_items)} 条 · 聚成 {len(clusters)} 簇",
         f"- fatal {sum(1 for i in all_items if i['level']=='fatal')} · fixable {sum(1 for i in all_items if i['level']=='fixable')} · 未标级 {len(untagged)}",
         "", "## 审稿人分数", "", "| 审稿人 | overall | confidence | 弱点数 | fatal |", "|---|---|---|---|---|"]
    L += [f"| {r} | {esc(o)} | {esc(c)} | {n} | {fa} |" for r, o, c, n, fa in summary]
    L += ["", "## Top-3 拒稿理由", "",
          "处置（导师填）：fatal → `收窄 claim`（写明改哪句，回 claims.md）或 `回到设计`（propose-experiments --amend）；fixable → `todo`（已入 ledger/todo-runs.md）。", "",
          "| # | 拒稿理由 | 等级 | 提出者 | 稿中位置 | 处置 |", "|---|---|---|---|---|---|"]
    for ci, c in enumerate(clusters[:3], 1):
        lv = "fatal" if any(i["level"] == "fatal" for i in c) else c[0]["level"]
        L.append(f"| {ci} | {esc(c[0]['text'])} | {lv} | {', '.join(sorted({i['reviewer'] for i in c}))} "
                 f"| {esc('; '.join(dict.fromkeys(i['loc'] for i in c if i['loc'])))} |  |")
    L += ["", "## 全量弱点清单", "", "| ID | 簇 | 弱点 | 等级 | 审稿人 | 稿中位置 | 处置 |", "|---|---|---|---|---|---|---|"]
    for c in clusters:
        for i in c:
            L.append(f"| W{i['wid']} | C{i['cid']} | {esc(i['text'])} | {i['level']} | {i['reviewer']} | {esc(i['loc'])} |  |")
    if untagged:
        L += ["", f"> 有 {len(untagged)} 条未标 [fatal]/[fixable]，请审稿 agent 或学生补标后重跑。"]
    L += ["", "## 处置签字", "",
          "- 每条 fatal 的处置已落到具体句子 / 设计变更：☐",
          "- 签字（人的动作）：`python3 <plugin>/scripts/paper_ws.py <paper-id> review approved --by <导师>`", ""]
    out = d / "verdict.md"
    out.write_text("\n".join(L), encoding="utf-8")

    todo = ws / "ledger" / "todo-runs.md"
    todo.parent.mkdir(parents=True, exist_ok=True)
    existing = todo.read_text(encoding="utf-8") if todo.exists() else "# 待补实验队列\n\n来源：内审 fixable / rebuttal 分诊。勾销 = 对应 run 已经 record-run 入账（写上 run_id）。\n\n"
    added = 0
    for i in all_items:
        if i["level"] != "fixable":
            continue
        tag = f"<!-- internal-{a.n}:W{i['wid']} -->"
        if tag in existing:
            continue
        existing += f"- [ ] {i['text']}" + (f" → {i['loc']}" if i["loc"] else "") + f"（internal-{a.n} W{i['wid']} · {i['reviewer']}）{tag}\n"
        added += 1
    todo.write_text(existing, encoding="utf-8")

    st = load_status(ws)
    counts = st.setdefault("counts", {})
    counts["internal_reviews"] = max(int(counts.get("internal_reviews") or 0), a.n)
    save_status(ws, st)

    print(f"已写 {out}")
    print(f"弱点 {len(all_items)} 条 → {len(clusters)} 簇；Top-3：")
    for ci, c in enumerate(clusters[:3], 1):
        print(f"  {ci}. [{c[0]['level']}] {c[0]['text'][:60]}（{len({i['reviewer'] for i in c})} 位审稿人）")
    print(f"fixable 新入待补队列 {added} 条 → {todo}")
    print(f"status.counts.internal_reviews = {counts['internal_reviews']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
