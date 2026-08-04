"""bom_build — 从人工梳理的 xlsx 清单构建 BOM（唯一事实源）。

输入两份工作簿，职责不同：
  A《…功能点测算.xlsx》 —— **条目主源**。已拆到功能点粒度，提供层级、
     名称、描述、NESMA 类型。必须使用 P0 修正版（原版 sheet4 的
     系统/子系统列是合并单元格，无法逐行归属）。
  B《…建设清单.xlsx》   —— **成熟度来源**。提供「是否已有 / 当前开发情况 /
     复用%」三种口径的产品成熟度信号，以及人天与报价（仅作交叉校验，
     **不得用于定价** —— 见方案 §0.3 方法论倒挂）。

A 的粒度细于 B（1509 : 约 1100），按层级键做 N:1 关联。
两表列结构逐 sheet 不同，故一律按表头名定位，不硬编码列号。

用法：
    python3 bom_build.py --fp <A.xlsx> --list <B.xlsx> --out <bom_dir> [--version 0.9.0]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import openpyxl

from bom_schema import Bom, BomItem, Nesma, Path_, Runtime

# ---- sheet → 产品线 / 代码 --------------------------------------------

SHEET_MAP: dict[str, dict[str, str]] = {
    "1.数智底座-平台能力":        {"line": "数智底座", "code": "PLAT",  "class": "SOFTWARE_FP"},
    "1.数智底座-知识工程":        {"line": "数智底座", "code": "KNOW",  "class": "KB"},
    "1.数智底座-数据建设":        {"line": "数智底座", "code": "DATA",  "class": "DATASET"},
    "2.智能能力中枢-行业专业模型": {"line": "智能能力中枢", "code": "MODL", "class": "SOFTWARE_FP"},
    "2.智能能力中枢-智能体平台":   {"line": "智能能力中枢", "code": "AGTP", "class": "SOFTWARE_FP"},
    "2.智能能力中枢-康养智能体":   {"line": "智能能力中枢", "code": "AGNT", "class": "SOFTWARE_FP"},
    "3.生态运营与交易中台":       {"line": "生态运营与交易中台", "code": "ECO", "class": "SOFTWARE_FP"},
    "4.多角色业务应用":          {"line": "多角色业务应用", "code": "APP",  "class": "SOFTWARE_FP"},
}

HARDWARE_SHEET = "7.硬件设备（服务站点）"

#: 多角色业务应用的 8 个子系统 → 短代码（用于条目 id）
SUBSYSTEM_CODES = [
    ("政府监管与决策端", "GOV"),
    ("机构区域运营端", "ORG"),
    ("机构站点管理系统", "SITE"),
    ("社区站点管理端", "COMM"),
    ("站长智助", "MGR"),
    ("护工智助", "CARE"),
    ("服务商经营管理", "VEND"),
    ("老年人/家属服务应用", "USER"),
]

#: 应用类型因子取值 → 分类名（山东标准 表2）。
#: BOM 只存分类名（产品事实），取值留给区域标准包。
APP_TYPE_BY_FACTOR = {1.0: "业务处理", 1.2: "科技", 1.3: "多媒体", 1.5: "智能信息",
                      1.7: "基础软件、支撑软件", 1.9: "通信控制", 2.0: "流程控制"}

#: 成熟度信号归一 —— 三种口径统一到 new / partial / existing
MATURITY_MAP = {
    "是": "existing", "否": "new", "需要迭代": "partial",
    "已有成熟版本": "existing", "已有demo": "partial", "没有，规划中": "new",
}

#: 运营期触发器的文本证据。命中只产出**候选**（runtime.reviewed=False），
#: 需 P2 人工复核 —— 这两个标记决定运营期是否出现推理算力费与外部大模型 API 费。
GPU_EVIDENCE = ["推理引擎", "GPU", "算力", "模型训练", "微调", "蒸馏", "量化压缩", "算子"]
EXT_LLM_EVIDENCE = ["大模型", "LLM", "通用模型", "token"]

#: 层级列中的编辑残留标记 —— 属源数据缺陷，如实导入并在构建报告中列出，不静默修正。
EDIT_ARTIFACTS = ["合并", "删除", "待定", "待补", "TODO", "重复", "?", "？"]

DETAIL_HEADERS = ["建设详情", "功能描述", "功能点描述"]
MATURITY_HEADERS = ["是否已有", "当前开发情况", "复用", "是否已有对应功能"]
EFFORT_HEADERS = ["人/天", "人天"]
PRICE_HEADERS = ["报价"]


# ---- 工具 --------------------------------------------------------------


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _key(text: str) -> str:
    """关联键归一：去掉全部空白 + 统一全半角括号。

    两表同一层级名的写法并不一致 —— 例如 B 表的「AI计算平台\\n（AI计算引擎）」
    在 A 表里是「AI计算平台（AI计算引擎）」。不归一会整块漏关联。
    """
    t = re.sub(r"\s+", "", text or "")
    return t.replace("(", "（").replace(")", "）")


def _norm_maturity(raw: str) -> str | None:
    """把 是/否 · 已有demo · 复用80% 三种口径归一。"""
    raw = raw.strip()
    if not raw:
        return None
    if raw in MATURITY_MAP:
        return MATURITY_MAP[raw]
    m = re.match(r"^(\d+(?:\.\d+)?)%$", raw)
    if m:
        pct = float(m.group(1))
        if pct >= 100:
            return "existing"
        if pct > 0:
            return "partial"
        return "new"
    return None


def _subsystem_code(system: str) -> str:
    for needle, code in SUBSYSTEM_CODES:
        if needle in system:
            return code
    return "MISC"


# ---- B 表：成熟度索引 --------------------------------------------------


def build_maturity_index(list_path: Path) -> tuple[dict[tuple[str, ...], dict], dict]:
    """扫描《建设清单》，产出 层级键 → {maturity, evidence, 人天, 报价} 索引。

    返回 (index, stats)。index 的键是层级元组，同时登记全深度与各级前缀，
    以便 A 表按 (L1,L2,L3) → (L1,L2) → (L1) 逐级回退命中。
    """
    wb = openpyxl.load_workbook(list_path, data_only=True)
    index: dict[tuple[str, ...], dict] = {}
    stats: dict[str, Any] = {"sheets": {}}

    for ws in wb.worksheets:
        if ws.title in ("总表", "报价参照说明", HARDWARE_SHEET):
            continue
        header = [_s(c) for c in next(ws.iter_rows(min_row=2, max_row=2, values_only=True))]
        try:
            detail_col = next(i for i, h in enumerate(header) if h in DETAIL_HEADERS)
        except StopIteration:
            continue
        hier_cols = list(range(detail_col))
        mat_col = next((i for i, h in enumerate(header) if h in MATURITY_HEADERS), None)
        eff_col = next((i for i, h in enumerate(header) if h in EFFORT_HEADERS), None)
        pri_col = next((i for i, h in enumerate(header) if h in PRICE_HEADERS), None)

        carry = [""] * len(hier_cols)
        section = ""          # 多角色/生态 表里的「一、政府监管与决策端」分节行
        rows = matched = 0

        for row in ws.iter_rows(min_row=3, values_only=True):
            cells = [_s(c) for c in row]
            if not any(cells):
                continue
            detail = cells[detail_col] if detail_col < len(cells) else ""
            first = cells[0] if cells else ""

            # 分节行：仅首列有值，无描述 → 记为当前子系统，不作数据行
            if first and not detail and not any(cells[1:detail_col + 1]):
                section = first
                carry = [""] * len(hier_cols)
                continue
            if not detail:
                continue

            for i in hier_cols:
                v = cells[i] if i < len(cells) else ""
                if v:
                    carry[i] = v
                    for j in range(i + 1, len(hier_cols)):
                        carry[j] = ""      # 上级变动，下级失效
            levels = [c for c in carry if c]
            if not levels:
                continue
            rows += 1

            maturity = _norm_maturity(cells[mat_col]) if mat_col is not None and \
                mat_col < len(cells) else None
            payload = {
                "maturity": maturity,
                "maturity_raw": cells[mat_col] if mat_col is not None and
                mat_col < len(cells) else "",
                "maturity_header": header[mat_col] if mat_col is not None else "",
                "effort_person_days": _num(cells[eff_col]) if eff_col is not None and
                eff_col < len(cells) else None,
                "quote_yuan": _num(cells[pri_col]) if pri_col is not None and
                pri_col < len(cells) else None,
                "sheet": ws.title,
                "section": section,
            }
            if maturity:
                matched += 1
            # 全深度键 + 各级前缀键（前缀仅在未被占用时登记，避免覆盖更精确的条目）
            for depth in range(len(levels), 0, -1):
                key = (ws.title, _key(section), *(_key(x) for x in levels[:depth]))
                if depth == len(levels) or key not in index:
                    index[key] = payload
        stats["sheets"][ws.title] = {"rows": rows, "with_maturity": matched}
    return index, stats


def _num(text: str) -> float | None:
    try:
        return float(str(text).replace(",", ""))
    except (TypeError, ValueError):
        return None


# ---- A 表：条目主源 ----------------------------------------------------


def build(fp_path: Path, list_path: Path, version: str) -> tuple[Bom, dict]:
    mat_index, mat_stats = build_maturity_index(list_path)
    wb = openpyxl.load_workbook(fp_path, data_only=True)

    # 参数设置：应用类型因子 → 分类名
    ps = wb["参数设置"]
    app_factor = {_s(ps.cell(r, 2).value): ps.cell(r, 3).value
                  for r in range(15, 30) if _s(ps.cell(r, 2).value)}
    # 测算汇总：系统 → 开发类别
    sm = wb["测算汇总"]
    dev_cat = {_s(sm.cell(r, 2).value): _s(sm.cell(r, 12).value)
               for r in range(5, 20) if _s(sm.cell(r, 2).value)}

    items: list[BomItem] = []
    counters: Counter = Counter()
    report: dict[str, Any] = {
        "join": {"hit_full": 0, "hit_prefix": 0, "miss": 0, "miss_samples": []},
        "per_sheet": {}, "maturity_index": mat_stats,
        "runtime_flags": {"gpu": 0, "ext_llm": 0}, "anomalies": [], "name_fallback": 0,
    }

    for sheet_name, meta in SHEET_MAP.items():
        ws = wb[sheet_name]
        last = max(r for r in range(5, ws.max_row + 1) if _s(ws.cell(r, 10).value))
        carry = ["", "", ""]
        n_rows = 0

        for r in range(5, last + 1):
            ftype = _s(ws.cell(r, 10).value)
            if ftype not in {"ILF", "ELF", "EI", "EO", "EQ"}:
                continue
            system = _s(ws.cell(r, 3).value)
            if not system:
                raise ValueError(
                    f"{sheet_name} 第 {r} 行 系统/子系统 为空 —— "
                    f"请确认使用的是 P0 修正版工作簿")

            # 层级向下填充
            for i, col in enumerate((4, 5, 6)):
                v = _s(ws.cell(r, col).value)
                if v:
                    carry[i] = v
                    for j in range(i + 1, 3):
                        carry[j] = ""
            levels = [c for c in carry if c]

            raw_name = _s(ws.cell(r, 7).value)
            desc = _s(ws.cell(r, 8).value)
            # 源表的「功能点名称」列约 45% 是描述的机器截断（如「基于MNA-SF/MNA等量表规」），
            # 不是可用名称。识别出这类，退回最深层级名 —— 至少是人写的短语。
            # 真正的功能点命名属 P2 重拆范畴，此处只做可用性兜底。
            truncated = bool(raw_name and desc.startswith(raw_name) and len(desc) > len(raw_name))
            fallback = carry[2] or carry[1] or carry[0]
            if truncated and fallback:
                name = fallback
                report["name_fallback"] += 1
            else:
                name = raw_name or fallback or desc[:30] or f"{sheet_name}#{r}"

            # 成熟度关联：全深度 → 逐级回退
            section = system.split("-", 1)[1] if sheet_name == "4.多角色业务应用" and \
                "-" in system else ""
            joined, how = None, "miss"
            src_sheet = sheet_name
            for depth in range(len(levels), 0, -1):
                key = (src_sheet, _key(section), *(_key(x) for x in levels[:depth]))
                if key in mat_index:
                    joined = mat_index[key]
                    how = "hit_full" if depth == len(levels) else "hit_prefix"
                    break
            report["join"][how] += 1
            if how == "miss" and len(report["join"]["miss_samples"]) < 15:
                report["join"]["miss_samples"].append(
                    {"sheet": sheet_name, "row": r, "levels": levels, "name": name[:30]})

            code = meta["code"]
            if sheet_name == "4.多角色业务应用":
                code = f"APP.{_subsystem_code(system)}"
            counters[code] += 1
            item_id = f"FP.{code}.{counters[code]:04d}"

            text = f"{name} {desc}"
            gpu_hits = [k for k in GPU_EVIDENCE if k in text]
            llm_hits = [k for k in EXT_LLM_EVIDENCE if k in text]
            runtime = Runtime(
                needs_inference_gpu=bool(gpu_hits),
                calls_external_llm=bool(llm_hits),
                evidence=sorted(set(gpu_hits + llm_hits)),
                reviewed=False,          # 自动识别产出候选，待 P2 人工确认
            )
            report["runtime_flags"]["gpu"] += bool(gpu_hits)
            report["runtime_flags"]["ext_llm"] += bool(llm_hits)

            for lvl in levels:
                if lvl in EDIT_ARTIFACTS:
                    report["anomalies"].append(
                        {"item": item_id, "sheet": sheet_name, "row": r,
                         "issue": f"层级列含编辑残留标记 {lvl!r}", "levels": levels})

            maturity = (joined or {}).get("maturity") or "new"
            evidence = None
            if joined and joined.get("maturity_raw"):
                evidence = (f"{joined['maturity_header']}={joined['maturity_raw']}"
                            f"（源自建设清单 {joined['sheet']}）")

            legacy = {}
            if joined:
                if joined.get("effort_person_days") is not None:
                    legacy["person_days"] = joined["effort_person_days"]
                if joined.get("quote_yuan") is not None:
                    legacy["quote_yuan"] = joined["quote_yuan"]
                if legacy:
                    legacy["note"] = "仅供交叉校验，不得用于定价"
                    legacy["granularity"] = "建设清单行（粗于本条目）"

            items.append(BomItem(
                id=item_id,
                cls=meta["class"],
                name=name,
                path=Path_(product_line=meta["line"], system=system,
                           l1=levels[0] if len(levels) > 0 else None,
                           l2=levels[1] if len(levels) > 1 else None,
                           l3=levels[2] if len(levels) > 2 else None),
                description=desc,
                nesma=Nesma(type=ftype, counted_by="import:0802原表"),
                app_type=APP_TYPE_BY_FACTOR.get(app_factor.get(system)),
                dev_category=dev_cat.get(system) or None,
                maturity=maturity,
                maturity_evidence=evidence,
                runtime=runtime,
                since=version,
                status="draft",
                source=f"raw-input/0802…功能点测算-P0修正.xlsx#{sheet_name}!B{r}",
                legacy_quote=legacy,
            ))
            n_rows += 1
        report["per_sheet"][sheet_name] = n_rows

    items.extend(_build_hardware(list_path, version, report))

    taxonomy = _build_taxonomy(items)
    bom = Bom(version, items, taxonomy)
    return bom, report


def _build_hardware(list_path: Path, version: str, report: dict) -> list[BomItem]:
    """硬件设备 sheet —— 不走功能点法，转为 HARDWARE 条目并保留询价规格。"""
    wb = openpyxl.load_workbook(list_path, data_only=True)
    if HARDWARE_SHEET not in wb.sheetnames:
        return []
    ws = wb[HARDWARE_SHEET]
    header = [_s(c) for c in next(ws.iter_rows(min_row=2, max_row=2, values_only=True))]
    col = {h: i for i, h in enumerate(header)}
    out: list[BomItem] = []
    scenario = category = ""
    n = 0

    for row in ws.iter_rows(min_row=3, values_only=True):
        cells = [_s(c) for c in row]
        device = cells[col["设备"]] if "设备" in col and col["设备"] < len(cells) else ""
        if not device:
            continue
        category = cells[col["类别"]] or category
        scenario = cells[col["服务场景"]] or scenario
        n += 1
        out.append(BomItem(
            id=f"HW.DEV.{n:04d}",
            cls="HARDWARE",
            name=device,
            path=Path_(product_line="硬件设备", system="服务站点智能设备",
                       l1=category or None, l2=scenario or None),
            description=cells[col["功能描述"]] if "功能描述" in col else "",
            maturity="existing",
            maturity_evidence="外采通用设备，非自研",
            spec={
                "unit": cells[col["单位"]] if "单位" in col else "",
                "qty": _num(cells[col["数量"]]) if "数量" in col else None,
                "reference_unit_price_yuan": _num(cells[col["单价（元）"]])
                if "单价（元）" in col else None,
                "pricing_basis": "TODO(P4)：按区域标准补三家盖章询价单",
            },
            since=version,
            status="draft",
            source=f"raw-input/0802…建设清单.xlsx#{HARDWARE_SHEET}",
        ))
    report["per_sheet"][HARDWARE_SHEET] = n
    return out


def _build_taxonomy(items: list[BomItem]) -> dict[str, Any]:
    tree: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for it in items:
        tree[it.path.product_line][it.path.system] += 1
    return {
        "product_lines": {
            line: {"systems": dict(systems), "items": sum(systems.values())}
            for line, systems in tree.items()
        },
        "id_scheme": "FP.{系统代码}.{4位序号} / HW.DEV.{4位序号}",
        "subsystem_codes": dict(SUBSYSTEM_CODES),
    }


# ---- CLI ---------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="从 xlsx 清单构建 BOM")
    ap.add_argument("--fp", required=True, type=Path, help="功能点测算工作簿（P0 修正版）")
    ap.add_argument("--list", required=True, type=Path, dest="list_path",
                    help="建设清单工作簿（成熟度来源）")
    ap.add_argument("--out", required=True, type=Path, help="BOM 输出目录")
    ap.add_argument("--version", default="0.9.0")
    args = ap.parse_args()

    bom, report = build(args.fp, args.list_path, args.version)
    bom.validate()
    written = bom.save(args.out)

    (args.out / "build-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    j = report["join"]
    total = j["hit_full"] + j["hit_prefix"] + j["miss"]
    print(f"BOM v{args.version} 已生成：{len(bom)} 条，{len(written)} 个分片")
    print(f"成熟度关联：全深度命中 {j['hit_full']}，前缀命中 {j['hit_prefix']}，"
          f"未命中 {j['miss']}（覆盖率 {(total - j['miss']) / total:.1%}）")
    print(f"运营期触发器：GPU {report['runtime_flags']['gpu']} 条，"
          f"外部大模型 {report['runtime_flags']['ext_llm']} 条")


if __name__ == "__main__":
    main()
