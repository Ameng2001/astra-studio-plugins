---
name: init-session
description: 'Initialize a fund-review session workspace. Parses the budget standard PDF, quote xlsx files, and optional quote guideline into structured JSON under `.fund-review/{session-id}/`. Called automatically by quote-optimize on first run; can also be invoked directly. Trigger phrases: "初始化报价会话", "解析标准和报价", "/fund-review:init".'
allowed-tools: Read, Write, Bash, Glob
user-invocable: false
---

# init-session

Bootstrap a fund-review working session. Idempotent — re-running with the same session id refreshes the parsed artifacts.

## Inputs (positional or auto-detected)
- `--standard <path.pdf>` — local budget standard (default: glob `*预算支出标准*.pdf` in cwd)
- `--quotes <path.xlsx>...` — one or more quote spreadsheets (default: glob `*报价清单*.xlsx`)
- `--guideline <path.md>` — optional global pricing guideline document
- `--mapping-pack <key>` — subject mapping pack key (default: `default-liuzhou-kindergarten`)
- `--session-id <id>` — defaults to a timestamped id

## Steps

1. **Resolve inputs** — if not provided, auto-glob cwd. If multiple candidates, list them and ask user to confirm.
2. **Create session dir** — `.fund-review/{session-id}/`.
3. **Parse PDF** via `${CLAUDE_SKILL_DIR}/../../scripts/parse_standard.py` (see `${CLAUDE_SKILL_DIR}/../../references/standard-parser.md`):
   - Extract section tree, per-clause text, page anchors, formulas, unit prices, ranges
   - Write `standard.json`
4. **Parse quote xlsx(s)** via `${CLAUDE_SKILL_DIR}/../../scripts/parse_quote.py` (see `${CLAUDE_SKILL_DIR}/../../references/quote-parser.md`):
   - For each workbook: sheet names, header row, rows with cells (preserve row/col indices for later rewrite)
   - Write `quote.json` (multi-workbook structure)
5. **Copy guideline** as `guideline.md` if provided; if absent, write a stub `guideline.md` with content `# 全局报价指引（未提供）` and continue. Downstream `quote-optimize` will fall back to mapping pack defaults only.
6. **Load mapping pack** from `${CLAUDE_SKILL_DIR}/../../references/mapping/{pack}.yml` → `mapping.json`.
7. **Write `session.json`**:
   ```json
   { "session_id": "...", "created_at": "...", "inputs": {...}, "status": "ready", "schema_version": 1 }
   ```
8. Print summary: counts (clauses parsed, sheets, rows), session path, suggested next command `/fund-review:optimize`.

## Output contract (downstream skills depend on these)
- `.fund-review/{session-id}/standard.json`
- `.fund-review/{session-id}/quote.json`
- `.fund-review/{session-id}/guideline.md` (or missing)
- `.fund-review/{session-id}/mapping.json`
- `.fund-review/{session-id}/session.json`

## Error handling
- PDF parse failure → write what was parsed, mark `status: partial` in session.json, surface to user
- xlsx with merged cells → openpyxl unmerge in a copy; record warning
- No standard PDF found → block, ask user to provide path

## Notes
- This skill is **stateful** — all session output lives under `.fund-review/`. The workspace is git-tracked by default (per studio config); user may add it to `.gitignore` if sensitive.
- Do NOT call optimize/rewrite/review directly from here; they're separate skills.
