"""bom_logical_files —— 按声明的逻辑数据组补建 ILF/ELF，并施加单一维护者规则。

## 分工

`bom_decompose` 从描述里切出**事务功能**（EI/EO/EQ）—— 那能自动做，
因为「支持查看X」这种句子本身就描述了一个基本过程。

**逻辑文件不能自动抽。** 试过用正则从描述里抓名词短语，抓出来的是
「编辑和禁用等」「通过大」「支持园所」—— 中文名词边界不是正则能定的。
拿那个往 BOM 里写 ILF，是把噪声当功能点卖。

所以数据组与维护方**由人声明**（`bom/logical-files.yaml`），本模块负责：

  1. 在已拆出的基本过程里找每个数据组的引用证据（按 name + aliases 匹配）
  2. 校验证据是否够：引用数 < min_references 的报「声明了但无证据」
  3. 校验维护方是否成立：维护方子系统里没有 EI 引用它 → 报错
  4. 按**单一维护者规则**落条目：维护方记 ILF，其余引用方各记一条 ELF
  5. 把「谁引用了它、凭哪句话」写进 rationale —— 每条都能回答「为什么是 ELF」

## 单一维护者规则

一个逻辑文件只由一个子系统记 ILF，其余记 ELF（业务侧 2026-08-07 裁定）。
NESMA 表7.5：「只有当一个逻辑文件不是应用程序的内部逻辑文件时，
它才会被计为一个外部逻辑文件」。

康养项目上出过 25 处重复（「长者档案」×5、「订单」×6）—— 同一个数据组
在每个用到它的子系统各记一次 ILF，规模凭空翻几倍。门禁 G-15 拦的就是这个。

用法：
    python3 bom_logical_files.py --bom <bom_dir> --scope <split.yaml> \\
        [--version 0.3.0] [--apply]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

p_exists = Path.exists

#: 维护动词 —— 命中才算「维护引用」，即该子系统对这个数据组执行了增删改
#: 「管理」是中文需求里最常见的维护信号（「教学计划管理」「充值管理」），
#: 漏了它会让两个确有维护动作的数据组被判成「维护方无维护动作」。
#: 它确实偏宽（「设备监控中心管理」可能只读），但本表只用于**校验**声明的
#: 维护方站不站得住，维护方本身由人裁定 —— 宽一点不会改变归属，
#: 而漏掉会让正确的声明被误报。命中的动词写进 rationale，复核时可查。
MAINTAIN_VERBS = ("新增", "添加", "编辑", "修改", "删除", "录入", "维护",
                  "配置", "设置", "上传", "导入", "审核", "登记", "创建",
                  "发布", "禁用", "启用", "注册", "绑定", "分配",
                  "管理", "充值", "结算")


def _boundary_map(bom_dir: Path) -> dict[str, str]:
    """系统 → 功能点计数的应用边界。

    **表格组织粒度 ≠ 应用边界。** NESMA 的 ELF 按应用边界计：边界内多个模块
    引用同一个逻辑文件只算一次，跨边界才计 ELF。把基座从 1 个系统拆成 7 个，
    逻辑文件 UFP 从 758 涨到 1,168 —— 那 410 个功能点是分表分出来的，
    不是建设内容带来的。所以边界在 taxonomy 里单独声明，与 systems 解耦。
    """
    tax = yaml.safe_load((bom_dir / "taxonomy.yaml").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for b, spec in (tax.get("counting_boundaries") or {}).items():
        for s in spec.get("systems") or []:
            out[s] = b
    return out


def analyze(bom_dir: Path, scope: dict, decl: dict) -> dict[str, Any]:
    effort_systems = {x["name"] for x in scope["effort_method"]["systems"]}
    bmap = _boundary_map(bom_dir)
    min_refs = decl.get("min_references", 2)

    items: list[dict] = []
    for f in sorted(bom_dir.glob("items/*.yaml")):
        items += yaml.safe_load(f.read_text(encoding="utf-8"))["items"]

    # **排除逻辑文件条目自身。** 它们的 description 就是声明里的 basis，
    # 而 basis 里必然写着数据组的名字 —— 于是逻辑文件互相匹配，引用数凭空虚增。
    # 实测：「设备主数据」86 条引用里有一批是 LF.* 条目命中我自己写的那句话。
    # 用虚增的引用数去论证拆分，是拿自己的话当证据。
    scoped = [i for i in items
              if i["path"]["system"] not in effort_systems
              and not i["id"].startswith("LF.")]

    results: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []

    for g in decl["groups"]:
        needles = [g["name"]] + list(g.get("aliases") or [])
        refs: dict[str, list[dict]] = defaultdict(list)
        for it in scoped:
            text = f"{it.get('name','')}。{it.get('description','')}"
            hit = next((n for n in needles if n in text), None)
            if not hit:
                continue
            # 按**边界**归集，不按系统 —— 见 _boundary_map
            refs[bmap.get(it["path"]["system"], it["path"]["system"])].append({
                "id": it["id"], "hit": hit,
                "maintains": any(v in text for v in MAINTAIN_VERBS),
                "quote": (it.get("description") or "")[:60],
            })

        total = sum(len(v) for v in refs.values())
        if total < min_refs:
            problems.append({
                "group": g["name"], "kind": "声明了但无证据",
                "detail": f"仅 {total} 个基本过程引用，低于门槛 {min_refs} —— "
                          f"要么别名没覆盖到实际用词，要么它不是独立数据组"})
            continue

        owner = bmap.get(g["maintainer"], g["maintainer"])
        if owner not in refs:
            problems.append({
                "group": g["name"], "kind": "维护方无引用",
                "detail": f"声明的维护方「{owner}」的基本过程里一处都没提到它"})
            continue
        if not any(r["maintains"] for r in refs[owner]):
            problems.append({
                "group": g["name"], "kind": "维护方无维护动作",
                "detail": f"「{owner}」引用了它但没有增删改动作 —— "
                          f"维护方判定无依据，请复核是否该由 "
                          f"{[s for s,v in refs.items() if any(r['maintains'] for r in v)]} 维护"})

        results.append({"group": g, "refs": dict(refs), "total": total})

    return {"results": results, "problems": problems, "items": items,
            "effort_systems": effort_systems, "boundary_map": bmap}


def build_entries(analysis: dict, version: str) -> list[dict]:
    """把分析结果落成 BOM 条目。维护方一条 ILF，其余各一条 ELF。"""
    # 逻辑文件的软件类别**随所在子系统**，不能硬编码。
    # 之前写死「业务处理」，让基座（应用集成和科学计算）的 ILF/ELF 归错类别 ——
    # 类别因子一旦从 1.0 调开，这些条目就会按别的系数计价且看不出来。
    sys_app: dict[str, str] = {}
    for it in analysis["items"]:
        if it.get("app_type") and not it["id"].startswith("LF."):
            sys_app.setdefault(it["path"]["system"], it["app_type"])

    def _app(host: str) -> str:
        if host not in sys_app:
            raise ValueError(
                f"系统「{host}」下没有带 app_type 的条目，无法为其逻辑文件定软件类别。"
                f"不兜底成「业务处理」—— 类别因子一旦从 1.0 调开，兜底的条目会"
                f"按别的系数计价且看不出来")
        return sys_app[host]
    out: list[dict] = []
    seq: Counter = Counter()
    bmap = analysis.get("boundary_map") or {}
    #: 边界 → 该边界下用哪个系统承载条目（取维护方所在系统，其余取首个引用系统）
    for r in analysis["results"]:
        g = r["group"]
        owner = bmap.get(g["maintainer"], g["maintainer"])
        for system, rs in sorted(r["refs"].items()):
            is_owner = system == owner
            gseq = g.get("seq")
            if gseq is None:
                raise ValueError(
                    f"数据组「{g['name']}」缺 seq —— id 必须由声明决定，"
                    f"不能按遍历顺序编：分区一变编号就重排，"
                    f"而飞书上那一行、明细表里那一行都还指着旧含义")
            # 条目挂在边界内的代表系统上：维护方边界挂维护方那个系统，
            # 引用方边界挂它第一个引用条目所在的系统
            host = (g["maintainer"] if is_owner
                    else _host_system(rs, analysis, system))
            code = _code(host)
            maint = [x for x in rs if x["maintains"]]
            ex = (maint or rs)[0]
            out.append({
                "id": f"LF.{code}.{gseq:04d}",
                "class": "SOFTWARE_FP",
                "name": f"{g['name']}（{'内部逻辑文件' if is_owner else '外部接口文件'}）",
                "path": {"product_line": _line(host), "system": host,
                         "l1": "逻辑文件", "l2": g["name"], "l3": g["name"]},
                "description": g.get("basis", "").strip(),
                "nesma": {
                    "type": "ILF" if is_owner else "ELF",
                    "rationale": (
                        (f"判为 ILF：本子系统维护「{g['name']}」这一逻辑数据组"
                         f"（NESMA 表6 内部逻辑文件）。依据：{ex['id']} 「{ex['quote']}」"
                         if is_owner else
                         f"判为 ELF：「{g['name']}」由「{owner}」维护，"
                         f"本子系统只引用不维护（NESMA 表7.5：只有当一个逻辑文件"
                         f"不是应用程序的内部逻辑文件时，才计为外部逻辑文件）。"
                         f"引用依据：{ex['id']} 「{ex['quote']}」")
                        + f"。※ 单一维护者规则由业务侧 2026-08-07 裁定；"
                          f"本组共 {r['total']} 处引用，跨 {len(r['refs'])} 个子系统"),
                    "counted_by": f"bom_logical_files@{version}",
                    "logical_file_role": "maintainer" if is_owner else "reference",
                    "logical_file_note": g.get("basis", "").strip().splitlines()[0],
                },
                # 用 host（具体系统）查，不用 system（边界名）—— 边界名下没有条目，
                # 查不到会静默落到兜底的「业务处理」，让基座的逻辑文件归错类别
                "app_type": _app(host),
                "maturity": "new",
                "maturity_evidence": "新建项目 —— 复用度默认取 1（复用度低）",
                "source": f"bom/logical-files.yaml#{g['name']}",
                "since": version,
                "status": "draft",
                "tags": ["逻辑文件-声明补建"],
                "referenced_by": [x["id"] for x in rs][:12],
            })
    return out


#: 系统 → id 短代码。**从 taxonomy.yaml 读**，不在这里硬编码 ——
#: 产品架构调整时（如基座拆成 7 个系统）只改 taxonomy 一处，
#: 硬编码在脚本里会让两处不同步，而不同步的表现是「未登记的子系统」报错，
#: 或者更糟：某个系统悄悄用了别的代码，id 与归属对不上。
_CODES: dict[str, str] = {}


def _host_system(rs: list[dict], analysis: dict, boundary: str) -> str:
    """引用方边界里，把 ELF 条目挂到第一个引用它的具体系统上。"""
    by_id = {i["id"]: i for i in analysis["items"]}
    for x in rs:
        it = by_id.get(x["id"])
        if it:
            return it["path"]["system"]
    return boundary


_LINES: dict[str, str] = {}


def _load_codes(bom_dir: Path) -> None:
    tax = yaml.safe_load((bom_dir / "taxonomy.yaml").read_text(encoding="utf-8"))
    for line, ls in (tax.get("product_lines") or {}).items():
        for sysname, spec in (ls.get("systems") or {}).items():
            spec = spec if isinstance(spec, dict) else {}
            if spec.get("code"):
                _CODES[sysname] = spec["code"]
            _LINES[sysname] = line


def _line(system: str) -> str:
    """系统 → 产品线。**不拿系统名兜底** —— 之前 product_line 直接写成
    host（系统名），造出了「家平台」「Harness引擎」这种幽灵产品线，
    结果是这两个系统在按产品线汇总时凭空消失，而合计看起来仍然正常。"""
    if system not in _LINES:
        raise ValueError(f"系统「{system}」未在 taxonomy.yaml 登记产品线")
    return _LINES[system]


def _code(system: str) -> str:
    if system not in _CODES:
        raise ValueError(
            f"未登记的系统 {system!r} —— 请在 bom/taxonomy.yaml 里给它一个 code。"
            f"已登记：{sorted(_CODES)}")
    return _CODES[system]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", required=True, type=Path)
    ap.add_argument("--scope", required=True, type=Path)
    ap.add_argument("--version", default="0.3.0")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    scope = yaml.safe_load(a.scope.read_text(encoding="utf-8"))
    decl = yaml.safe_load((a.bom / "logical-files.yaml").read_text(encoding="utf-8"))
    _load_codes(a.bom)
    an = analyze(a.bom, scope, decl)
    entries = build_entries(an, a.version)

    W = {"ILF": 10, "ELF": 7}
    add = sum(W[e["nesma"]["type"]] for e in entries)
    n_ilf = sum(1 for e in entries if e["nesma"]["type"] == "ILF")
    n_elf = len(entries) - n_ilf

    print(f"逻辑文件 {'（已写入）' if a.apply else '（dry-run）'}")
    print(f"  声明 {len(decl['groups'])} 组 → 成立 {len(an['results'])} 组")
    print(f"  落条目：ILF {n_ilf} 条 ×10 + ELF {n_elf} 条 ×7 = {add:,} UFP")
    if an["problems"]:
        print(f"\n  ⚠ {len(an['problems'])} 处需处理：")
        for p in an["problems"]:
            print(f"    [{p['kind']}] {p['group']}：{p['detail'][:96]}")

    if a.apply:
        by_system: dict[str, list] = defaultdict(list)
        for e in entries:
            by_system[e["path"]["system"]].append(e)
        import re
        for system, group in by_system.items():
            fn = re.sub(r"[（）()、/]", "-", system).strip("-") + ".yaml"
            if not p_exists(a.bom / "items" / fn):
                (a.bom / "items" / fn).write_text(
                    yaml.safe_dump({"schema_version": 1,
                                    "bom_version": a.version, "items": []},
                                   allow_unicode=True, sort_keys=False),
                    encoding="utf-8")
            p = a.bom / "items" / fn
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            data["items"] = [i for i in data["items"]
                             if not i["id"].startswith("LF.")] + group
            data["bom_version"] = a.version
            p.write_text(yaml.safe_dump(data, allow_unicode=True,
                                        sort_keys=False, width=200),
                         encoding="utf-8")
        (a.bom / "VERSION").write_text(a.version + "\n", encoding="utf-8")
        (a.bom / "logical-files-report.json").write_text(json.dumps({
            "declared": len(decl["groups"]), "established": len(an["results"]),
            "ilf": n_ilf, "elf": n_elf, "ufp_added": add,
            "problems": an["problems"],
            "groups": [{"name": r["group"]["name"],
                        "maintainer": r["group"]["maintainer"],
                        "refs_total": r["total"],
                        "systems": sorted(r["refs"])}
                       for r in an["results"]],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {a.bom}")


if __name__ == "__main__":
    main()
