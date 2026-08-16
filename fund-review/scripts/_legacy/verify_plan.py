"""verify_plan — purely accounting verification of G1 ±10% band.

Reads quote.json (parsed cached values, formulas already evaluated by Excel)
+ approved-plan.json (decisions). Recomputes the projected total without
relying on openpyxl formula evaluation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PRICE_HEADERS = ["成本单价", "单价（元）"]
TOTAL_HEADERS = ["成本总价", "成本总价（元）", "研发费计算（元）", "研发费用报价", "研发报价",
                 "对外总价", "对外总价（元）", "对外运营报价（元）", "运维报价", "对外总价"]
QTY_HEADERS = ["人/天", "人天", "数量"]


def get_total_from_row(cells: dict) -> float:
    """First numeric value found among known total headers; 0 if none."""
    for h in TOTAL_HEADERS:
        v = cells.get(h)
        if isinstance(v, (int, float)):
            return float(v)
    return 0.0


def compute_total(quote: dict, decisions_by_loc: dict | None = None) -> dict:
    by_wb = {}
    grand = 0.0
    for wb in quote["workbooks"]:
        sub = 0.0
        sheets_breakdown = []
        for sh in wb["sheets"]:
            if sh["kind"] == "summary":
                continue
            sheet_sub = 0.0
            for row in sh["rows"]:
                key = (wb["path"], sh["name"], row["row_index"])
                orig_total = get_total_from_row(row["cells"])
                if decisions_by_loc and key in decisions_by_loc:
                    d = decisions_by_loc[key]
                    if d["category"] == "labor-pricing":
                        # delta_amount from plan
                        sheet_sub += orig_total + d.get("estimated_delta_amount", 0)
                    else:
                        sheet_sub += orig_total
                else:
                    sheet_sub += orig_total
            sub += sheet_sub
            sheets_breakdown.append((sh["name"], sheet_sub))
        by_wb[Path(wb["path"]).name] = {"total": sub, "sheets": sheets_breakdown}
        grand += sub
    return {"grand_total": grand, "by_workbook": by_wb}


def main(session_dir: str) -> None:
    s = Path(session_dir)
    quote = json.loads((s / "quote.json").read_text())
    plan = json.loads((s / "approved-plan.json").read_text())

    orig = compute_total(quote)
    decisions_by_loc = {
        (d["target"]["workbook"], d["target"]["sheet"], d["target"]["row"]): d
        for d in plan["decisions"]
        if d.get("decision") == "accept"
    }
    new = compute_total(quote, decisions_by_loc)

    delta = new["grand_total"] - orig["grand_total"]
    delta_pct = delta / orig["grand_total"] * 100 if orig["grand_total"] else 0
    lo, hi = orig["grand_total"] * 0.90, orig["grand_total"] * 1.10

    print(f"{'workbook':70s} {'original':>14s} {'optimized':>14s} {'Δ':>14s}")
    print("-" * 116)
    for name in orig["by_workbook"]:
        o = orig["by_workbook"][name]["total"]
        n = new["by_workbook"][name]["total"]
        print(f"{name[:70]:70s} ¥{o:>13,.0f} ¥{n:>13,.0f} ¥{n-o:>+13,.0f}")
    print("-" * 116)
    print(f"{'GRAND TOTAL':70s} ¥{orig['grand_total']:>13,.0f} ¥{new['grand_total']:>13,.0f} ¥{delta:>+13,.0f}")
    print()
    print(f"  delta: {delta_pct:+.2f}%")
    print(f"  G1 ±10% band: ¥{lo:,.0f} ~ ¥{hi:,.0f}")
    print(f"  within band: {lo <= new['grand_total'] <= hi}")


if __name__ == "__main__":  # pragma: no cover
    main(sys.argv[1] if len(sys.argv) > 1 else ".fund-review/smoke")
