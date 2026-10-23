#!/usr/bin/env python3
"""Create the studio-paper runtime workspace for one paper.

    init_workspace.py <paper-id> --venue se|ml[:<name>[:<year>]]
                      [--title ...] [--cfp-url ...]
                      [--import-evolve <ontology_id>] [--allow-missing-source]
                      [--rebuttal-limit ...]

Produces .studio-paper/{paper-id}/{config.yaml,status.json,README.md} plus the
seven stage directories (each with .gitkeep). Never overwrites: if the
workspace already exists it prints the current stage + gate summary and exits 0.

Exit codes: 0 created / already exists · 2 bad arguments or refused.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN / "scripts"))
from paper_ws import GATES, load_status, now_iso, project_root, workspace  # noqa: E402

TEMPLATES = PLUGIN / "templates"
STAGE_DIRS = ["positioning", "proposal", "ledger", "results", "review", "submission", "rebuttal"]
GATE_LABEL = {
    "thesis": "D2 论点句",
    "criteria": "D3 判据冻结",
    "results": "D7 结果冻结",
    "review": "D8 fatal 处置",
    "submission": "D9 投稿放行",
    "rebuttal": "D11 回应表",
}
DEFAULT_REBUTTAL = {"se": "TBD（按当年 CFP；SE 会议常见 500–1000 词）",
                    "ml": "TBD（按当年 CFP；OpenReview 常见每条 5000 字符）"}


def die(msg: str, code: int = 2) -> None:
    print(f"✗ {msg}", file=sys.stderr)
    raise SystemExit(code)


def yq(s: str) -> str:
    """Double-quoted YAML scalar, safe for both PyYAML and read_yaml_lite."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace(" #", " ＃")
    return f'"{s}"'


def parse_venue(v: str) -> tuple[str, str, str]:
    parts = v.split(":")
    if len(parts) > 3 or parts[0] not in ("se", "ml"):
        die(f"--venue 格式应为 se|ml[:<名称>[:<年份>]]，收到 {v!r}（家族决定评审表与 rebuttal 规则）")
    family = parts[0]
    name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "TBD"
    year = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "null"
    if year != "null" and not re.fullmatch(r"20\d\d", year):
        die(f"会议年份应为四位数字，收到 {year!r}")
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
        die(f"会议名称只允许字母数字与 _.-，收到 {name!r}")
    return family, name, year


def gate_summary(ws: Path) -> str:
    st = load_status(ws)
    lines = [f"  阶段: {st.get('stage')}   iteration: {st.get('iteration')}"]
    for g in GATES:
        info = st.get("gates", {}).get(g, {})
        s = info.get("status")
        extra = f"（{info.get('approved_by')} @ {info.get('approved_at')}）" if s == "approved" else ""
        lines.append(f"  {GATE_LABEL[g]:<12} gates.{g:<10} = {s}{extra}")
    return "\n".join(lines)


def render_config(paper_id, title, family, name, year, cfp_url, ontology_id, rebuttal_limit) -> str:
    text = (TEMPLATES / "runtime-config.yaml.tmpl").read_text(encoding="utf-8")
    text = text.replace('title: "{{title}}"', f"title: {yq(title)}")
    text = text.replace('cfp_url: ""', f"cfp_url: {yq(cfp_url)}")
    if ontology_id is None:
        # Drop the example source; keep an explicit empty list.
        text = re.sub(
            r"( *)sources:[^\n]*\n(?:\1  [^\n]*\n)+",
            lambda m: f"{m.group(1)}sources: []                    # record-run --import 的来源；--import-evolve 登记\n",
            text, count=1)
    else:
        text = text.replace("{{ontology_id}}", ontology_id)
    text = (text.replace("{{paper_id}}", paper_id)
                .replace("{{venue_family}}", family)
                .replace("{{venue_name}}", name)
                .replace("{{venue_year}}", year)
                .replace("{{rebuttal_limit}}", yq(rebuttal_limit)))
    left = re.findall(r"\{\{[^}]+\}\}", text)
    if left:
        die(f"config 模板有未填占位符: {sorted(set(left))}")
    return text


def render_status(paper_id: str) -> str:
    text = (TEMPLATES / "runtime-status.json.tmpl").read_text(encoding="utf-8")
    text = text.replace("{{PAPER_ID}}", paper_id).replace("{{TIMESTAMP}}", now_iso())
    left = re.findall(r"\{\{[^}]+\}\}", text)
    if left:
        die(f"status 模板有未填占位符: {sorted(set(left))}")
    json.loads(text)  # must stay valid JSON
    return text


def render_readme(paper_id, title, family, name, year, ontology_id) -> str:
    src = (f"`.studio-evolve/{ontology_id}/ledger/growth-ledger.md`（record-run --import）"
           if ontology_id else "无（之后可在 config.yaml 的 `ledger.sources` 登记）")
    return f"""# {title}

paper-id `{paper_id}` · 目标会议 {family.upper()} / {name} / {year} · 台账导入源：{src}

本目录由 `/studio-paper:init-workspace` 创建，git 跟踪。阈值见 `config.yaml`，闸门状态见 `status.json`（`/studio-paper:status` 汇总）。

## 阶段与目录

| 目录 | 阶段 | 主要产物 | 技能 |
|---|---|---|---|
| `positioning/` | 定位 | literature-matrix.md · nearest-diff.md · thesis.md | position-paper |
| `proposal/` | 开题 | rq.yaml · criteria.yaml · criteria.lock · figure-plan.md · baselines.md · changes.md | propose-experiments |
| `ledger/` | 实验 | runs.jsonl（只追加）· hypotheses.md · drift.md | record-run |
| `results/` | 结果冻结 | tables/ · figures/ · coverage.md · freeze.lock · evidence-pack/ | freeze-results |
| `review/` | 内审 | claims.md · internal-{{n}}/ | review-internally |
| `submission/` | 投稿 | checklist · anonymity-report.md · snapshot.json | check-submission |
| `rebuttal/` | 回应 | reviews.md · responses.md · outcome.md | respond-reviews |

## 六个导师闸门（插件只预审，签字是人的动作）

| 闸门 | status.json | 签字时机 | 不放行的典型情形 |
|---|---|---|---|
| D2 论点句 | `gates.thesis` | 定位 → 开题 | 说不出最近三篇；用「和」拼两个贡献 |
| D3 判据冻结 | `gates.criteria` | 开题 → 实验 | 某 RQ 没有失败判据；n / seeds / 污染关系缺 |
| D7 结果冻结 | `gates.results` | 实验 → 写作 | 只有成功 run；有 RQ 无表；挑窗挑种子 |
| D8 fatal 处置 | `gates.review` | 内审后 | fatal 项无处置；claim 回指不到证据 |
| D9 投稿放行 | `gates.submission` | 投稿前 | 近邻未重扫；匿名泄露；artifact 跑不通 |
| D11 回应表 | `gates.rebuttal` | rebuttal 提交前 | 用「我们认为」回应数据质疑；补实验未入账 |

规则：判据未冻结（`proposal/criteria.lock` 缺失或哈希不符）时，record-run 只收 exploratory；改判据必须走 `--amend`，旧版本留在 `proposal/history/`。

## 下一步

```
/studio-paper:position-paper {paper_id}
```
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="创建 .studio-paper/{paper-id}/ 运行时工作区")
    ap.add_argument("paper_id", help="kebab-case 论文 id，即目录名")
    ap.add_argument("--venue", required=True, help="se|ml[:<名称>[:<年份>]]，如 se:ICSE:2027")
    ap.add_argument("--title", default=None, help="论文暂定题目（默认用 paper-id）")
    ap.add_argument("--cfp-url", default="", help="当年 CFP 链接")
    ap.add_argument("--import-evolve", metavar="ONTOLOGY_ID", default=None,
                    help="登记 .studio-evolve/{id}/ledger/growth-ledger.md 为台账导入源")
    ap.add_argument("--allow-missing-source", action="store_true",
                    help="growth-ledger.md 尚不存在时仍登记（studio-evolve 还没跑出第一轮）")
    ap.add_argument("--rebuttal-limit", default=None, help="rebuttal 字数或页数限制，如 '500 words'")
    a = ap.parse_args()

    ws = workspace(a.paper_id, must_exist=False)
    if ws.exists():
        if (ws / "status.json").is_file():
            print(f"工作区已存在，不覆盖: {ws}")
            print(gate_summary(ws))
            print(f"查看全部论文: /studio-paper:status")
            return 0
        die(f"{ws} 已存在但没有 status.json —— 不是 studio-paper 工作区或已损坏，请人工检查")

    family, name, year = parse_venue(a.venue)
    title = a.title or a.paper_id
    rebuttal_limit = a.rebuttal_limit or DEFAULT_REBUTTAL[family]

    if a.import_evolve is not None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]*", a.import_evolve):
            die(f"ontology_id 不合法: {a.import_evolve!r}")
        src = project_root() / ".studio-evolve" / a.import_evolve / "ledger" / "growth-ledger.md"
        if not src.is_file():
            if not a.allow_missing_source:
                die(f"找不到 {src}\n  studio-evolve 还没跑出成长台账？确认 ontology_id，或加 --allow-missing-source 先登记")
            print(f"⚠ {src} 尚不存在，已按 --allow-missing-source 登记", file=sys.stderr)

    config = render_config(a.paper_id, title, family, name, year, a.cfp_url, a.import_evolve, rebuttal_limit)
    status = render_status(a.paper_id)

    ws.mkdir(parents=True)
    (ws / "config.yaml").write_text(config, encoding="utf-8")
    (ws / "status.json").write_text(status, encoding="utf-8")
    for d in STAGE_DIRS:
        (ws / d).mkdir()
        (ws / d / ".gitkeep").write_text("", encoding="utf-8")
    (ws / "README.md").write_text(render_readme(a.paper_id, title, family, name, year, a.import_evolve),
                                  encoding="utf-8")

    rel = ws.relative_to(project_root())
    print(f"✓ 已创建 {rel}/")
    print(f"  会议: {family} / {name} / {year}   题目: {title}")
    print(f"  台账导入源: {'.studio-evolve/' + a.import_evolve if a.import_evolve else '无'}")
    print(f"  文件: config.yaml status.json README.md + {len(STAGE_DIRS)} 个阶段目录")
    print(f"  闸门: {len(GATES)} 个，均为 not_started")
    print(f"下一步: /studio-paper:position-paper {a.paper_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
