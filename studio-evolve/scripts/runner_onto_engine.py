#!/usr/bin/env python3
"""runner_onto_engine.py —— 把案例喂给 clife-onto-engine，产出 growth_metrics.py 要的 results.jsonl。

  runner_onto_engine.py run --engine-root <onto-fundary-plugins> --target-dir <编译产物目录> --ontology <id> \
      --state h_i --cases-dir .studio-evolve/{id}/cases [--case-ids a,b | --cases-file holdout.yaml] \
      --repeat 3 --router manual --recordings routes.json [--actor-role 医师] --out results.jsonl
  runner_onto_engine.py golden-to-cases --golden <plugin>/cq/golden.yaml --out-dir .studio-evolve/{id}/cases/sim [--family golden]

调查结论（2026-10-23，引擎 0.1.0）
  (a) 加载编译产物：插件包的 `__init__.py` 在 import 时把 Object/Link/Rule/Action 注册进 `clife_onto_engine.sdk.spi`
      这个进程级单例的 `spi.registry`（glycowise 用绝对 import；grass 用相对 import，所以要按「包」加载）。
      有 `seed_reference_data(store)` 就调用它填 `InMemoryStore`。本脚本只读：执行一律走 `ActionEngine.validate`（预演，永不落库）
      与 `oql.execute`，和引擎自己的 `cq.run_cq_suite` 同一条路径。
  (b) 自然语言 → 结构化意图的路由，引擎已有：`intent.compiler.IntentCompiler(client, registry).compile(ontology, q)`，
      LLM 只在能力清单内提议、内核校验（动作存在、参数⊆声明、必填齐全），输出 kind ∈ action/query/telemetry/advise/clarify/reject。
      client 是 `intent.llm.LLMClient` 协议：`ReplayLLMClient(recordings)` 按口语键回放原始 JSON（引擎 CI 用它固化真 Qwen 行为），
      `OpenAICompatibleClient` 接任意 OpenAI 兼容端点（读 env 或 llm.local.json）。
      本脚本的 `--router manual` = 人工转写的意图 JSON 走 ReplayLLMClient（格式与 tests/fixtures/qwen_replay.json 同：{口语: 原始JSON}），
      `--router live` = OpenAICompatibleClient 子类（temperature 0，记 usage token）。两者都经过同一个 IntentCompiler 校验。
  (c) 成本：live 路由记 prompt+completion token；动作执行无 token，steps = 1（路由）+ 1（执行）+ 评估到的违规数；
      查询 steps = 1 + OQL CostMeter.total；wall_s 为路由 + 执行墙钟。manual 路由 tokens=null（growth_metrics 退化为按 steps 计成本）。

pass 判定（对照 case.expect）
  action(X)   : 路由 kind=action 且 action==X，且 validate().would_commit
  rejected(R) : 路由 kind=action，validate() 不 commit，且 R 在违规规则名里（精确或前缀，如 R35 匹配 R35_夜间覆盖不足禁强化）
  query       : 路由 kind=query 且 oql.execute 行数 ≥ 1
  其余 kind（telemetry / advise / clarify / reject）对以上三种 expect 都算 fail（本体没把话接住）。
  路由无录制 / LLM 不可达 → pass=null, flags=[ROUTER_UNAVAILABLE]；引擎抛异常（如 handler 未实现）→ pass=null, flags=[ENGINE_ERROR:<类型>]。
  pass=null 的行不进通过率，由 growth_metrics 单独计数 —— 绝不把「跑不起来」算成通过或失败。

状态前置（case.setup，2026-10-23 加）
  真实窗口里的裁决常依赖同窗口前序动作写下的状态（前序 commit 的对象、告警、证据包……）。案例可带
    setup: [{action, params, actor_role?, actor_id?, trace_id?}, …]
  runner 对这类案例单独起一份新鲜 seed 的 store，按序真 execute 这些步骤（落库），再对口语做 validate / OQL。
  setup 里某一步被**本状态**拒绝 → 跳过该步继续搭（候选本体把这步历史改写成拒绝，是它的反事实，照常评判），flags=[SETUP_DIVERGED:<步>:<动作>]；
  抛错 → pass=null, flags=[SETUP_FAILED:<步>]（状态没搭起来不评判）。没有 setup 的案例仍共用一份只读 seed。
  动作或规则抛 NotImplementedError（声明了、实现是桩）→ pass=0, flags=[IMPL_MISSING]：算本体没做到，可评判；其它异常 → pass=null, ENGINE_ERROR。
  make_real_cases.py 把同窗口里该轨迹之前所有非 rejected 的 execute 落成 setup，于是逐案例验证能复现窗口当时的状态。

案例自带路由与禁触规则（case.route / case.expect_not，2026-10-23 加）
  route: {kind, action, params | oql, confidence}  —— 真实轨迹当时真的走过的意图。带 route 的案例不查 --recordings，
         其 q 与全部 variants 都按这份意图经同一个 IntentCompiler 校验（同一句口语在 golden 与真实窗口里路由不同时，真实案例以窗口为准）。
  expect_not: [规则名…]  —— 反馈判定「不该响」的规则（真实轨迹里引擎命中了、业务侧裁定为假阳性）。命中任一条（精确或前缀）即 fail，
         哪怕 expect 的规则也命中了：rejected(D10) 之外还响了 R51 就是没通过。
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import load_cases, load_yaml  # noqa: E402


# ---------------------------------------------------------------- engine loading
def load_engine(engine_root: Path):
    sys.path.insert(0, str(engine_root))
    import clife_onto_engine  # noqa: F401
    from clife_onto_engine.sdk import spi
    return spi


def load_plugin(target_dir: Path):
    """把编译产物目录当作包加载（支持包内相对 import）。返回模块。"""
    target_dir = target_dir.resolve()
    name = f"onto_plugin_{target_dir.name}"
    spec = importlib.util.spec_from_file_location(name, target_dir / "__init__.py",
                                                  submodule_search_locations=[str(target_dir)])
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- routers
class UsageClient:
    """OpenAICompatibleClient 的同款实现，多记一份 usage（引擎的客户端不暴露 token）。"""

    def __init__(self, config_path: str | None, temperature: float = 0.0):
        from clife_onto_engine.intent.llm import _load_config
        cfg = _load_config(config_path)
        if not (cfg["base_url"] and cfg["api_key"] and cfg["model"]):
            raise RuntimeError("LLM 配置缺失：需 base_url / api_key / model（env 或 llm.local.json）")
        from openai import OpenAI
        self._c = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
        self.model, self.temperature = cfg["model"], temperature
        self.last_usage = None

    def complete_json(self, system: str, user: str) -> dict:
        resp = self._c.chat.completions.create(
            model=self.model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"}, temperature=self.temperature, extra_body={"enable_thinking": False})
        u = getattr(resp, "usage", None)
        self.last_usage = (int(u.prompt_tokens or 0) + int(u.completion_tokens or 0)) if u else None
        return json.loads(resp.choices[0].message.content or "{}")


def make_client(router: str, recordings: Path | None, llm_config: str | None):
    if router == "manual":
        from clife_onto_engine.intent.llm import ReplayLLMClient
        if not recordings:
            sys.exit("--router manual 需要 --recordings（{口语: 意图JSON}）")
        rec = json.loads(recordings.read_text(encoding="utf-8"))
        return ReplayLLMClient(rec)
    if router == "live":
        return UsageClient(llm_config)
    sys.exit(f"未知 router {router}")


# ---------------------------------------------------------------- execution
EXPECT_RE = re.compile(r"^(query|action|rejected)(?:\((.*)\))?$")


def parse_expect(e: str) -> tuple[str, str | None]:
    m = EXPECT_RE.match(str(e).strip())
    if not m:
        raise ValueError(f"expect 非法: {e!r}")
    return m.group(1), (m.group(2).strip() if m.group(2) else None)


def rule_hit(target: str, rules: list[str]) -> bool:
    return any(r == target or r.startswith(target + "_") or r.startswith(target) and target[-1:].isdigit() for r in rules)


def run_one(q: str, expect: str, *, ontology: str, compiler, engine, store, registry, actor, client, expect_not=()) -> dict:
    """路由 + 执行一条话。返回 {pass, kind, actual, steps, tokens, wall_s, flags}。"""
    from clife_onto_engine.query import QueryView
    from clife_onto_engine.query.oql import execute as oql_execute
    kind_exp, target = parse_expect(expect)
    t0 = time.perf_counter()
    flags: list[str] = []
    tokens = None
    try:
        intent = compiler.compile(ontology, q, actor_role=actor.role)
    except KeyError:
        return {"pass": None, "kind": None, "actual": "no-recording", "steps": 1, "tokens": None,
                "wall_s": round(time.perf_counter() - t0, 4), "flags": ["ROUTER_UNAVAILABLE"]}
    except Exception as e:  # 网络 / LLM 配置
        return {"pass": None, "kind": None, "actual": f"router-error:{type(e).__name__}", "steps": 1, "tokens": None,
                "wall_s": round(time.perf_counter() - t0, 4), "flags": [f"ROUTER_UNAVAILABLE:{type(e).__name__}"]}
    tokens = getattr(client, "last_usage", None)
    steps = 1
    passed, actual = 0, intent.kind
    try:
        if intent.kind == "action":
            steps += 1
            prev = engine.validate(ontology, intent.action, dict(intent.params), actor)
            rules = [v.rule for v in prev.violations]
            steps += len(rules)
            actual = f"action({intent.action})·{'commit' if prev.would_commit else 'reject' + str(rules)}"
            if kind_exp == "action":
                passed = int(intent.action == target and prev.would_commit)
            elif kind_exp == "rejected":
                passed = int((not prev.would_commit) and rule_hit(target or "", rules))
            forbidden = [f for f in (expect_not or ()) if rule_hit(str(f), rules)]
            if forbidden:
                passed = 0
                flags.append("FORBIDDEN_RULE_HIT:" + ",".join(forbidden))
        elif intent.kind == "query":
            steps += 1
            res = oql_execute(intent.oql, QueryView(store, []), registry)
            steps += int(res.cost.get("total", 0))
            actual = f"query·{len(res.rows)} 行"
            passed = int(kind_exp == "query" and len(res.rows) >= 1)
        else:
            actual = f"{intent.kind}" + (f"·{intent.error}" if intent.error else "") + (f"·{intent.question}" if intent.question else "")
            passed = 0
    except NotImplementedError as e:
        # 动作 / 规则在本体里有声明但实现是桩（IMPL_MISSING）：这是本体没做到，不是基础设施错误 → 判不通过，可评判
        return {"pass": 0, "kind": intent.kind, "actual": f"impl-missing:{str(e)[:80]}", "steps": steps,
                "tokens": tokens, "wall_s": round(time.perf_counter() - t0, 4), "flags": ["IMPL_MISSING"]}
    except Exception as e:
        return {"pass": None, "kind": intent.kind, "actual": f"engine-error:{type(e).__name__}:{str(e)[:80]}", "steps": steps,
                "tokens": tokens, "wall_s": round(time.perf_counter() - t0, 4), "flags": [f"ENGINE_ERROR:{type(e).__name__}"]}
    return {"pass": passed, "kind": intent.kind, "actual": actual, "steps": steps, "tokens": tokens,
            "wall_s": round(time.perf_counter() - t0, 4), "flags": flags}


def apply_setup(engine, ontology: str, steps, Actor, default_role: str) -> tuple[bool, list[str]]:
    """按序真 execute 案例的 setup 步骤。返回 (可评判, flags)：
    被拒的步跳过并记 SETUP_DIVERGED（本状态改写了窗口历史，是它的反事实）；抛错记 SETUP_FAILED 且不可评判。"""
    flags: list[str] = []
    for i, st in enumerate(steps or []):
        actor = Actor(str(st.get("actor_id") or "evolve-setup"), str(st.get("actor_role") or default_role))
        try:
            res = engine.execute(ontology, st["action"], dict(st.get("params") or {}), actor)
        except Exception as e:  # noqa: BLE001
            return False, flags + [f"SETUP_FAILED:{i}:{st.get('action')}:{type(e).__name__}"]
        decision = getattr(res, "decision", None)
        if decision not in ("committed", "pending_hil"):
            flags.append(f"SETUP_DIVERGED:{i}:{st.get('action')}")
    return True, flags


def cmd_run(a) -> None:
    spi = load_engine(a.engine_root)
    plugin = load_plugin(a.target_dir)
    from clife_onto_engine.intent.compiler import IntentCompiler
    from clife_onto_engine.kernel import ActionEngine
    from clife_onto_engine.query import InMemoryStore
    from clife_onto_engine.sdk.context import Actor
    registry = spi.registry
    store = InMemoryStore()
    seeded = False
    if hasattr(plugin, "seed_reference_data"):
        plugin.seed_reference_data(store); seeded = True
    engine = ActionEngine(registry, store=store)
    client = make_client(a.router, a.recordings, a.llm_config)
    compiler = IntentCompiler(client, registry)
    actor = Actor("evolve-runner", a.actor_role)

    cases = load_cases(a.cases_dir)
    ids = None
    if a.cases_file:
        y = load_yaml(a.cases_file)
        ids = set(map(str, y.get("ids", y.get("source_cases", y)) if isinstance(y, dict) else y))
    if a.case_ids:
        ids = (ids or set()) | {x.strip() for x in a.case_ids.split(",") if x.strip()}
    todo = [cases[c] for c in sorted(cases) if ids is None or c in ids]
    missing = sorted((ids or set()) - set(cases))
    if missing:
        print(f"⚠ 案例库里没有：{missing}")
    if not todo:
        sys.exit("没有可跑的案例")

    rows, n_null, n_pass, n_total = [], 0, 0, 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("a" if a.append else "w", encoding="utf-8") as fh:
        for c in todo:
            base = {"state": a.state, "case": c["id"], "tier": int(c.get("tier", 0)), "family": c.get("family"),
                    "origin": c.get("origin", "real"), "observed_at": str(c.get("observed_at", ""))}
            utterances = [(None, c["q"])] + [(f"{v.get('kind', 'variant')}-{i}", v["q"]) for i, v in enumerate(c.get("variants") or []) if isinstance(v, dict) and v.get("q")]
            setup = c.get("setup") or []
            base["setup_steps"] = len(setup)
            expect_not = tuple(str(x) for x in (c.get("expect_not") or []))
            c_compiler = compiler
            if c.get("route"):  # 案例自带真实轨迹的意图：q 与全部 variants 都按它走，不查共享录制
                from clife_onto_engine.intent.llm import ReplayLLMClient
                c_compiler = IntentCompiler(ReplayLLMClient({q: c["route"] for _, q in utterances}), registry)
            c_store, c_engine, setup_ok, setup_flags = store, engine, True, []
            if setup:  # 状态前置：这条案例独占一份新鲜 seed，按序真 execute 前序动作后再评判
                c_store = InMemoryStore()
                if seeded:
                    plugin.seed_reference_data(c_store)
                c_engine = ActionEngine(registry, store=c_store)
                setup_ok, setup_flags = apply_setup(c_engine, a.ontology, setup, Actor, a.actor_role)
            for run in range(a.repeat):
                for variant, q in utterances:
                    if not setup_ok:
                        r = {"pass": None, "kind": None, "actual": "setup-failed", "steps": 0, "tokens": None, "wall_s": 0.0, "flags": list(setup_flags)}
                    else:
                        r = run_one(q, c["expect"], ontology=a.ontology, compiler=c_compiler, engine=c_engine, store=c_store,
                                    registry=registry, actor=actor, client=client, expect_not=expect_not)
                        r["flags"] = list(setup_flags) + list(r.get("flags") or [])
                    row = {**base, "variant": variant, "run": run, **r, "q": q, "expect": str(c["expect"])}
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    rows.append(row)
                    if variant is None:
                        n_total += 1
                        if r["pass"] is None:
                            n_null += 1
                        else:
                            n_pass += r["pass"]
    flagc: dict[str, int] = {}
    for r in rows:
        for f in r["flags"]:
            flagc[f.split(":")[0] + (":" + f.split(":")[1] if f.startswith("ENGINE_ERROR") else "")] = flagc.get(f.split(":")[0] + (":" + f.split(":")[1] if f.startswith("ENGINE_ERROR") else ""), 0) + 1
    print(f"✓ runner onto-engine · 本体 {a.ontology} · 状态 {a.state} · {len(todo)} 案例 × {a.repeat} run · 行 {len(rows)} → {a.out}")
    print(f"  原题判定：pass {n_pass} / 可判 {n_total - n_null} / 共 {n_total}（不可判 {n_null}）· seed {'已注入' if seeded else '无'} · 路由 {a.router}"
          + (f" · flags {flagc}" if flagc else ""))
    if a.verbose:
        seen = set()
        for r in rows:
            if r["variant"] is None and r["run"] == 0 and r["case"] not in seen:
                seen.add(r["case"])
                mark = "·" if r["pass"] is None else ("✓" if r["pass"] else "✗")
                print(f"  {mark} {r['case']}  期望 {r['expect']}  实际 {r['actual']}" + (f"  {r['flags']}" if r["flags"] else ""))


def cmd_golden(a) -> None:
    """把 cq/golden.yaml（- q / expect 列表）转成案例目录。tier / family 粗分，origin=sim，observed_at=今天。"""
    import yaml  # golden 是列表 YAML，需要 PyYAML
    items = yaml.safe_load(a.golden.read_text(encoding="utf-8")) or []
    a.out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    n = 0
    for i, it in enumerate(items, 1):
        q, expect = it.get("q"), str(it.get("expect", "")).strip()
        if not q or not expect:
            continue
        kind, _ = parse_expect(expect)
        tier = {"query": 1, "action": 3, "rejected": 3}[kind]
        cid = f"{a.family}-{i:02d}"
        (a.out_dir / f"{cid}.yaml").write_text(
            "\n".join([f"id: {cid}", f"q: {json.dumps(q, ensure_ascii=False)}", f"expect: {expect}", f"tier: {tier}", f"family: {a.family}",
                       "origin: sim", f"observed_at: {today}", "elements: []", "label:", f"  answer: {json.dumps(expect, ensure_ascii=False)}",
                       "  source: simulate", "variants: []"]) + "\n", encoding="utf-8")
        n += 1
    print(f"✓ golden → {n} 条案例 → {a.out_dir}（tier 按 expect 粗分：query=1、action/rejected=3；origin=sim）")


def main() -> None:
    ap = argparse.ArgumentParser(description="案例 → clife-onto-engine → results.jsonl", formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__.split("调查结论", 1)[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="跑案例，写 results.jsonl")
    r.add_argument("--engine-root", required=True, type=Path, help="含 clife_onto_engine 包的目录（onto-fundary-plugins）")
    r.add_argument("--target-dir", required=True, type=Path, help="编译产物目录（含 __init__.py，如 plugins/glycowise）")
    r.add_argument("--ontology", required=True, help="本体 id（plugin.yaml 的 ontology）")
    r.add_argument("--state", required=True, help="写进结果行的本体状态名（h_i / h_prime）")
    r.add_argument("--cases-dir", required=True, type=Path, help="案例目录（递归读全部 yaml；案例可带 setup 状态前置步骤，见模块说明）")
    r.add_argument("--case-ids"); r.add_argument("--cases-file", type=Path)
    r.add_argument("--repeat", type=int, default=1, help="每条跑几次（config.repeat_runs）")
    r.add_argument("--router", choices=["manual", "live"], default="manual")
    r.add_argument("--recordings", type=Path, help="manual：{口语: 意图JSON}")
    r.add_argument("--llm-config", default=None, help="live：llm.local.json 路径（缺省读 env）")
    r.add_argument("--actor-role", default="医师")
    r.add_argument("--out", required=True, type=Path)
    r.add_argument("--append", action="store_true")
    r.add_argument("--verbose", "-v", action="store_true")
    r.set_defaults(fn=cmd_run)
    g = sub.add_parser("golden-to-cases", help="cq/golden.yaml → 案例目录")
    g.add_argument("--golden", required=True, type=Path); g.add_argument("--out-dir", required=True, type=Path)
    g.add_argument("--family", default="golden")
    g.set_defaults(fn=cmd_golden)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
