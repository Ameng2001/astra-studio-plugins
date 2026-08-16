---
name: quote-rewrite
description: 'Apply an approved optimization plan to the original quote spreadsheets. Modifies subject codes, splits platform/LLM semantics, recomputes labor pricing, restructures sheets, and recomputes all totals. Outputs `quote-final.xlsx` (one per source workbook) plus a changelog. Trigger phrases: "按方案重写报价", "应用优化方案", "/fund-review:rewrite".'
allowed-tools: Read, Write, Bash, Glob
user-invocable: true
---

# quote-rewrite

Deterministic rewrite — no LLM. Each suggestion in `approved-plan.json` maps to a typed operation; this skill executes them, then recomputes derived totals.

## Preconditions
- `.fund-review/{session-id}/approved-plan.json` exists (otherwise refuse and instruct user to run `quote-optimize` first)
- Original xlsx paths still resolve (from `session.json`)

## Steps

1. **Load** approved-plan.json + session.json + original xlsx files (`openpyxl.load_workbook(keep_vba=False)`)
2. **Group decisions** by target workbook → sheet → row, sort by row descending (so structural ops don't shift indices for later ops)
3. **Apply each accepted/edited decision** via `${CLAUDE_SKILL_DIR}/../../scripts/apply_change.py`:

| suggestion.category | operation |
|---|---|
| `subject-mapping` | ensure 「标准科目」 column exists; set value |
| `semantic-split` | replace cell text in 建设详情/详情 (preserve newlines and formatting) |
| `labor-pricing` | set unit price; recompute 成本总价/对外总价 in same row |
| `sheet-restructure` | create/rename/merge sheets per plan's target schema |
| `overlap-attribution` | set 「分摊比例」 column + recompute split totals |
| `other` | record as warning; skip |

4. **Recompute all aggregates**:
   - Row-level totals (any column expressed as `单价 × 数量` or `单价 × 人天`)
   - Sheet-level subtotals
   - Cross-sheet summary sheets (e.g., 设备汇总)
   - Workbook-level total (if there's a 汇总 sheet)
5. **Write outputs**:
   - `quote-final-{original-name}.xlsx` next to the originals (or under session workspace if user opts)
   - `.fund-review/{session-id}/changelog.md` — per-decision result: applied / skipped (with reason) / warning
6. **Sanity check**: assert that the sum of `estimated_delta_amount` from accepted decisions roughly matches the actual aggregate delta (within 1%); raise warning if not

## Safety
- **Never** overwrite the originals. Always produce a new file.
- If any decision fails to apply (e.g., row no longer matches), record in changelog and continue with the rest. Do not fail the entire run.
- Preserve styles, merged cells (unmerge → apply → remerge), and column widths.

## Output contract
- `quote-final-*.xlsx` (one per source workbook)
- `.fund-review/{session-id}/changelog.md`
- Updates `session.json` with `status: rewritten` and paths to outputs
