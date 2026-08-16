"""bom_build_preschool — 从《柳州市学前教育数智服务平台建设项目估算表》构建 BOM。

与 `bom_build.py`（康养）的关系：**同一套 BOM schema，不同的源表形态**，
所以是两个导入器而不是一个带开关的。康养的源是两份工作簿做 N:1 关联
（功能点测算表 × 建设清单），幼教的源是一份工作簿的四级序号树：

    一      需求分析                        ← 阶段行，工作量估算法的产物
    二      系统设计
    三      软件开发（编码）
    （一）   柳州市学前教育数智服务平台（市平台）   ← 子系统
    1       区域办学                        ← 功能
    1.1     园所分布  <描述 203 字>          ← **末级，BOM 条目**
    四      系统测试
    五      实施部署

## 只取「三、软件开发（编码）」下的末级

其余四个阶段行在功能点法下**不单列** —— 柳州标准 p.10 2.1 明文：
「软件开发费按实际需求及任务量计算…费用包含需求分析、设计、编码、测试、
部署实施以及项目管理、培训等」。FP 单价里已经含了这五段，再把阶段行
导成条目就是把同一份工作量计两遍。

阶段行的人月仍然读出来存进 `effort_method_crosscheck.json`，用于与功能点法
对量级 —— 那是**校验**，不是定价（理由见 derive_fp.py 模块注释）。

## NESMA 类型由规则引擎从描述独立判定

不接受「导入时先填一个类型再补理由」。`nesma_classify.classify()` 读描述、
按标准表6-表10 判型，rationale 写明命中的规则与原文片段。判不出来的
留 `type: null` 并进人工裁决清单 —— 留空会被 bom_validate 拦住，
比塞一个 EI 蒙混过关强。

用法：
    python3 bom_build_preschool.py --src <估算表.xlsx> --out <bom_dir> [--version 0.1.0]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import openpyxl
import yaml

import nesma_classify

# ---- 源表结构 ---------------------------------------------------------

STAGE_RE = re.compile(r"^[一二三四五六七八九十]+$")
SUB_RE = re.compile(r"^[（(]\s*([一二三四五六七八九十]+|\d+)\s*[)）]$")
FUNC_RE = re.compile(r"^\d+$")
LEAF_RE = re.compile(r"^\d+\.\d+$")

DEV_STAGE = "软件开发（编码）"

#: 子系统名 → 条目 id 里的短代码。**必须在这里显式登记** ——
#: 自动生成代码会让 id 随源表改名而漂移，而 id 是跨版本追溯的锚。
SYSTEM_CODES: dict[str, str] = {
    "柳州市学前教育数智服务平台（市平台）": "CITY",
    "柳州市学前教育数智服务平台（园平台）": "PARK",
    "柳州市学前教育数智服务平台（家平台）": "HOME",
    "柳州市学前教育数智服务运营平台": "OPS",
    "数智民生生态体系平台支撑基座": "BASE",
    "数智幼教行业大模型知识工程建设": "KNOW",
    "数智幼教行业大模型数据建设": "DATA",
    "数智幼教行业专业模型建设": "MODL",
    "数智幼教场景智能体建设": "AGNT",
}

#: 系统 → BOM class。KB/DATASET 不走功能点法，走各自的计价口径。
SYSTEM_CLASS: dict[str, str] = {
    "KNOW": "KB",
    "DATA": "DATASET",
}

#: 系统 → 柳州表3 软件类别。
#:
#: ⚠️ **不再往条目上盖。** 软件类别按子系统取值（表3 注1「原则上按照主体功能的
#: 类型取值」），柳州公式里因子乘的是子系统的 UFP 合计 —— 粒度由公式位置定死。
#: 权威值现在在 `bom/taxonomy.yaml` 的子系统节点（`app_type` + `app_type_basis`），
#: 依据和取值同处一行，正是注1 要的形状。
#:
#: 从前这张表把值盖到每一条上，926 条各带一个从系统抄来的值。它看起来像
#: 逐条判定，其实是把结论抄了 926 遍 —— 拿它举证等于用结论证明结论；
#: 而它在飞书里可编辑，改了要么不生效、要么让引擎拒绝出表。
#:
#: 本表保留，仅供**引导 taxonomy 初值**时参照，不写进条目。
#: 条目级 `app_type` 留空 = 未逐条判定。真要逐条判（例如论证注1 第二条
#: 「多种类型功能占比比较均衡」）时再填，那时它才是证据。
SYSTEM_APP_TYPE: dict[str, str] = {
    "CITY": "业务处理",
    "PARK": "业务处理",
    "HOME": "业务处理",
    "OPS": "业务处理",
    "BASE": "应用集成和科学计算",
    "KNOW": "智能信息",
    "DATA": "大数据、多媒体",
    "MODL": "智能信息",
    "AGNT": "智能信息",
}

WORKDAYS_PER_MONTH = 21.75      # 柳州标准 p.14 ③：174 = 21.75 × 8


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def parse_sheet(ws, sheet_name: str) -> tuple[list[dict], list[dict]]:
    """返回 (末级条目, 阶段行)。

    源表把「系统」放在 A 列的裸文本行（底座 5 个系统串在一张表里），
    子系统放在（一）（二），功能放 1，末级放 1.1 —— 逐行走状态机。
    """
    leaves: list[dict] = []
    stages: list[dict] = []
    system = sheet_name          # 平台开发整张表就是一个系统组
    stage = subsystem = func = ""

    for r in range(1, ws.max_row + 1):
        no, name = _s(ws.cell(r, 1).value), _s(ws.cell(r, 2).value)
        if not no or no == "序号":
            continue

        if no in ("小计", "合计"):
            continue

        if STAGE_RE.match(no):
            stage, subsystem, func = name, "", ""
            stages.append({"system": system, "stage": name, "row": r,
                           "man_months": ws.cell(r, 4).value,
                           "unit_price_wan": ws.cell(r, 6).value,
                           "amount_wan": ws.cell(r, 7).value})
            continue

        if SUB_RE.match(no):
            subsystem, func = name, ""
            continue

        if FUNC_RE.match(no):
            func = name
            continue

        if LEAF_RE.match(no):
            if stage != DEV_STAGE:
                # 只有「三、软件开发（编码）」下才有功能条目；其余阶段有末级
                # 说明源表结构变了，不能默默跳过
                raise ValueError(
                    f"{sheet_name} r{r}：末级 {no} 出现在阶段「{stage}」下，"
                    f"而非「{DEV_STAGE}」—— 源表结构与导入器假设不符")
            leaves.append({
                "system": system, "subsystem": subsystem, "func": func,
                "no": no, "name": name,
                "description": _s(ws.cell(r, 3).value),
                "man_months": ws.cell(r, 4).value,
                "role": _s(ws.cell(r, 5).value),
                "unit_price_wan": ws.cell(r, 6).value,
                "amount_wan": ws.cell(r, 7).value,
                "row": r,
            })
            continue

        # 剩下的裸文本 = 系统标题行（底座表把 5 个系统串在一起）
        system = no
        stage = subsystem = func = ""

    return leaves, stages


def build(src: Path, out: Path, version: str) -> dict[str, Any]:
    wb = openpyxl.load_workbook(src, data_only=True)
    all_leaves: list[dict] = []
    all_stages: list[dict] = []
    for sn in ("平台开发", "底座开发"):
        if sn not in wb.sheetnames:
            raise ValueError(f"源表缺 sheet「{sn}」")
        lv, st = parse_sheet(wb[sn], sn)
        all_leaves += lv
        all_stages += st

    # 平台开发整张表是一个系统，子系统即（一）～（四）；底座反之。
    # 统一成「system = 有 SYSTEM_CODES 登记的那一级」。
    for lf in all_leaves:
        if lf["system"] == "平台开发":
            lf["system"] = lf["subsystem"]
            lf["subsystem"] = lf["func"]
            lf["func"] = ""

    unknown = sorted({lf["system"] for lf in all_leaves} - set(SYSTEM_CODES))
    if unknown:
        raise ValueError(
            f"未登记的系统名 {unknown} —— 请在 SYSTEM_CODES 显式登记，"
            f"不自动生成代码（id 是跨版本追溯的锚，不能随源表改名漂移）")

    # ---- 逐条判型 ----
    seq: Counter = Counter()
    items: list[dict] = []
    undecided: list[dict] = []
    for lf in all_leaves:
        code = SYSTEM_CODES[lf["system"]]
        seq[code] += 1
        text = f"{lf['name']}。{lf['description']}"
        v = nesma_classify.classify(text)

        nesma: dict[str, Any] | None = None
        if v.type:
            hits = "、".join(v.hits[:4]) if v.hits else ""
            nesma = {
                "type": v.type,
                "rationale": (f"判为 {v.type}：{v.rule.summary}（{v.rule.citation}）"
                              + (f"；命中原文「{hits}」" if hits else "")
                              + "。※ 由规则引擎从功能描述独立判定，非人工逐条撰写"
                                " —— 进入 reviewed 前须双人复核（G-02c）"),
                "counted_by": f"nesma_classify:{v.rule.id}@{version}",
            }
        else:
            undecided.append({
                "system": lf["system"], "no": lf["no"], "name": lf["name"],
                "row": lf["row"], "sheet_hint": lf["system"],
                "reason": ("命中不计数规则：" + v.rule.summary) if v.excluded
                          else "规则引擎无命中，须人工判型",
            })

        pd = (lf["man_months"] or 0) * WORKDAYS_PER_MONTH
        items.append({
            "id": f"FP.{code}.{seq[code]:04d}",
            "class": SYSTEM_CLASS.get(code, "SOFTWARE_FP"),
            "name": lf["name"],
            "path": {"product_line": lf["system"], "system": lf["system"],
                     "l1": lf["subsystem"] or lf["system"],
                     "l2": lf["func"] or lf["subsystem"] or lf["system"],
                     "l3": lf["name"]},
            "description": lf["description"],
            **({"nesma": nesma} if nesma else {}),
            # app_type 不写：见 SYSTEM_APP_TYPE 上方说明，权威值在 taxonomy 子系统节点
            "maturity": "new",
            "maturity_evidence": "新建项目 —— 柳州标准复用度默认取 1（复用度低），见 pack.factors.reuse",
            "source": f"{src.name}#{'底座开发' if code not in ('CITY','PARK','HOME','OPS') else '平台开发'}!A{lf['row']}",
            "since": version,
            "status": "draft",
            "legacy_quote": {
                "person_days": round(pd, 2),
                "man_months": lf["man_months"],
                "quote_yuan": round((lf["amount_wan"] or 0) * 10000, 2),
                "note": "源表按柳州 2.1.1 工作量估算法计；仅供交叉校验，不得用于定价",
                "granularity": "与本条目同级",
            },
        })

    # ---- 落盘 ----
    out.mkdir(parents=True, exist_ok=True)
    (out / "items").mkdir(exist_ok=True)
    by_system: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_system[it["path"]["system"]].append(it)

    for system, group in by_system.items():
        fn = re.sub(r"[（）()、/]", "-", system).strip("-") + ".yaml"
        (out / "items" / fn).write_text(
            yaml.safe_dump({"schema_version": 1, "bom_version": version,
                            "items": group},
                           allow_unicode=True, sort_keys=False, width=200),
            encoding="utf-8")

    taxonomy = {
        "product_lines": {s: {"items": len(g)} for s, g in by_system.items()},
        "id_scheme": "FP.{系统代码}.{4位序号}",
        "system_codes": {s: SYSTEM_CODES[s] for s in by_system},
    }
    (out / "taxonomy.yaml").write_text(
        yaml.safe_dump(taxonomy, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    (out / "VERSION").write_text(version + "\n", encoding="utf-8")

    # 阶段行：工作量估算法的原始数据，留作交叉校验，**不进 BOM**
    (out / "effort-method-crosscheck.json").write_text(
        json.dumps({"note": "柳州 2.1.1 工作量估算法的阶段行；仅供与功能点法对量级，不用于定价",
                    "workdays_per_month": WORKDAYS_PER_MONTH,
                    "stages": all_stages}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    report = {
        "bom_version": version,
        "source": str(src),
        "items": len(items),
        "systems": {s: len(g) for s, g in by_system.items()},
        "typed": sum(1 for i in items if "nesma" in i),
        "undecided": len(undecided),
        "type_distribution": dict(Counter(
            i["nesma"]["type"] for i in items if "nesma" in i)),
        "legacy_total_wan": round(sum(
            (i["legacy_quote"]["quote_yuan"] or 0) for i in items) / 10000, 4),
        "undecided_items": undecided,
    }
    (out / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--version", default="0.1.0")
    a = ap.parse_args()
    rep = build(a.src, a.out, a.version)

    print(f"BOM {rep['bom_version']} → {a.out}")
    print(f"  条目 {rep['items']}　判型 {rep['typed']}　待人工判型 {rep['undecided']}")
    for s, n in rep["systems"].items():
        print(f"    {s:<34} {n:>4}")
    print(f"  类型分布 {rep['type_distribution']}")
    print(f"  源表工作量法合计 {rep['legacy_total_wan']} 万元（仅供校验）")
    if rep["undecided"]:
        print(f"\n  ⚠ {rep['undecided']} 条未判型，见 build-report.json"
              f" —— 留空而非猜一个，bom_validate 会拦")


if __name__ == "__main__":
    main()
