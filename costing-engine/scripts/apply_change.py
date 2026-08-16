"""apply_change — execute a single approved suggestion against an xlsx workbook.

Used by quote-rewrite. Each suggestion category maps to one operation here.
All ops are designed to be idempotent — re-running with the same plan produces
the same output.
"""
from __future__ import annotations

import json
import sys
from copy import copy
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils import get_column_letter, column_index_from_string

from formula_engine import compute_total


# ---- helpers ---------------------------------------------------------------
def _last_content_col(ws, header_row: int) -> int:
    """最后一个表头非空的列（忽略原始文件遗留的大量空尾随列）。"""
    last = 0
    for c in range(1, ws.max_column + 1):
        if ws.cell(row=header_row, column=c).value not in (None, ""):
            last = c
    return last or 1


def ensure_column(ws, header_row: int, header_name: str) -> int:
    """Return the 1-indexed column for `header_name`; if missing, create it
    immediately after the last *content* column (紧贴数据列，不落到空尾列区)。"""
    for c in range(1, ws.max_column + 1):
        if ws.cell(row=header_row, column=c).value == header_name:
            return c
    new_col = _last_content_col(ws, header_row) + 1
    ws.cell(row=header_row, column=new_col, value=header_name)
    return new_col


def set_cell(ws, row: int, col_letter_or_idx, value):
    if isinstance(col_letter_or_idx, str):
        col = column_index_from_string(col_letter_or_idx)
    else:
        col = int(col_letter_or_idx)
    ws.cell(row=row, column=col, value=value)


def recompute_row_total(ws, row: int, header_row: int, columns: dict[str, int]) -> None:
    """Re-emit 成本总价 / 对外总价 as formulas referencing the price × qty cells.

    Writing a formula (vs static number) preserves Excel-native behavior — when
    the user opens the file and edits 单价 or 人天, totals re-compute automatically.
    Falls back to static product when qty column is unknown.
    """
    qty_col = None
    for q in ("人/天", "人天", "数量"):
        if q in columns:
            qty_col = columns[q]
            break
    if qty_col is None:
        return
    qty_letter = get_column_letter(qty_col)

    for price_h, total_h in [
        ("成本单价", "成本总价"),
        ("对外单价", "对外总价"),
        ("单价（元）", "成本总价（元）"),
    ]:
        pc, tc = columns.get(price_h), columns.get(total_h)
        if not (pc and tc):
            continue
        price_letter = get_column_letter(pc)
        ws.cell(row=row, column=tc, value=f"={price_letter}{row}*{qty_letter}{row}")


def columns_index(ws, header_row: int) -> dict[str, int]:
    return {
        ws.cell(row=header_row, column=c).value: c
        for c in range(1, ws.max_column + 1)
        if ws.cell(row=header_row, column=c).value
    }


# ---- operations ------------------------------------------------------------
def op_subject_mapping(ws, suggestion: dict, header_row: int) -> str:
    row = suggestion["target"]["row"]
    new_subject = suggestion["proposed_change"].get("subject_id") or suggestion["proposed_change"].get("new_value")
    col = ensure_column(ws, header_row, "标准科目")
    ws.cell(row=row, column=col, value=new_subject)
    return f"row {row}: 标准科目 ← {new_subject}"


def op_semantic_split(ws, suggestion: dict, header_row: int) -> str:
    row = suggestion["target"]["row"]
    col_ref = suggestion["target"].get("col")
    columns = columns_index(ws, header_row)
    if isinstance(col_ref, str) and col_ref in columns:
        col = columns[col_ref]
    elif isinstance(col_ref, str) and len(col_ref) <= 2 and col_ref.isalpha():
        col = column_index_from_string(col_ref)
    else:
        col = columns.get("建设详情") or columns.get("详情") or columns.get("建设详情（定位）")
        if col is None:
            return f"row {row}: 找不到详情列，跳过"
    new_text = suggestion["proposed_change"]["new_value"]
    ws.cell(row=row, column=col, value=new_text)
    return f"row {row}: 详情列 ← (语义改写, {len(new_text)} chars)"


def op_labor_pricing(ws, suggestion: dict, header_row: int) -> str:
    row = suggestion["target"]["row"]
    columns = columns_index(ws, header_row)
    new_value = suggestion["proposed_change"]["new_value"]
    target_col_header = suggestion["target"].get("col_header") or "成本单价"
    col = columns.get(target_col_header)
    if col is None:
        return f"row {row}: 找不到列 {target_col_header}，跳过"
    ws.cell(row=row, column=col, value=new_value)
    recompute_row_total(ws, row, header_row, columns)
    return f"row {row}: {target_col_header} ← {new_value} (合计已重算)"


def op_overlap_attribution(ws, suggestion: dict, header_row: int) -> str:
    row = suggestion["target"]["row"]
    ratio = suggestion["proposed_change"].get("ratio")
    col = ensure_column(ws, header_row, "分摊比例")
    ws.cell(row=row, column=col, value=ratio)
    columns = columns_index(ws, header_row)
    # Optionally scale 成本总价
    if "成本总价" in columns and isinstance(ratio, (int, float)):
        total_col = columns["成本总价"]
        cur = ws.cell(row=row, column=total_col).value
        if isinstance(cur, (int, float)):
            ws.cell(row=row, column=total_col, value=round(cur * ratio))
    return f"row {row}: 分摊比例 ← {ratio}"


def op_add_summary_sheet(wb, suggestion: dict) -> str:
    """Add a summary sheet using pre-computed totals from scan_summary_sheets
    (which read quote.json with already-evaluated formulas)."""
    pc = suggestion["proposed_change"]
    sheet_name = pc["sheet_name"]
    if sheet_name in wb.sheetnames:
        return f"summary sheet '{sheet_name}' already exists; skipped"
    ws = wb.create_sheet(sheet_name, 0)
    ws.append(["序号", "园所/分项", "合计金额（元）", "备注"])
    grand_total = 0.0
    for i, (sheet_name_src, sub_total) in enumerate(pc.get("per_sheet_totals", []), 1):
        ws.append([i, sheet_name_src, round(sub_total), ""])
        grand_total += sub_total
    ws.append([])
    ws.append([None, "总计", round(grand_total), "六园所设备总价"])
    return f"created summary '{sheet_name}' aggregating {len(pc.get('per_sheet_totals',[]))} sheets, total ¥{grand_total:,.0f}"


def _transform_deploy_sheet(ws, wb_path: str, quote: dict) -> None:
    """把「平台部署&交付」改造为「系统集成费测算依据」：
       + PDF表10对照列  + 重分类去向列  + 重分类声明 + 单价合规校验。"""
    from deploy_breakdown import compute as _dep
    import openpyxl as _ox

    # 找 header 行（含「单价」「小计」的行）
    header_row = 1
    for r in range(1, min(ws.max_row + 1, 6)):
        rowvals = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
        if any(isinstance(v, str) and ("单价" in v or "小计" in v) for v in rowvals):
            header_row = r
            break
    ncol = _last_content_col(ws, header_row)

    # 新增两列表头（紧贴最后内容列）
    col_t10 = ncol + 1
    col_reclass = ncol + 2
    ws.cell(row=header_row, column=col_t10, value="PDF表10单价对照")
    ws.cell(row=header_row, column=col_reclass, value="重分类去向")

    # PDF 表10：集成专业技术人员 1000 / 专家人员 2000
    def t10_label(unit_price):
        if not isinstance(unit_price, (int, float)):
            return ""
        if abs(unit_price - 1000) <= 1:
            return "符合集成专业技术人员标准 ¥1000/人天 (表10) ✓"
        if abs(unit_price - 2000) <= 1:
            return "符合专家人员标准 ¥2000/人天 (表10) ✓"
        if 1000 < unit_price < 2000:
            return f"介于集成技术员¥1000~专家¥2000之间 (表10) ✓"
        if unit_price > 2000:
            return f"超专家¥2000上限，需说明 (表10)"
        return f"低于集成技术员¥1000 (表10)"

    # 重分类去向：复用 deploy_breakdown 行级映射
    dep = _dep(quote)
    line_map = {}
    for ln in dep.get("lines", []):
        line_map[(ln["sheet"], ln["row"])] = ln["reclassify_to"]

    sheet_name = ws.title
    # 找单价列、行号
    price_col = None
    for c in range(1, ncol + 1):
        if ws.cell(row=header_row, column=c).value in ("单价", "成本单价"):
            price_col = c
            break

    for r in range(header_row + 1, ws.max_row + 1):
        first = ws.cell(row=r, column=1).value
        if isinstance(first, str) and ("合计" in first or "总计" in first):
            continue
        rowtext = " ".join(str(ws.cell(row=r, column=c).value or "")
                           for c in range(1, ncol + 1))
        reclass = line_map.get((sheet_name, r), "")
        if "差旅" in rowtext or "差旅" in reclass:
            ws.cell(row=r, column=col_t10,
                    value="差旅费按实计列 (PDF 2.1.⑤)，非人天单价，不适用表10")
        else:
            up = ws.cell(row=r, column=price_col).value if price_col else None
            ws.cell(row=r, column=col_t10, value=t10_label(up))
        ws.cell(row=r, column=col_reclass, value=reclass)

    # 重分类声明（红色加粗）
    note_row = ws.max_row + 2
    cell = ws.cell(row=note_row, column=1,
                   value="【重分类声明】本表为《系统集成费》《培训费》《差旅费》测算依据。"
                         "内容已按《柳财审〔2020〕16号》表10 拆分计入《其他费用与预备费》，"
                         "项目合计以《项目总报价汇总》为准，本表金额不重复计入总价。"
                         "各档单价均符合 PDF 表10 系统集成临时人员标准。")
    try:
        cell.font = _ox.styles.Font(bold=True, color="C00000")
    except Exception:
        pass


def _add_device_brand_column(wb, wb_path: str, quote: dict) -> None:
    """每个园所 sheet 增「参考品牌型号(≥3·待核价)」列，按设备名生成国产化候选。"""
    from device_brands import suggest as _brand
    # quote.json 里该工作簿各 sheet 的 header_row + 设备名列名
    sheet_hr = {s["name"]: s["header_row"]
                for w in quote["workbooks"] if w["path"] == wb_path
                for s in w["sheets"]}
    NAME_KEYS = ("设备名称", "产品名称", "名称", "场景名称")
    for sn in wb.sheetnames:
        if sn not in sheet_hr:
            continue
        ws = wb[sn]
        hr = sheet_hr[sn]
        headers = {ws.cell(row=hr, column=c).value: c
                   for c in range(1, ws.max_column + 1)
                   if ws.cell(row=hr, column=c).value}
        if "参考品牌型号(≥3·待核价)" in headers:
            continue
        name_col = next((headers[k] for k in NAME_KEYS if k in headers), None)
        if name_col is None:
            continue
        last = max(headers.values())
        new_col = last + 1
        ws.cell(row=hr, column=new_col, value="参考品牌型号(≥3·待核价)")
        for r in range(hr + 1, ws.max_row + 1):
            nm = ws.cell(row=r, column=name_col).value
            if isinstance(nm, str) and nm.strip():
                ws.cell(row=r, column=new_col, value=_brand(nm))


def op_sheet_restructure(wb, suggestion: dict) -> str:
    op = suggestion["proposed_change"].get("op")
    if op == "create_summary":
        sheet_name = suggestion["proposed_change"]["sheet_name"]
        if sheet_name in wb.sheetnames:
            return f"summary sheet {sheet_name} already exists"
        ws_new = wb.create_sheet(sheet_name)
        ws_new.append(["序号", "园所/分项", "金额（元）"])
        running_total = 0.0
        for i, src_sheet in enumerate(suggestion["proposed_change"].get("source_sheets", []), 1):
            if src_sheet not in wb.sheetnames:
                continue
            ws_src = wb[src_sheet]
            # naive sum: scan last column for numbers
            total = sum(
                v
                for row in ws_src.iter_rows(values_only=True)
                for v in row
                if isinstance(v, (int, float))
            )
            ws_new.append([i, src_sheet, total])
            running_total += total
        ws_new.append([None, "合计", running_total])
        return f"created summary sheet {sheet_name} with {len(suggestion['proposed_change'].get('source_sheets', []))} sources"
    return f"unsupported restructure op: {op}"


# ---- driver ----------------------------------------------------------------
def apply_plan(plan_path: str, quote_json_path: str) -> dict[str, Any]:
    plan = json.loads(Path(plan_path).read_text())
    quote = json.loads(Path(quote_json_path).read_text())

    # group decisions by workbook path
    by_wb: dict[str, list[dict]] = {}
    add_cost_items: list[dict] = []
    commercial_items: list[dict] = []
    for d in plan["decisions"]:
        if d.get("decision") not in {"accept", "edit"}:
            continue
        wb_path = d["target"]["workbook"]
        # 完整性补全 + 比例守恒 — meta 目标，单独处理
        if wb_path.startswith("@new:") and d["category"] == "add-cost-item":
            add_cost_items.append(d)
            continue
        if d["category"] == "add-summary-sheet":
            # group with the device workbook for summary insertion
            by_wb.setdefault(wb_path, []).append(d)
            continue
        if d["category"] == "commercial-component":
            commercial_items.append(d)
            continue
        if wb_path == "@meta":
            continue   # compliance-cap 信息项，不落表
        by_wb.setdefault(wb_path, []).append(d)

    changelog = []
    output_paths = []
    for wb_path, decisions in by_wb.items():
        wb = openpyxl.load_workbook(wb_path)
        # sheet → header_row from quote.json
        sheet_header = {
            s["name"]: s["header_row"]
            for w in quote["workbooks"]
            if w["path"] == wb_path
            for s in w["sheets"]
        }
        # apply in descending row order to avoid index shift
        decisions.sort(key=lambda d: -int(d["target"].get("row", 0)))
        for d in decisions:
            cat = d["category"]
            sheet_name = d["target"].get("sheet")
            try:
                if cat == "add-summary-sheet":
                    msg = op_add_summary_sheet(wb, d)
                elif cat == "sheet-restructure":
                    msg = op_sheet_restructure(wb, d)
                else:
                    ws = wb[sheet_name]
                    hr = sheet_header.get(sheet_name, 1)
                    if cat == "subject-mapping":
                        msg = op_subject_mapping(ws, d, hr)
                    elif cat == "semantic-split":
                        msg = op_semantic_split(ws, d, hr)
                    elif cat == "labor-pricing":
                        msg = op_labor_pricing(ws, d, hr)
                    elif cat == "overlap-attribution":
                        msg = op_overlap_attribution(ws, d, hr)
                    else:
                        msg = f"unsupported category: {cat}"
                changelog.append({"id": d["id"], "status": "applied", "detail": msg})
            except Exception as exc:  # pragma: no cover
                changelog.append({"id": d["id"], "status": "failed", "detail": str(exc)})

        # 部署&交付 sheet → 改造为「系统集成费测算依据」
        for sn in list(wb.sheetnames):
            if "部署" in sn and "交付" in sn:
                _transform_deploy_sheet(wb[sn], wb_path, quote)

        # 设备工作簿：每行补 ≥3 个国产化品牌型号候选（T2，供商务核价）
        if "设备" in Path(wb_path).name:
            _add_device_brand_column(wb, wb_path, quote)

        out_path = str(Path(wb_path).with_name(f"quote-final-{Path(wb_path).stem}.xlsx"))
        wb.save(out_path)
        output_paths.append(out_path)

    # 商业组件剥离合计 → 填补「软件产品购置费」
    sw_purchase_total = sum(
        d["proposed_change"]["detach_amount_to_purchase_fee"] for d in commercial_items
    )

    # 创建「其他费用与预备费」补全 workbook
    if add_cost_items:
        wb_new = openpyxl.Workbook()
        ws = wb_new.active
        ws.title = "其他费用与预备费"
        ws.append(["序号", "费用名称", "金额（元）", "计算依据", "标准条款", "PDF 页", "备注"])
        order = ["系统集成费", "第三方测试费", "设计/咨询服务费", "工程监理费",
                 "信息安全等级保护测评费", "信息安全风险评估费", "密码应用安全评估费",
                 "预备费", "软件产品购置费"]
        sorted_items = sorted(
            add_cost_items,
            key=lambda d: order.index(d["proposed_change"]["fee_name"])
            if d["proposed_change"]["fee_name"] in order else 99,
        )
        seq = 0
        total = 0.0
        for d in sorted_items:
            pc = d["proposed_change"]
            ref = d["standard_refs"][0] if d["standard_refs"] else {}
            seq += 1
            fee_name = pc["fee_name"]
            amount = pc["amount"]
            note = pc.get("note", "")
            # 软件产品购置费：本项目主要采用开源/国产技术栈，默认 ¥0，
            # 由技术据实补充（明细 sheet 提供占位行）
            if fee_name == "软件产品购置费":
                amount = round(sw_purchase_total, 0)  # 停用自动剥离后恒为 0
                note = "本项目主要采用开源/国产技术栈，默认 ¥0；如确有商业软件采购，"\
                       "由技术在「软件产品购置费明细」sheet 据实补充"
            ws.append([
                seq, fee_name, amount,
                pc.get("formula_explain", ""),
                ref.get("section", ""), ref.get("page", ""), note,
            ])
            total += amount
            changelog.append({"id": d["id"], "status": "applied",
                              "detail": f"新增「{fee_name}」 ¥{amount:,.0f}"})
        ws.append([])
        ws.append([None, "其他费用与预备费合计", total])

        # ---- 新增 sheet：软件产品购置费明细（PDF 表6 格式，纯技术填写）----
        if True:
            ws2 = wb_new.create_sheet("软件产品购置费明细")
            ws2.append([None, "【纯技术填写】本项目主要采用开源/国产技术栈"
                        "（如 PyTorch/Triton 等推理框架、PostgreSQL/达梦等数据库均开源/国产），"
                        "默认无商业软件购置费（¥0）。如项目确有商业 OS/数据库/中间件/工具采购，"
                        "由技术人员在下表据实补充，并附 ≥3 品牌型号询价单。"])
            ws2.append(["序号", "分类名称", "参考品牌型号（≥3，PDF表6注3）", "性能/用途",
                        "数量", "金额（元）", "来源（剥离自）", "标准条款"])
            # 按 kind 分组合并
            BRAND_REF = {
                "AI 推理": "NVIDIA Triton / TensorFlow Serving / 商汤 SenseCore / 百度 PaddleServing",
                "AI 算力": "NVIDIA A800 / 华为昇腾910B / 海光 DCU Z100 / 寒武纪 MLU370",
                "GPU 算力": "NVIDIA A800 / 华为昇腾910B / 海光 DCU Z100 / 寒武纪 MLU370",
                "数据库": "达梦 DM8 / 人大金仓 KingbaseES V8 / openGauss / PostgreSQL",
                "中间件": "东方通 TongWeb / 金蝶 Apusic / 宝兰德 / Apache Tomcat",
                "报表 BI": "帆软 FineBI / 永洪 BI / 思迈特 Smartbi / Apache Superset",
                "BI": "帆软 FineBI / 永洪 BI / 思迈特 Smartbi / Apache Superset",
                "安全": "深信服 / 天融信 / 启明星辰 / 奇安信",
                "网络": "华为 / 新华三 H3C / 锐捷 / 中兴",
                "操作系统": "麒麟 KylinOS / 统信 UOS / 欧拉 openEuler / Red Hat",
                "办公套件": "金山 WPS / 永中 Office / 数科 OFD",
                "数据可视化": "帆软 / 阿里 DataV / 海致星图 / ECharts(开源)",
                "3D 可视化": "Hightopo HT / 山海鲸 / 优锘 ThingJS",
                "工作流引擎": "活字格 / 炎黄盈动 AWS / Camunda / Flowable(开源)",
            }
            seq2 = 0
            sw_total = 0.0
            # 每行只归到「主组件」(detected_components 里 ratio 最高的)，避免跨类重复计金额
            by_kind: dict[str, dict] = {}   # kind -> {amount, labels, srcs}
            for d in commercial_items:
                pc = d["proposed_change"]
                comps = pc["detected_components"]
                if not comps:
                    continue
                dominant = max(comps, key=lambda c: c["ratio"])
                kind = dominant["kind"]
                slot = by_kind.setdefault(kind, {"amount": 0.0, "labels": set(), "srcs": set()})
                slot["amount"] += pc["detach_amount_to_purchase_fee"]
                slot["labels"].add(dominant["label"])
                slot["srcs"].add(f"{d['target']['sheet'][:12]}行{d['target']['row']}")
            for kind, slot in by_kind.items():
                seq2 += 1
                srcs = ", ".join(sorted(slot["srcs"]))[:60]
                labels = " / ".join(sorted(slot["labels"]))[:40]
                ws2.append([
                    seq2, kind,
                    BRAND_REF.get(kind, "待商务补充 ≥3 个品牌型号 + 询价单"),
                    labels,
                    "按需", round(slot["amount"], 0), srcs, "三.(一).2.4 表6",
                ])
                sw_total += slot["amount"]
            # 技术补充占位行（蓝斜体待补，由技术人员填）
            for ph in ("操作系统（如使用商业 OS）", "数据库（如使用商业 DB）",
                        "中间件 / 应用服务器", "其他商业软件 / 工具"):
                seq2 += 1
                ws2.append([seq2, ph,
                            "待技术补充 ≥3 个国产/主流品牌型号 + 询价单",
                            "待技术确认是否涉及", "待补", "待技术补充",
                            "待技术补充", "三.(一).2.4 表6"])
            ws2.append([])
            ws2.append([None, "软件产品购置费合计（开源/国产栈默认 ¥0，待技术据实补充）",
                        None, None, None, 0])
            ws2.append([])
            ws2.append([None, "说明：本表为技术补充占位（蓝色斜体=待填）。本项目技术栈"
                        "以开源/国产为主，开源组件（Triton/PyTorch/PostgreSQL 等）属定制"
                        "开发范畴、不计软件购置费，其费用保留在大模型/平台软件报价。"
                        "GPU/算力硬件亦不在此表。仅当确有商业软件采购时由技术据实补充。"])

        out_path = str(Path(quote_json_path).parent / "quote-additional-fees.xlsx")
        wb_new.save(out_path)
        output_paths.append(out_path)

    return {"changelog": changelog, "outputs": output_paths}


if __name__ == "__main__":  # pragma: no cover
    if len(sys.argv) != 3:
        sys.exit("usage: python apply_change.py <approved-plan.json> <quote.json>")
    result = apply_plan(sys.argv[1], sys.argv[2])
    print(json.dumps(result, ensure_ascii=False, indent=2))
