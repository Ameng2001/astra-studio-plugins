# subject-mapping — 行项目 ↔ 标准科目 映射字典

A pluggable dictionary that maps a quote row to one or more clauses in the budget standard. Plugin ships with at least one default pack (`default-liuzhou-kindergarten.yml`); users can override per project.

## Pack format (YAML)

```yaml
pack_id: default-liuzhou-kindergarten
standard_doc_id: liufuhshen-2020-16
version: 1
rules:
  # 1. Match by sheet kind + keyword
  - id: M001
    when:
      workbook_kind: platform
      sheet_kind: items
      content_match: ["管理", "展示", "数据可视化", "大屏"]
    map_to:
      - section_id: 三.(一).2.1          # 定制软件开发费
        category: 应用集成               # → factor 1.0-1.2
        reuse: 低                        # → 1.0
  - id: M002
    when:
      workbook_kind: platform
      sheet_kind: items
      content_match: ["业务", "申报", "审批", "记录"]
    map_to:
      - section_id: 三.(一).2.1
        category: 业务处理
        reuse: 低
  - id: M010
    when:
      workbook_kind: llm
      sheet_kind: build              # 建设期
      content_match: ["模型", "推理", "训练", "智能体"]
    map_to:
      - section_id: 三.(一).2.1
        category: 人工智能           # 1.0-1.5
        reuse: 低
  - id: M020
    when:
      workbook_kind: llm
      sheet_kind: ops                # 运营/运维
      content_match: ["token", "调用", "API"]
    map_to:
      - section_id: "三.(三).2.4.2"  # 软件运维 - 规模单价法 (p.30-31)
        method: 软件运维-规模单价法
        formula: "(软件规模 × 运维功能点单价) × 级别 × 能力 × 特征 + 直接非人力(算力)"
        note: "运维标准 8.39-12.59 万/人年；token 算力费列直接非人力成本"
  - id: M030
    when:
      workbook_kind: device
    map_to:
      - section_id: "三.(一).2.5"    # 硬件设备购置费 表7 (p.18-19)
        method: 表7-硬件购置
        note: "≥3 个品牌型号对比 + 市场询价"

# 2. Pre-built terminology vocabularies (used by semantic-split scanner)
vocabularies:
  platform_feature:        # 平台 sheets 应使用
    - 功能 ; 特性 ; 模块 ; 配置 ; 页面 ; 流程 ; 表单 ; 报表
  llm_capability:          # 大模型 sheets 应使用
    - 能力 ; 推理 ; 生成 ; 知识 ; 智能体 ; 检索 ; 微调 ; token

# 3. Labor-pricing baselines (folded into formula-engine)
labor_baselines:
  man_month_rate: 17000      # 标准 1.7 万/人月
  workdays_per_month: 21.75
  default_per_day_rate: 781.6   # 17000 / 21.75
```

## Resolution algorithm
1. Iterate `rules[]` in order; first match wins (deterministic)
2. If no rule matches → row is unmapped → emits `subject-mapping` finding with severity `medium`
3. Multiple `map_to[]` entries are allowed for split attribution

## User override
- User can drop `fund-review.mapping.yml` at project root → loaded after the default pack and merged (user rules take precedence)
- A future skill `extend-mapping` (not v0.1) can interactively grow the pack from unmapped rows
