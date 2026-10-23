#!/usr/bin/env python3
"""Shared workspace helpers for studio-paper scripts (stdlib only).

Workspace root: .studio-paper/{paper_id}/ under the project root (cwd or
$STUDIO_PAPER_ROOT). Every skill script imports this module via
`sys.path.insert(0, <plugin>/scripts)`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
STAGES = ["positioning", "proposal", "experiments", "writing", "review", "submission", "rebuttal", "published"]
GATES = ["thesis", "criteria", "results", "review", "submission", "rebuttal"]


def now_iso() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def project_root() -> Path:
    return Path(os.environ.get("STUDIO_PAPER_ROOT", os.getcwd())).resolve()


def workspace(paper_id: str, must_exist: bool = True) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", paper_id):
        raise SystemExit(f"paper-id 必须是 kebab-case: {paper_id!r}")
    ws = project_root() / ".studio-paper" / paper_id
    if must_exist and not ws.is_dir():
        raise SystemExit(f"工作区不存在: {ws} —— 先跑 /studio-paper:init-workspace {paper_id}")
    return ws


def read_yaml_lite(path: Path) -> dict:
    """Minimal YAML reader for the flat/nested maps this plugin writes.

    Supports: nested maps by indentation, scalars, inline lists [a, b],
    block lists of scalars, inline maps {a: b}. Enough for config.yaml /
    rq.yaml / criteria.yaml; anything fancier should use PyYAML if present.
    """
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        pass
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lines.append((len(line) - len(line.lstrip()), line.strip()))
    value, _ = _parse_block(lines, 0, 0)
    return value if isinstance(value, dict) else {}


def _parse_block(lines, i, indent):
    """Parse lines[i:] at exactly `indent`; return (value, next_index)."""
    if i >= len(lines) or lines[i][0] < indent:
        return {}, i
    is_list = lines[i][1].startswith("- ")
    out: object = [] if is_list else {}
    while i < len(lines):
        ind, text = lines[i]
        if ind < indent:
            break
        if ind > indent:  # stray deeper line without a parent key; skip
            i += 1
            continue
        if is_list:
            if not text.startswith("- "):
                break
            body = text[2:].strip()
            if ":" in body and not body.startswith(("[", "{", '"', "'")):
                # map item: first key on the dash line, rest at indent+2
                k, _, v = body.partition(":")
                item = {}
                if v.strip():
                    item[k.strip()] = _scalar(v)
                    i += 1
                else:
                    sub, i = _parse_block(lines, i + 1, indent + 2)
                    item[k.strip()] = sub
                rest, i = _parse_block(lines, i, indent + 2) if i < len(lines) and lines[i][0] == indent + 2 and not lines[i][1].startswith("- ") else ({}, i)
                if isinstance(rest, dict):
                    item.update(rest)
                out.append(item)
            else:
                out.append(_scalar(body))
                i += 1
        else:
            if text.startswith("- ") or ":" not in text:
                break
            k, _, v = text.partition(":")
            k = k.strip(); v = v.strip()
            if v:
                out[k] = _scalar(v)
                i += 1
            else:
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                if nxt and nxt[0] > indent:
                    out[k], i = _parse_block(lines, i + 1, nxt[0])
                elif nxt and nxt[0] == indent and nxt[1].startswith("- "):
                    out[k], i = _parse_block(lines, i + 1, indent)  # list at same indent as key
                else:
                    out[k] = {}
                    i += 1
    return out, i


def _scalar(v: str):
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        return [_scalar(x) for x in inner.split(",")] if inner else []
    if v.startswith("{") and v.endswith("}"):
        out = {}
        for part in v[1:-1].split(","):
            if ":" in part:
                a, _, b = part.partition(":")
                out[a.strip()] = _scalar(b)
        return out
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if v in ("null", "~", "None"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def load_config(ws: Path) -> dict:
    return read_yaml_lite(ws / "config.yaml")


def load_status(ws: Path) -> dict:
    return json.loads((ws / "status.json").read_text(encoding="utf-8"))


def save_status(ws: Path, status: dict) -> None:
    status["updated_at"] = now_iso()
    (ws / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def git_info(cwd: Path | None = None) -> dict:
    cwd = cwd or project_root()
    def run(*args):
        try:
            return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.DEVNULL, text=True).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""
    commit = run("rev-parse", "--short", "HEAD")
    dirty = bool(run("status", "--porcelain")) if commit else None
    user = run("config", "user.name") or os.environ.get("USER", "")
    return {"commit": commit or None, "dirty": dirty, "user": user}


def gate_require(ws: Path, gate: str) -> None:
    st = load_status(ws)
    g = st.get("gates", {}).get(gate, {})
    if g.get("status") != "approved":
        raise SystemExit(f"前置闸门未签字: gates.{gate} = {g.get('status')!r} —— 导师签字后再继续")


def gate_set(ws: Path, gate: str, status: str, by: str = "", content_hash: str | None = None, notes: str = "") -> dict:
    """status ∈ pending | approved | rejected. Only a human-provided `by` may approve."""
    if gate not in GATES:
        raise SystemExit(f"未知闸门 {gate}; 可选 {GATES}")
    if status == "approved" and not by:
        raise SystemExit("approved 必须带签字人（--by），插件不得代签")
    st = load_status(ws)
    g = st.setdefault("gates", {}).setdefault(gate, {})
    g.update({"status": status, "notes": notes})
    if status == "approved":
        g.update({"approved_at": now_iso(), "approved_by": by, "hash": content_hash})
    elif status == "pending":
        g.update({"requested_at": now_iso(), "hash": content_hash})
    save_status(ws, st)
    return g


DECISIONS = ["accept", "major-revision", "reject"]


def decision_set(ws: Path, decision: str, notes: str = "") -> dict:
    if decision not in DECISIONS:
        raise SystemExit(f"决定须为 {DECISIONS}")
    st = load_status(ws)
    st["decision"] = decision
    st["decision_at"] = now_iso()
    if notes:
        st["decision_notes"] = notes
    if decision == "accept":
        st["stage"] = "published"
    elif decision == "reject":
        st["stage"] = "positioning"  # 改投：回定位重扫近邻
    save_status(ws, st)
    return {"decision": decision, "stage": st["stage"]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="gate helper: record a human gate decision (or the venue decision) in status.json")
    ap.add_argument("paper_id")
    ap.add_argument("gate", choices=GATES + ["decision"])
    ap.add_argument("status", choices=["pending", "approved", "rejected"] + DECISIONS)
    ap.add_argument("--by", default="", help="签字人（approved 必填）")
    ap.add_argument("--hash", default=None, help="被签字产物的内容哈希")
    ap.add_argument("--notes", default="")
    a = ap.parse_args()
    ws = workspace(a.paper_id)
    if a.gate == "decision":
        print(json.dumps(decision_set(ws, a.status, a.notes), ensure_ascii=False)); raise SystemExit(0)
    g = gate_set(ws, a.gate, a.status, a.by, a.hash, a.notes)
    print(json.dumps({"gate": a.gate, **g}, ensure_ascii=False, indent=2))
