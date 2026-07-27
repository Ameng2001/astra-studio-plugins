# quote-parser — xlsx → quote.json

Convert one or more quote xlsx files into a uniform structure the optimize/rewrite/review skills can operate on.

## Required output shape

```jsonc
{
  "workbooks": [
    {
      "path": "广西数智幼教项目平台软件功能及报价清单-20260430.xlsx",
      "kind": "platform"|"llm"|"device"|"mixed"|"unknown",
      "sheets": [
        {
          "name": "幼教综合服务平台",
          "kind": "items"|"summary"|"header"|"unknown",
          "header_row": 2,
          "columns": [
            { "index": 1, "header": "序号" },
            { "index": 7, "header": "详情" },
            { "index": 8, "header": "成本单价" },
            { "index": 10, "header": "人/天" }
          ],
          "rows": [
            {
              "row_index": 3,
              "cells": { "序号": 1, "建设内容": "智慧管理", "详情": "...", "成本单价": 1200, "人/天": 80, "成本总价": 96000 }
            }
          ],
          "merged_cells": [ "A1:K1" ],
          "notes": [ ... ]
        }
      ]
    }
  ]
}
```

## Auto-classification rules
- **platform**: sheet contains columns 成本单价 + 对外单价 + 人/天 → `kind: platform`
- **llm**: workbook name contains 大模型 or 行业模型 → `kind: llm` (sheets further classified as 建设 vs 运营 by header signature)
- **device**: workbook contains multiple 园所 sheets with 报价单位/询价单位 header → `kind: device`
- **mixed**: any workbook with sheets of differing kinds (warn in output)

## Implementation
- Library: `openpyxl` (load_workbook with `data_only=True` for computed values; second pass with `data_only=False` to capture formulas)
- Preserve `row_index` / `col_index` so quote-rewrite can target cells precisely
- Handle merged cells: record the merge range, expose the value only on the top-left cell
- Skip fully blank rows but keep their indices in `gaps[]` for rewrite to honor structure

## Known quirks of the三件 xlsx
- 平台软件主表用 R1 做标题行、R2 才是 header → `header_row: 2`
- 大模型 9 sheet 中 L1/L2 × 建设/运营 的 8 张表 header 结构差异较大；用列名签名映射，不要靠位置
- 设备 6 个园所 sheet 顶部是询价单模板格式（4 列对齐），实际明细从 R7 左右开始；要做 sniff

## Test fixtures
- 真实三件 xlsx → 解析后的 quote.json
- 单元测试：每个 sheet kind 各至少一例
