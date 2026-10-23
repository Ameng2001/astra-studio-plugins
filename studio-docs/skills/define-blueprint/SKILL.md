---
name: define-blueprint
description: Create or customize a document blueprint that defines the structure, compliance rules, and export format for a project document type (proposal, feasibility study, tender, etc.). Use when you need to define a new document type or customize an existing blueprint for your project.
allowed-tools: Read, Write, Glob
user-invocable: true
---

# Define Blueprint

Create a new document blueprint or customize an existing one. A blueprint defines the chapter structure, compliance rules, writing guidelines, and export format for a specific project document type (e.g., 建设方案, 可行性研究报告, 招标文件).

## Blueprint Lookup Order

Skills look for blueprints in this order:
1. **Project-level**: `studio/blueprints/*.yaml` — your project's customized blueprints (git-tracked)
2. **Built-in**: `${CLAUDE_SKILL_DIR}/../../templates/blueprints/*.yaml` — shipped with studio-docs

Project-level blueprints with the same filename **override** built-in ones. This lets you customize the built-in construction-plan or feasibility-study to match your project's requirements.

## Built-in Blueprints

The following blueprints ship with studio-docs:

| Blueprint | Display Name | Lifecycle Stage | Chapters |
|-----------|-------------|-----------------|----------|
| `project-proposal.yaml` | 项目建议书 | initiation | 8 |
| `feasibility-study.yaml` | 可行性研究报告 | justification | 12+附录 |
| `preliminary-design.yaml` | 初步设计 | design | 11+附录 |
| `tender-document.yaml` | 招标文件 | procurement | 7+附件 |
| `bid-document.yaml` | 投标文件 | response | 3大部分 |
| `construction-plan.yaml` | 建设方案 | implementation | 9+附录 |
| `acceptance-package.yaml` | 验收材料 | delivery | 9+附件 |
| `academic-paper.yaml` | 学术论文（CS 会议） | publication | 8+附录 |

The first seven cover the typical lifecycle of a government IT project — from initiation through delivery. `academic-paper` is different in kind: its `artifact_sources` are not studio planning artifacts but the files of an **evidence pack** (`artifact_root`, by default `.studio-paper/{paper-id}/results/evidence-pack`), and its compliance rules forbid any number that is not in that pack.

## Workflow

1. **Choose approach** — use built-in, customize existing, or create from scratch
2. **Discover blueprints** — locate built-in and project-level blueprints
3. **Gather requirements** — understand the document type and audience
4. **Generate blueprint** — produce the YAML definition
5. **Save** — write to project-level `studio/blueprints/`

## Step 1: Choose Approach

Ask the user what they need:

**A) Use a built-in blueprint as-is**
- User wants one of the 7 built-in document types without modification
- List the built-in blueprints table above and let the user pick
- Copy the selected blueprint to `studio/blueprints/` so it becomes part of the project

**B) Customize an existing blueprint**
- User wants to modify a built-in blueprint (add/remove chapters, change compliance rules, adjust style)
- Read the built-in blueprint YAML, then make modifications based on user requirements
- Save to `studio/blueprints/` with the same filename (overrides built-in)

**C) Create from scratch**
- User needs a document type not covered by the built-in blueprints
- Gather all requirements from the user
- Produce a new YAML blueprint following the standard schema

## Step 2: Blueprint Discovery

Scan for existing blueprints in both locations:

```
# Built-in blueprints
${CLAUDE_SKILL_DIR}/../../templates/blueprints/*.yaml

# Project-level blueprints (take precedence)
studio/blueprints/*.yaml
```

Report what's available. If the user's desired document type already exists at the project level, warn them and ask whether to overwrite or create a variant (e.g., `feasibility-study-v2.yaml`).

## Step 3: Gather Requirements

For **customization** or **creation from scratch**, ask the user:

### Required Information

- **Document type name**: What is this document called? (e.g., "实施方案", "系统设计说明书")
- **Target audience**: Who reads this document? (e.g., 政府评审专家, 甲方项目经理, 内部技术团队)
- **Lifecycle stage**: Where does this document fit in the project lifecycle? (initiation / justification / design / procurement / response / implementation / delivery)

### Chapter Structure

- **Confirm or customize chapters**: Present the default chapter structure (if customizing) or ask the user to list their chapters
- For each chapter, gather:
  - Title (e.g., "一、项目概述")
  - Required sections within the chapter
  - `artifact_sources`: which planning artifacts feed into this chapter (event-storm, domain-model, skill-map, brief, etc.)
  - Estimated word count range
  - Whether the chapter is optional or required

### Compliance Requirements

- **Policy framework**: Which national/local policies must be referenced? (e.g., 《"十四五"数字经济发展规划》)
- **Standard compliance**: Which technical standards apply? (e.g., GB/T 22239 信息安全等级保护)
- **Format requirements**: Specific formatting rules from the issuing authority
- **Mandatory sections**: Sections that must appear regardless of project specifics (e.g., 信息安全, 经费预算)

### Export Format Preferences

- **Primary format**: Markdown / Word / PDF
- **Template**: Whether a specific Word template should be applied
- **Numbering style**: Chinese ordinals (一、二、三) vs Arabic (1, 2, 3) vs mixed
- **Header levels**: Maximum nesting depth

If the user provides a brief description, extrapolate reasonable defaults from knowledge of the document type. Present the draft for validation before saving.

## Step 4: Generate Blueprint YAML

Follow the standard blueprint schema:

```yaml
# Blueprint: {document_type}
name: {kebab-case-name}
display_name: "{中文显示名}"
version: "1.0"
lifecycle_stage: {stage}
description: "{一句话描述}"

metadata:
  audience: ["{audience1}", "{audience2}"]
  typical_length: "{e.g., 50-80页}"
  review_cycle: "{e.g., 内部评审→专家评审→修改定稿}"
  inherits_from: null  # or a previous document type that feeds into this one

style:
  tone: formal  # formal / semi-formal / technical
  numbering: chinese_ordinal  # chinese_ordinal / arabic / mixed
  paragraph_min_words: 150
  paragraph_max_words: 400
  four_char_density: high  # high / medium / low
  policy_citation_density: 2-3_per_chapter

compliance:
  policies:
    - name: "《政策名称》"
      id: "国办发〔20XX〕XX号"
      relevance: "{why this policy matters}"
  standards:
    - name: "GB/T XXXXX"
      description: "{standard description}"
  mandatory_sections:
    - "信息安全与等级保护"
    - "经费预算与资金来源"

chapters:
  - id: ch1
    title: "一、{章节标题}"
    required: true
    sections:
      - title: "{节标题}"
        guidance: "{写作指导}"
    artifact_sources:
      - type: event-storm
        usage: "{how this artifact feeds the chapter}"
    estimated_words: 3000-5000
    
  # ... more chapters

appendices:
  - id: app1
    title: "附录一：{附录标题}"
    required: false
    description: "{what goes here}"

export:
  primary_format: markdown
  word_template: null  # path to .docx template if applicable
  header_depth: 4
  toc: true
  page_numbers: true
```

### Schema Validation Rules

Before saving, validate the blueprint:
- Every chapter must have an `id`, `title`, and `estimated_words`
- `artifact_sources` should reference known artifact types: `event-storm`, `domain-model`, `skill-map`, `brief`, `persona-insight`, `journey-map`, `process-flow`, `behavior-matrix`
- `lifecycle_stage` must be one of: `initiation`, `justification`, `design`, `procurement`, `response`, `implementation`, `delivery`
- `compliance.policies` should have at least one entry for government documents
- Chapter `id` values must be unique

## Step 5: Save

Write the blueprint to `studio/blueprints/{doc-type}.yaml`.

If `studio/blueprints/` doesn't exist, create it (with `.gitkeep`).

The filename should be kebab-case matching the blueprint `name` field: "可行性研究报告" → `feasibility-study.yaml`.

## Step 6: Report

Print a summary of the created/modified blueprint:

```
Blueprint created: studio/blueprints/{doc-type}.yaml

  Document type:  {display_name}
  Lifecycle:      {lifecycle_stage}
  Chapters:       {count} chapters + {appendix_count} appendices
  Compliance:     {policy_count} policies, {standard_count} standards
  Style:          {tone}, {numbering} numbering
  Est. length:    {typical_length}
  Export:         {primary_format}

This blueprint is now available for:
  /studio-docs:plan    — plan a document using this blueprint
  /studio-docs:generate — generate document content

To share with your team, commit studio/blueprints/ to git.
```

If the user customized a built-in blueprint, note: "This overrides the built-in `{name}`. Delete `studio/blueprints/{name}.yaml` to revert to the default."

## Tips

- When creating blueprints for a document series (e.g., 建议书 → 可研 → 初设), use `inherits_from` to chain them. This tells plan-document to look for content from the previous document that can be referenced or expanded.
- Keep `estimated_words` realistic — overestimating leads to padding, underestimating leads to shallow content.
- The `artifact_sources` mapping is critical for plan-document: it determines which planning artifacts feed into each chapter. Be specific about the `usage` field — it guides the writer on how to use the source material.
