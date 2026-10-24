#!/usr/bin/env python3
"""chain_drive.py —— P-B 独立链协议的链推进器：一条判定臂、一条本体链，把同一条提案流按序喂给它。

  chain_drive.py run --manifest chains/manifest.yaml --gate-mode 3d --work-dir chains/ \
      --engine-root /path/onto-fundary-plugins --config .studio-evolve/x/config.yaml \
      --cases-dir .studio-evolve/x/cases --recordings plugins/x/cq/all_routes.json [--repeat 3] \
      [--delta 0.03] [--accept-rate 0.6] [--seed 0] [--final-sets sets/real-all.yaml,sets/B-sim.yaml]
  chain_drive.py summarize --chains chains/3d/chain.json chains/1d/chain.json --out chains/F1.md

与 gate_decide 的区别：gate_decide 判一条提案（P-A：所有臂看同一个 hᵢ）；本脚本让每个臂维护**自己的**本体状态——
接受的提案合入本臂的链，拒绝的不合入，下一条提案在本臂当前状态上评判。两个臂跑完后比末轮能力（F1 / T3）。

manifest.yaml：
  ancestor_dir: path/to/ancestor/plugins/x     # 祖先本体 h_0（所有臂同一份），目录名最后一段 = 插件包名
  ontology: x
  proposals:                                    # 按时间顺序
    - id: P5  num: 5  round: 2  window: 2026-W43
      patch: studio/changes/x/proposals/P5.patch      # 相对 repo_root；patch -p1 在「含 plugins/」的沙箱根应用
      di: [sets/P5-Di.yaml]                            # 内关卡案例集（多份取并集）
      holdout: sets/holdout-2.yaml                     # 外关卡留出集
      holdout_next: sets/holdout-3.yaml                # 可选：下一轮留出集（按 B_{i+1} 判回退用）
  sets_root: .studio-evolve/x                   # di / holdout 的相对根（默认 repo_root）

每一步：cand = copy(chain_state) + patch（dry-run 失败 → PATCH_CONFLICT，跳过不判）；runner 跑 hᵢ / h' 各在 Dᵢ 与 B 上，
growth_metrics 出读数，gate_decide 以 --gate-mode 裁决（attempt 恒 1：重放流里没有修订）；ACCEPT → 链状态替换为 cand。
同一状态 × 同一案例集的读数按目录哈希缓存，不重复跑。每轮末记录链状态在本轮 B（与 B_{i+1}）上的 A / T。

诚实声明（写进输出）：这是**重放流**近似——提案由原始 3d 链归因产出，1d 链等别的臂没有自己的归因；一条臂拒绝了前序提案后，
后续补丁可能冲突（PATCH_CONFLICT 计入 steps，不算接受也不算拒绝）。真正的 P-B 需要每条链各自归因；本脚本给的是 F1 的试运行曲线。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _common import fmt, load_yaml, scripts_hash, write_json  # noqa: E402

PY = sys.executable


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, **kw)


def dir_hash(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.suffix in (".py", ".yaml", ".json", ".md") and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(d)).encode()); h.update(p.read_bytes())
    return h.hexdigest()[:16]


def set_ids(paths: list[Path]) -> list[str]:
    ids: list[str] = []
    for p in paths:
        y = load_yaml(p)
        ids += [str(x) for x in (y.get("ids", y) if isinstance(y, dict) else y)]
    return sorted(set(ids))


def apply_patch(root: Path, patch: Path) -> tuple[bool, str]:
    dry = sh(["patch", "-p1", "-d", str(root), "--dry-run", "-s", "-f", "-i", str(patch)])
    if dry.returncode != 0:
        return False, (dry.stdout + dry.stderr).strip()[-800:]
    real = sh(["patch", "-p1", "-d", str(root), "-s", "-f", "-i", str(patch)])
    return real.returncode == 0, (real.stdout + real.stderr).strip()[-800:]


class Chain:
    def __init__(self, a, manifest: dict, repo_root: Path):
        self.a, self.m, self.repo = a, manifest, repo_root
        self.sets_root = (repo_root / manifest.get("sets_root", ".")).resolve()
        self.work = (repo_root / a.work_dir / a.gate_mode).resolve()
        self.pkg = Path(manifest["ancestor_dir"]).name
        self.state_root = self.work / "state"                       # 含 plugins/<pkg>
        self.cache: dict[tuple[str, str], Path] = {}
        self.steps: list[dict] = []
        self.rounds: dict[int, dict] = {}
        (self.work / "results").mkdir(parents=True, exist_ok=True)
        (self.work / "readings").mkdir(exist_ok=True)
        (self.work / "verdicts").mkdir(exist_ok=True)

    # ---- 状态目录 ----
    def state_dir(self, root: Path) -> Path:
        return root / "plugins" / self.pkg

    def init_state(self):
        anc = (self.repo / self.m["ancestor_dir"]).resolve()
        if self.state_root.exists():
            shutil.rmtree(self.state_root)
        shutil.copytree(anc, self.state_dir(self.state_root), ignore=shutil.ignore_patterns("__pycache__"))

    def make_cand(self, pid: str) -> Path:
        root = self.work / f"cand-{pid}"
        if root.exists():
            shutil.rmtree(root)
        shutil.copytree(self.state_dir(self.state_root), self.state_dir(root), ignore=shutil.ignore_patterns("__pycache__"))
        return root

    # ---- 读数 ----
    def reading(self, target: Path, label: str, set_name: str, set_files: list[Path], tag: str) -> Path:
        key = (dir_hash(target), set_name)
        if key in self.cache:
            return self.cache[key]
        ids_file = self.work / "results" / f"{tag}.ids.yaml"
        ids_file.write_text("ids:\n" + "".join(f"- {i}\n" for i in set_ids(set_files)), encoding="utf-8")
        res = self.work / "results" / f"{tag}.jsonl"
        r = sh([PY, str(HERE / "runner_onto_engine.py"), "run", "--engine-root", self.a.engine_root, "--target-dir", str(target),
                "--ontology", self.m["ontology"], "--state", label, "--cases-dir", self.a.cases_dir, "--cases-file", str(ids_file),
                "--repeat", str(self.a.repeat), "--router", "manual", "--recordings", self.a.recordings, "--out", str(res)]
               + (["--family-override", self.a.family_override] if self.a.family_override else []))
        if r.returncode != 0:
            sys.exit(f"runner 失败 [{tag}]:\n{r.stderr[-1500:]}")
        out = self.work / "readings" / f"{tag}.json"
        g = sh([PY, str(HERE / "growth_metrics.py"), "compute", "--results", str(res), "--runner", "onto-engine", "--state", label,
                "--cases-file", str(ids_file), "--theta-tier", str(self.a.theta_tier), "--out", str(out)]
               + (["--family-override", self.a.family_override] if self.a.family_override else []))
        if g.returncode != 0:
            sys.exit(f"growth_metrics 失败 [{tag}]:\n{g.stderr[-1500:]}")
        self.cache[key] = out
        return out

    def summary(self, reading: Path) -> dict:
        d = json.loads(reading.read_text())
        return {"A": d.get("A"), "T": d.get("T"), "S_consist": d.get("S_consist"), "W_family": d.get("W_family"),
                "C_gate": d.get("C_gate"), "n_cases": d.get("n_cases"), "n_unscored": d.get("n_unscored"),
                "file": str(reading.relative_to(self.work))}

    # ---- 一步 ----
    def step(self, p: dict):
        pid, rnd = p["id"], int(p["round"])
        di_files = [self.sets_root / x for x in p["di"]]
        b_file = self.sets_root / p["holdout"]
        t0 = time.time()
        rec = {"id": pid, "num": p["num"], "round": rnd, "window": p.get("window"), "state_before": dir_hash(self.state_dir(self.state_root))}
        cand = self.make_cand(pid)
        ok, msg = apply_patch(cand, (self.repo / p["patch"]).resolve())
        if not ok:
            rec.update({"patch": "PATCH_CONFLICT", "patch_msg": msg, "decision": None})
            self.steps.append(rec); shutil.rmtree(cand)
            print(f"  {pid}: PATCH_CONFLICT（链已偏离提案的基线）"); return
        rec["patch"] = "APPLIED"
        hi_di = self.reading(self.state_dir(self.state_root), "h_i", f"Di:{pid}", di_files, f"{pid}-hi-Di")
        hp_di = self.reading(self.state_dir(cand), "h_prime", f"Di:{pid}", di_files, f"{pid}-hp-Di")
        hi_b = self.reading(self.state_dir(self.state_root), "h_i", p["holdout"], [b_file], f"{pid}-hi-B")
        hp_b = self.reading(self.state_dir(cand), "h_prime", p["holdout"], [b_file], f"{pid}-hp-B")
        verdict = self.work / "verdicts" / f"{rnd}-{p['num']}-1-{self.a.gate_mode}.json"
        cmd = [PY, str(HERE / "gate_decide.py"), "--config", self.a.config, "--proposal", str(p["num"]), "--attempt", "1", "--round", str(rnd),
               "--window", str(p.get("window") or f"round-{rnd}"), "--inner-base", str(hi_di), "--inner-cand", str(hp_di),
               "--outer-pair", "default", str(hi_b), str(hp_b), "--holdout-file", str(b_file), "--sandbox", str(cand),
               "--gate-mode", self.a.gate_mode, "--verdict-out", str(verdict)]
        if self.a.delta is not None: cmd += ["--delta", str(self.a.delta)]
        if self.a.accept_rate is not None: cmd += ["--accept-rate", str(self.a.accept_rate)]
        if self.a.seed is not None: cmd += ["--seed", str(self.a.seed)]
        g = sh(cmd)
        if g.returncode != 0 or not verdict.exists():
            sys.exit(f"gate_decide 失败 [{pid}]:\n{g.stdout[-800:]}\n{g.stderr[-1500:]}")
        v = json.loads(verdict.read_text())
        rec.update({"decision": v["decision"], "inner_pass": v.get("inner_pass"), "outer_pass": v.get("outer_pass"),
                    "triggered": v.get("triggered"), "verdict": str(verdict.relative_to(self.work)),
                    "Di": {"h_i": self.summary(hi_di), "h_prime": self.summary(hp_di)},
                    "B": {"h_i": self.summary(hi_b), "h_prime": self.summary(hp_b)}})
        if v["decision"] == "ACCEPT":
            shutil.rmtree(self.state_root); shutil.move(str(cand), str(self.state_root))
            rec["merged"] = True
        else:
            shutil.rmtree(cand); rec["merged"] = False
        rec["state_after"] = dir_hash(self.state_dir(self.state_root))
        rec["wall_s"] = round(time.time() - t0, 1)
        self.steps.append(rec)
        r = self.rounds.setdefault(rnd, {"round": rnd, "holdout": p["holdout"], "holdout_next": p.get("holdout_next"), "accepted": [], "judged": []})
        r["judged"].append(pid)
        if rec["merged"]: r["accepted"].append(pid)
        print(f"  {pid}: {v['decision']}  A_B {fmt(rec['B']['h_i']['A'])}→{fmt(rec['B']['h_prime']['A'])}  ({rec['wall_s']}s)")

    def close_round(self, rnd: int):
        r = self.rounds[rnd]
        st = self.state_dir(self.state_root)
        r["state"] = dir_hash(st)
        r["on_B"] = self.summary(self.reading(st, "h_i", r["holdout"], [self.sets_root / r["holdout"]], f"round{rnd}-state-B"))
        if r.get("holdout_next"):
            r["on_B_next"] = self.summary(self.reading(st, "h_i", r["holdout_next"], [self.sets_root / r["holdout_next"]], f"round{rnd}-state-Bnext"))

    def run(self):
        self.init_state()
        print(f"链 {self.a.gate_mode}：祖先 {self.m['ancestor_dir']} ({dir_hash(self.state_dir(self.state_root))})")
        props = sorted(self.m["proposals"], key=lambda p: (int(p["round"]), int(p["num"])))
        cur = None
        for p in props:
            if cur is not None and int(p["round"]) != cur:
                self.close_round(cur)
            cur = int(p["round"])
            self.step(p)
        if cur is not None:
            self.close_round(cur)
        final = {}
        for s in (self.a.final_sets or []):
            final[s] = self.summary(self.reading(self.state_dir(self.state_root), "h_i", s, [self.sets_root / s], f"final-{Path(s).stem}"))
        out = {"gate_mode": self.a.gate_mode, "ancestor_dir": self.m["ancestor_dir"], "ontology": self.m["ontology"],
               "delta": self.a.delta, "accept_rate": self.a.accept_rate, "seed": self.a.seed, "repeat": self.a.repeat,
               "note": "重放流近似：提案来自原始 3d 链的归因，各臂没有自己的归因；PATCH_CONFLICT 的提案不计入接受或拒绝。",
               "steps": self.steps, "rounds": [self.rounds[k] for k in sorted(self.rounds)], "final": final,
               "accepted": [s["id"] for s in self.steps if s.get("merged")],
               "n_judged": sum(1 for s in self.steps if s.get("decision")), "n_conflict": sum(1 for s in self.steps if s.get("patch") == "PATCH_CONFLICT"),
               "final_state": dir_hash(self.state_dir(self.state_root)), "script_hash": scripts_hash(), "exec": "AUTO", "family_override": self.a.family_override}
        write_json(self.work / "chain.json", out)
        print(f"✓ 链 {self.a.gate_mode}：判 {out['n_judged']} 条，接受 {len(out['accepted'])} 条 {out['accepted']}，冲突 {out['n_conflict']} → {self.work / 'chain.json'}")


def summarize(chains: list[Path], out: Path):
    data = [json.loads(p.read_text()) for p in chains]
    rounds = sorted({r["round"] for d in data for r in d["rounds"]})
    L = ["# F1 / T3 —— 独立链末轮能力（重放流试运行，探索性）", "",
         "> 每臂一条链：接受的提案合入本臂状态，下一条提案在本臂当前状态上判。提案流来自原始 3d 链的归因（各臂没有自己的归因），"
         "PATCH_CONFLICT 表示链已偏离提案基线、该提案无法在本臂上评判。数字为探索性，不进 criteria 判定。", "",
         "## F1 数据：链状态在本轮留出集 B_r 上的 A（原题总通过率）", "",
         "| round | holdout | " + " | ".join(f"{d['gate_mode']} A(B_r)" for d in data) + " | " + " | ".join(f"{d['gate_mode']} 接受" for d in data) + " |",
         "|---|---|" + "---|" * (2 * len(data))]
    for r in rounds:
        cells, acc, hold = [], [], ""
        for d in data:
            rr = next((x for x in d["rounds"] if x["round"] == r), None)
            hold = rr["holdout"] if rr else hold
            cells.append("—" if not rr else fmt(rr['on_B']['A']))
            acc.append("—" if not rr else (",".join(rr["accepted"]) or "∅"))
        L.append(f"| {r} | {hold} | " + " | ".join(cells) + " | " + " | ".join(acc) + " |")
    L += ["", "## T3 数据：末轮状态在最终评测集上的读数", "", "| set | " + " | ".join(f"{d['gate_mode']} A" for d in data) + " | " + " | ".join(f"{d['gate_mode']} T" for d in data) + " |",
          "|---|" + "---|" * (2 * len(data))]
    for s in sorted({k for d in data for k in d["final"]}):
        A = [("—" if s not in d["final"] else fmt(d['final'][s]['A'])) for d in data]
        T = [("—" if s not in d["final"] else "(" + ",".join("—" if v is None else f"{v:.2f}" for v in d["final"][s]["T"].values()) + ")") for d in data]
        L.append(f"| {s} | " + " | ".join(A) + " | " + " | ".join(T) + " |")
    L += ["", "## 各臂步骤", ""]
    for d in data:
        L.append(f"- **{d['gate_mode']}**：判 {d['n_judged']} · 接受 {len(d['accepted'])} {d['accepted']} · 冲突 {d['n_conflict']} · 末态 {d['final_state']}")
        for s in d["steps"]:
            if s.get("patch") == "PATCH_CONFLICT":
                L.append(f"  - {s['id']}（r{s['round']}）：PATCH_CONFLICT")
            else:
                un = (s["Di"]["h_prime"].get("n_unscored") or 0) - (s["Di"]["h_i"].get("n_unscored") or 0)
                L.append(f"  - {s['id']}（r{s['round']}）：{s['decision']}  A_Di {fmt(s['Di']['h_i']['A'])}→{fmt(s['Di']['h_prime']['A'])}  A_B {fmt(s['B']['h_i']['A'])}→{fmt(s['B']['h_prime']['A'])}"
                         + (f"  触发 {s['triggered']}" if s.get("triggered") else "") + (f"  ⚠ 候选多 {un} 行不可评分（引擎报错）" if un > 0 else ""))
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"✓ {out}")


def main():
    ap = argparse.ArgumentParser(description="P-B 独立链推进器")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--manifest", required=True, type=Path)
    r.add_argument("--repo-root", type=Path, default=Path.cwd())
    r.add_argument("--work-dir", required=True, help="相对 repo_root；每臂一个子目录 {work}/{gate_mode}/")
    r.add_argument("--gate-mode", required=True)
    r.add_argument("--engine-root", required=True)
    r.add_argument("--config", required=True)
    r.add_argument("--cases-dir", required=True)
    r.add_argument("--recordings", required=True)
    r.add_argument("--repeat", type=int, default=3)
    r.add_argument("--theta-tier", type=float, default=0.8)
    r.add_argument("--delta", type=float)
    r.add_argument("--accept-rate", type=float)
    r.add_argument("--seed", type=int)
    r.add_argument("--final-sets", help="逗号分隔，相对 sets_root")
    r.add_argument("--family-override", help="yaml {case_id: family}，透传给 runner 与 growth_metrics（2026-10-24；与 holdout_sample 同表，读数与留出集同键）")
    s = sub.add_parser("summarize")
    s.add_argument("--chains", nargs="+", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.cmd == "summarize":
        summarize(a.chains, a.out); return
    a.final_sets = [x.strip() for x in a.final_sets.split(",")] if a.final_sets else []
    m = load_yaml(a.manifest)
    Chain(a, m, a.repo_root.resolve()).run()


if __name__ == "__main__":
    main()
