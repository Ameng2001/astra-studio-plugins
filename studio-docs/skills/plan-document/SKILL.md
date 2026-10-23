---
name: plan-document
description: Plan a document by analyzing studio planning artifacts, mapping them to blueprint chapters, and producing a parallel writing strategy with checkpoint recovery. Use when you're ready to generate a formal document from existing planning work.
allowed-tools: Read, Write, Glob, Grep
user-invocable: true
---

# Plan Document

Analyze the studio planning workspace, map artifacts to blueprint chapters, and produce a comprehensive doc plan with parallel writing strategy and checkpoint recovery. This skill bridges the gap between planning (event-storm, domain-model, skill-design) and writing (write-section, assemble-document).

**Important: plan-document does NOT write content.** It only produces the doc plan YAML. The `generate` command orchestrates the actual writing by chaining write-section → assemble-document.

## Prerequisites

Before running plan-document, the project should have:
- A `studio/` workspace with planning artifacts (from studio-planner skills)
- At least one blueprint available (built-in or project-level)
- Optionally, one or more writer agents (from create-doc-expert)

## Workflow

1. **Pre-check** — verify workspace and available resources
2. **Select blueprint** — choose the document type
3. **Select domain** — choose the planning domain
4. **Select writer** — choose or skip the writer agent
5. **Artifact mapping** — map planning artifacts to blueprint chapters
6. **Parallel strategy** — design the concurrent writing plan
7. **Generate doc plan** — produce the YAML plan file
8. **Present plan** — show summary and ask for confirmation
9. **Report** — final summary with next steps

## Step 1: Pre-check

Verify the workspace is ready for document planning:

```
# Required: studio workspace exists
studio/

# Available blueprints (project-level overrides built-in)
studio/blueprints/*.yaml
${CLAUDE_SKILL_DIR}/../../templates/blueprints/*.yaml

# Available writers
studio/agents/writers/*.md

# Planning artifacts
studio/changes/{domain}/
```

**Checks to perform:**
- `studio/` directory exists → if not, abort with message: "No studio workspace found. Run `/studio-planner:event-storm` first."
- At least one blueprint is available (built-in or project-level) → if not, abort with message: "No blueprints found. Run `/studio-docs:blueprint` to create one."
- At least one domain exists in `studio/changes/` → if not, abort with message: "No planning artifacts found. Run `/studio-planner:event-storm` first."

Report the pre-check results before proceeding.

## Step 2: Select Blueprint

Determine which document type to plan:

**If `$ARGUMENTS` specifies a type** (e.g., "建设方案 linxi-edutech"):
- Parse the document type from arguments
- Resolve to a blueprint file: check `studio/blueprints/` first, then built-in `${CLAUDE_SKILL_DIR}/../../templates/blueprints/`
- Match by `display_name` (中文名) or `name` (kebab-case)

**Otherwise:**
- List all available blueprints with their display name, lifecycle stage, and chapter count
- Ask the user to select one

Read the selected blueprint YAML and parse its full structure — chapters, sections, artifact sources, compliance requirements, and style settings.

## Step 3: Select Domain

Determine which planning domain to use:

**If `$ARGUMENTS` specifies a domain** (e.g., "建设方案 linxi-edutech"):
- Use the specified domain
- Verify `studio/changes/{domain}/` exists

**Otherwise:**
- List all domains in `studio/changes/` with a brief summary of available artifacts
- Ask the user to select one

Once the domain is selected, perform a deep scan of all artifacts:

```
studio/changes/{domain}/
├── event-storm.md          # Events, personas, journeys
├── domain-model.md         # Domain boundaries, plugin candidates
├── {plugin}/
│   ├── brief.md            # Plugin brief
│   ├── skill-map.md        # Skill design
│   └── SKILL.md            # Individual skill specs
├── behavior-matrix.md      # Actor-action-data cross-reference
├── persona-insight/        # Persona cards
├── journey-map/            # Journey maps
└── process-flow/           # Process flow diagrams
```

Catalog every artifact found — file path, type, size, and a brief content summary (first heading or description line). This catalog is used in Step 5 for mapping.

## Step 4: Select Writer

Determine which writer agent to use:

**If `$ARGUMENTS` specifies a writer:**
- Resolve to a writer file in `studio/agents/writers/`
- Read the writer definition

**Otherwise:**
- Scan `studio/agents/writers/*.md` for available writers
- If writers exist, list them with domain and style summary, let the user choose
- If no writers exist, inform the user:
  ```
  No document writers found. You can:
  A) Proceed without a writer (uses default formal style)
  B) Create one first: /studio-docs:writer
  ```
- The user can proceed without a writer — the plan will set `writer: null` and write-section will use a default formal Chinese style

## Step 5: Artifact Mapping

This is the core analytical step. For each chapter defined in the blueprint, find and map the relevant planning artifacts.

### Mapping Process

For each chapter in the blueprint:

1. **Read `artifact_sources`** from the blueprint chapter definition — these specify which artifact types the chapter expects (e.g., `event-storm`, `domain-model`, `skill-map`)

2. **Match against actual files** in the domain workspace:
   - `event-storm` → `studio/changes/{domain}/event-storm.md`
   - `domain-model` → `studio/changes/{domain}/domain-model.md`
   - `skill-map` → `studio/changes/{domain}/{plugin}/skill-map.md` (may have multiple)
   - `brief` → `studio/changes/{domain}/{plugin}/brief.md` (may have multiple)
   - `persona-insight` → `studio/changes/{domain}/persona-insight/*.md`
   - `journey-map` → `studio/changes/{domain}/journey-map/*.md`
   - `process-flow` → `studio/changes/{domain}/process-flow/*.md`
   - `behavior-matrix` → `studio/changes/{domain}/behavior-matrix.md`

3. **Plugin-specific chapters** (e.g., "建设内容", "系统设计"):
   - Scan all plugin directories: `studio/changes/{domain}/{plugin}/`
   - Collect `brief.md` and `skill-map.md` from each plugin
   - These chapters typically need sub-task splitting based on plugin count

4. **Evidence-pack blueprints**: If the blueprint declares a top-level `artifact_root` (e.g. `academic-paper.yaml`), skip the studio mapping above and resolve every `artifact_sources` entry relative to that directory after substituting `{paper-id}` (ask the user for it, or take the only subdirectory of `.studio-paper/`). An entry ending in `/` is a directory — include all files in it. A missing file is a **requires-input** gap, never derivable: these blueprints forbid numbers from anywhere else.

5. **Document inheritance**: If the blueprint has `inherits_from` (e.g., 建设方案 inherits from 可研):
   - Check if the previous document exists: `docs/{domain}/{previous_display_name}.md`
   - If found, add it as an artifact reference for chapters that overlap (e.g., "项目概述" can reference the 可研 version)

### Gap Analysis

After mapping, identify:
- **Fully covered chapters**: All expected artifact types have matching files
- **Partially covered chapters**: Some artifact types are missing
- **Uncovered chapters**: No matching artifacts at all — these will likely need manual research or user input

Classify each gap:
- **Derivable**: The content can be reasonably derived from other available artifacts (e.g., "项目背景" can be derived from event-storm even if not explicitly listed)
- **Requires input**: The content needs information not present in any artifact (e.g., "经费预算" typically needs external budget data)
- **Auto-generate**: Certain chapters follow a formula and can be generated from template + basic facts (e.g., "项目管理", "质量保证")

## Step 6: Parallel Strategy

Design a concurrent writing strategy that maximizes throughput while respecting dependencies.

### Chapter Dependency Analysis

Determine which chapters can be written in parallel:
- **Independent chapters**: No cross-references to other chapters (e.g., "项目背景" and "经费预算" are independent)
- **Dependent chapters**: Reference content from other chapters (e.g., "总体设计" depends on "需求分析"; "实施计划" depends on "建设内容")
- **Summary chapters**: Synthesize content from multiple other chapters (e.g., "项目概述" may summarize the entire document — write last)

### Parallel Group Assignment

Group chapters into parallel batches:
- **Group A**: Independent chapters with no prerequisites → write first, all in parallel
- **Group B**: Chapters depending on Group A → write after Group A completes
- **Group C**: Summary/synthesis chapters → write last

Within each group, set `max_concurrent` based on the total task count (typically 2-4 concurrent tasks).

### Adaptive Splitting

For large chapters, split into sub-tasks:

| Estimated Size | Strategy | Task Count |
|---------------|----------|------------|
| < 5,000 chars | `single` — one write task | 1 |
| 5,000–15,000 chars | `by-section` — split by major sections | 2–3 |
| > 15,000 chars | `by-subsection` — split by sub-sections | 4–6 |

Splitting criteria:
- Each sub-task should be self-contained enough to write independently
- Sub-tasks within the same chapter share the same parallel group
- The assemble step will merge sub-tasks back together

## Step 7: Generate Doc Plan

Write the doc plan YAML to `studio/changes/{domain}/doc-plan-{type}.yaml`:

```yaml
document_type: {blueprint-name}
blueprint: {blueprint-filename}.yaml
domain: {domain-name}
writer: {writer-slug}  # or null
target: docs/{domain}/{display_name}.md
parts_dir: docs/{domain}/.parts/
created_at: {ISO-8601 timestamp}

chapters:
  - id: ch1
    title: "一、项目概述"
    parallel_group: A
    artifact_refs:
      - path: studio/changes/{domain}/event-storm.md
        usage: "全局概览、事件清单"
      - path: studio/changes/{domain}/domain-model.md
        usage: "架构概要、插件清单"
    estimated_words: 5000
    split_strategy: single
    status: pending

  - id: ch5
    title: "五、建设内容"
    parallel_group: B
    split_strategy: by-subsection
    sub_tasks:
      - id: ch5a
        title: "（一）~（三）基础模块"
        parallel_group: B
        artifact_refs:
          - path: studio/changes/{domain}/{plugin1}/brief.md
            usage: "模块功能定义"
          - path: studio/changes/{domain}/{plugin1}/skill-map.md
            usage: "技能清单、复杂度评估"
        estimated_words: 6000
        status: pending
      - id: ch5b
        title: "（四）~（五）核心模块"
        parallel_group: B
        artifact_refs:
          - path: studio/changes/{domain}/{plugin2}/brief.md
            usage: "模块功能定义"
          - path: studio/changes/{domain}/{plugin2}/skill-map.md
            usage: "技能清单、复杂度评估"
        estimated_words: 8000
        status: pending

  # ... more chapters

parallel_groups:
  A:
    max_concurrent: 3
    chapters: [ch1, ch2, ch3]
  B:
    max_concurrent: 4
    chapters: [ch5a, ch5b, ch5c, ch5d]
  C:
    max_concurrent: 2
    chapters: [ch6, ch7, ch8, ch9]

recovery:
  retry_limit: 2
  checkpoint_interval: 2000  # chars written between checkpoints
  fallback: manual  # manual / skip / template
```

### Status Values

Each chapter/sub-task tracks its status through the writing lifecycle:
- `pending` — not yet started
- `writing` — currently being written by write-section
- `written` — content generated, saved to parts_dir
- `failed` — write-section failed (will retry up to retry_limit)
- `manual` — flagged for manual writing by the user

### Recovery Configuration

The `recovery` section enables resilient writing:
- `retry_limit`: How many times to retry a failed chapter before marking as `manual`
- `checkpoint_interval`: Characters between intermediate saves (prevents losing progress on large chapters)
- `fallback`: What to do when retries are exhausted:
  - `manual` — mark for human writing
  - `skip` — skip the chapter, add a placeholder
  - `template` — use a generic template for the chapter

## Step 8: Present Plan to User

Show a comprehensive summary for the user to review before confirming.

### Chapter → Artifact Mapping Table

```
Chapter                     | Artifacts                          | Coverage
----------------------------|------------------------------------|----------
一、项目概述                 | event-storm, domain-model          | ✅ Full
二、现状分析                 | event-storm, persona-insight       | ✅ Full
三、需求分析                 | event-storm, journey-map           | ⚠️ Partial
四、总体设计                 | domain-model                       | ✅ Full
五、建设内容 (split: 4 tasks)| plugin briefs, skill-maps          | ✅ Full
六、信息安全                 | (template)                         | 📝 Auto
七、实施计划                 | process-flow                       | ⚠️ Partial
八、经费预算                 | (none)                             | ❌ Manual
九、效益分析                 | event-storm                        | ⚠️ Partial
```

### Parallel Strategy Diagram

```
Phase 1 (Group A):  ch1 ──┐
                    ch2 ──┼── parallel (max 3)
                    ch3 ──┘
                         ↓
Phase 2 (Group B):  ch5a ─┐
                    ch5b ─┼── parallel (max 4)
                    ch5c ─┤
                    ch5d ─┘
                         ↓
Phase 3 (Group C):  ch6 ──┐
                    ch7 ──┤
                    ch8 ──┼── parallel (max 2)
                    ch9 ──┘
```

### Summary Statistics

```
Total chapters:     {count}
Total sub-tasks:    {count}
Estimated words:    {total} (~{pages} pages)
Full coverage:      {count} chapters
Partial coverage:   {count} chapters
Manual required:    {count} chapters
Writer:             {writer name or "default formal style"}
```

### Gap Highlights

If any chapters have gaps, highlight them:
```
⚠️  Gaps requiring attention:
  - 三、需求分析: Missing journey-map artifacts. Consider running /studio-planner:journey-map first.
  - 八、经费预算: No matching artifacts. You'll need to provide budget data manually.
```

Ask the user to confirm or adjust the plan. They can:
- Approve the plan as-is
- Change parallel grouping
- Mark specific chapters as `manual` upfront
- Adjust estimated word counts
- Exclude optional chapters

## Step 9: Report

After the user confirms, print the final summary:

```
Doc plan created: studio/changes/{domain}/doc-plan-{type}.yaml

  Document:       {display_name}
  Blueprint:      {blueprint file}
  Domain:         {domain}
  Writer:         {writer or "default"}
  Target:         docs/{domain}/{display_name}.md
  
  Chapters:       {count} ({sub_task_count} writing tasks)
  Parallel phases: {phase_count}
  Est. total:     {word_count} words (~{page_count} pages)
  
  Coverage:
    ✅ Full:      {count}
    ⚠️ Partial:   {count}
    📝 Auto:      {count}
    ❌ Manual:    {count}

Next step: Run `/studio-docs:generate {domain} {type}` to start writing.

The generate command will:
  1. Read this doc plan
  2. Write each chapter using write-section (respecting parallel groups)
  3. Assemble all parts into the final document
  4. Run compliance checks against the blueprint
```

## Tips

- **Run plan-document before generate** — always plan first so you can review the artifact mapping and parallel strategy. Jumping straight to generate without a plan leads to poorly structured documents.
- **Iterate on the plan** — if the artifact mapping shows gaps, pause and run the missing planning skills (event-storm, journey-map, etc.) before proceeding.
- **Multiple plans can coexist** — you can have `doc-plan-feasibility-study.yaml` and `doc-plan-construction-plan.yaml` in the same domain, each planning a different document from the same artifacts.
- **Plan is resumable** — if generation is interrupted, the status fields in the plan track which chapters are done. Re-running generate picks up where it left off.
- **Manual chapters are not failures** — some chapters (like 经费预算) inherently need human input. Marking them as `manual` upfront is better than generating low-quality content.
