"""quote_sync —— 把丙/丙附里人填的商机因子同步回输入 YAML。默认 dry-run。

## 数据流

    deal.yaml + device-config.yaml
            │
         引擎 ├──> 甲 / 甲附 / 乙        送审，静态
            └──> 丙 / 丙附             可编辑视图（黄格）
                      │ 人改
                      ↓
                  quote_sync ──默认──> 只报差异与影响（dry-run）
                             ──--write─> 回写 YAML，再重跑引擎

**同步写回的是输入 YAML，不是直接改甲/乙。** 甲乙都是引擎产物；
让丙去改乙、乙再改甲的话，会出现「乙已按新参数改过、甲还是旧值」的半同步状态，
而两本都长得完全正常，谁也不报错。

## 影响是用引擎算的，不是另写一套

dry-run 要回答「同步后总投资变多少」。这里不另算一遍 ——
把候选 YAML 写进临时副本，**跑同一个引擎**，比两次的 generate-report.json。
另写一套算法的话，它和引擎迟早会分家，而分家的那天 dry-run 会给出一个
看起来很合理的错数。

## 冲突检测

丙/丙附 生成时盖了 `_基线` 隐藏页，记着当时 deal.yaml 与 device-config.yaml 的指纹。
同步时若指纹与现状对不上，说明有人在这之后改过输入 —— 拒绝覆盖并列出两边。

用法：
    python3 quote_sync.py --root <项目根> --deal <deal.yaml>            # dry-run
    python3 quote_sync.py --root <项目根> --deal <deal.yaml> --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date as _date
from pathlib import Path
from typing import Any

import openpyxl
import yaml

_VER = re.compile(r"_v([\d.]+)_(\d{8})\.xlsx$")


import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from device_quote_tool import display_names as _display_names

def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "—"


def _sha_fp_settings(p: Path) -> str:
    """指纹范围 = 同步会写的范围。见 quote_generate_liuzhou 里的同名函数。"""
    if not p.exists():
        return "—"
    d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    blob = yaml.safe_dump(d.get("fp_method_settings") or {},
                          allow_unicode=True, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def latest(out: Path, book: str, required: bool = True) -> Path | None:
    """取 out/ 下该分册的最新一版。**按文件名里的版本号取，不按 mtime** ——
    重新生成会刷新 mtime，但那不代表它是新版本。"""
    cands = [p for p in out.glob(f"*_{book}_*.xlsx") if _VER.search(p.name)]
    if not cands:
        if not required:
            return None
        raise SystemExit(f"{out} 下找不到「{book}」分册。先跑一次 quote_generate_liuzhou。")
    def key(p):
        m = _VER.search(p.name)
        return ([int(x) for x in m.group(1).split(".")], m.group(2))
    return max(cands, key=key)


def read_baseline(p: Path) -> dict[str, str]:
    wb = openpyxl.load_workbook(p, read_only=True)
    if "_基线" not in wb.sheetnames:
        raise SystemExit(
            f"{p.name} 里没有 `_基线` 页 —— 它是 v0.7.1 之前生成的。\n"
            f"  没有基线就没法做冲突检测，而没有冲突检测的同步就是「后写覆盖先写」。\n"
            f"  请先重跑一次 quote_generate_liuzhou 生成带基线的分册。")
    ws = wb["_基线"]
    return {r[0]: r[1] for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}


# ============================================================
# 读丙附：录入表 → 候选 device-config
# ============================================================

def parse_prices(p: Path, mats: list[dict]) -> tuple[dict, list[str]]:
    """读丙附「00_本商机报价」页 —— 本商机的对外报价，**每个物料一个**。

    价格不按行读：357 行只有 89 个物料，按行读等于允许同一物料出现几个价，
    而不一致时表上看不出来。一处一填，读也只读那一处。
    """
    wb = openpyxl.load_workbook(p, data_only=True)
    if "00_本商机报价" not in wb.sheetnames:
        return {}, []
    ws = wb["00_本商机报价"]
    # **按显示名反查料号**，与出表共用 device_quote_tool.display_names。
    # 原来是 {name: code}，同名物料只留最后一个 —— 方案人员选了睡眠垫 HA0，
    # 同步回来会变成 HA1，而两者同价 1,100，金额一分不差，查都查不出来。
    code_of = {v: k for k, v in _display_names(mats).items()}
    h = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    if "市场单价(元)" not in h or "设备名称" not in h:
        return {}, ["丙附 00_本商机报价 缺「设备名称」或「市场单价(元)」列"]
    out, issues = {}, []
    for r in range(4, ws.max_row + 1):
        nm = str(ws.cell(r, h["设备名称"]).value or "").strip()
        if not nm:
            continue
        v = ws.cell(r, h["市场单价(元)"]).value
        if v in (None, ""):
            continue
        if nm not in code_of:
            issues.append(f"报价页第 {r} 行「{nm}」：目录里没有这个设备，已忽略")
            continue
        if not isinstance(v, (int, float)) or v <= 0:
            issues.append(f"报价页第 {r} 行「{nm}」：市场单价「{v}」不是正数，已忽略"
                          f"（留空表示按参考价，**不要填 0** —— "
                          f"0 会被当成报价 0 元，而那是待核价的意思）")
            continue
        out[code_of[nm]] = float(v)
    return out, issues


def parse_entry(p: Path, mats: list[dict], cfg: dict) -> tuple[list[dict], list[str]]:
    """读丙附的各园所页，还原成 device-config 的 targets。"""
    # **按显示名反查料号**，与出表共用 device_quote_tool.display_names。
    # 原来是 {name: code}，同名物料只留最后一个 —— 方案人员选了睡眠垫 HA0，
    # 同步回来会变成 HA1，而两者同价 1,100，金额一分不差，查都查不出来。
    code_of = {v: k for k, v in _display_names(mats).items()}
    kinds = {t["id"]: t.get("kind", "garden") for t in cfg["targets"]}
    counts = {t["id"]: t.get("count") for t in cfg["targets"]}
    wb = openpyxl.load_workbook(p, data_only=True)
    targets, issues = [], []
    for sn in wb.sheetnames:
        if sn.startswith(("_", "00_", "90_")):
            continue
        ws = wb[sn]
        h = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
        need = {"子场景", "设备名称", "数量", "备注"}
        if not need <= set(h):
            issues.append(f"{sn}: 缺列 {sorted(need - set(h))}")
            continue
        items = []
        for r in range(4, ws.max_row + 1):
            nm = ws.cell(r, h["设备名称"]).value
            sc = ws.cell(r, h["子场景"]).value
            q = ws.cell(r, h["数量"]).value
            if str(nm or "").strip() in ("", "合计"):
                # 有数量没设备名 —— 填了一半。**不静默跳过**：跳过等于把这笔钱丢了。
                if isinstance(q, (int, float)) and q:
                    issues.append(f"{sn} 第 {r} 行：填了数量 {q:g} 但没选设备，已忽略")
                continue
            nm = str(nm).strip()
            if not sc:
                issues.append(f"{sn} 第 {r} 行「{nm}」：没选子场景，已忽略")
                continue
            if nm not in code_of:
                issues.append(f"{sn} 第 {r} 行「{nm}」：目录里没有这个设备，已忽略 —— "
                              f"手打的名字带不出型号和单价")
                continue
            if not isinstance(q, (int, float)) or not q:
                issues.append(f"{sn} 第 {r} 行「{nm}」：数量为空或 0，已忽略")
                continue
            it = {"material": code_of[nm], "scene": str(sc).strip(), "qty": q}
            note = ws.cell(r, h["备注"]).value
            if note:
                it["note"] = str(note).strip()
            items.append(it)
        t = {"id": sn, "kind": kinds.get(sn, "garden")}
        if t["kind"] == "batch":
            t["count"] = counts.get(sn) or 200
            t["count_note"] = "园所数；items 里的数量已是全批总量，勿再乘"
        t["items"] = sorted(items, key=lambda x: (x["scene"], x["material"]))
        targets.append(t)
    return targets, issues


# ============================================================
# 读丙：试算参数 → 候选 fp_method_settings
# ============================================================

def read_reuse_modules_all(p: Path) -> list[dict]:
    """丙本 03 的**全部**行（含没改的），每行给出生效档位。

    众数必须在**全部模块**上算，不能只在改动行上算 —— 只按改动行取众数的话，
    填 2 行就会把子系统默认档改掉，没动过的另外 45 个模块跟着换档，
    金额少一大截而清单上看不出来。这个 bug 险些写进 taxonomy。
    """
    wb = openpyxl.load_workbook(p, data_only=True)
    if "03_复用度模块清单" not in wb.sheetnames:
        return []
    ws = wb["03_复用度模块清单"]
    for r in range(1, 8):
        h = {ws.cell(r, c).value: c for c in range(1, ws.max_column + 1)}
        if "功能模块" in h and "复用度" in h:
            break
    else:
        return []
    out: list[dict] = []
    for rr in range(r + 1, ws.max_row + 1):
        sysname = ws.cell(rr, h["子系统"]).value
        mod = ws.cell(rr, h["功能模块"]).value
        if not sysname or not mod:
            continue
        cur = str(ws.cell(rr, h["当前复用度"]).value or "低").strip()
        new = str(ws.cell(rr, h["复用度"]).value or "").strip() or cur
        out.append({"system": str(sysname).strip(), "module": str(mod).strip(),
                    "was": cur, "level": new,
                    "basis": str(ws.cell(rr, h["取值依据"]).value or "").strip(),
                    "ufp": ws.cell(rr, h["UFP"]).value or 0,
                    # 工作量法行的「功能模块」列填的是**阶段名**，落点是
                    # bom/effort-wbs.yaml 的 reuse_by_stage，不是 taxonomy。
                    # 不分流的话，阶段名会被当成功能模块写进 taxonomy，
                    # 而那里根本没有这个名字 —— 引擎下次直接拒绝出表。
                    # 工作量法行的「功能模块」列填的是**活动名**，落点是
                    # bom/effort-wbs.yaml 里该活动的 reuse 节点，不是 taxonomy。
                    # 不分流的话活动名会被当功能模块写进 taxonomy，而那里没有
                    # 这个名字 —— 引擎下次直接拒绝出表。
                    # 「阶段·无活动」是纯展示行（该阶段 WBS 没有活动可挂复用度），
                    # 单独标出并在写回时跳过 —— 它的「功能模块」不是活动名，
                    # 混进写回会被当成孤儿而拦住整次同步。
                    "kind": _kind_of(str(ws.cell(rr, h["同前缀组"]).value or ""))})
    return out


def _kind_of(grp: str) -> str:
    """由「同前缀组」列判定这一行的落点文件。

    三种落点互不相通，**判错不会报错、只会静默写到错的地方**：
      对象…  → bom/items/*.yaml 该条目的 reuse 节点（工作量法，现行）
      活动/阶段… → bom/effort-wbs.yaml（工作量法旧粒度，已退役，仅识别）
      其余    → bom/taxonomy.yaml 的 modules（功能点法）
    """
    if grp.startswith("对象"):
        return "effort_obj"
    if grp == "阶段·无活动":
        return "effort_noact"
    if grp.startswith(("活动", "阶段")):
        return "effort"
    return "fp"


def read_reuse_modules(p: Path) -> list[dict]:
    """丙本 03_复用度模块清单 —— 只取「填的 ≠ 当前」的行。

    表里预填了当前值，全表回读会把没动过的行也当成改动，
    于是每次同步都报一大片假改动，真改动淹在里面。
    """
    wb = openpyxl.load_workbook(p, data_only=True)
    if "03_复用度模块清单" not in wb.sheetnames:
        return []
    ws = wb["03_复用度模块清单"]
    for r in range(1, 8):
        h = {ws.cell(r, c).value: c for c in range(1, ws.max_column + 1)}
        if "功能模块" in h and "复用度" in h:
            break
    else:
        return []
    out: list[dict] = []
    for rr in range(r + 1, ws.max_row + 1):
        sysname = ws.cell(rr, h["子系统"]).value
        mod = ws.cell(rr, h["功能模块"]).value
        new = ws.cell(rr, h["复用度"]).value
        cur = ws.cell(rr, h["当前复用度"]).value
        if not sysname or not mod or not new:
            continue
        if str(new).strip() == str(cur or "").strip():
            continue
        out.append({"system": str(sysname).strip(), "module": str(mod).strip(),
                    "was": str(cur or "").strip(), "level": str(new).strip(),
                    "basis": str(ws.cell(rr, h["取值依据"]).value or "").strip(),
                    "ufp": ws.cell(rr, h["UFP"]).value or 0,
                    # 「阶段·无活动」是纯展示行（该阶段 WBS 没有活动可挂复用度），
                    # 单独标出并在写回时跳过 —— 它的「功能模块」不是活动名，
                    # 混进写回会被当成孤儿而拦住整次同步。
                    "kind": _kind_of(str(ws.cell(rr, h["同前缀组"]).value or ""))})
    return out


def read_effort_basis(root: Path, p: Path) -> list[dict]:
    """丙本 03 的**工作量测算参数**（量纲 / 数量 / 单位工作量 / 依据）改动。

    单位工作量不是标准因子 —— 表1 注3 的三个可调量只有人工成本、风险系数、
    复用系数。它在更前面一层：`量纲 × 单位工作量 → 人月`，而人月怎么估
    标准明确不管（表1 注1 只给方法不给数）。所以它没有档位表可借、没有
    「偏离缺省」的概念，守卫只有一条：**改了必须给得出依据**。

    只回「填的 ≠ BOM 现值」的行；全表回读会把没动过的行也当改动。
    """
    import glob as _g
    cur: dict[tuple, dict] = {}
    for f in _g.glob(str(root / "bom" / "items" / "*.yaml")):
        for i in yaml.safe_load(Path(f).read_text(encoding="utf-8"))["items"]:
            if i.get("class") != "SOFTWARE_EFFORT":
                continue
            cur[(i["path"]["system"], i["name"])] = i.get("effort_basis") or {}
    wb = openpyxl.load_workbook(p, data_only=True)
    if "03_复用度模块清单" not in wb.sheetnames:
        return []
    ws = wb["03_复用度模块清单"]
    for r in range(1, 8):
        h = {ws.cell(r, c).value: c for c in range(1, ws.max_column + 1)}
        if "功能模块" in h and "单位工作量(人月/单位)" in h:
            break
    else:
        return []
    out: list[dict] = []
    for rr in range(r + 1, ws.max_row + 1):
        grp = str(ws.cell(rr, h["同前缀组"]).value or "")
        if not grp.startswith("对象"):
            continue                       # 功能点法行没有这几列
        sysname = str(ws.cell(rr, h["子系统"]).value or "").strip()
        obj = str(ws.cell(rr, h["功能模块"]).value or "").strip()
        if not sysname or not obj:
            continue
        c0 = cur.get((sysname, obj)) or {}
        unit = str(ws.cell(rr, h["可核查量纲"]).value or "").strip()
        qty = ws.cell(rr, h["数量"]).value
        per = ws.cell(rr, h["单位工作量(人月/单位)"]).value
        bas = str(ws.cell(rr, h["单位工作量依据"]).value or "").strip()
        if qty is None or per is None:
            continue
        same = (unit == str(c0.get("unit") or "")
                and abs(float(qty) - float(c0.get("qty") or 0)) < 1e-9
                and abs(float(per) - float(c0.get("per_unit_mm") or 0)) < 1e-9)
        if same:
            continue
        out.append({"system": sysname, "object": obj,
                    "unit": unit, "qty": float(qty), "per_unit_mm": float(per),
                    "basis": bas, "basis_was": str(c0.get("basis") or "").strip(),
                    "was": (f"{c0.get('qty')} {c0.get('unit')} × "
                            f"{c0.get('per_unit_mm')}"),
                    "now": f"{qty} {unit} × {per}",
                    "was_mm": float(c0.get("man_months") or 0),
                    "now_mm": round(float(qty) * float(per), 4)})
    return out


def write_effort_basis(root: Path, rows: list[dict]) -> list[str]:
    """把工作量测算参数写回 bom/items 的 effort_basis。"""
    import glob as _g
    want = {(c["system"], c["object"]): c for c in rows}
    seen: set[tuple] = set()
    for c in rows:
        if not c["basis"]:
            raise SystemExit(
                f"⛔ {c['system']} · {c['object']} 改了工作量测算参数"
                f"（{c['was']} → {c['now']}）但没写依据。\n"
                f"  标准不规定人月如何估算（表1 注1 只给方法），所以这个数"
                f"**唯一的支撑就是依据本身** —— 没有依据的人月，评审拿"
                f"数量列一除就会问「这个单位工作量哪来的」。")
        # **依据必须跟着数字一起改。** 「单位工作量依据」列是从 BOM 预填的，
        # 只改数字不改依据时 basis 非空、守卫放行 —— 而留在表上的是解释
        # 旧数字的那句话。这种「有依据但依据说的是别的数」比空着更危险：
        # 空着评审会问，对不上评审会当我们在编。
        if c["basis"] == c.get("basis_was"):
            raise SystemExit(
                f"⛔ {c['system']} · {c['object']} 把工作量测算参数从"
                f"「{c['was']}」改成「{c['now']}」，但「单位工作量依据」"
                f"一个字没动。\n"
                f"  留在表上的依据解释的是**改之前**那个数：\n"
                f"    {c['basis'][:100]}…\n"
                f"  请把依据改成能解释新数的说法后再同步。")
    for f in _g.glob(str(root / "bom" / "items" / "*.yaml")):
        fp = Path(f)
        doc = yaml.safe_load(fp.read_text(encoding="utf-8"))
        dirty = False
        for i in doc.get("items") or []:
            if i.get("class") != "SOFTWARE_EFFORT":
                continue
            k = (i["path"]["system"], i["name"])
            if k not in want:
                continue
            seen.add(k)
            c = want[k]
            b = i.setdefault("effort_basis", {})
            b["unit"] = c["unit"]
            b["qty"] = c["qty"]
            b["per_unit_mm"] = c["per_unit_mm"]
            # **人月由引擎算，不从表上读。** 表上「→工作量(人月)」是只读展示列；
            # 若从那里读，量纲/数量改了而该列没重算时会写进一个对不上的人月，
            # 且乘回去看着完全正常。
            b["man_months"] = round(c["qty"] * c["per_unit_mm"], 4)
            b["basis"] = c["basis"]
            b["source"] = "丙本 03 录入（quote_sync --write-bom）"
            dirty = True
        if dirty:
            fp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                                         default_flow_style=False, width=100),
                          encoding="utf-8")
    stray = sorted(set(want) - seen)
    if stray:
        raise SystemExit(
            f"⛔ 丙本 03 里的建设对象 {stray} 在 bom/items/*.yaml 中找不到 —— "
            f"名字对不上不会报错，只会静默不生效。")
    return [f"[工作量测算参数] {len(rows)} 个建设对象的量纲/数量/单位工作量已更新"]


def read_official_col(p: Path, col: str) -> dict[str, Any]:
    """乙本 01_功能点测算表 某列的逐子系统值 —— 引擎算定的送审值。"""
    wb = openpyxl.load_workbook(p, data_only=True)
    if "01_功能点测算表" not in wb.sheetnames:
        return {}
    ws = wb["01_功能点测算表"]
    for r in range(1, 8):
        h = {ws.cell(r, c).value: c for c in range(1, ws.max_column + 1)}
        if "子系统" in h and col in h:
            break
    else:
        return {}
    out: dict[str, Any] = {}
    for rr in range(r + 1, ws.max_row + 1):
        k = ws.cell(rr, h["子系统"]).value
        v = ws.cell(rr, h[col]).value
        if k and v not in (None, "") and "合计" not in str(k):
            out[str(k).strip()] = v
    return out


def _unused_read_reuse_official(p: Path) -> dict[str, float]:
    """（已由 read_official_col 取代，保留仅为历史参考）"""
    wb = openpyxl.load_workbook(p, data_only=True)
    if "01_功能点测算表" not in wb.sheetnames:
        return {}
    ws = wb["01_功能点测算表"]
    for r in range(1, 8):
        h = {ws.cell(r, c).value: c for c in range(1, ws.max_column + 1)}
        if "子系统" in h and "复用系数" in h:
            break
    else:
        return {}
    out: dict[str, float] = {}
    for rr in range(r + 1, ws.max_row + 1):
        k = ws.cell(rr, h["子系统"]).value
        v = ws.cell(rr, h["复用系数"]).value
        if k and isinstance(v, (int, float)) and "合计" not in str(k):
            out[str(k).strip()] = float(v)
    return out


def parse_params(p: Path, pack_prod: float) -> dict[str, Any]:
    wb = openpyxl.load_workbook(p, data_only=True)
    if "01_试算参数" not in wb.sheetnames:
        return {}
    ws = wb["01_试算参数"]
    h = {ws.cell(3, c).value: c for c in range(1, ws.max_column + 1)}
    if "参数" not in h or "取值" not in h:
        return {}
    out: dict[str, Any] = {"app_type_factors": {}}
    for r in range(4, ws.max_row + 1):
        k = ws.cell(r, h["参数"]).value
        v = ws.cell(r, h["取值"]).value
        if not k or not isinstance(v, (int, float)):
            continue
        k = str(k).strip()
        if k == "软件开发生产率":
            out["productivity_ratio"] = round(v / pack_prod, 6)
        elif k.startswith("软件类别 ·"):
            out["app_type_factors"][k.split("·", 1)[1].strip()] = v
    # 复用系数在 02_二层试算 里是**逐子系统**一列。这里读回来不是为了写进
    # deal.yaml —— 复用度是产品事实，落在 bom/taxonomy.yaml 的 maturity ——
    # 而是为了在同步时把「你改了它但它不会跟着走」当面说清楚。
    # 不读的话，方案在丙本调了复用系数、跑同步看到「软件因子改动 0 处」，
    # 会以为没改成或者已经生效，两种误解都比报错糟。
    out["reuse_trial"] = {}
    if "02_二层试算" in wb.sheetnames:
        w2 = wb["02_二层试算"]
        h2 = {}
        # **表头识别不能绑死一个会改名的列。** 原判据是
        # `"子系统" in row and "复用系数" in row`，而 02 表改版后那一列已改叫
        # 「复用调整后功能点」—— 于是 h2 恒为空，下面两段（复用度、软件类别）
        # 一次都进不去，`reuse_trial` / `app_type_trial` 双双读空。
        # 后果是**在丙本 02 上手工改软件类别或复用度，同步读不到、也不报错**，
        # 安静地当成「没改」。实测：全域数据平台由「应用集成和科学计算」改成
        # 「大数据、多媒体」后同步报「软件因子改动 0 处」。
        # 现在只认「子系统」这个稳定列，再按需要的列各自判断有没有。
        for r in range(1, 8):
            row = {w2.cell(r, c).value: c for c in range(1, w2.max_column + 1)}
            if "子系统" in row and ("复用度" in row or "软件类别" in row
                                    or "复用系数" in row):
                h2, hr = row, r
                break
        if not h2:
            print("  ⚠ 丙本 02_二层试算 没认出表头 —— 软件类别/复用度的手工改动"
                  "本次**读不到**。请检查该表列名是否改过。")
        # 读「复用度」下拉而不是「复用系数」：系数是 VLOOKUP 出来的公式，
        # data_only 读到的是上次保存时的缓存值 —— 人改了下拉但没让 Excel
        # 重算就存盘的话，缓存里还是旧系数，比出来「没改」。档位是人直接填的，
        # 不经公式，读它才拿得到真实意图。
        if h2 and "复用度" in h2:
            for r in range(hr + 1, w2.max_row + 1):
                k2 = w2.cell(r, h2["子系统"]).value
                v2 = w2.cell(r, h2["复用度"]).value
                if k2 and v2:
                    out["reuse_trial"][str(k2).strip()] = str(v2).strip()
        # 软件类别列同理：它是**归类**（这个子系统属表3 哪一类），
        # 权威值在 bom/taxonomy.yaml 的子系统节点，不是商机参数。
        # 读回来只为在同步时说清「你改了它但它不会跟着走」。
        out["app_type_trial"] = {}
        if h2 and "软件类别" in h2:
            for r in range(hr + 1, w2.max_row + 1):
                k2 = w2.cell(r, h2["子系统"]).value
                v2 = w2.cell(r, h2["软件类别"]).value
                if k2 and v2:
                    out["app_type_trial"][str(k2).strip()] = str(v2).strip()
    return out


# ============================================================
# 差异
# ============================================================

def diff_targets(old: list[dict], new: list[dict],
                 name_of: dict[str, str]) -> tuple[list[str], list[str]]:
    """比差异。**键必须含备注** —— 同一 (园, 场景, 物料) 可以有多个位点。

    PAD 学之友在智慧教学场景下就有三个位点（教室 5 / 厨房 1 / 园医 1），
    额温枪在晨检午检场景下有两个（每班一 6 / 增补 2）。键漏掉备注的话，
    这些位点在字典里互相覆盖：改了其中一个显示不出来，删了一个也看不见，
    而差异表**看起来一切正常**。实测漏报 3 改 1。
    """
    def key(t, it):
        return (t["id"], it["scene"], it["material"], it.get("note", ""))

    def index(ts, label):
        d, dup = {}, []
        for t in ts:
            for it in t["items"]:
                k = key(t, it)
                if k in d:
                    dup.append(f"  ⚠ {label}里有完全相同的两行（园/场景/设备/备注全同）："
                               f"{k[0]} · {k[1]} · {name_of.get(k[2], k[2])}"
                               + (f" · {k[3]}" if k[3] else "")
                               + f"　×{d[k]['qty']:g} 与 ×{it['qty']:g}")
                    d[k] = {**it, "qty": d[k]["qty"] + it["qty"]}
                else:
                    d[k] = it
        return d, dup

    o, _ = index(old, "配置")
    n, dup = index(new, "录入表")
    out = []
    def lb(k):
        return (f"{k[0]} · {k[1]} · {name_of.get(k[2], k[2])}"
                + (f" · {k[3][:20]}" if k[3] else ""))
    for k in sorted(set(n) - set(o)):
        out.append(f"  ＋ 新增　{lb(k)}　×{n[k]['qty']:g}")
    for k in sorted(set(o) - set(n)):
        out.append(f"  － 删除　{lb(k)}　原 ×{o[k]['qty']:g}")
    for k in sorted(set(o) & set(n)):
        a, b = o[k], n[k]
        if a["qty"] != b["qty"]:
            out.append(f"  ≠ 数量　{lb(k)}　{a['qty']:g} → {b['qty']:g}")
        # 单价也要比 —— 只比数量的话，方案人员在丙附改了报价，同步会报
        # 「无改动」，而金额其实变了。改价不比改量小。
        pa, pb = a.get("unit_price"), b.get("unit_price")
        if pa != pb:
            _f = lambda v: "参考价" if v in (None, "") else f"{v:,.2f}"
            out.append(f"  ≠ 单价　{lb(k)}　{_f(pa)} → {_f(pb)}")
    return out, dup


#: 同步时对软件类别因子的处置。表3 注1：「凡取值超过1的，需列明具体取值依据」。
#: 同步能带一个 >1.0 的值进来，却带不进依据 —— 若给它安一个「**待补**」占位，
#: 生成端的守卫就被绕过了：一个没有依据的 >1.0 因子会一路进到送审金额里。
#: 所以这里**拒绝写**，让人回 deal.yaml 把依据写清楚。


def apply_params(d: dict, params: dict, pack_default: dict) -> list[str]:
    """把丙本的试算参数落到 deal 上，**只写真的变了的**。返回被拒绝的项。

    全量覆写会把没动过的因子也写一遍，还得给它们编一个 basis ——
    读 deal.yaml 的人会以为这些都是有意设的取值。
    """
    fp = d.setdefault("fp_method_settings", {})
    if "productivity_ratio" in params:
        if abs(params["productivity_ratio"] - fp.get("productivity_ratio", 1.0)) > 1e-9:
            fp["productivity_ratio"] = params["productivity_ratio"]
    refused = []
    for cat, val in (params.get("app_type_factors") or {}).items():
        cur = (fp.get("app_type_factors") or {}).get(cat)
        cv = cur["value"] if isinstance(cur, dict) else cur
        eff = cv if cv is not None else pack_default.get(cat)
        if eff is not None and abs(eff - val) < 1e-9:
            continue                       # 没变，不写
        basis = cur.get("basis") if isinstance(cur, dict) else None
        if val > 1.0 and not basis:
            refused.append(
                f"{cat} → {val}：表3 注1「凡取值超过1的，需列明具体取值依据」。"
                f"同步带得进取值带不进依据 —— 请在 deal.yaml 的 "
                f"fp_method_settings.app_type_factors.{cat}.basis 写明后再同步。")
            continue
        sec = fp.setdefault("app_type_factors", {})
        sec[cat] = {"value": val, **({"basis": basis} if basis else {})}
    return refused


def run_engine(root: Path, deal_dir: Path, deal_name: str,
               cfg: dict | None, params: dict | None,
               pack_default: dict,
               refused_out: list[str] | None = None) -> dict[str, Any]:
    """把候选写进临时副本，跑**同一个引擎**，返回 generate-report.json。"""
    # 引擎按 `deal.yaml` 的三级父目录推项目根，所以临时副本**必须落在项目内**
    # 的同一层级（deals/<临时名>/），放到 /tmp 会让 root 推成 /tmp 而找不到标准包。
    # 引擎按 deal.yaml 的**三级父目录**推项目根，所以临时副本必须正好落在
    # deals/<临时名>/ 这一层 —— 多套一层目录，root 就推成 deals/ 了。
    holder = Path(tempfile.mkdtemp(prefix=".sync-", dir=str(deal_dir.parent)))
    try:
        tmp = holder
        shutil.copytree(deal_dir, tmp, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("out"))
        if cfg is not None:
            (tmp / "device-config.yaml").write_text(
                yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False,
                               default_flow_style=False, width=100),
                encoding="utf-8")
        if params:
            d = yaml.safe_load((tmp / deal_name).read_text(encoding="utf-8"))
            # **拒绝项必须回传。** 从前这里丢掉了返回值，于是被守卫挡下的因子
            # 在试跑里根本没生效，dry-run 就报「无变化」—— 人看到的是
            # 「改了 3 处 → 金额没动」，会以为这几个因子不影响金额，
            # 而真相是它们压根没进去。静默吞掉拒绝比拒绝本身危险得多。
            _ref = apply_params(d, params, pack_default)
            if refused_out is not None:
                refused_out.extend(_ref)
            (tmp / deal_name).write_text(
                yaml.safe_dump(d, allow_unicode=True, sort_keys=False,
                               default_flow_style=False, width=100),
                encoding="utf-8")
        o = holder / "_out"
        r = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "quote_generate_liuzhou.py"),
             "--deal", str(tmp / deal_name), "--out", str(o)],
            cwd=root, capture_output=True, text=True)
        rep = o / "generate-report.json"
        if r.returncode or not rep.exists():
            raise SystemExit(
                f"按同步后的输入试跑引擎失败，**没有写任何文件**：\n"
                f"{(r.stderr or r.stdout)[-1600:]}")
        return json.loads(rep.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(holder, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--deal", type=Path, required=True)
    ap.add_argument("--write", action="store_true", help="回写 YAML（默认只报告）")
    ap.add_argument("--force", action="store_true", help="指纹对不上也写（危险）")
    ap.add_argument("--write-bom", action="store_true", dest="write_bom",
                    help="额外把丙本 03 的复用度写进 bom/taxonomy.yaml。"
                         "**这是产品级 BOM，改了影响所有商机** —— 故与 --write 分开，"
                         "不让跨层写入变成一次「同步」的静默副作用")
    a = ap.parse_args()

    deal_dir = a.deal.parent
    deal = yaml.safe_load(a.deal.read_text(encoding="utf-8"))
    out = deal_dir / "out"
    dev = a.root / "bom" / "devices"
    mats = yaml.safe_load((dev / "materials.yaml").read_text(encoding="utf-8"))["materials"]
    name_of = {m["code"]: m["name"] for m in mats}
    cfg_p = deal_dir / "device-config.yaml"
    cfg = yaml.safe_load(cfg_p.read_text(encoding="utf-8"))

    f_bf = latest(out, "丙附")
    # 新商机第一次同步时还没有丙本（它由引擎出，而引擎要等硬件配好）。
    # 没有就跳过软件因子，不是报错 —— 报错会把新商机堵在第一步。
    f_b = latest(out, "丙", required=False)
    base = read_baseline(f_bf)

    # ---- 冲突检测 ----
    now = {"fp_method_settings.sha": _sha_fp_settings(a.deal),
           "device-config.yaml.sha": _sha(cfg_p)}
    bad = {k: (base.get(k), v) for k, v in now.items() if base.get(k) != v}
    if bad and not a.force:
        raise SystemExit(
            "输入在丙/丙附生成之后被改过，**拒绝同步**：\n"
            + "".join(f"  {k}: 生成时 {o} → 现在 {n}\n" for k, (o, n) in bad.items())
            + "  同步会把这些改动覆盖掉，而覆盖掉的那份不会留下任何痕迹。\n"
              "  正确做法：先重跑 quote_generate_liuzhou 出一版新的丙/丙附，\n"
              "  把你在旧丙附里的改动搬过去，再同步。确实要覆盖请加 --force。")

    targets, issues = parse_entry(f_bf, mats, cfg)
    prices, p_iss = parse_prices(f_bf, mats)
    issues += p_iss
    params = parse_params(f_b, 6.51) if f_b else {}
    d_hw, dup = diff_targets(cfg["targets"], targets, name_of)
    issues += dup
    # 报价改动单独比 —— 它不在 targets 里，只比 targets 会漏掉整段价格变化。
    _op = cfg.get("prices") or {}
    _pd = []
    for c_ in sorted(set(_op) | set(prices)):
        a_, b_ = _op.get(c_), prices.get(c_)
        if a_ != b_:
            _f = lambda v: "参考价" if v is None else f"{v:,.2f}"
            _pd.append(f"  ≠ 报价　{name_of.get(c_, c_)}　{_f(a_)} → {_f(b_)}")
    d_hw += _pd

    cur_fp = deal.get("fp_method_settings") or {}
    # 生效值 = deal 覆盖优先，没覆盖就是标准包默认。
    # 只跟 deal 的覆盖比的话，「从默认值改成 1.2」会检测不到 ——
    # deal 里那一项压根不存在，比出来是「没变」。
    pack = yaml.safe_load(
        (a.root / deal["baseline"] / "pack.yaml").read_text(encoding="utf-8"))
    pack_default = ((pack.get("factors") or {}).get("app_type") or {}).get("values") or {}
    d_sw = []
    if params.get("productivity_ratio") not in (None, cur_fp.get("productivity_ratio", 1.0)):
        d_sw.append(f"  ≠ 生产率浮动　{cur_fp.get('productivity_ratio', 1.0)} → "
                    f"{params['productivity_ratio']}")
    for cat, val in (params.get("app_type_factors") or {}).items():
        c = (cur_fp.get("app_type_factors") or {}).get(cat)
        cv = (c["value"] if isinstance(c, dict) else c)
        if cv is None:
            cv = pack_default.get(cat)
        if cv is not None and abs(cv - val) > 1e-9:
            d_sw.append(f"  ≠ 软件类别因子·{cat}　{cv} → {val}")

    # 复用系数改了 —— 不写回，明说落实路径。
    # 基准取**乙本 01_功能点测算表**的同名列：那是引擎算定的送审值，
    # 而丙本是试算本。拿丙本自己的基线比等于自己跟自己比，永远无差。
    reuse_moved = []
    cat_moved = []
    mod_moved = read_reuse_modules(f_b) if f_b else []
    eb_moved = read_effort_basis(a.root, f_b) if f_b else []
    _rt = params.get("reuse_trial") or {}
    _at = params.get("app_type_trial") or {}
    _f_y = latest(out, "乙", required=False)
    if _f_y:
        for sysname, was in read_official_col(_f_y, "复用度档位").items():
            val = _rt.get(sysname)
            if val is not None and str(was).strip() != val:
                reuse_moved.append((sysname, str(was).strip(), val))
        for sysname, was in read_official_col(_f_y, "软件类别").items():
            val = _at.get(sysname)
            if val is not None and str(was).strip() != val:
                cat_moved.append((sysname, str(was).strip(), val))

    print(f"同步检查　{deal['deal_id']}")
    print(f"  丙附：{f_bf.name}")
    print(f"  丙　：{f_b.name if f_b else '（尚未出过 —— 跳过软件因子）'}")
    if issues:
        print(f"\n⚠ 录入表里有 {len(issues)} 处填了一半或填错，**已忽略**（不进配置）：")
        for x in issues[:12]:
            print("   ", x)
        if len(issues) > 12:
            print(f"    …另有 {len(issues) - 12} 处")
    print(f"\n硬件配置改动 {len(d_hw)} 处" + ("：" if d_hw else " —— 无"))
    for x in d_hw[:25]:
        print(x)
    if len(d_hw) > 25:
        print(f"    …另有 {len(d_hw) - 25} 处")
    print(f"\n软件因子改动 {len(d_sw)} 处" + ("：" if d_sw else " —— 无"))
    for x in d_sw:
        print(x)

    if mod_moved:
        print(f"\n■ 丙本 03_复用度模块清单 有 {len(mod_moved)} 处模块复用度改动"
              f"（共 {sum(int(m['ufp'] or 0) for m in mod_moved):,} UFP）：")
        for m in mod_moved[:20]:
            nb = "" if m["basis"] else "　⚠ 缺依据"
            print(f"  · {m['system']} · {m['module']}　{m['was']} → {m['level']}"
                  f"　{m['ufp']} UFP{nb}")
        if len(mod_moved) > 20:
            print(f"    …另有 {len(mod_moved) - 20} 处")
        _nb = [m for m in mod_moved if not m["basis"] and m["level"] != "低"]
        if _nb:
            print(f"  ⛔ 其中 {len(_nb)} 处偏离缺省但**没写依据**，写入会被拒绝"
                  f"（表3 注2；方向是降价，没出处的折扣评审当「随意定价」看）。")
        print("  这些是**产品级** BOM（bom/taxonomy.yaml），改了影响所有商机，"
              "故不随 --write 写入；")
        print("  确认后加 --write-bom。写入时**逐模块声明**（只写非「低」的），"
              "不设子系统默认 —— 未声明的模块一律取缺省「低」= 1.0。")
        print("  另需在 deal.yaml 打开 fp_method_settings.reuse.apply_maturity "
              "才会真正影响金额。")
    if cat_moved:
        print(f"\n⚠ 丙本 02_二层试算 的软件类别被改了 {len(cat_moved)} 处，"
              f"**本命令不会写回**：")
        for sysname, was, now in cat_moved:
            print(f"  · {sysname}　{was} → {now}")
        print("  软件类别按**子系统**取值（表3 注1「原则上按照主体功能的类型取值」），"
              "柳州公式里")
        print("  因子乘的是子系统的 UFP 合计 —— 粒度由公式位置定死在这一层。")
        print("  落实要改 bom/taxonomy.yaml 该子系统的 app_type + app_type_basis，"
              "不在 deal.yaml：")
        print("  「这个子系统的主体功能属哪一类」是产品事实，换个商机也成立。")
        print("  ⚠ 改归类会换掉适用的取值区间（如 业务处理 0.8–1.0 → 人工智能 1.0–1.5），"
              "取值超 1 仍需按注1 列明依据。")
    if reuse_moved:
        print(f"\n⚠ 丙本 02_二层试算 的复用度被改了 {len(reuse_moved)} 处，"
              f"**本命令不会写回**：")
        for sysname, was, now in reuse_moved:
            print(f"  · {sysname}　{was} → {now}")
        print("  复用度是**产品事实**（这个模块是不是我们已有的成熟产品），"
              "换个商机也成立，")
        print("  所以落在 bom/taxonomy.yaml 该子系统的 maturity + maturity_basis，"
              "不在 deal.yaml。")
        print("    existing 成熟沿用 / partial 部分改造 / new 全新定制")
        print("  只有本商机的例外才写 deal.yaml 的 "
              "fp_method_settings.reuse.overrides.<子系统>.{level,basis}。")
        print("  两处都必须写依据 —— 复用系数往下调是降价，"
              "没出处的折扣评审当「随意定价」看。")

    if eb_moved:
        _dm = sum(m["now_mm"] - m["was_mm"] for m in eb_moved)
        print(f"\n■ 丙本 03 的**工作量测算参数**有 {len(eb_moved)} 处改动"
              f"（人月合计 {_dm:+.2f}）：")
        for m in eb_moved[:20]:
            print(f"  · {m['system']} · {m['object']}　{m['was']} → {m['now']}"
                  f"　人月 {m['was_mm']:.2f} → {m['now_mm']:.2f}")
        if len(eb_moved) > 20:
            print(f"    …另有 {len(eb_moved) - 20} 处")
        _nb = [m for m in eb_moved
               if not m["basis"] or m["basis"] == m.get("basis_was")]
        if _nb:
            print(f"  ⚠ 其中 {len(_nb)} 处的依据缺失或未跟着改，"
                  f"--write-bom 会拒绝：")
            for m in _nb[:8]:
                print(f"     · {m['system']} · {m['object']}")
            print("  单位工作量**不是标准因子**（表1 注3 只有人工成本/风险/复用），")
            print("  标准不规定人月如何估算 —— 这个数唯一的支撑就是依据本身。")

    if a.write_bom:
        if not mod_moved and not eb_moved:
            print("\n--write-bom：丙本 03 没有改动，未写 BOM。")
        # **两个落点各自判断。** 从前共用一个 else，于是只改了工作量参数、
        # 一处复用度都没动时，taxonomy.yaml 也被整份重写一遍 —— 内容多半
        # 一样，所以看不出来，但它是产品级 BOM，无谓的重写会把别的商机
        # 拖进一次没人打算做的变更。
        if mod_moved:
            for n in write_taxonomy_reuse(a.root, read_reuse_modules_all(f_b)):
                print(f"  {n}")
            print(f"\n已写入 bom/taxonomy.yaml（{len(mod_moved)} 处）。"
                  f"**这是产品级 BOM，其他商机重出时也会用到新档位。**")
            print("  下一步：确认 deal.yaml 的 apply_maturity 已打开，"
                  "然后重跑 quote_generate_liuzhou。")
        if eb_moved:
            for _n2 in write_effort_basis(a.root, eb_moved):
                print(f"  {_n2}")
            print(f"\n已写入 bom/items/*.yaml 的 effort_basis"
                  f"（{len(eb_moved)} 个建设对象）。重跑 quote_generate_liuzhou 生效。")
    if not d_hw and not d_sw:
        print("\n没有可同步的改动。"
              if (reuse_moved or cat_moved or mod_moved or eb_moved)
              else "\n没有改动，无需同步。")
        return

    # 报价与配置一起写回。空字典也要写 —— 人把价清空了也是一次改动，
    # 不写等于「清空无效」，而表上看不出来。
    cand = {**cfg, "targets": targets, "prices": prices}
    rep_p = out / "generate-report.json"
    if not rep_p.exists():
        # 首次同步：没出过套表，没有可比的基线。**不试跑引擎** ——
        # 新商机的软件源表多半还没给，跑出来的失败信息只会盖住真正要看的差异表。
        print("\n（本商机尚未出过套表，无可比基线，跳过影响试算）")
        if not a.write:
            print("\n这是 dry-run，**没有写任何文件**。确认无误后加 --write 回写。")
            return
        _write_back(a, cfg_p, cand, params, pack_default, entry=f_bf)
        return
    else:
        before = json.loads(rep_p.read_text(encoding="utf-8"))
        refused: list[str] = []
        after = run_engine(a.root, deal_dir, a.deal.name, cand, params,
                           pack_default, refused_out=refused)
    if refused:
        # **先说拒绝，再说影响。** 顺序反过来的话，人先读到「无变化」就走了，
        # 而「无变化」正是因为这些取值没进去 —— 那是最容易被误读成
        # 「这几个因子不影响金额」的形状。
        print(f"\n⛔ 以下 {len(refused)} 项软件因子**被守卫挡下，不会生效**：")
        for x in refused:
            print("   ", x)
        print("   下面的试算是**按挡下后的取值**跑的 —— 若这几项显示无变化，"
              "是因为它们没进去，不是因为它们不影响金额。")
    if before is not None:
        _report_impact(before, after)

    if not a.write:
        print("\n这是 dry-run，**没有写任何文件**。确认无误后加 --write 回写。")
        return
    _write_back(a, cfg_p, cand, params, pack_default, entry=f_bf)


def _report_impact(before: dict, after: dict) -> None:
    print("\n同步后的影响（由同一个引擎试跑得出，非另算）：")
    KEYS = [("software_wan", "软件开发费", "万元"),
            ("hardware_yuan", "硬件设备购置费", "元"),
            ("other_fees_wan", "其他费用与预备费", "万元"),
            ("grand_total_yuan", "工程总投资", "元")]
    for k, lb, u in KEYS:
        b, af = before.get(k), after.get(k)
        if b is None or af is None:
            continue
        d = af - b
        flag = "" if abs(d) < 0.005 else ("  ⬆" if d > 0 else "  ⬇")
        print(f"  {lb:16s} {b:>16,.2f} → {af:>16,.2f} {u}"
              + (f"　{d:+,.2f}{flag}" if abs(d) >= 0.005 else "　无变化"))
    if before.get("hardware_pending") != after.get("hardware_pending"):
        print(f"  {'待核价设备':16s} {before.get('hardware_pending'):>16} → "
              f"{after.get('hardware_pending'):>16} 项")
    rb, ra = before.get("other_fees_ratio"), after.get("other_fees_ratio")
    if rb is not None and ra is not None and abs(ra - rb) > 1e-6:
        print(f"  {'其他费用占预算比':14s} {rb:>16.2%} → {ra:>16.2%}"
              f"　（扣集成费后不得超 10%）")
    # 其他费用是**业务侧计列额**，不随基数自动变；变的是上限与占比。
    # 说成「已含该联动」会让人以为核减空间自己调整过了 —— 那是错的。
    if abs((after.get("hardware_yuan") or 0) - (before.get("hardware_yuan") or 0)) >= 0.005:
        print("  ⚠ 硬件金额变了，**系统集成费与预备费的计费基数跟着变**。"
              "但其他费用各项是业务侧计列额，不会自动跟着调 ——\n"
              "     变的是**标准上限**与占比。重出套表后请核对甲本 03 的"
              "「结论」列有没有新的超上限项。")


def write_taxonomy_reuse(root: Path, allrows: list[dict]) -> list[str]:
    """把复用度改动写进 bom/taxonomy.yaml。**逐模块声明，不设子系统默认。**

    只写非「低」的模块 —— 低是表3 注2 的缺省，未声明即为低。
    不再取众数提为子系统默认：默认会被将来新增的模块继承，
    而那意味着全新写的功能自动拿到折扣，不报错、不提示。
    yaml 因此变长，但这一段是机器写的，人只在丙本 03 填。
    """
    import collections
    L2M = {"高": "existing", "中": "partial", "低": "new"}
    # 工作量法的阶段行走另一个文件：bom/effort-wbs.yaml 的 reuse_by_stage
    eff_rows = [c for c in allrows if c.get("kind") == "effort"]
    obj_rows = [c for c in allrows if c.get("kind") == "effort_obj"]
    allrows = [c for c in allrows if c.get("kind") == "fp"]
    p = root / "bom" / "taxonomy.yaml"
    tax = yaml.safe_load(p.read_text(encoding="utf-8"))
    # 先把「本次填的」与「原有的」合成每个子系统的完整模块档位视图
    bysys: dict[str, dict[str, tuple[str, str]]] = {}
    for c in allrows:
        if not c["basis"] and c["level"] != "低":
            raise SystemExit(
                f"⛔ {c['system']} · {c['module']} 取「{c['level']}」但没写依据。\n"
                f"  表3 注2 的缺省是低（1.0），偏离缺省必须列明依据 —— "
                f"这个方向是降价，没出处的折扣评审当「随意定价」看。\n"
                f"  请在丙本 03_复用度模块清单 的「取值依据」列补齐后再同步。")
        bysys.setdefault(c["system"], {})[c["module"]] = (c["level"], c["basis"])
    notes = []
    for line in (tax.get("product_lines") or {}).values():
        for sysname, node in ((line or {}).get("systems") or {}).items():
            if sysname not in bysys:
                continue
            node = node if isinstance(node, dict) else {}
            # 用**全部行**重建，只留非「低」的 —— 低是缺省，写出来是噪音；
            # 而且不在旧 modules 上增量叠加：叠加会留下已删模块名，看不出来。
            keep = {m: {"maturity": L2M[lv],
                        **({"maturity_basis": bs} if bs else {})}
                    for m, (lv, bs) in sorted(bysys[sysname].items())
                    if lv != "低"}
            # 子系统级 maturity 不再参与计价，一并清掉，免得留个不生效的值误导人
            node.pop("maturity", None)
            node.pop("maturity_basis", None)
            if keep:
                node["modules"] = keep
            else:
                node.pop("modules", None)
            cnt = collections.Counter(v["maturity"] for v in keep.values())
            if keep:                       # 0 声明的子系统不刷屏
                notes.append(f"{sysname}: 声明 {len(keep)} 个模块（{dict(cnt)}），"
                             f"其余 {len(bysys[sysname]) - len(keep)} 个按缺省「低」")
            (line["systems"])[sysname] = node
    p.write_text(yaml.safe_dump(tax, allow_unicode=True, sort_keys=False,
                                default_flow_style=False, width=100),
                 encoding="utf-8")

    if eff_rows:
        # 写到**活动节点**上，与 role / stage / pct 并列 —— 复用度是这块工作的
        # 属性，和它的角色、阶段归属同级。写成一张平行的 reuse_by_stage 表，
        # 活动改名或删除时那张表会留下对不上的孤儿，且没有任何东西会报。
        wp = root / "bom" / "effort-wbs.yaml"
        doc = yaml.safe_load(wp.read_text(encoding="utf-8"))
        doc.pop("reuse_by_stage", None)          # 旧的平行表，清掉
        idx = {(sysn, a["name"]): a
               for sysn, t in (doc.get("templates") or {}).items()
               for a in (t.get("activities") or [])}
        stray = [(c["system"], c["module"]) for c in eff_rows
                 if (c["system"], c["module"]) not in idx]
        if stray:
            raise SystemExit(
                f"⛔ 丙本 03 里的工作量法行 {stray} 在 bom/effort-wbs.yaml 的"
                f" templates 中找不到对应活动 —— 名字对不上不会报错，只会静默不生效。\n"
                f"  「（WBS 无对应活动）」的阶段行不要填复用度，那种阶段没有活动"
                f"可挂，须先补 WBS 模板。")
        n_set = 0
        for c in eff_rows:
            node = idx[(c["system"], c["module"])]
            if not c["basis"] and c["level"] != "低":
                raise SystemExit(
                    f"⛔ 工作量法 {c['system']} · {c['module']} 取「{c['level']}」"
                    f"但没写依据。表1 注3「复用系数根据开发内容确定」，"
                    f"偏离缺省必须列明依据（档位借用表3 注2，属推断）。")
            if c["level"] == "低":
                node.pop("reuse", None)          # 低是缺省，不写
            else:
                node["reuse"] = {"level": c["level"], "basis": c["basis"]}
                n_set += 1
        wp.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False,
                                     default_flow_style=False, width=100),
                      encoding="utf-8")
        notes.append(f"[工作量法·活动·已退役] {n_set} 个活动声明非缺省复用度，"
                     f"其余 {len(eff_rows) - n_set} 个按缺省「低」")

    if obj_rows:
        # 工作量法的复用度落在**建设对象**上 —— 即 BOM 条目自身的 reuse 节点，
        # 与 effort_basis 并列。写成一张平行表的话，条目改名或删除会留下
        # 对不上的孤儿，且没有任何东西会报。
        import glob as _g
        want = {(c["system"], c["module"]): c for c in obj_rows}
        seen: set[tuple] = set()
        n_set = 0
        for f in _g.glob(str(root / "bom" / "items" / "*.yaml")):
            fp = Path(f)
            doc = yaml.safe_load(fp.read_text(encoding="utf-8"))
            dirty = False
            for i in doc.get("items") or []:
                if i.get("class") != "SOFTWARE_EFFORT":
                    continue
                k = (i["path"]["system"], i["name"])
                if k not in want:
                    continue
                seen.add(k)
                c = want[k]
                if not c["basis"] and c["level"] != "低":
                    raise SystemExit(
                        f"⛔ 工作量法 {c['system']} · {c['module']} 取"
                        f"「{c['level']}」但没写依据。表1 注3「复用系数根据开发"
                        f"内容确定」，偏离缺省必须列明依据（档位借用表3 注2，"
                        f"属推断）。")
                if c["level"] == "低":
                    dirty = i.pop("reuse", None) is not None or dirty
                else:
                    i["reuse"] = {"level": c["level"], "basis": c["basis"]}
                    dirty = True
                    n_set += 1
            if dirty:
                fp.write_text(yaml.safe_dump(doc, allow_unicode=True,
                                             sort_keys=False,
                                             default_flow_style=False, width=100),
                              encoding="utf-8")
        stray = sorted(set(want) - seen)
        if stray:
            raise SystemExit(
                f"⛔ 丙本 03 里的工作量法行 {stray} 在 bom/items/*.yaml 中找不到"
                f"对应的 SOFTWARE_EFFORT 条目 —— 名字对不上不会报错，"
                f"只会静默不生效。")
        notes.append(f"[工作量法·建设对象] {n_set} 个对象声明非缺省复用度，"
                     f"其余 {len(obj_rows) - n_set} 个按缺省「低」")
    return notes


def _write_back(a, cfg_p: Path, cand: dict, params: dict,
                pack_default: dict, entry: Path | None = None) -> None:
    cfg_p.write_text(yaml.safe_dump(cand, allow_unicode=True, sort_keys=False,
                                    default_flow_style=False, width=100),
                     encoding="utf-8")
    wrote_deal = False
    if params:
        d = yaml.safe_load(a.deal.read_text(encoding="utf-8"))
        refused = apply_params(d, params, pack_default)
        if refused:
            print("\n⛔ 以下软件因子**未回写**：")
            for x in refused:
                print("   ", x)
        a.deal.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False,
                                         default_flow_style=False, width=100),
                          encoding="utf-8")
        wrote_deal = True
    print(f"\n已回写：{cfg_p.name}" + ("、" + a.deal.name if wrote_deal else ""))

    # 顺手按新配置重出丙附，把 `_基线` 指纹刷新。
    # 不刷的话下一次同步必报冲突（基线还是同步前那版），而新商机这时候
    # 还跑不了引擎（软件源表都没给），人就卡在「改一轮就得 --force」上了。
    if entry is not None:
        try:
            import device_quote_tool as dqt
            dqt.emit(a.root, a.deal.parent, entry, baseline={
                "生成时间": _date.today().strftime("%Y%m%d"),
                "deal_id": cand.get("deal", ""),
                "fp_method_settings.sha": _sha_fp_settings(a.deal),
                "device-config.yaml.sha": _sha(cfg_p),
                "说明": "由 quote_sync --write 回写后重出，指纹已刷新。",
            })
            print(f"  已按新配置重出丙附并刷新基线：{entry.name}")
        except Exception as e:                     # 重出失败不该让回写白做
            print(f"  ⚠ 丙附重出失败（{e}）。配置**已经写好了**，"
                  f"但下次同步会因基线过期报冲突 —— 重跑一次生成器即可。")
    print("  ⚠ 送审件还是旧的。请重跑 quote_generate_liuzhou 出新版套表。")


if __name__ == "__main__":
    main()
