---
name: create-doc-expert
description: Create a document writing expert with a specific style, terminology preferences, and policy citation habits. Use when you need a specialist writer for government proposals, technical documents, or industry-specific reports. Extends the standard expert with a Writing Style section.
allowed-tools: Read, Write, Glob
user-invocable: true
---

# Create Doc Expert

Create a new document writing expert — a specialized agent definition that captures a specific writing style, terminology preferences, policy citation habits, and paragraph patterns. The expert can then be referenced by plan-document and write-section to produce consistent, on-brand document content.

This skill extends the standard create-expert workflow with a **Writing Style** section that codifies measurable stylistic features extracted from reference documents or user descriptions.

## Agent Lookup Order

Skills look for writer agent definitions in this order:
1. **Project-level**: `studio/agents/writers/*.md` — your team's customized writers (git-tracked)
2. **Built-in template**: `${CLAUDE_SKILL_DIR}/../../agents/_doc-expert-template.md` — starter template

Project-level writers are available to all document generation skills. Multiple writers can coexist — plan-document lets the user choose which writer to use for each document.

## Workflow

1. **Discover existing writers** — check what's already available
2. **Choose approach** — analyze reference docs, manual definition, or customize existing
3. **Gather information** — collect domain, style, and behavioral details
4. **Write agent definition** — produce the .md file with Writing Style section
5. **Save** — write to project-level `studio/agents/writers/`
6. **Report** — show style fingerprint summary

## Step 1: Discover Existing Writers

Scan for existing writer agents:

```
# Project-level writers
studio/agents/writers/*.md

# Built-in template
${CLAUDE_SKILL_DIR}/../../agents/_doc-expert-template.md
```

Report what's available. If writers already exist, list them with their domain and target document types so the user can decide whether to create a new one or customize an existing one.

## Step 2: Choose Approach

Ask the user what they need:

**A) Analyze reference documents**
- User provides 1–3 sample documents (file paths) that exemplify the desired writing style
- Read each document and extract writing style features:
  - **四字短语密度**: Count four-character phrases per paragraph, classify as 高/中/低
  - **句式模式**: Identify recurring sentence structures (progressive 递进, parallel 对仗, conditional 条件)
  - **政策引用习惯**: Extract citation format, frequency, and preferred sources
  - **段落结构**: Analyze paragraph composition (thesis → evidence → value, or other patterns)
  - **术语偏好**: Note which synonyms the author prefers (e.g., "赋能" vs "助力", "打造" vs "构建")
  - **标志性表达**: Collect distinctive phrases that appear repeatedly
- Present the extracted style profile for user validation before generating the agent

**B) Manual definition**
- User describes the writing style they want in their own words
- Skill translates the description into structured style dimensions
- Fill in gaps with reasonable defaults for the document domain

**C) Customize existing writer**
- User wants to modify an existing writer's style or domain
- Read the existing writer file, then make modifications
- Save to `studio/agents/writers/` with a new or same filename

## Step 3: Gather Information

### Basic Identity

- **Expert title**: What should this writer be called? (e.g., "解决方案专家-肖作仕", "政府IT方案撰写专家")
- **Domain scope**: What area does this writer cover? (e.g., "政府IT项目方案编制", "教育信息化建设文档")
- **Credentials**: What background does this writer have? (e.g., "10年政府IT项目方案撰写经验")

### Writing Style Dimensions

Gather or extract these dimensions:

**语言标记 (Language Markers)**
- 四字短语密度: 高(每段5+)、中(每段2-4)、低(每段0-1)
  - Examples: "统筹规划、协同推进、融合创新"
- 对仗句式频率: 高/中/低
  - Examples: "既要…又要…", "不仅…而且…"
- 递进模式: Identify progressive patterns
  - Examples: "依托…构建…实现…", "以…为基础，以…为抓手，以…为目标"
- 标志性表达: List distinctive phrases the writer uses repeatedly
  - Examples: "赋能增效", "数字化转型", "高质量发展"

**政策引用 (Policy Citations)**
- 引用格式模板: How are policies cited?
  - Example: `《XXX》（国办发〔20XX〕XX号）`
- 偏好政策来源: Which authorities are cited most?
  - Example: 国务院、教育部、人社部、工信部
- 引用密度: How often do policy citations appear?
  - Example: 每章2-3处政策引用, 关键论点必引政策

**段落模式 (Paragraph Pattern)**
- 结构: How are paragraphs composed?
  - Example: 论点 + 论据 + 价值阐述 (thesis + evidence + value proposition)
- 长度范围: Target paragraph length
  - Example: 200-400 characters
- 量化数据要求: How much data is expected?
  - Example: 每段至少1个量化数据或具体指标

**术语偏好 (Terminology Preferences)**
- Collect pairs of synonyms with the writer's preference:
  - "赋能" vs "助力" → prefer "赋能"
  - "打造" vs "构建" → prefer "打造"
  - "落地" vs "实施" → prefer "落地"
  - "抓手" vs "着力点" → prefer "抓手"

### Target Document Types

- Which blueprint types is this writer suited for?
  - Example: `["feasibility-study", "construction-plan", "project-proposal"]`

### Behavioral Patterns

- How does this writer approach a new chapter? (e.g., "Always opens with policy context before diving into specifics")
- What does this writer prioritize? (e.g., "Quantifiable benefits over qualitative descriptions")
- What does this writer avoid? (e.g., "Vague generalizations without supporting data")

If the user provides a brief description or reference documents, extrapolate the full style profile. Present the draft for validation.

## Step 4: Write Agent Definition

Follow the extended structure that includes the Writing Style section:

```markdown
---
name: {expert-slug}
description: {One-line summary of which document types, industries, and writing style this expert covers — this is what the runtime matches against to discover relevant writers, so name the document categories and the situations that call for it.}
---

# Role: {Expert Title}

You are a {credentials} specializing in writing formal project documents.

## Your Domain

{2-3 sentence domain description covering the writer's area of expertise,
the types of documents they produce, and the audience they write for.}

## Your Perspective

{What lens this writer sees through. What they prioritize when writing.
For example: "You see every document as a persuasion tool — each chapter
must build the case for project approval with concrete evidence and
clear value propositions."}

## Writing Style

### Language Markers
- Four-character phrase density: {高/中/低} (e.g., "统筹规划、协同推进、融合创新")
- Parallel structures: {frequency} (e.g., "既要…又要…", "不仅…而且…")
- Progressive patterns: {patterns} (e.g., "依托…构建…实现…")
- Signature expressions: {list of distinctive phrases}

### Policy Citations
- Format: {e.g., 《XXX》（国办发〔20XX〕XX号）}
- Preferred sources: {e.g., 国务院、教育部、人社部}
- Citation density: {e.g., 每章2-3处政策引用}

### Paragraph Pattern
- Structure: {e.g., thesis + evidence + value proposition}
- Length: {e.g., 200-400 characters}
- Data requirement: {e.g., 每段至少1个量化数据}

### Terminology Preferences
- {term_pair}: prefer "{preferred}" over "{avoided}"
- {term_pair}: prefer "{preferred}" over "{avoided}"
- {term_pair}: prefer "{preferred}" over "{avoided}"

## What You Contribute

### Document Expertise
- {Specialized knowledge area 1}
- {Specialized knowledge area 2}
- {Specialized knowledge area 3}

### Quality Criteria
- {What makes a good document in this domain}
- {Minimum standards the writer enforces}
- {Red flags the writer watches for}

## How You Write

- {Behavioral pattern 1: e.g., "Always open a chapter with macro policy context before narrowing to project specifics"}
- {Behavioral pattern 2: e.g., "Every claim must be backed by either a policy reference or a quantitative metric"}
- {Behavioral pattern 3: e.g., "Use progressive sentence structures to build momentum toward the value proposition"}
- {Behavioral pattern 4: e.g., "Close each major section with a bridge sentence linking to the next topic"}

## Target Document Types

- {blueprint-name-1}: {why this writer is suited}
- {blueprint-name-2}: {why this writer is suited}
```

### Writing Style Extraction Guidelines

When analyzing reference documents (Approach A), use these heuristics:

| Feature | How to Measure | Classification |
|---------|---------------|----------------|
| 四字短语密度 | Count 4-char phrases per paragraph | 高: ≥5, 中: 2-4, 低: 0-1 |
| 对仗句式 | Count parallel structures per page | 高: ≥3, 中: 1-2, 低: <1 |
| 政策引用密度 | Count citations per chapter | 高: ≥4, 中: 2-3, 低: 0-1 |
| 段落长度 | Average character count per paragraph | 短: <150, 中: 150-350, 长: >350 |
| 量化数据 | Count numbers/percentages per paragraph | 高: ≥2, 中: 1, 低: <1 |

## Step 5: Save

Write the agent definition to `studio/agents/writers/{expert-slug}.md`.

If `studio/agents/writers/` doesn't exist, create it (with `.gitkeep`).

The filename should be kebab-case derived from the expert title:
- "解决方案专家-肖作仕" → `solution-expert-xiao.md`
- "政府IT方案撰写专家" → `gov-it-proposal-writer.md`

## Step 6: Report

Print a summary showing the style fingerprint:

```
Writer created: studio/agents/writers/{expert-slug}.md

  Expert:         {title}
  Domain:         {domain scope}
  
  Style Fingerprint:
    四字短语:      {高/中/低} — {example phrases}
    对仗句式:      {频率}
    段落模式:      {structure} ({length} chars)
    政策引用:      {density}, {preferred sources}
    术语偏好:      {key preferences}
  
  Target types:   {list of blueprint names}

This writer is now available for:
  /studio-docs:plan     — select this writer when planning a document
  /studio-docs:generate — uses writer style for content generation
  /studio-docs:write    — applies writer style per section

To share with your team, commit studio/agents/writers/ to git.
```

If the user customized an existing writer, note: "This replaces the previous `{name}`. The old version is no longer active."

## Tips

- **Reference document analysis is the best approach** for capturing a real person's writing style. Even 2-3 pages of sample text can produce a useful style profile.
- **Multiple writers can coexist** — a project might have one writer for technical chapters and another for executive summaries.
- **Style consistency is more important than style perfection** — it's better to have a slightly imperfect but consistently applied style than to manually rewrite every paragraph.
- **Test the writer** by running a small section generation with write-section and comparing the output to the reference documents. Iterate on the style definition until satisfied.
- When the user says "I need a writer like 肖总" or provides a name, ask for sample documents from that person — names alone don't capture writing style.
