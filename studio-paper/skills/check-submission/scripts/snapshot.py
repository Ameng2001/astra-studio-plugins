#!/usr/bin/env python3
"""版本快照 + 记录投稿放行 —— check-submission 最后一步（闸门 D9）。

    snapshot.py <paper-id> <draft.md> [--artifact <dir>] [--camera-ready] --by <导师> [--dry-run]

前置（任一不满足 → 退出码 2，不写任何东西）：
  - gates.review == approved，且 status.counts.internal_reviews ≥ 2
  - 投稿模式：submission/anonymity-report.md 存在、结论「通过」、且其 draft_hash 与当前稿件一致
  - camera-ready：status.decision ∈ {accept, major-revision}；submission/revision-checklist.md 存在且没有未勾的 `- [ ]`
  - 当天的 submission/rescan-*.md（position-paper --rescan 产出）缺失只告警

写入：投稿模式 submission/snapshot.json；camera-ready 模式 submission/snapshot-camera-ready.json
（保留投稿版本快照供 respond-reviews 引用）。字段：mode · ts · by · git{commit,dirty} · draft_hash ·
artifact_hash（排序后 "相对路径 文件哈希" 清单的 sha256）· artifact_files · ledger_hash · rescan ·
anonymity_report_hash · revision_checklist_hash。随后 gate_set(submission, approved, by, draft_hash)。

--by 必须是导师在审批检查点选择「确认」后给出的名字；插件不得代签。--dry-run 只打印快照、不写不签，
用于呈现审批摘要。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import gate_set, git_info, load_status, now_iso, sha256_file, sha256_text, workspace  # noqa: E402
from ledger_io import ledger_hash  # noqa: E402

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}


def artifact_hash(root: Path) -> tuple[str, int]:
    lines = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = Path(dp) / fn
            if p.is_file() and not p.is_symlink():
                lines.append(f"{p.relative_to(root).as_posix()} {sha256_file(p)}")
    lines.sort()
    return sha256_text("\n".join(lines)), len(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="投稿 / camera-ready 版本快照 + 记录 D9 放行")
    ap.add_argument("paper_id")
    ap.add_argument("draft")
    ap.add_argument("--artifact", default=None)
    ap.add_argument("--camera-ready", action="store_true")
    ap.add_argument("--by", default="", help="签字人（导师确认后给出）")
    ap.add_argument("--dry-run", action="store_true", help="只算快照、不写文件、不签字")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    if not a.dry_run and not a.by:
        raise SystemExit("缺 --by：快照即放行记录，必须由导师确认后带签字人名")
    draft = Path(a.draft).resolve()
    if not draft.is_file():
        raise SystemExit(f"稿件不存在: {draft}")
    st = load_status(ws)
    sub = ws / "submission"
    blockers, warnings = [], []

    if (st.get("gates", {}).get("review") or {}).get("status") != "approved":
        blockers.append(f"gates.review = {(st.get('gates', {}).get('review') or {}).get('status')!r}，fatal 处置未签字")
    n_rev = int((st.get("counts") or {}).get("internal_reviews") or 0)
    if n_rev < 2:
        blockers.append(f"counts.internal_reviews = {n_rev} < 2（写作中一次 + 冻结前一次）")
    draft_hash = sha256_file(draft)
    mode = "camera-ready" if a.camera_ready else "submit"

    anon = sub / "anonymity-report.md"
    anon_hash = sha256_file(anon) if anon.exists() else None
    if mode == "submit":
        if not anon.exists():
            blockers.append("缺 submission/anonymity-report.md —— 先跑 anonymity_scan.py")
        else:
            t = anon.read_text(encoding="utf-8")
            if "结论：通过" not in t:
                blockers.append("匿名报告结论不是「通过」—— 去掉泄露后重扫")
            m = re.search(r"draft_hash:\s*(sha256:[0-9a-f]+)", t)
            if not m or m.group(1) != draft_hash:
                blockers.append("匿名报告是针对旧版稿件的（draft_hash 不一致）—— 对当前稿件重扫")

    checklist_hash = None
    if mode == "camera-ready":
        if st.get("decision") not in ("accept", "major-revision"):
            blockers.append(f"status.decision = {st.get('decision')!r}，camera-ready 需要 accept / major-revision")
        rc = sub / "revision-checklist.md"
        if not rc.exists():
            blockers.append("缺 submission/revision-checklist.md（修订清单）")
        else:
            open_items = [l.strip() for l in rc.read_text(encoding="utf-8").splitlines() if re.match(r"^\s*[-*]\s*\[\s\]", l)]
            if open_items:
                blockers.append(f"修订清单还有 {len(open_items)} 项未勾：" + "；".join(re.sub(r"^[-*]\s*\[\s\]\s*", "", i)[:60] for i in open_items[:5]))
            checklist_hash = sha256_file(rc)

    today = now_iso()[:10]
    rescans = sorted(sub.glob(f"rescan-{today}*.md")) if sub.exists() else []
    if not rescans:
        warnings.append(f"今天（{today}）没有 submission/rescan-*.md —— 近邻重扫未跑或不是今天跑的")

    art_hash, art_n = (None, 0)
    if a.artifact:
        root = Path(a.artifact).resolve()
        if not root.is_dir():
            blockers.append(f"artifact 目录不存在: {root}")
        else:
            art_hash, art_n = artifact_hash(root)
    else:
        warnings.append("未给 --artifact，快照不含 artifact 哈希")

    g = git_info()
    if g.get("dirty"):
        warnings.append("git 工作区有未提交改动 —— 快照的 commit 不能完整复现当前状态")
    snap = {
        "paper": a.paper_id, "mode": mode, "ts": now_iso(), "by": a.by or None,
        "git": {"commit": g.get("commit"), "dirty": g.get("dirty")},
        "draft": str(draft), "draft_hash": draft_hash,
        "artifact": str(Path(a.artifact).resolve()) if a.artifact else None, "artifact_hash": art_hash, "artifact_files": art_n,
        "ledger_hash": ledger_hash(ws),
        "rescan": rescans[-1].name if rescans else None,
        "anonymity_report_hash": anon_hash,
        "revision_checklist_hash": checklist_hash,
    }
    for w in warnings:
        print(f"告警：{w}")
    if blockers:
        print("阻塞（未写快照、未记放行）：")
        for b in blockers:
            print(f"  - {b}")
        return 2
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    if a.dry_run:
        print("--dry-run：未写文件、未签字。导师确认后去掉 --dry-run 并带 --by 重跑。")
        return 0
    out = sub / ("snapshot.json" if mode == "submit" else "snapshot-camera-ready.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    gate_set(ws, "submission", "approved", a.by, draft_hash, notes=f"{mode} snapshot {out.name}")
    print(f"已写 {out}；gates.submission = approved（{a.by}）。提交动作请在会议系统由人完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
