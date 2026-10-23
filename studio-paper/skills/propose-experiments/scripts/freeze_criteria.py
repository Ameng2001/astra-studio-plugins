#!/usr/bin/env python3
"""Freeze / check / amend the pre-registered criteria of one paper (gate D3).

    freeze_criteria.py <paper-id> freeze --by <导师>     # 导师签字后冻结
    freeze_criteria.py <paper-id> check [--json]        # record-run 调用：lock 是否与现文件一致
    freeze_criteria.py <paper-id> amend --reason "..."  # 改判据：留痕、闸门回 pending（旧版在 history/v{N}/）

Expected YAML shapes (examples: ../templates/rq.yaml.example, criteria.yaml.example).
Keep them as id-keyed nested maps with inline lists so read_yaml_lite can parse them:

  proposal/rq.yaml
    paper: <paper-id>
    thesis_ref: positioning/thesis.md
    rqs:
      RQ-1:
        question: "..."
        thesis_part: "..."              # 挂论点句的哪一部分
        hypotheses:
          H-1a: {statement / direction / metric}

  proposal/criteria.yaml
    criteria:
      RQ-1:
        failure: "..."                  # 必填：什么算失败（文字）
        pass: {metric: T_tier3, op: ">=", value: 0.65}   # 必填：机器可判
        fail: {metric: T_tier3, op: "<",  value: 0.60}   # 必填：机器可判
        n_min: 60                       # 必填（或 n）
        seeds: 3                        # 必填
        contamination: excluded         # 必填：excluded | known_overlap | unknown
        success / metric / interval / protocol / baselines / figure / contamination_note   # 建议

Every freeze also snapshots the signed text to proposal/history/v{N}/{criteria.yaml,rq.yaml,criteria.lock}.

proposal/criteria.lock (JSON):
    {"version": N, "hash": sha256(criteria.yaml text), "rq_hash": sha256(rq.yaml text),
     "rqs": [...], "ts": ..., "commit": ..., "dirty": ..., "by": ...}

Exit codes: 0 ok · 1 check failed / nothing to amend · 2 refused (blockers, gate, missing files).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
from paper_ws import (gate_require, gate_set, git_info, load_config, load_status,  # noqa: E402
                      now_iso, read_yaml_lite, save_status, sha256_text, workspace)

CONTAMINATION = {"excluded", "known_overlap", "unknown"}
RQ_ID = re.compile(r"RQ-\d+")
H_ID = re.compile(r"H-\d+[a-z]?")


def die(msg: str, code: int = 2) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    raise SystemExit(code)


def paths(ws: Path) -> dict:
    p = ws / "proposal"
    return {"dir": p, "rq": p / "rq.yaml", "criteria": p / "criteria.yaml", "lock": p / "criteria.lock",
            "history": p / "history", "changes": p / "changes.md"}


def read_lock(lock: Path) -> dict | None:
    if not lock.is_file():
        return None
    try:
        return json.loads(lock.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{lock} 不是合法 JSON（被手改过？）: {e}", 1)


def next_version(history: Path) -> int:
    vs = [int(m.group(1)) for d in history.glob("v*") if (m := re.fullmatch(r"v(\d+)", d.name))] if history.is_dir() else []
    return max(vs, default=0) + 1


def validate(ws: Path, P: dict) -> tuple[list[str], list[str], list[str]]:
    """Return (rq_ids, blockers, warnings)."""
    blockers: list[str] = []
    warnings: list[str] = []
    for k in ("rq", "criteria"):
        if not P[k].is_file():
            blockers.append(f"缺 {P[k].relative_to(ws)}（模板见 skills/propose-experiments/templates/{P[k].name}.example）")
    if blockers:
        return [], blockers, warnings

    rq_doc = read_yaml_lite(P["rq"])
    cr_doc = read_yaml_lite(P["criteria"])
    rqs = rq_doc.get("rqs") if isinstance(rq_doc, dict) else None
    crit = cr_doc.get("criteria") if isinstance(cr_doc, dict) else None
    if not isinstance(rqs, dict) or not rqs:
        return [], ["rq.yaml 没有 `rqs:` 映射（RQ-n: {...}）或为空"], warnings
    if not isinstance(crit, dict):
        crit = {}
        blockers.append("criteria.yaml 没有 `criteria:` 映射（RQ-n: {...}）")

    cfg = load_config(ws)
    th = cfg.get("thresholds", {}) if isinstance(cfg, dict) else {}
    n_floor, seeds_floor = th.get("n_min"), th.get("seeds_min")

    rq_ids = list(rqs.keys())
    for rid in rq_ids:
        body = rqs[rid] if isinstance(rqs[rid], dict) else {}
        if not RQ_ID.fullmatch(str(rid)):
            blockers.append(f"{rid}: RQ 编号须为 RQ-<数字>")
        if not body.get("question"):
            blockers.append(f"{rid}: rq.yaml 缺 question")
        if not body.get("thesis_part"):
            warnings.append(f"{rid}: rq.yaml 未写 thesis_part（挂论点句哪一部分）")
        hyps = body.get("hypotheses") or {}
        if not isinstance(hyps, dict) or not hyps:
            warnings.append(f"{rid}: 没有假设 H-n")
        else:
            for hid in hyps:
                if not H_ID.fullmatch(str(hid)):
                    blockers.append(f"{rid}.{hid}: 假设编号须为 H-<数字>[a-z]")

        c = crit.get(rid)
        if not isinstance(c, dict):
            blockers.append(f"{rid}: criteria.yaml 没有这一节 —— 写不出判据的 RQ 不立项")
            continue
        if not str(c.get("failure") or "").strip():
            blockers.append(f"{rid}: 缺 failure（什么结果算失败，文字）")
        for cond in ("pass", "fail"):
            cv = c.get(cond)
            if not (isinstance(cv, dict) and cv.get("metric") and str(cv.get("op", ">=")) in (">=", ">", "<=", "<", "==")
                    and isinstance(cv.get("value"), (int, float))):
                blockers.append(f"{rid}: 缺 {cond}（机器可判条件 {{metric, op, value}}）")
        n = c.get("n_min", c.get("n"))
        if not isinstance(n, int) or n <= 0:
            blockers.append(f"{rid}: 缺 n_min / n（正整数样本量）")
        elif isinstance(n_floor, int) and n < n_floor:
            warnings.append(f"{rid}: n={n} < config.thresholds.n_min={n_floor}，结果只能进探索性")
        seeds = c.get("seeds")
        if not isinstance(seeds, int) or seeds <= 0:
            blockers.append(f"{rid}: 缺 seeds（正整数种子数）")
        elif isinstance(seeds_floor, int) and seeds < seeds_floor:
            warnings.append(f"{rid}: seeds={seeds} < config.thresholds.seeds_min={seeds_floor}，进不了主表")
        cont = c.get("contamination")
        if cont not in CONTAMINATION:
            blockers.append(f"{rid}: contamination 须为 {sorted(CONTAMINATION)} 之一（当前 {cont!r}）")
        for k in ("success", "interval", "protocol"):
            if not c.get(k):
                warnings.append(f"{rid}: 建议写 {k}")
    for extra in crit:
        if extra not in rqs:
            warnings.append(f"criteria.yaml 有 {extra}，rq.yaml 里没有（孤儿判据）")
    return rq_ids, blockers, warnings


def cmd_freeze(ws: Path, P: dict, by: str) -> int:
    if not by.strip():
        die("--by 必填：签字人是导师本人，插件不得代签")
    try:
        gate_require(ws, "thesis")
    except SystemExit as e:  # 论点句未签字 → 拒绝立项
        die(f"{e} （先跑 /studio-paper:position-paper {ws.name} 拿到 D2 签字）")
    lock = read_lock(P["lock"])
    if lock:
        cur = sha256_text(P["criteria"].read_text(encoding="utf-8")) if P["criteria"].is_file() else None
        if cur == lock.get("hash"):
            print(f"已冻结（v{lock.get('version')}，{lock.get('by')} @ {lock.get('ts')}），内容未变，无需重复冻结")
            return 0
        die("criteria.lock 已存在但 criteria.yaml 已被改动 —— 冻结后改判据必须走 amend：\n"
            f"  freeze_criteria.py {ws.name} amend --reason \"...\"")

    rq_ids, blockers, warnings = validate(ws, P)
    for w in warnings:
        print(f"⚠ {w}")
    if blockers:
        print(f"✗ 不能冻结，{len(blockers)} 项阻塞：", file=sys.stderr)
        for b in blockers:
            print(f"  - {b}", file=sys.stderr)
        return 2

    crit_text = P["criteria"].read_text(encoding="utf-8")
    rq_text = P["rq"].read_text(encoding="utf-8")
    h = sha256_text(crit_text)
    g = git_info()
    lock = {
        "paper": ws.name,
        "version": next_version(P["history"]),
        "hash": h,
        "rq_hash": sha256_text(rq_text),
        "rqs": rq_ids,
        "ts": now_iso(),
        "commit": g["commit"],
        "dirty": g["dirty"],
        "by": by,
    }
    P["lock"].write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 签字快照：history/v{N}/ 保存被签字的原文，amend 时即使工作文件已被改也能追溯
    snap = P["history"] / f"v{lock['version']}"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "criteria.yaml").write_text(crit_text, encoding="utf-8")
    (snap / "rq.yaml").write_text(rq_text, encoding="utf-8")
    shutil.copy2(P["lock"], snap / "criteria.lock")
    gate_set(ws, "criteria", "approved", by, h, notes=f"v{lock['version']} · {len(rq_ids)} RQ")
    st = load_status(ws)
    st.setdefault("counts", {})["rqs"] = len(rq_ids)
    st["stage"] = "experiments"
    save_status(ws, st)

    print(f"✓ 判据已冻结 v{lock['version']}  {h[:23]}…")
    print(f"  RQ: {', '.join(rq_ids)}   签字: {by}   commit: {g['commit']}{' (dirty)' if g['dirty'] else ''}")
    if g["dirty"]:
        print("  ⚠ 工作树有未提交改动 —— 建议提交 proposal/ 后再跑正式实验，lock 里的 commit 才能复现")
    print(f"  写入 proposal/criteria.lock + history/v{lock['version']}/ 签字快照；status.json gates.criteria = approved，stage = experiments")
    print(f"下一步: /studio-paper:record-run {ws.name}")
    return 0


def cmd_check(ws: Path, P: dict, as_json: bool) -> int:
    lock = read_lock(P["lock"])
    res: dict = {"ok": False, "lock": str(P["lock"].relative_to(ws))}
    if lock is None:
        res["reason"] = "判据未冻结（无 criteria.lock）"
    elif not P["criteria"].is_file():
        res["reason"] = "criteria.yaml 不存在"
    else:
        cur = sha256_text(P["criteria"].read_text(encoding="utf-8"))
        rq_cur = sha256_text(P["rq"].read_text(encoding="utf-8")) if P["rq"].is_file() else None
        res.update({"version": lock.get("version"), "hash": lock.get("hash"), "current_hash": cur})
        if cur != lock.get("hash"):
            res["reason"] = "criteria.yaml 冻结后被改动（未走 amend）"
        elif rq_cur != lock.get("rq_hash"):
            res["reason"] = "rq.yaml 冻结后被改动（未走 amend）"
        else:
            res["ok"] = True
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    elif res["ok"]:
        print(f"✓ 判据冻结有效 v{res['version']}  {res['hash'][:23]}…（{lock.get('by')} @ {lock.get('ts')}）")
    else:
        print(f"✗ {res['reason']} —— 正式 run 将被判为 exploratory", file=sys.stderr)
    return 0 if res["ok"] else 1


def cmd_amend(ws: Path, P: dict, reason: str) -> int:
    if not reason.strip():
        die("--reason 必填：改判据必须写明原因（评审可见变更史）")
    lock = read_lock(P["lock"])
    if lock is None:
        die("判据尚未冻结，直接改 criteria.yaml / rq.yaml 再 freeze 即可，不需要 amend", 1)
    v = lock.get("version") or next_version(P["history"])
    dest = P["history"] / f"v{v}"
    if not (dest / "criteria.yaml").is_file():
        # 快照缺失（旧版脚本冻结 / 被删）：只能存当前文件，并标注是否与签字哈希一致
        dest.mkdir(parents=True, exist_ok=True)
        for k in ("criteria", "rq", "lock"):
            if P[k].is_file():
                shutil.copy2(P[k], dest / P[k].name)
        print(f"⚠ history/v{v}/ 无签字快照，已存当前文件代替", file=sys.stderr)
    cur = sha256_text(P["criteria"].read_text(encoding="utf-8")) if P["criteria"].is_file() else None
    unsigned_edit = cur != lock.get("hash")

    g = git_info()
    if not P["changes"].is_file():
        P["changes"].write_text("# 判据变更记录（只追加）\n\n| 时间 | 操作人 | 解冻的版本 | 旧哈希 | 原因 |\n|---|---|---|---|---|\n",
                                encoding="utf-8")
    safe_reason = reason.replace("|", "｜").replace("\n", " ")
    if unsigned_edit:
        safe_reason += "（解冻前 criteria.yaml 已有未签字改动）"
    with P["changes"].open("a", encoding="utf-8") as f:
        f.write(f"| {now_iso()} | {g['user'] or '?'} | v{v}（快照 history/v{v}/） | "
                f"`{lock.get('hash')}` | {safe_reason} |\n")

    P["lock"].unlink()
    gate_set(ws, "criteria", "pending", content_hash=None, notes=f"amend: {safe_reason}")
    st = load_status(ws)
    g_c = st["gates"]["criteria"]
    g_c["approved_at"] = None  # 旧签字保存在 history/v{v}/criteria.lock
    g_c["approved_by"] = None
    st["stage"] = "proposal"
    save_status(ws, st)

    print(f"✓ 已解冻 v{v}：签字原文保留在 proposal/history/v{v}/，changes.md 追加一行")
    print(f"  status.json gates.criteria = pending，stage = proposal；criteria.lock 已移除")
    print(f"  改完 rq.yaml / criteria.yaml 后：research-advisor 预审 → 导师签字 → freeze --by <导师>")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="D3 判据冻结：freeze / check / amend")
    ap.add_argument("paper_id")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze", help="导师签字后冻结 criteria.yaml + rq.yaml")
    f.add_argument("--by", required=True, help="签字导师姓名")
    c = sub.add_parser("check", help="lock 存在且哈希一致 → 0，否则 1")
    c.add_argument("--json", action="store_true", help="机器可读输出（record-run 用）")
    m = sub.add_parser("amend", help="解冻改判据：签字快照留在 history/v{N}/，changes.md 留痕，闸门回 pending")
    m.add_argument("--reason", required=True)
    a = ap.parse_args()

    ws = workspace(a.paper_id)
    P = paths(ws)
    P["dir"].mkdir(exist_ok=True)
    if a.cmd == "freeze":
        return cmd_freeze(ws, P, a.by)
    if a.cmd == "check":
        return cmd_check(ws, P, a.json)
    return cmd_amend(ws, P, a.reason)


if __name__ == "__main__":
    sys.exit(main())
