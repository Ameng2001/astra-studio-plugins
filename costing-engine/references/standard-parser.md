# standard-parser — PDF → standard.json

Convert a budget standard PDF (e.g., 《柳财审〔2020〕16号》) into a structured JSON the rest of the plugin can query.

## Required output shape

```jsonc
{
  "doc_id": "liufuhshen-2020-16",
  "title": "柳州市本级信息化建设项目预算支出标准（试行）",
  "issued_at": "2020-12-28",
  "page_count": 31,
  "sections": [
    {
      "id": "三.(一).2.1",
      "title": "定制软件开发费",
      "page_start": 12,
      "page_end": 14,
      "text": "...",                       // full clause text
      "kind": "formula"|"unit-price"|"narrative"|"table",
      "rule": {                            // present when machine-checkable
        "formula": "FP × 6.51 / 174 × 17000 + direct_non_labor",
        "parameters": {
          "category_factor": { "业务处理": [0.8, 1.0], "应用集成": [1.0, 1.2], "大数据多媒体": [1.0, 1.3], "人工智能": [1.0, 1.5] },
          "reuse_factor": { "高": 0.333, "中": 0.667, "低": 1.0 }
        },
        "unit": "元",
        "applies_to": ["定制软件开发"]
      }
    }
  ],
  "tables": [ ... ],            // separate index for fast lookup of 表3、表4
  "appendix_terms": [ ... ]
}
```

## Implementation
- Primary tool: `pdfplumber` (or `pypdf` + custom layout heuristics)
- Section detection: outline (TOC) if present; fallback to heading regex (`^[一二三四五六七八九十]、`, `^（[一二三四五六七八九十]+）`, `^[0-9]+\.[0-9]+`)
- Table detection: `pdfplumber.Page.extract_tables()` with column-clustering tuning
- Page anchors: every section node records `page_start` (1-indexed, matching the PDF reader)

## Known quirks of the柳州 PDF
- Source is an `about:blank` printout — body text wraps with `\n` mid-paragraph; concatenate and strip
- Tables 表3 / 表4 / 表5 ... are scattered with section text; parse each separately
- Some clauses give ranges (e.g., `0.8-1.0`); extract as `[min, max]` not single number
- Page numbers in the source are `i/31` footers; parser must use PDF-level page index, not the printed label

## Test fixtures
- `tests/fixtures/standard-liuzhou-2020-16.pdf` (subset of the real PDF)
- Golden JSON: `tests/fixtures/standard-liuzhou-2020-16.expected.json`
- Coverage target v0.1: 软件 + OA + 网站章节 ≥ 95% 字段解析正确
