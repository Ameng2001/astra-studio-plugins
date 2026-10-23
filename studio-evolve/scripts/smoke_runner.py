#!/usr/bin/env python3
"""smoke_runner.py —— runner_onto_engine.py 的端到端冒烟（需要本机有 onto-fundary-plugins）。

  smoke_runner.py [--engine-root ../../onto-fundary-plugins]

grass 插件（引擎自带、规则与 seed 完整）+ 引擎 CI 录制的真 Qwen 路由（tests/fixtures/qwen_replay.json）：
  fixtures/runner-grass/cases/real/ 9 条案例 → 期望 6 条通过（2 action commit、2 rejected 命中规则、2 query ≥1 行），
  3 条真阴性必须判 fail 而不是「跑不起来」：信息不足→clarify、遥测 kind、查询 0 行。
找不到引擎时退出码 0 并打印 SKIP（不能让没有引擎的机器红）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine-root", type=Path, default=HERE.parents[2] / "onto-fundary-plugins")
    a = ap.parse_args()
    if not (a.engine_root / "clife_onto_engine").is_dir():
        print(f"SKIP: 没有引擎 {a.engine_root}"); return
    rec = a.engine_root / "tests" / "fixtures" / "qwen_replay.json"
    work = Path(tempfile.mkdtemp(prefix="evolve-runner-"))
    out = work / "results.jsonl"
    r = subprocess.run([sys.executable, str(HERE / "runner_onto_engine.py"), "run", "--engine-root", str(a.engine_root),
                        "--target-dir", str(a.engine_root / "plugins" / "grass"), "--ontology", "grass", "--state", "h_i",
                        "--cases-dir", str(HERE / "fixtures" / "runner-grass" / "cases"), "--repeat", "2", "--router", "manual",
                        "--recordings", str(rec), "--actor-role", "施工方", "--out", str(out), "-v"], capture_output=True, text=True)
    print(r.stdout.strip()); 
    if r.returncode:
        print(r.stderr); raise SystemExit("runner 失败")
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    first = {r["case"]: r for r in rows if r["run"] == 0 and r["variant"] is None}
    exp_pass = {"grass-01", "grass-02", "grass-03", "grass-04", "grass-05", "grass-06"}
    exp_fail = {"grass-07", "grass-08", "grass-09"}
    assert all(first[c]["pass"] == 1 for c in exp_pass), {c: first[c]["actual"] for c in exp_pass}
    assert all(first[c]["pass"] == 0 for c in exp_fail), {c: first[c]["actual"] for c in exp_fail}
    assert not any(r["pass"] is None for r in rows), "不该有不可判的行"
    assert all(r["steps"] >= 2 for r in rows if r["pass"] == 1), "通过的案例至少路由+执行两步"
    rd = work / "readings.json"
    r2 = subprocess.run([sys.executable, str(HERE / "growth_metrics.py"), "compute", "--results", str(out), "--runner", "onto-engine",
                         "--state", "h_i", "--out", str(rd)], capture_output=True, text=True)
    print(r2.stdout.strip())
    reading = json.loads(rd.read_text())
    assert reading["n_cases"] == 9 and abs(reading["A"] - 6 / 9) < 1e-9 and reading["runs"] == 2, reading
    print("\n✓ runner 冒烟通过：真路由 + 真引擎，6 通过 / 3 真阴性，读数 A=0.667")


if __name__ == "__main__":
    main()
