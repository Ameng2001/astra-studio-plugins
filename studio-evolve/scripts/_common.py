#!/usr/bin/env python3
"""studio-evolve 脚本公共件：案例读取、结果行读取、脚本哈希、极简 YAML 回退。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SCRIPTS = ["growth_metrics.py", "gate_decide.py", "holdout_sample.py", "runner_onto_engine.py", "_common.py"]


def scripts_hash() -> str:
    """三个脚本 + 公共件的联合 sha256（写进 verdict.json 与 growth-ledger 的 script_hash）。"""
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in SCRIPTS:
        p = here / name
        if p.is_file():
            h.update(name.encode()); h.update(p.read_bytes())
    return "sha256:" + h.hexdigest()


def load_yaml(path: Path) -> dict:
    """优先 PyYAML；没有时退化为只读顶层标量键（id / tier / family / origin / observed_at / expect）。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text) or {}
    except ImportError:
        out: dict = {}
        for line in text.splitlines():
            m = re.match(r"^([A-Za-z_][\w]*):\s*(.*?)\s*(?:#.*)?$", line)
            if m and m.group(2):
                v = m.group(2).strip().strip('"').strip("'")
                out[m.group(1)] = int(v) if v.isdigit() else v
        out.setdefault("variants", [])
        return out


def load_cases(cases_dir: Path, pattern: str = "*.yaml") -> dict[str, dict]:
    """递归读案例 yaml，返回 {case_id: case}。"""
    cases: dict[str, dict] = {}
    for p in sorted(cases_dir.rglob(pattern)):
        if p.name.startswith("_") or p.parent.name == "holdout":
            continue
        c = load_yaml(p)
        cid = str(c.get("id") or p.stem)
        c["id"] = cid
        c.setdefault("origin", "sim" if "/sim/" in str(p) else "real")
        c["_path"] = str(p)
        cases[cid] = c
    return cases


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fmt(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.3f}"
    return str(x)


def load_family_override(path) -> dict[str, str]:
    """family override 表（2026-10-24）：yaml，顶层 `families: {case_id: family}` 或直接是映射 → {case_id: family}。
    holdout_sample / runner_onto_engine / growth_metrics 三处共用同一读法，保证留出集分层与读数同键。"""
    if not path:
        return {}
    ov = load_yaml(Path(path))
    ov = ov.get("families", ov) if isinstance(ov, dict) else {}
    return {str(k): str(v) for k, v in (ov or {}).items() if v}


def apply_family_override(items, ov: dict[str, str], key: str = "case") -> int:
    """就地覆盖 items（dict 列表或 {id: dict}）的 family；返回实际改变的条数。key 是 dict 里的 id 字段名（案例用 id，结果行用 case）。"""
    n = 0
    seq = items.values() if isinstance(items, dict) else items
    for it in seq:
        fam = ov.get(str(it.get(key)))
        if fam:
            if it.get("family") != fam:
                n += 1
            it["family"] = fam
    return n

